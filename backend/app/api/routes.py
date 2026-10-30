"""Road-following route suggestions via an OSRM-compatible routing service."""

from __future__ import annotations

import logging
import math
import os

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator
import networkx as nx

from ..state_manager import state_manager
from ..road_loader import routing_graph

router = APIRouter(prefix="/api/routes", tags=["routes"])
logger = logging.getLogger(__name__)


class RoutePoint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # GeoJSON and the OSRM URL both use [longitude, latitude].
    coordinates: list[float] = Field(min_length=2, max_length=2)
    label: str = Field(default="Selected point", max_length=120)

    @model_validator(mode="after")
    def validate_wgs84(self) -> "RoutePoint":
        longitude, latitude = self.coordinates
        if not (-180 <= longitude <= 180 and -90 <= latitude <= 90):
            raise ValueError("Coordinates must be [longitude, latitude] within WGS84 bounds")
        return self


class RouteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    origin: RoutePoint
    destination: RoutePoint

    @model_validator(mode="after")
    def points_must_differ(self) -> "RouteRequest":
        if self.origin.coordinates == self.destination.coordinates:
            raise ValueError("Origin and destination must be different locations")
        return self


class RouteCompareRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    origin: RoutePoint
    destinations: list[RoutePoint] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def destinations_must_differ(self) -> "RouteCompareRequest":
        points = [tuple(item.coordinates) for item in self.destinations]
        if tuple(self.origin.coordinates) in points or len(points) != len(set(points)):
            raise ValueError("Comparison destinations must be distinct from the origin and each other")
        return self


def _instruction(step: dict) -> str:
    maneuver = step.get("maneuver") or {}
    kind = str(maneuver.get("type") or "continue").replace("_", " ")
    modifier = str(maneuver.get("modifier") or "").replace("_", " ")
    road = step.get("name") or step.get("ref")
    if kind == "arrive":
        text = "Arrive at your destination"
    elif kind == "depart":
        text = "Start"
    else:
        text = f"{kind.capitalize()}{f' {modifier}' if modifier else ''}"
    return f"{text} onto {road}" if road else text


def _valid_metric(value: object) -> bool:
    return isinstance(value, (int, float)) and math.isfinite(value) and value >= 0


def _distance_m(a: list[float], b: list[float]) -> float:
    lon1, lat1 = map(math.radians, a)
    lon2, lat2 = map(math.radians, b)
    dlat, dlon = lat2 - lat1, lon2 - lon1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 6_371_008.8 * 2 * math.asin(math.sqrt(h))


def _valid_geometry(geometry: object) -> bool:
    if not isinstance(geometry, dict) or geometry.get("type") != "LineString":
        return False
    coordinates = geometry.get("coordinates")
    if not isinstance(coordinates, list) or len(coordinates) < 2:
        return False
    return all(
        isinstance(point, list) and len(point) >= 2
        and all(isinstance(value, (int, float)) and math.isfinite(value) for value in point[:2])
        and -180 <= point[0] <= 180 and -90 <= point[1] <= 90
        for point in coordinates
    )


