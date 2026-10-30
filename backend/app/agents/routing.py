"""Validated, deterministic road-graph construction and Dijkstra route proposals.

Roads are directed from ``source`` to optional ``target``; reverse travel is
added only when ``bidirectional`` is explicitly true. Coordinate locations map
to exact road endpoints by default; callers may opt into bounded snapping to a
nearby connected node. Missing connectivity is omitted rather than inferred.
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
DEFAULT_MAX_SNAP_DISTANCE_M = 1_500.0


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


def _nearest_connected_node(
    location: Coordinate,
    road_graph: RoadGraph,
    max_distance_m: float,
    endpoint: str,
) -> tuple[str | None, float | None]:
    """Snap an asset coordinate to a nearby node with traversable graph edges."""
    candidates = (
        (node_id, coordinate)
        for node_id, coordinate in road_graph.node_coordinates.items()
        if node_id in road_graph.graph
        and (
            road_graph.graph.out_degree(node_id) > 0
            if endpoint == "source"
            else road_graph.graph.in_degree(node_id) > 0
        )
    )
    nearest = min(
        (
            (_haversine_distance([location, coordinate]), node_id)
            for node_id, coordinate in candidates
        ),
        default=None,
    )
    if nearest is None or nearest[0] > max_distance_m:
        return None, None
    return nearest[1], nearest[0]


def route_between(
    roads: Sequence[RoadData],
    source: Location,
    target: Location,
    risk_weight: float = 5.0,
    *,
    prebuilt_graph: RoadGraph | None = None,
    shortest_path_cache: dict | None = None,
    snap_locations: bool = False,
    max_snap_distance_m: float = DEFAULT_MAX_SNAP_DISTANCE_M,
    location_snap_cache: dict | None = None,
) -> RouteResult:
    """Return the minimum-cost connected path, or an explicit unreachable result."""
    # Workflow evaluation routes many unit/shelter pairs over the same snapshot.
    # Reuse one validated graph instead of parsing the entire OSM network per pair.
    road_graph = prebuilt_graph or build_road_graph(roads, risk_weight=risk_weight)
    graph = road_graph.graph
    source_node, source_error = _resolve_location(source, road_graph)
    target_node, target_error = _resolve_location(target, road_graph)
    source_coordinate = target_coordinate = None
    source_connector_m = target_connector_m = 0.0
    if snap_locations:
        for location, error, label in (
            (source, source_error, "source"),
            (target, target_error, "target"),
        ):
            if error is None or isinstance(location, str):
                continue
            try:
                coordinate = GeoJSONLineString(coordinates=[location, location]).coordinates[0]
            except (TypeError, ValidationError):
                continue
            cache_key = (tuple(coordinate), label)
            cached = location_snap_cache.get(cache_key) if location_snap_cache is not None else None
            snapped = cached if cached is not None else _nearest_connected_node(
                coordinate, road_graph, max_snap_distance_m, label
            )
            if location_snap_cache is not None and cached is None:
                location_snap_cache[cache_key] = snapped
            snapped_node, connector_m = snapped
            if snapped_node is None:
                snap_error = (
                    f"{label} coordinate {coordinate!r} has no connected road node "
                    f"within {max_snap_distance_m:.0f} m"
                )
                if label == "source":
                    source_error = snap_error
                else:
                    target_error = snap_error
                continue
            if label == "source":
                source_node, source_error = snapped_node, None
                source_coordinate, source_connector_m = coordinate, connector_m or 0.0
            else:
                target_node, target_error = snapped_node, None
                target_coordinate, target_connector_m = coordinate, connector_m or 0.0
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
        coordinates = [road_graph.node_coordinates[source_node]]
        if source_coordinate is not None:
            coordinates.insert(0, source_coordinate)
        if target_coordinate is not None:
            coordinates.append(target_coordinate)
        return RouteResult(
            reachable=True,
            source=source_node,
            target=target_node,
            nodes=[source_node],
            road_ids=[],
            route_geometry=GeoJSONLineString(coordinates=coordinates) if len(coordinates) >= 2 else None,
            total_cost=source_connector_m + target_connector_m,
        )

    try:
        if shortest_path_cache is None:
            nodes = nx.dijkstra_path(graph, source_node, target_node, weight="cost")
        else:
            if source_node not in shortest_path_cache:
                shortest_path_cache[source_node] = nx.single_source_dijkstra(
                    graph, source_node, weight="cost"
                )
            nodes = shortest_path_cache[source_node][1].get(target_node)
            if nodes is None:
                raise nx.NetworkXNoPath
    except (nx.NetworkXNoPath, nx.NodeNotFound):
        return RouteResult(
            reachable=False,
            source=source_node,
            target=target_node,
            failure_reason="destination is disconnected or inaccessible on traversable roads",
        )

    road_ids: list[str] = []
    merged_coordinates: list[Coordinate] = [source_coordinate] if source_coordinate is not None else []
    total_cost = source_connector_m + target_connector_m
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

    if target_coordinate is not None and merged_coordinates[-1] != target_coordinate:
        merged_coordinates.append(target_coordinate)

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
