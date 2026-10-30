"""Validated shared inputs and outputs for deterministic agent calculations.

These contracts describe the agreed MVP data shape. Road ``source`` and
optional ``target`` are graph-node IDs; the first and last LineString
coordinates must correspond to those nodes. Missing targets remain readable
for backward compatibility but cannot produce a routing edge.
"""

from __future__ import annotations

import math
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator, model_validator

Coordinate = tuple[float, float]
RiskLevel = Literal["LOW", "MODERATE", "HIGH", "CRITICAL", "UNAVAILABLE"]
RISK_INDICATORS = (
    "flood_severity",
    "rainfall_indicator",
    "elevation_risk",
    "infrastructure_vulnerability",
    "population_exposure",
)
PRIORITY_INDICATORS = (
    "explicit_priority",
    "risk_score",
    "affected_population",
    "unmet_demand",
    "route_feasibility",
)


class ContractModel(BaseModel):
    """Base class that rejects undeclared fields at integration boundaries."""

    model_config = ConfigDict(extra="forbid")


class GeoJSONLineString(ContractModel):
    """GeoJSON line geometry whose positions use [longitude, latitude]."""

    type: Literal["LineString"] = "LineString"
    coordinates: list[Coordinate] = Field(min_length=2)

    @field_validator("coordinates")
    @classmethod
    def validate_coordinates(cls, coordinates: list[Coordinate]) -> list[Coordinate]:
        for longitude, latitude in coordinates:
            if not (math.isfinite(longitude) and math.isfinite(latitude)):
                raise ValueError("coordinates must be finite")
            if not -180 <= longitude <= 180:
                raise ValueError("longitude must be in [-180, 180]")
            if not -90 <= latitude <= 90:
                raise ValueError("latitude must be in [-90, 90]")
        return coordinates


class RoadContract(ContractModel):
    """Road edge; geometry order is source-to-target, and direction is one-way by default."""

    id: str = Field(min_length=1)
    source: str = Field(min_length=1)
    target: str | None = Field(default=None, min_length=1)
    status: str = Field(min_length=1)
    risk_score: float = Field(ge=0, le=1, allow_inf_nan=False)
    geometry: GeoJSONLineString
    distance_m: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    bidirectional: bool = False

    @field_validator("status")
    @classmethod
    def normalize_status(cls, status: str) -> str:
        normalized = status.strip().casefold()
        if not normalized:
            raise ValueError("status cannot be blank")
        return normalized


class ShelterContract(ContractModel):
    """Shelter state and itemized demand; priority fields are optional observations."""

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    location: Coordinate | str
    capacity: StrictInt = Field(ge=0)
    occupancy: StrictInt = Field(ge=0)
    demand: dict[str, StrictInt] = Field(default_factory=dict)
    priority: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)
    risk_score: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)
    affected_population: StrictInt | None = Field(default=None, ge=0)

    @field_validator("location", mode="before")
    @classmethod
    def normalize_location(cls, location: object) -> object:
        if isinstance(location, list) and len(location) == 2:
            return tuple(location)
        return location

    @field_validator("demand")
    @classmethod
    def validate_demand(cls, demand: dict[str, int]) -> dict[str, int]:
        if any(not item.strip() for item in demand):
            raise ValueError("demand item names cannot be blank")
        if any(quantity < 0 for quantity in demand.values()):
            raise ValueError("demand quantities must be non-negative integers")
        return demand

    @model_validator(mode="after")
    def validate_capacity(self) -> ShelterContract:
        if self.occupancy > self.capacity:
            raise ValueError("shelter occupancy cannot exceed capacity")
        return self