def _weather_aware_route(origin: list[float], destination: list[float], state=None) -> dict:
    """Route on the local OSM graph after weather events have closed roads."""
    state = state or state_manager.get_state()
    graph = routing_graph(state)
    if not graph.number_of_nodes():
        raise HTTPException(status_code=404, detail="No traversable roads remain in the simulated area.")
    graph.remove_nodes_from(list(nx.isolates(graph)))
    if not graph.number_of_nodes():
        raise HTTPException(status_code=404, detail="No traversable roads remain in the simulated area.")
    try:
        import osmnx as ox
        start = ox.distance.nearest_nodes(graph, origin[0], origin[1])
        end = ox.distance.nearest_nodes(graph, destination[0], destination[1])
        nodes = nx.shortest_path(graph, start, end, weight=lambda u, v, data: min(
            edge['length'] * (1 + 5 * edge.get('risk_score', 0)) for edge in data.values()
        ))
    except ImportError as error:
        if 'scikit-learn' in str(error):
            raise HTTPException(
                status_code=503,
                detail='Weather-aware routing requires scikit-learn. Install backend requirements and restart FastAPI.',
            ) from None
        raise
    except (nx.NetworkXNoPath, nx.NodeNotFound, ValueError):
        raise HTTPException(status_code=404, detail="No route avoids the roads blocked by simulated weather.") from None
    coordinates = [origin]
    distance = 0.0
    for u, v in zip(nodes, nodes[1:]):
        options = graph.get_edge_data(u, v)
        edge = min(options.values(), key=lambda item: item['length'] * (1 + 5 * item.get('risk_score', 0)))
        points = list(edge['geometry'].coords)
        if points[0] != (graph.nodes[u]['x'], graph.nodes[u]['y']):
            points.reverse()
        coordinates.extend([list(point) for point in points[1:]])
        distance += edge['length']
    coordinates.append(destination)
    start_coordinate = [graph.nodes[start]['x'], graph.nodes[start]['y']]
    end_coordinate = [graph.nodes[end]['x'], graph.nodes[end]['y']]
    route_distance = distance + _distance_m(origin, start_coordinate) + _distance_m(destination, end_coordinate)
    return {
        "origin": {"coordinates": origin, "label": "Selected point"},
        "destination": {"coordinates": destination, "label": "Selected point"},
        "route": {"geometry": {"type": "LineString", "coordinates": coordinates},
                  "distance_m": route_distance, "duration_s": route_distance / 8.3,
                  "steps": [{"instruction": "Follow the weather adjusted mapped road route", "distance_m": route_distance, "duration_s": route_distance / 8.3}]},
        "alternatives_count": 0, "safety_status": "simulated_road_avoidance",
        "safety_message": "Route avoids blocked roads and penalizes restricted roads in this simulation. Conditions are simulated and do not verify real-world safety.",
        "source": "Local OpenStreetMap road graph and simulated road status",
    }


@router.post("/calculate")
async def calculate_route(body: RouteRequest) -> dict:
    state = state_manager.get_state()
    if any(road.status != "open" for road in state.roads):
        return _weather_aware_route(body.origin.coordinates, body.destination.coordinates, state)
    base_url = os.getenv("OSRM_BASE_URL", "https://router.project-osrm.org").strip().rstrip("/")
    if not base_url.startswith(("https://", "http://")):
        raise HTTPException(status_code=500, detail="OSRM_BASE_URL must be an http(s) URL")
    origin_lon, origin_lat = body.origin.coordinates
    dest_lon, dest_lat = body.destination.coordinates
    url = f"{base_url}/route/v1/driving/{origin_lon},{origin_lat};{dest_lon},{dest_lat}"
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(12.0, connect=5.0)) as client:
            response = await client.get(url, params={"overview": "full", "geometries": "geojson", "steps": "true", "alternatives": "2"})
            response.raise_for_status()
            payload = response.json()
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="The routing service timed out. Please try again.") from None
    except (httpx.HTTPError, ValueError) as error:
        logger.warning("Routing service request failed (%s)", type(error).__name__)
        raise HTTPException(status_code=502, detail="The routing service could not be reached. Please try again.") from None

    if not isinstance(payload, dict):
        raise HTTPException(status_code=502, detail="The routing service returned an invalid response.")
    if payload.get("code") != "Ok" or not payload.get("routes"):
        code = payload.get("code")
        message = "No road route was found between these locations." if code == "NoRoute" else "The routing service could not calculate this route."
        raise HTTPException(status_code=404 if code == "NoRoute" else 502, detail=message)

    candidates = []
    for route in payload["routes"]:
        geometry = route.get("geometry")
        coordinates = geometry.get("coordinates") if isinstance(geometry, dict) else None
        if not _valid_geometry(geometry) or not _valid_metric(route.get("distance")) or not _valid_metric(route.get("duration")):
            continue
        legs = route.get("legs") or []
        steps = [
            {"instruction": _instruction(step), "distance_m": step.get("distance", 0), "duration_s": step.get("duration", 0)}
            for leg in legs for step in leg.get("steps", [])
        ]
        candidates.append({"geometry": geometry, "distance_m": route.get("distance"), "duration_s": route.get("duration"), "steps": steps})
    if not candidates:
        raise HTTPException(status_code=502, detail="The routing service returned invalid route geometry.")

    # OSRM alternatives are real road routes. Choose the quickest; distance breaks ties.
    candidates.sort(key=lambda route: (route["duration_s"], route["distance_m"]))
    return {
        "origin": body.origin.model_dump(),
        "destination": body.destination.model_dump(),
        "route": candidates[0],
        "alternatives_count": len(candidates) - 1,
        "safety_status": "not_verified",
        "safety_message": "Route follows mapped roads, but disaster safety is not verified. Current road closures, flooding, and passability are not connected to this routing service.",
        "source": "OSRM / OpenStreetMap road network",
    }


