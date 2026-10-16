"""Pure event reducer: validate and change a copy, never mutate the input state."""
from .models import DisasterEvent, RainfallMeasurement, TwinState


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
        entity.status, entity.risk_score = 'blocked', p['risk_score']
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
    candidate.timestamp = event.timestamp
    candidate.revision += 1
    return TwinState.model_validate(candidate.model_dump())
