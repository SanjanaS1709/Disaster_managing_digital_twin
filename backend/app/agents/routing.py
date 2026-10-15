"""Validated, deterministic road-graph construction and Dijkstra route proposals.

Roads are directed from ``source`` to optional ``target``; reverse travel is
added only when ``bidirectional`` is explicitly true. Coordinate locations map
only to exact road endpoint coordinates, never to a nearby node. Missing
connectivity is omitted rather than inferred.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TypeAlias

import networkx as nx
from pydantic import ValidationError

from app.schemas.contracts import Coordinate, GeoJSONLineString, RoadContract, RouteResult

RoadData: TypeAlias = RoadContract | Mapping[str, object]
Location: TypeAlias = str | Coordinate | list[float]
EARTH_RADIUS_METERS = 6_371_008.8


@dataclass(frozen=True)
class RoadGraph:
    """Feasible graph plus roads excluded at the boundary with explanations."""

    graph: nx.MultiDiGraph
    excluded_roads: tuple[tuple[str, str], ...]
    node_coordinates: Mapping[str, Coordinate]


def _as_road(road: RoadData) -> RoadContract:
    if isinstance(road, RoadContract):
        return road
    return RoadContract.model_validate(road)


def _haversine_distance(coordinates: Sequence[Coordinate]) -> float:
    distance = 0.0
    for (lon1, lat1), (lon2, lat2) in zip(coordinates, coordinates[1:]):
        phi1, phi2 = math.radians(lat1), math.radians(lat2)
        delta_phi = math.radians(lat2 - lat1)
        delta_lambda = math.radians(lon2 - lon1)
        a = (
            math.sin(delta_phi / 2) ** 2
            + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2
        )
        distance += 2 * EARTH_RADIUS_METERS * math.asin(math.sqrt(min(1.0, a)))
    return distance


def _set_node_coordinate(
    node_coordinates: dict[str, Coordinate], node_id: str, coordinate: Coordinate
) -> None:
    existing = node_coordinates.get(node_id)
    if existing is not None and existing != coordinate:
        raise ValueError(
            f"node {node_id!r} has conflicting endpoint coordinates {existing!r} and {coordinate!r}"
        )
    node_coordinates[node_id] = coordinate


def build_road_graph(roads: Sequence[RoadData], risk_weight: float = 5.0) -> RoadGraph:
    """Build a directed multigraph; only open roads with explicit endpoints are edges."""
    if not math.isfinite(risk_weight) or risk_weight < 0:
        raise ValueError("risk_weight must be finite and non-negative")

    parsed_roads = [_as_road(road) for road in roads]
    road_ids = [road.id for road in parsed_roads]
    if len(road_ids) != len(set(road_ids)):
        raise ValueError("road IDs must be unique within a graph")

    graph = nx.MultiDiGraph()
    node_coordinates: dict[str, Coordinate] = {}
    excluded: list[tuple[str, str]] = []

    for road in sorted(parsed_roads, key=lambda item: item.id):
        if road.target is None:
            excluded.append((road.id, "missing target node; connectivity is not established"))
            continue
        if road.source == road.target:
            raise ValueError(f"road {road.id!r} cannot connect a node to itself")

        coordinates = road.geometry.coordinates
        source_coordinate, target_coordinate = coordinates[0], coordinates[-1]
        _set_node_coordinate(node_coordinates, road.source, source_coordinate)
        _set_node_coordinate(node_coordinates, road.target, target_coordinate)
        graph.add_node(road.source, coordinate=source_coordinate)
        graph.add_node(road.target, coordinate=target_coordinate)

        if road.status != "open":
            excluded.append((road.id, f"road status {road.status!r} is not traversable"))
            continue

        distance_m = road.distance_m or _haversine_distance(coordinates)
        if not math.isfinite(distance_m) or distance_m <= 0:
            raise ValueError(f"road {road.id!r} must have a positive finite distance")
        cost = distance_m * (1.0 + risk_weight * road.risk_score)
        edge_data = {
            "road_id": road.id,
            "source": road.source,
            "destination": road.target,
            "distance_m": distance_m,
            "risk_score": road.risk_score,
            "geometry": tuple(coordinates),
            "cost": cost,
        }
        graph.add_edge(road.source, road.target, key=road.id, **edge_data)
        if road.bidirectional:
            reverse_geometry = tuple(reversed(coordinates))
            graph.add_edge(
                road.target,
                road.source,
                key=road.id,
                road_id=road.id,
                source=road.target,
                destination=road.source,
                distance_m=distance_m,
                risk_score=road.risk_score,
                geometry=reverse_geometry,
                cost=cost,
            )

    return RoadGraph(
        graph=graph,
        excluded_roads=tuple(excluded),
        node_coordinates=node_coordinates,
    )


def _location_label(location: Location) -> str:
    if isinstance(location, str):
        return location
    coordinate = tuple(location)
    return f"[{coordinate[0]}, {coordinate[1]}]"


def _resolve_location(
    location: Location,
    road_graph: RoadGraph,
) -> tuple[str | None, str | None]:
    if isinstance(location, str):
        if location in road_graph.graph:
            return location, None
        return None, f"node {location!r} is absent from the road graph"

    try:
        parsed = GeoJSONLineString(
            coordinates=[location, location]  # Reuse the shared longitude/latitude validator.
        ).coordinates[0]
    except (TypeError, ValidationError) as error:
        return None, f"invalid coordinate location: {error}"
    matches = [
        node_id
        for node_id, endpoint in road_graph.node_coordinates.items()
        if endpoint == parsed
    ]
    if not matches:
        return None, f"coordinate {parsed!r} has no exact road-endpoint mapping"
    if len(matches) > 1:
        return None, f"coordinate {parsed!r} maps ambiguously to nodes {sorted(matches)!r}"
    return matches[0], None


def route_between(
    roads: Sequence[RoadData],
    source: Location,
    target: Location,
    risk_weight: float = 5.0,
) -> RouteResult:
    """Return the minimum-cost connected path, or an explicit unreachable result."""
    road_graph = build_road_graph(roads, risk_weight=risk_weight)
    graph = road_graph.graph
    source_node, source_error = _resolve_location(source, road_graph)
    target_node, target_error = _resolve_location(target, road_graph)
    source_label = source_node or _location_label(source)
    target_label = target_node or _location_label(target)

    if source_error or target_error:
        return RouteResult(
            reachable=False,
            source=source_label,
            target=target_label,
            failure_reason="; ".join(error for error in (source_error, target_error) if error),
        )
    assert source_node is not None and target_node is not None

    if source_node == target_node:
        return RouteResult(
            reachable=True,
            source=source_node,
            target=target_node,
            nodes=[source_node],
            road_ids=[],
            route_geometry=None,
            total_cost=0.0,
        )

    try:
        nodes = nx.dijkstra_path(graph, source_node, target_node, weight="cost")
    except (nx.NetworkXNoPath, nx.NodeNotFound):
        return RouteResult(
            reachable=False,
            source=source_node,
            target=target_node,
            failure_reason="destination is disconnected or inaccessible on traversable roads",
        )

    road_ids: list[str] = []
    merged_coordinates: list[Coordinate] = []
    total_cost = 0.0
    for edge_source, edge_target in zip(nodes, nodes[1:]):
        edge_options = graph.get_edge_data(edge_source, edge_target)
        if not edge_options:
            raise RuntimeError("Dijkstra returned a node transition absent from the graph")
        selected = min(
            edge_options.values(),
            key=lambda edge: (edge["cost"], edge["road_id"]),
        )
        if selected["source"] != edge_source or selected["destination"] != edge_target:
            raise RuntimeError("selected road edge does not match its path transition")
        road_ids.append(selected["road_id"])
        total_cost += selected["cost"]
        edge_coordinates = list(selected["geometry"])
        if merged_coordinates and merged_coordinates[-1] == edge_coordinates[0]:
            edge_coordinates = edge_coordinates[1:]
        merged_coordinates.extend(edge_coordinates)

    route_geometry = GeoJSONLineString(coordinates=merged_coordinates)
    return RouteResult(
        reachable=True,
        source=source_node,
        target=target_node,
        nodes=nodes,
        road_ids=road_ids,
        route_geometry=route_geometry,
        total_cost=total_cost,
    )