@router.post("/compare")
async def compare_destinations(body: RouteCompareRequest) -> dict:
    """Rank candidate destinations from one origin using OSRM road distance and time."""
    base_url = os.getenv("OSRM_BASE_URL", "https://router.project-osrm.org").strip().rstrip("/")
    if not base_url.startswith(("https://", "http://")):
        raise HTTPException(status_code=500, detail="OSRM_BASE_URL must be an http(s) URL")
    points = [body.origin, *body.destinations]
    coordinates = ";".join(f"{point.coordinates[0]},{point.coordinates[1]}" for point in points)
    url = f"{base_url}/table/v1/driving/{coordinates}"
    destination_indexes = ";".join(str(index) for index in range(1, len(points)))
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(12.0, connect=5.0)) as client:
            response = await client.get(url, params={"sources": "0", "destinations": destination_indexes, "annotations": "distance,duration"})
            response.raise_for_status()
            payload = response.json()
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="The routing service timed out. Please try again.") from None
    except (httpx.HTTPError, ValueError) as error:
        logger.warning("Routing comparison request failed (%s)", type(error).__name__)
        raise HTTPException(status_code=502, detail="The routing service could not be reached. Please try again.") from None
    if not isinstance(payload, dict) or payload.get("code") != "Ok":
        raise HTTPException(status_code=502, detail="The routing service could not compare these destinations.")
    distance_rows = payload.get("distances")
    duration_rows = payload.get("durations")
    if not isinstance(distance_rows, list) or not distance_rows or not isinstance(distance_rows[0], list) or not isinstance(duration_rows, list) or not duration_rows or not isinstance(duration_rows[0], list):
        raise HTTPException(status_code=502, detail="The routing service returned an invalid comparison matrix.")
    distances = distance_rows[0]
    durations = duration_rows[0]
    candidates = [
        {"destination": point.model_dump(), "distance_m": distances[index], "duration_s": durations[index]}
        for index, point in enumerate(body.destinations)
        if index < len(distances) and index < len(durations) and _valid_metric(distances[index]) and _valid_metric(durations[index])
    ]
    if not candidates:
        raise HTTPException(status_code=404, detail="No candidate destinations could be reached by road.")
    max_distance = max(item["distance_m"] for item in candidates) or 1
    max_duration = max(item["duration_s"] for item in candidates) or 1
    for item in candidates:
        item["rank_score"] = (item["distance_m"] / max_distance + item["duration_s"] / max_duration) / 2
    candidates.sort(key=lambda item: (item["rank_score"], item["duration_s"], item["distance_m"]))
    for rank, item in enumerate(candidates, start=1):
        item["rank"] = rank
    return {
        "origin": body.origin.model_dump(),
        "candidates": candidates,
        "unreachable_count": len(body.destinations) - len(candidates),
        "safety_status": "not_verified",
        "safety_message": "Ranked by mapped road distance and estimated time. Disaster hazards, closures, and current passability are not verified.",
    }