class ReliefUnitContract(ContractModel):
    """Relief-unit location and inventory; null inventory means unknown, not unlimited."""

    id: str = Field(min_length=1)
    location: Coordinate | str
    inventory: dict[str, StrictInt | None] = Field(default_factory=dict)

    @field_validator("location", mode="before")
    @classmethod
    def normalize_location(cls, location: object) -> object:
        if isinstance(location, list) and len(location) == 2:
            return tuple(location)
        return location

    @field_validator("inventory")
    @classmethod
    def validate_inventory(cls, inventory: dict[str, int | None]) -> dict[str, int | None]:
        if any(not item.strip() for item in inventory):
            raise ValueError("inventory item names cannot be blank")
        if any(quantity is not None and quantity < 0 for quantity in inventory.values()):
            raise ValueError("inventory quantities must be non-negative integers")
        return inventory


class ZoneContract(ContractModel):
    """Zone-level risk and population observations."""

    id: str = Field(min_length=1)
    location: Coordinate | None = None
    risk_score: float = Field(ge=0, le=1, allow_inf_nan=False)
    affected_population: StrictInt = Field(ge=0)


class TwinStateContract(ContractModel):
    """Shared twin snapshot; it is input data and does not provide mutation methods."""

    simulation_id: str = Field(min_length=1)
    timestamp: str = Field(min_length=1)
    revision: StrictInt = Field(ge=0)
    roads: list[RoadContract]
    shelters: list[ShelterContract]
    relief_units: list[ReliefUnitContract]
    zones: list[ZoneContract]

    @model_validator(mode="before")
    @classmethod
    def adapt_shared_twin_snapshot(cls, value: object) -> object:
        """Translate the authoritative twin's entity fields into agent contracts."""
        if not isinstance(value, dict):
            return value
        value = dict(value)
        roads = []
        for raw in value.get("roads", []):
            if not isinstance(raw, dict):
                roads.append(raw)
                continue
            road = dict(raw)
            coordinates = road.get("coordinates")
            if "geometry" not in road and coordinates is not None:
                road["geometry"] = {"type": "LineString", "coordinates": coordinates}
            if road.get("u") is not None:
                road["source"] = str(road["u"])
            if road.get("source") in (None, "synthetic_demo", "contemporary_osm", "local_graphml_unverified") and coordinates:
                road["source"] = f"{coordinates[0][0]},{coordinates[0][1]}"
            if road.get("v") is not None:
                road["target"] = str(road["v"])
            elif "target" not in road:
                road["target"] = road.get("v")
            if road.get("target") is None and coordinates:
                road["target"] = f"{coordinates[-1][0]},{coordinates[-1][1]}"
            if "distance_m" not in road:
                road["distance_m"] = road.get("length_m")
            road["bidirectional"] = road.get("bidirectional", False)
            roads.append({key: road[key] for key in ("id", "source", "target", "status", "risk_score", "geometry", "distance_m", "bidirectional") if key in road})
        value["roads"] = roads
        value["shelters"] = [
            {key: shelter[key] for key in ("id", "name", "location", "capacity", "occupancy", "demand", "priority", "risk_score", "affected_population") if key in shelter}
            if isinstance(shelter, dict) else shelter
            for shelter in value.get("shelters", [])
        ]
        value["relief_units"] = [
            {key: unit[key] for key in ("id", "location", "inventory") if key in unit}
            if isinstance(unit, dict) else unit
            for unit in value.get("relief_units", [])
        ]
        value["zones"] = [
            {key: zone[key] for key in ("id", "location", "risk_score", "affected_population") if key in zone}
            if isinstance(zone, dict) else zone
            for zone in value.get("zones", [])
        ]
        return value


class RiskIndicators(ContractModel):
    """Normalized risk observations, where omitted or null values remain missing."""

    flood_severity: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)
    rainfall_indicator: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)
    elevation_risk: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)
    infrastructure_vulnerability: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)
    population_exposure: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)


