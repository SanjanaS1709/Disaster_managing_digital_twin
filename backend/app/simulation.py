"""Pure event reducer: validate and change a copy, never mutate the input state."""
from math import cos, radians, sqrt

from .models import DisasterEvent, RainfallMeasurement, TwinState


def _distance_to_road_m(center, coordinates):
    """Approximate point-to-polyline distance in meters near the impact center."""
    lon0, lat0 = center
    cos_lat = cos(radians(lat0))
    points = [((lon - lon0) * 111_320 * cos_lat, (lat - lat0) * 110_574) for lon, lat in coordinates]
    nearest = float('inf')
    for (x1, y1), (x2, y2) in zip(points, points[1:]):
        dx, dy = x2 - x1, y2 - y1
        length_squared = dx * dx + dy * dy
        fraction = 0 if length_squared == 0 else max(0, min(1, -(x1 * dx + y1 * dy) / length_squared))
        nearest = min(nearest, sqrt((x1 + fraction * dx) ** 2 + (y1 + fraction * dy) ** 2))
    return nearest


def rainfall_severity(value: float, unit: str) -> float:
    """Map a simulated rainfall reading to a deterministic impact severity."""
    intensity = value if unit == 'mm/hour' else value / 2
    if intensity < 15:
        return 0.25
    if intensity < 30:
        return 0.5
    if intensity < 50:
        return 0.75
    return 1.0


def road_weather_impact(roads, center, severity: float, target_road_id: str | None = None):
    """Return status/risk overlays for roads in a severity-scaled local area."""
    if severity <= 0.25:
        blocked_radius, restricted_radius = 0, 250
    elif severity <= 0.5:
        blocked_radius, restricted_radius = 250, 800
    elif severity <= 0.75:
        blocked_radius, restricted_radius = 600, 1500
    else:
        blocked_radius, restricted_radius = 1000, 2500

    updates = {}
    for road in roads:
        road_id = road.id if hasattr(road, 'id') else road['id']
        coordinates = road.coordinates if hasattr(road, 'coordinates') else road.get('coordinates') or road.get('geometry', {}).get('coordinates', [])
        if not coordinates:
            continue
        distance = 0 if road_id == target_road_id else _distance_to_road_m(center, coordinates)
        status = road.status if hasattr(road, 'status') else road.get('status', 'open')
        risk_score = road.risk_score if hasattr(road, 'risk_score') else road.get('risk_score', 0.0)
        if blocked_radius and distance <= blocked_radius:
            status = 'blocked'
        elif distance <= restricted_radius and status == 'open':
            status = 'restricted'
        else:
            continue
        proximity_severity = severity * (1 - 0.35 * min(1, distance / restricted_radius))
        updates[road_id] = {
            'status': status,
            'risk_score': max(risk_score, min(1.0, proximity_severity)),
        }
    return updates


def reduce_event(state: TwinState, event: DisasterEvent) -> TwinState:
    if event.simulation_id != state.simulation_id:
        raise ValueError('Invalid simulation ID')
    if event.timestamp < state.timestamp:
        raise ValueError('Event timestamp is older than the simulation clock')
    candidate = state.model_copy(deep=True)
    group = {'ROAD_FLOODED': candidate.roads, 'ROAD_REOPENED': candidate.roads,
             'SHELTER_OVER_CAPACITY': candidate.shelters,
             'RESOURCE_SHORTAGE': candidate.relief_units,
             'RAINFALL_INCREASE': candidate.zones}[event.type]
    entity = next((entity for entity in group if entity.id == event.target_id), None)
    if entity is None:
        raise ValueError(f'Unknown target ID {event.target_id} for {event.type}')
    p = event.payload
    if event.type == 'ROAD_FLOODED':
        center = entity.coordinates[len(entity.coordinates) // 2]
        updates = road_weather_impact(candidate.roads, center, p['risk_score'], entity.id)
        for road in candidate.roads:
            if road.id in updates:
                road.status = updates[road.id]['status']
                road.risk_score = updates[road.id]['risk_score']
    elif event.type == 'ROAD_REOPENED':
        entity.status, entity.risk_score = p['status'], p['risk_score']
    elif event.type == 'SHELTER_OVER_CAPACITY':
        entity.occupancy = p['occupancy']
    elif event.type == 'RESOURCE_SHORTAGE':
        item = p['item']
        if item not in entity.inventory:
            raise ValueError(f'Unknown inventory item: {item}')
        if entity.inventory_units.get(item) != p['unit']:
            raise ValueError(f'Inventory unit mismatch for {item}')
        if entity.inventory[item] < p['quantity']:
            raise ValueError(f'Insufficient inventory for {item}')
        entity.inventory[item] -= p['quantity']
    else:
        previous = next((m for m in reversed(entity.rainfall) if m.unit == p['unit']), None)
        observed_at = event.observed_at or event.timestamp
        if previous is not None and observed_at < previous.timestamp:
            raise ValueError('Rainfall observation timestamp is older than the previous measurement in this unit')
        if previous is not None and p['value'] < previous.value:
            raise ValueError('RAINFALL_INCREASE cannot decrease the previous value in the same unit')
        entity.rainfall.append(RainfallMeasurement(**p, timestamp=observed_at))
        # Rainfall scales local restrictions and closures in this illustrative model.
        if entity.location is not None:
            severity = rainfall_severity(p['value'], p['unit'])
            updates = road_weather_impact(candidate.roads, entity.location, severity)
            for road in candidate.roads:
                if road.id in updates:
                    road.status = updates[road.id]['status']
                    road.risk_score = updates[road.id]['risk_score']
    candidate.timestamp = event.timestamp
    candidate.revision += 1
    return TwinState.model_validate(candidate.model_dump())
