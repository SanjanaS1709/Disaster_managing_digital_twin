"""Validated, additive contracts shared by simulation, routing and visualization."""
from typing import Annotated, Literal, Self

from pydantic import (AfterValidator, AwareDatetime, BaseModel, ConfigDict, Field,
                      computed_field, model_validator)

Identifier = Annotated[str, Field(min_length=1, pattern=r'^\S(?:.*\S)?$')]
Quantity = Annotated[int, Field(strict=True, ge=0)]
Risk = Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]


def validate_coordinate(value: list[float]) -> list[float]:
    if len(value) != 2 or not (-180 <= value[0] <= 180 and -90 <= value[1] <= 90):
        raise ValueError('Coordinates must be [longitude, latitude] within WGS84 bounds')
    return value


Coordinate = Annotated[list[float], AfterValidator(validate_coordinate)]


class Road(BaseModel):
    id: Identifier
    name: str = 'Unnamed Road'
    u: str | None = None
    v: str | None = None
    key: int | None = None
    length_m: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    source: str = 'synthetic_demo'
    status: Literal['open', 'restricted', 'blocked'] = 'open'
    risk_score: Risk = 0.0
    coordinates: list[Coordinate] = Field(min_length=2)


class Shelter(BaseModel):
    id: Identifier
    name: str
    location: Coordinate
    capacity: Quantity
    occupancy: Quantity
    demand: dict[Identifier, Quantity] = Field(default_factory=dict)
    source: str = 'synthetic_demo'

    @computed_field
    @property
    def over_capacity(self) -> bool:
        return self.occupancy > self.capacity

    @computed_field
    @property
    def excess_occupancy(self) -> int:
        return max(0, self.occupancy - self.capacity)


class ReliefUnit(BaseModel):
    id: Identifier
    location: Coordinate
    inventory: dict[Identifier, Quantity] = Field(default_factory=dict)
    inventory_units: dict[Identifier, Identifier] = Field(default_factory=dict)
    source: str = 'synthetic_demo'


class RainfallMeasurement(BaseModel):
    value: float = Field(ge=0, allow_inf_nan=False)
    unit: Literal['mm', 'mm/hour']
    timestamp: AwareDatetime
    source: Literal['simulation'] = 'simulation'


class DisasterZone(BaseModel):
    id: Identifier
    location: Coordinate | None = None
    risk_score: Risk
    affected_population: Quantity
    rainfall: list[RainfallMeasurement] = Field(default_factory=list)
    source: str = 'synthetic_demo'


class TwinState(BaseModel):
    simulation_id: Identifier
    timestamp: AwareDatetime
    revision: int = Field(default=1, ge=1)
    roads: list[Road]
    shelters: list[Shelter]
    relief_units: list[ReliefUnit]
    zones: list[DisasterZone]

    @model_validator(mode='after')
    def unique_ids(self) -> Self:
        ids = [e.id for group in (self.roads, self.shelters, self.relief_units, self.zones) for e in group]
        if len(ids) != len(set(ids)):
            raise ValueError('Entity IDs must be globally unique within a twin')
        return self


class Payload(BaseModel):
    model_config = ConfigDict(extra='forbid')


class FloodPayload(Payload):
    risk_score: Risk = 1.0


class ReopenPayload(Payload):
    status: Literal['open', 'restricted'] = 'open'
    risk_score: Risk = 0.2


class ShelterPayload(Payload):
    occupancy: Quantity


class ShortagePayload(Payload):
    item: Identifier
    quantity: Annotated[int, Field(strict=True, gt=0)]
    unit: Identifier


class RainfallPayload(Payload):
    value: float = Field(ge=0, allow_inf_nan=False)
    unit: Literal['mm', 'mm/hour']


PAYLOAD_MODELS = {
    'ROAD_FLOODED': FloodPayload, 'ROAD_REOPENED': ReopenPayload,
    'SHELTER_OVER_CAPACITY': ShelterPayload, 'RESOURCE_SHORTAGE': ShortagePayload,
    'RAINFALL_INCREASE': RainfallPayload,
}


class DisasterEvent(BaseModel):
    event_id: Identifier
    simulation_id: Identifier
    type: Literal['ROAD_FLOODED', 'ROAD_REOPENED', 'SHELTER_OVER_CAPACITY', 'RESOURCE_SHORTAGE', 'RAINFALL_INCREASE']
    timestamp: AwareDatetime
    observed_at: AwareDatetime | None = None
    target_id: Identifier
    payload: dict = Field(default_factory=dict)
    source: Literal['simulation'] = 'simulation'

    @model_validator(mode='after')
    def validate_payload(self) -> Self:
        self.payload = PAYLOAD_MODELS[self.type].model_validate(self.payload).model_dump()
        if self.observed_at is not None and self.observed_at > self.timestamp:
            raise ValueError('observed_at cannot be later than event timestamp')
        return self


class SimulationStatus(BaseModel):
    simulation_id: str
    run_id: str
    phase: Literal['ready', 'running', 'paused']
    timestamp: AwareDatetime
    revision: int
    accepted_events: int
    scheduled_events: int
    mode: Literal['manual'] = 'manual'


class EventRecord(BaseModel):
    event: DisasterEvent
    revision: int
    ingested_at: AwareDatetime
    accepted_at: AwareDatetime
    status: Literal['accepted'] = 'accepted'


class ScheduledEvent(BaseModel):
    event: DisasterEvent
    sequence: int
    ingested_at: AwareDatetime
    status: Literal['scheduled'] = 'scheduled'


class EventHistory(BaseModel):
    run_id: str
    accepted: list[EventRecord]
    scheduled: list[ScheduledEvent]


class StateSnapshot(BaseModel):
    run_id: str
    revision: int
    timestamp: AwareDatetime
    state: TwinState


class AdvanceRequest(Payload):
    seconds: float = Field(gt=0, le=31536000, allow_inf_nan=False)


class ReplayRequest(Payload):
    revision: int | None = Field(default=None, ge=1)
    run_id: str | None = None


class AgentReasoningTrace(BaseModel):
    model: str = 'external_agent'
    summary: str = Field(min_length=1, max_length=4000)
    steps: list[str] = Field(default_factory=list, max_length=50)


class ProposedAction(BaseModel):
    action_id: Identifier
    type: Literal['ROUTE_ASSIGNMENT', 'RESOURCE_TRANSFER']
    target_id: Identifier
    unit_id: Identifier | None = None
    route_road_ids: list[Identifier] = Field(default_factory=list)
    item: Identifier | None = None
    quantity: Quantity | None = None
    requires_approval: bool = True
    status: Literal['proposed', 'pending_approval', 'approved', 'rejected', 'applied', 'failed'] = 'proposed'
    reason: str = Field(min_length=1, max_length=2000)


class AgentResponse(BaseModel):
    event_id: Identifier
    state_revision: int = Field(ge=1)
    risk_assessments: list[dict] = Field(default_factory=list)
    proposed_actions: list[ProposedAction] = Field(default_factory=list)
    reasoning_trace: AgentReasoningTrace


class DecisionStatus(BaseModel):
    action_id: Identifier
    status: Literal['proposed', 'pending_approval', 'approved', 'rejected', 'applied', 'failed']
    state_revision: int
    decided_by: str | None = None
    reason: str


class ActionApplicationResult(BaseModel):
    action_id: Identifier
    status: Literal['applied', 'failed']
    state_revision: int
    detail: str