class RiskConfig(ContractModel):
    """Provisional weights and category boundaries for the interpretable MVP score."""

    weights: dict[str, float] = Field(
        default_factory=lambda: {
            "flood_severity": 0.35,
            "rainfall_indicator": 0.25,
            "elevation_risk": 0.15,
            "infrastructure_vulnerability": 0.15,
            "population_exposure": 0.10,
        }
    )
    low_threshold: float = Field(default=0.25, ge=0, le=1, allow_inf_nan=False)
    moderate_threshold: float = Field(default=0.50, ge=0, le=1, allow_inf_nan=False)
    high_threshold: float = Field(default=0.75, ge=0, le=1, allow_inf_nan=False)
    weight_tolerance: float = Field(default=1e-6, gt=0, le=1e-3, allow_inf_nan=False)

    @model_validator(mode="after")
    def validate_risk_configuration(self) -> RiskConfig:
        if set(self.weights) != set(RISK_INDICATORS):
            raise ValueError(f"weights must define exactly: {', '.join(RISK_INDICATORS)}")
        if any(not math.isfinite(weight) or weight < 0 for weight in self.weights.values()):
            raise ValueError("risk weights must be finite and non-negative")
        if not math.isclose(sum(self.weights.values()), 1.0, abs_tol=self.weight_tolerance):
            raise ValueError("risk weights must sum to 1 within weight_tolerance")
        if not self.low_threshold < self.moderate_threshold < self.high_threshold:
            raise ValueError("risk thresholds must be strictly increasing")
        return self


class RiskFactor(ContractModel):
    """One observed indicator and its effective contribution to a risk score."""

    value: float = Field(ge=0, le=1, allow_inf_nan=False)
    configured_weight: float = Field(ge=0, le=1, allow_inf_nan=False)
    effective_weight: float = Field(ge=0, le=1, allow_inf_nan=False)
    weighted_contribution: float = Field(ge=0, le=1, allow_inf_nan=False)


class RiskAssessment(ContractModel):
    """Interpretable risk result, including unavailable and partial assessments."""

    target_id: str = Field(min_length=1)
    risk_score: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)
    level: RiskLevel
    contributing_factors: dict[str, RiskFactor]
    missing_indicators: list[str]
    explanation: str
    complete: bool


class RouteResult(ContractModel):
    """Dijkstra route result; road IDs and nodes describe one connected path."""

    reachable: bool
    source: str | None = None
    target: str | None = None
    road_ids: list[str] = Field(default_factory=list)
    nodes: list[str] = Field(default_factory=list)
    route_geometry: GeoJSONLineString | None = None
    total_cost: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    failure_reason: str | None = None

    @model_validator(mode="after")
    def validate_route_shape(self) -> RouteResult:
        if self.reachable:
            if self.source is None or self.target is None or not self.nodes:
                raise ValueError("reachable routes require source, target, and a node sequence")
            if self.nodes[0] != self.source or self.nodes[-1] != self.target:
                raise ValueError("route node sequence must begin at source and end at target")
            if len(self.road_ids) != len(self.nodes) - 1:
                raise ValueError("a route must contain exactly one road ID per node transition")
            if self.total_cost is None or self.failure_reason is not None:
                raise ValueError("reachable routes require a cost and cannot have a failure reason")
            if self.road_ids and self.route_geometry is None:
                raise ValueError("routes with roads require route geometry")
        elif self.road_ids or self.nodes or self.route_geometry is not None or self.total_cost is not None:
            raise ValueError("unreachable routes cannot contain a partial path")
        elif not self.failure_reason:
            raise ValueError("unreachable routes require a failure reason")
        return self


class PriorityConfig(ContractModel):
    """Interpretable shelter priority weights; observed factors are renormalized."""

    weights: dict[str, float] = Field(
        default_factory=lambda: {
            "explicit_priority": 0.40,
            "risk_score": 0.25,
            "affected_population": 0.15,
            "unmet_demand": 0.10,
            "route_feasibility": 0.10,
        }
    )
    fallback_priority: float = Field(default=0.5, ge=0, le=1, allow_inf_nan=False)
    weight_tolerance: float = Field(default=1e-6, gt=0, le=1e-3, allow_inf_nan=False)

    @model_validator(mode="after")
    def validate_priority_weights(self) -> PriorityConfig:
        if set(self.weights) != set(PRIORITY_INDICATORS):
            raise ValueError(f"priority weights must define exactly: {', '.join(PRIORITY_INDICATORS)}")
        if any(not math.isfinite(weight) or weight < 0 for weight in self.weights.values()):
            raise ValueError("priority weights must be finite and non-negative")
        if not math.isclose(sum(self.weights.values()), 1.0, abs_tol=self.weight_tolerance):
            raise ValueError("priority weights must sum to 1 within weight_tolerance")
        return self


