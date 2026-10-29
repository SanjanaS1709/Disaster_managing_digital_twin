"""Road-following route suggestions via an OSRM-compatible routing service."""

from __future__ import annotations

import logging
import math
import os

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator

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


@router.post("/calculate")
async def calculate_route(body: RouteRequest) -> dict:
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
