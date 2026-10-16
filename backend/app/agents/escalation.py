"""Evaluate approval policy without applying or persisting proposed actions.

The thresholds are provisional examples, not validated operational policies.
Member 1's state manager remains responsible for revision and idempotency checks
when an approved proposal is eventually applied.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TypeAlias

from app.schemas.contracts import (
    AllocationResult,
    EscalationPolicy,
    EscalationRequest,
    EscalationResult,
    RoadContract,
    RouteResult,
)

RequestData: TypeAlias = EscalationRequest | Mapping[str, object]


def _known_roads(roads: list[RoadContract]) -> tuple[dict[str, RoadContract], list[str]]:
    result: dict[str, RoadContract] = {}
    errors: list[str] = []
    node_coordinates: dict[str, tuple[float, float]] = {}
    for road in roads:
        if road.id in result:
            errors.append(f"duplicate road ID {road.id!r} prevents route validation")
            continue
        result[road.id] = road
        if road.target is None:
            continue
        if road.source == road.target:
            errors.append(f"road {road.id!r} connects a node to itself")
            continue
        for node_id, coordinate in (
            (road.source, road.geometry.coordinates[0]),
            (road.target, road.geometry.coordinates[-1]),
        ):
            previous = node_coordinates.get(node_id)
            if previous is not None and previous != coordinate:
                errors.append(
                    f"node {node_id!r} has conflicting endpoint coordinates in the road snapshot"
                )
            else:
                node_coordinates[node_id] = coordinate
    return result, errors


def _validate_road_sequence(
    road_ids: list[str],
    source_location: str | tuple[float, float],
    target_location: str | tuple[float, float],
    roads: Mapping[str, RoadContract],
) -> str | None:
    if not road_ids:
        if source_location == target_location:
            return None
        return "non-identical endpoints cannot be connected by an empty route"
    current_node: str | None = source_location if isinstance(source_location, str) else None
    current_coordinate: tuple[float, float] | None = (
        source_location if not isinstance(source_location, str) else None
    )
    for road_id in road_ids:
        road = roads.get(road_id)
        if road is None:
            return f"route references unknown road {road_id!r}"
        if road.status != "open":
            return f"route includes non-traversable road {road_id!r} with status {road.status!r}"
        if road.target is None:
            return f"route road {road_id!r} has no validated destination node"
        start, end = road.geometry.coordinates[0], road.geometry.coordinates[-1]
        if current_node is not None:
            forward = road.source == current_node
            reverse = road.bidirectional and road.target == current_node
        else:
            forward = start == current_coordinate
            reverse = road.bidirectional and end == current_coordinate
        if forward:
            current_node = road.target
            current_coordinate = end
        elif reverse:
            current_node = road.source
            current_coordinate = start
        else:
            return f"route roads are disconnected at road {road_id!r}"
    if current_node is not None:
        matches_target = (
            current_node == target_location
            if isinstance(target_location, str)
            else current_coordinate == target_location
        )
    else:
        matches_target = current_coordinate == target_location
    if not matches_target:
        return "route endpoint does not match the action destination"
    return None


def _validate_route_result(
    route: RouteResult,
    roads: Mapping[str, RoadContract],
) -> str | None:
    if not route.reachable:
        return route.failure_reason or "route is unreachable"
    if len(set(route.road_ids)) != len(route.road_ids):
        return "route repeats a road ID and cannot be validated as a simple path"
    for index, road_id in enumerate(route.road_ids):
        road = roads.get(road_id)
        if road is None:
            return f"route references unknown road {road_id!r}"
        if road.status != "open":
            return f"route includes non-traversable road {road_id!r} with status {road.status!r}"
        if road.target is None:
            return f"route road {road_id!r} has no validated destination node"
        start, end = route.nodes[index], route.nodes[index + 1]
        if not (
            (road.source == start and road.target == end)
            or (road.bidirectional and road.source == end and road.target == start)
        ):
            return f"road {road_id!r} does not connect route nodes {start!r} to {end!r}"
    return None


def _validate_allocation(
    allocation: AllocationResult,
    request: EscalationRequest,
    roads: Mapping[str, RoadContract],
) -> tuple[list[str], bool]:
    violations: list[str] = []
    units = {unit.id: unit for unit in request.relief_units}
    shelters = {shelter.id: shelter for shelter in request.shelters}
    allocated_by_inventory: dict[tuple[str, str], int] = {}

    for proposal in allocation.proposals:
        unit = units.get(proposal.unit_id)
        shelter = shelters.get(proposal.shelter_id)
        if unit is None:
            violations.append(f"allocation references unknown relief unit {proposal.unit_id!r}")
            continue
        if shelter is None:
            violations.append(f"allocation references unknown shelter {proposal.shelter_id!r}")
            continue
        available = unit.inventory.get(proposal.item)
        if available is None:
            violations.append(
                f"available inventory for {proposal.unit_id}/{proposal.item} is unknown"
            )
            continue
        key = (proposal.unit_id, proposal.item)
        allocated_by_inventory[key] = allocated_by_inventory.get(key, 0) + proposal.quantity
        if proposal.route_road_ids:
            route_error = _validate_road_sequence(
                proposal.route_road_ids,
                unit.location,
                shelter.location,
                roads,
            )
            if route_error:
                violations.append(
                    f"allocation {proposal.allocation_id!r} has invalid route: {route_error}"
                )
        elif unit.location != shelter.location:
            violations.append(
                f"allocation {proposal.allocation_id!r} has no route between unit and shelter"
            )
    for (unit_id, item), quantity in allocated_by_inventory.items():
        available = units[unit_id].inventory[item]
        if available is not None and quantity > available:
            violations.append(
                f"proposed {item} quantity {quantity} exceeds available inventory "
                f"{available} for {unit_id}"
            )

    projected_occupancy = {shelter_id: shelter.occupancy for shelter_id, shelter in shelters.items()}
    for transfer in allocation.occupant_transfers:
        source = shelters.get(transfer.source_shelter_id)
        destination = shelters.get(transfer.destination_shelter_id)
        if source is None or destination is None:
            violations.append(
                f"occupant transfer {transfer.transfer_id!r} references an unknown shelter"
            )
            continue
        if transfer.proposed_quantity > transfer.requested_quantity:
            violations.append(
                f"occupant transfer {transfer.transfer_id!r} exceeds its requested quantity"
            )
        if transfer.status == "unavailable":
            if transfer.proposed_quantity != 0:
                violations.append(
                    f"unavailable transfer {transfer.transfer_id!r} has a positive quantity"
                )
            continue
        if transfer.proposed_quantity > 0:
            route_error = _validate_road_sequence(
                transfer.route_road_ids,
                source.location,
                destination.location,
                roads,
            )
            if route_error:
                violations.append(
                    f"occupant transfer {transfer.transfer_id!r} has invalid route: {route_error}"
                )
        projected_occupancy[source.id] -= transfer.proposed_quantity
        projected_occupancy[destination.id] += transfer.proposed_quantity
        if projected_occupancy[source.id] < 0:
            violations.append(f"occupant transfers exceed source occupancy at {source.id}")
        if projected_occupancy[destination.id] > destination.capacity:
            violations.append(f"occupant transfers exceed capacity at {destination.id}")
    return violations, bool(allocated_by_inventory)


def evaluate_escalation(
    request_data: RequestData,
    policy: EscalationPolicy | None = None,
) -> EscalationResult:
    """Validate hard constraints, then apply deterministic approval thresholds."""
    request = (
        request_data
        if isinstance(request_data, EscalationRequest)
        else EscalationRequest.model_validate(request_data)
    )
    selected_policy = policy or EscalationPolicy()
    rule_ids: list[str] = []
    hard_violations: list[str] = []
    roads, duplicate_road_errors = _known_roads(request.roads)
    if duplicate_road_errors:
        hard_violations.extend(duplicate_road_errors)
        rule_ids.append("HARD_INVALID_ROAD_SNAPSHOT")

    if request.route is not None:
        route_error = _validate_route_result(request.route, roads)
        if route_error:
            hard_violations.append(route_error)
            rule_ids.append("HARD_INVALID_ROUTE")

    has_allocations = False
    if request.allocation_result is not None:
        allocation_violations, has_allocations = _validate_allocation(
            request.allocation_result,
            request,
            roads,
        )
        if allocation_violations:
            hard_violations.extend(allocation_violations)
            rule_ids.append("HARD_RESOURCE_OR_CAPACITY_CONSTRAINT")

    if hard_violations:
        return EscalationResult(
            decision_id=request.decision_id,
            simulation_id=request.simulation_id,
            status="BLOCKED",
            requires_approval=False,
            rule_ids=list(dict.fromkeys(rule_ids)),
            explanation=(
                "Hard safety or resource constraints failed: "
                + "; ".join(hard_violations)
                + ". Human approval cannot override these constraints."
            ),
        )

    approval_reasons: list[str] = []
    if request.risk_score is None:
        rule_ids.append("APPROVAL_MISSING_RISK")
        approval_reasons.append("risk assessment is missing")
    elif request.risk_score >= selected_policy.critical_risk_threshold:
        rule_ids.append("APPROVAL_CRITICAL_RISK")
        approval_reasons.append(
            f"risk score {request.risk_score:.2f} meets the critical threshold "
            f"{selected_policy.critical_risk_threshold:.2f}"
        )

    if has_allocations and request.allocation_result is not None:
        units = {unit.id: unit for unit in request.relief_units}
        totals: dict[str, int] = {}
        available_totals: dict[str, int] = {}
        for proposal in request.allocation_result.proposals:
            unit = units.get(proposal.unit_id)
            if unit is None:
                continue
            available = unit.inventory.get(proposal.item)
            if available is None:
                continue
            totals[proposal.item] = totals.get(proposal.item, 0) + proposal.quantity
            available_totals[proposal.item] = available_totals.get(proposal.item, 0) + available
        high_impact_items = [
            item
            for item, quantity in totals.items()
            if quantity > selected_policy.maximum_item_quantity_for_auto_approval
            or (
                available_totals[item] > 0
                and quantity / available_totals[item]
                > selected_policy.maximum_inventory_fraction_for_auto_approval
            )
        ]
        if high_impact_items:
            rule_ids.append("APPROVAL_HIGH_IMPACT_ALLOCATION")
            approval_reasons.append(
                "allocation exceeds the provisional auto-approval threshold for "
                + ", ".join(sorted(high_impact_items))
            )

    if request.expected_revision != request.current_revision:
        rule_ids.append("APPROVAL_REVISION_MISMATCH")
        approval_reasons.append(
            f"expected revision {request.expected_revision} differs from current "
            f"revision {request.current_revision}"
        )
    if not request.evidence_complete:
        rule_ids.append("APPROVAL_UNCERTAIN_EVIDENCE")
        approval_reasons.append("evidence is incomplete or uncertain")

    low_risk = (
        request.risk_score is not None
        and request.risk_score < selected_policy.low_risk_auto_approval_threshold
    )
    auto_eligible = (
        low_risk
        and request.reversible
        and request.simulation_only
        and request.evidence_complete
        and request.expected_revision == request.current_revision
        and not approval_reasons
    )
    if auto_eligible:
        rule_ids.append("AUTO_APPROVAL_LOW_RISK_REVERSIBLE_SIMULATION")
        return EscalationResult(
            decision_id=request.decision_id,
            simulation_id=request.simulation_id,
            status="AUTO_APPROVED",
            requires_approval=False,
            rule_ids=rule_ids,
            explanation=(
                "Eligible low-risk, reversible, simulation-only action passed the "
                "configured checks. This is policy approval only; no authoritative "
                "state change has been applied."
            ),
        )

    if not approval_reasons:
        rule_ids.append("APPROVAL_NOT_AUTO_ELIGIBLE")
        approval_reasons.append(
            "action is not both low-risk, reversible, and simulation-only"
        )
    return EscalationResult(
        decision_id=request.decision_id,
        simulation_id=request.simulation_id,
        status="PENDING_APPROVAL",
        requires_approval=True,
        rule_ids=list(dict.fromkeys(rule_ids)),
        explanation=(
            "Human approval is required because "
            + "; ".join(approval_reasons)
            + ". Member 1's state manager must revalidate revision and idempotency "
            "before any approved action is applied."
        ),
    )