class ShelterPriority(ContractModel):
    """Shelter ordering score and visible omissions in its priority inputs."""

    shelter_id: str
    score: float = Field(ge=0, le=1, allow_inf_nan=False)
    contributing_factors: dict[str, float]
    missing_inputs: list[str]
    explanation: str


class ReservationContract(ContractModel):
    """Existing commitment supplied by the caller; this model stores nothing."""

    allocation_id: str = Field(min_length=1)
    unit_id: str = Field(min_length=1)
    shelter_id: str = Field(min_length=1)
    item: str = Field(min_length=1)
    quantity: StrictInt = Field(gt=0)


class AllocationProposal(ContractModel):
    """Proposed item delivery; it is not an authoritative inventory mutation."""

    allocation_id: str
    unit_id: str
    shelter_id: str
    item: str
    quantity: StrictInt = Field(gt=0)
    route_road_ids: list[str]
    status: Literal["proposed"] = "proposed"
    reason: str


class UnmetDemand(ContractModel):
    """Remaining item demand after commitments and proposed deliveries."""

    shelter_id: str
    item: str
    quantity: StrictInt = Field(gt=0)
    missing_inventory_units: list[str]
    reason: str


class OccupantTransferRequest(ContractModel):
    """Capacity-bounded occupant transfer request with a separately validated route."""

    transfer_id: str = Field(min_length=1)
    source_shelter_id: str = Field(min_length=1)
    destination_shelter_id: str = Field(min_length=1)
    quantity: StrictInt = Field(gt=0)
    route: RouteResult

    @model_validator(mode="after")
    def validate_distinct_shelters(self) -> OccupantTransferRequest:
        if self.source_shelter_id == self.destination_shelter_id:
            raise ValueError("occupant transfers require different source and destination shelters")
        return self


class OccupantTransferProposal(ContractModel):
    """Capacity-safe occupant transfer proposal and its unmet quantity."""

    transfer_id: str
    source_shelter_id: str
    destination_shelter_id: str
    requested_quantity: StrictInt
    proposed_quantity: StrictInt = Field(ge=0)
    route_road_ids: list[str]
    status: Literal["proposed", "unavailable"]
    reason: str


class AllocationResult(ContractModel):
    """Deterministic supply and occupant-transfer proposals for one evaluation."""

    evaluation_id: str
    shelter_priorities: list[ShelterPriority]
    proposals: list[AllocationProposal]
    unmet_demand: list[UnmetDemand]
    inventory_remaining: dict[str, dict[str, StrictInt | None]]
    missing_inventory: dict[str, list[str]]
    occupant_transfers: list[OccupantTransferProposal]


DisasterEventType = Literal[
    "ROAD_FLOODED",
    "ROAD_REOPENED",
    "SHELTER_OVER_CAPACITY",
    "RESOURCE_SHORTAGE",
    "RAINFALL_INCREASE",
    "UNKNOWN",
]


class DisasterEvent(ContractModel):
    """Validated structured event candidate; it is not applied to twin state."""

    event_id: str = Field(min_length=1, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
    simulation_id: str = Field(min_length=1, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
    type: DisasterEventType
    timestamp: str = Field(min_length=1)
    target_id: str | None = Field(
        default=None,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$",
    )
    payload: dict[str, object] = Field(default_factory=dict)
    source: str = Field(min_length=1)


class AgentEvaluationRequest(ContractModel):
    """Authoritative evaluation inputs supplied by the shared-backend caller."""

    twin_state: TwinStateContract
    event: DisasterEvent
    current_revision: StrictInt = Field(ge=0)
    seen_event_ids: list[str]
    reservations: list[ReservationContract]


class EventChangeRecord(ContractModel):
    """A proposed state change for Member 1 to validate and apply, never applied here."""

    entity_type: Literal["road", "shelter", "relief_unit", "zone", "simulation"]
    entity_id: str | None
    field: str
    reported_value: object
    evidence: list[str]


class PerceptionResult(ContractModel):
    """Deterministic event-validation outcome and downstream reevaluation signals."""

    status: Literal["validated", "duplicate", "rejected", "uncertain"]
    event: DisasterEvent | None = None
    change_records: list[EventChangeRecord] = Field(default_factory=list)
    reevaluate_agents: list[Literal["risk", "routing", "allocation", "escalation"]] = Field(
        default_factory=list
    )
    uncertainties: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class IncidentExtraction(ContractModel):
    """Strict structured extraction from untrusted text; it cannot express decisions."""

    event_type: DisasterEventType
    target_id: str | None = Field(
        default=None,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$",
    )
    reported_status: str | None = None
    reported_severity: str | None = None
    measurements: dict[str, float | str] = Field(default_factory=dict)
    evidence: list[str]
    uncertainties: list[str]
    requires_verification: Literal[True]

    @field_validator("measurements")
    @classmethod
    def validate_measurements(
        cls, measurements: dict[str, float | str]
    ) -> dict[str, float | str]:
        for key, value in measurements.items():
            if not key.strip():
                raise ValueError("measurement names cannot be blank")
            if isinstance(value, float) and not math.isfinite(value):
                raise ValueError("numeric measurements must be finite")
        return measurements


class DecisionExplanation(ContractModel):
    """Human-readable explanation that does not alter any structured decision."""

    explanation: str = Field(min_length=1, max_length=2000)


class EscalationPolicy(ContractModel):
    """Provisional deterministic escalation thresholds; not a safety certification."""

    critical_risk_threshold: float = Field(default=0.75, ge=0, le=1, allow_inf_nan=False)
    low_risk_auto_approval_threshold: float = Field(default=0.25, ge=0, le=1, allow_inf_nan=False)
    maximum_inventory_fraction_for_auto_approval: float = Field(
        default=0.20, gt=0, le=1, allow_inf_nan=False
    )
    maximum_item_quantity_for_auto_approval: StrictInt = Field(default=25, gt=0)


class EscalationRequest(ContractModel):
    """Decision candidate plus read-only validation snapshots for policy evaluation."""

    decision_id: str = Field(min_length=1)
    simulation_id: str = Field(min_length=1)
    action_kind: Literal["route", "allocation", "simulation_control"]
    expected_revision: StrictInt = Field(ge=0)
    current_revision: StrictInt = Field(ge=0)
    risk_score: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)
    route: RouteResult | None = None
    allocation_result: AllocationResult | None = None
    roads: list[RoadContract] = Field(default_factory=list)
    relief_units: list[ReliefUnitContract] = Field(default_factory=list)
    shelters: list[ShelterContract] = Field(default_factory=list)
    evidence_complete: bool = False
    reversible: bool = False
    simulation_only: bool = False

    @model_validator(mode="after")
    def validate_action_payload(self) -> EscalationRequest:
        if self.action_kind == "route" and self.route is None:
            raise ValueError("route actions require a route result")
        if self.action_kind == "allocation" and self.allocation_result is None:
            raise ValueError("allocation actions require an allocation result")
        if self.action_kind != "route" and self.route is not None:
            raise ValueError("only route actions accept a top-level route result")
        if self.action_kind != "allocation" and self.allocation_result is not None:
            raise ValueError("only allocation actions accept an allocation result")
        return self


class EscalationResult(ContractModel):
    """Policy outcome; approval does not mean an action was applied."""

    decision_id: str
    simulation_id: str
    status: Literal["AUTO_APPROVED", "PENDING_APPROVAL", "BLOCKED"]
    requires_approval: bool
    rule_ids: list[str]
    explanation: str
    state_applied: Literal[False] = False
