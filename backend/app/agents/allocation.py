"""Deterministic greedy proposals for relief supplies and occupant transfers.

This module is pure: it reads supplied state, routes, and commitments and
returns proposals without reserving or mutating authoritative inventory. Supply
items are allocated independently; occupant transfers are handled separately
against shelter occupancy and capacity.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import TypeAlias

from app.schemas.contracts import (
    AllocationProposal,
    AllocationResult,
    OccupantTransferProposal,
    OccupantTransferRequest,
    PriorityConfig,
    ReliefUnitContract,
    ReservationContract,
    RouteResult,
    ShelterContract,
    ShelterPriority,
    UnmetDemand,
)

UnitData: TypeAlias = ReliefUnitContract | Mapping[str, object]
ShelterData: TypeAlias = ShelterContract | Mapping[str, object]
RouteData: TypeAlias = RouteResult | Mapping[str, object]
ReservationData: TypeAlias = ReservationContract | Mapping[str, object]
TransferData: TypeAlias = OccupantTransferRequest | Mapping[str, object]


def _route_matches_locations(
    route: RouteResult,
    source_location: str | tuple[float, float],
    target_location: str | tuple[float, float],
) -> bool:
    if not route.reachable:
        return False
    if isinstance(source_location, str):
        source_matches = route.source == source_location
    else:
        source_matches = (
            route.route_geometry is not None
            and route.route_geometry.coordinates[0] == source_location
        )
    if isinstance(target_location, str):
        target_matches = route.target == target_location
    else:
        target_matches = (
            route.route_geometry is not None
            and route.route_geometry.coordinates[-1] == target_location
        )
    return source_matches and target_matches


def _parse_models(items: Sequence[object], model_type: type, label: str) -> list:
    parsed = [
        item if isinstance(item, model_type) else model_type.model_validate(item)
        for item in items
    ]
    identifiers = [item.id for item in parsed]
    if len(identifiers) != len(set(identifiers)):
        raise ValueError(f"{label} IDs must be unique")
    return parsed


def _route_model(route: RouteData) -> RouteResult:
    return route if isinstance(route, RouteResult) else RouteResult.model_validate(route)


def _stable_allocation_id(evaluation_id: str, unit_id: str, shelter_id: str, item: str) -> str:
    key = json.dumps([evaluation_id, unit_id, shelter_id, item], separators=(",", ":"))
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:20]
    return f"alloc-{digest}"


def _route_for_pair(
    routes: Mapping[tuple[str, str], RouteResult],
    unit_id: str,
    shelter_id: str,
) -> RouteResult | None:
    return routes.get((unit_id, shelter_id))


def _shelter_priorities(
    shelters: Sequence[ShelterContract],
    demand_remaining: Mapping[str, Mapping[str, int]],
    routes: Mapping[tuple[str, str], RouteResult],
    unit_ids: Sequence[str],
    config: PriorityConfig,
) -> list[ShelterPriority]:
    max_population = max(
        (shelter.affected_population for shelter in shelters if shelter.affected_population is not None),
        default=0,
    )
    remaining_demand = {
        shelter.id: sum(demand_remaining[shelter.id].values()) for shelter in shelters
    }
    max_demand = max(remaining_demand.values(), default=0)
    priorities: list[ShelterPriority] = []

    for shelter in shelters:
        factors: dict[str, float] = {}
        missing: list[str] = []
        if shelter.priority is None:
            missing.append("explicit_priority")
        else:
            factors["explicit_priority"] = shelter.priority
        if shelter.risk_score is None:
            missing.append("risk_score")
        else:
            factors["risk_score"] = shelter.risk_score
        if shelter.affected_population is None:
            missing.append("affected_population")
        else:
            factors["affected_population"] = (
                shelter.affected_population / max_population if max_population > 0 else 0.0
            )
        factors["unmet_demand"] = (
            remaining_demand[shelter.id] / max_demand if max_demand > 0 else 0.0
        )
        factors["route_feasibility"] = float(
            any(
                (route := _route_for_pair(routes, unit_id, shelter.id)) is not None
                and route.reachable
                for unit_id in unit_ids
            )
        )

        available_weight = sum(config.weights[name] for name in factors)
        if available_weight > 0:
            score = sum(
                config.weights[name] * value / available_weight
                for name, value in factors.items()
            )
        else:
            score = config.fallback_priority
        if missing:
            explanation = (
                "Observed priority factors were renormalized; missing inputs are reported "
                "and were not treated as zero."
            )
        else:
            explanation = "All configured shelter priority inputs were available."
        priorities.append(
            ShelterPriority(
                shelter_id=shelter.id,
                score=score,
                contributing_factors=factors,
                missing_inputs=missing,
                explanation=explanation,
            )
        )
    return sorted(priorities, key=lambda item: (-item.score, item.shelter_id))


def _allocate_occupant_transfers(
    requests: Sequence[TransferData],
    shelters: Mapping[str, ShelterContract],
) -> list[OccupantTransferProposal]:
    parsed = [
        request
        if isinstance(request, OccupantTransferRequest)
        else OccupantTransferRequest.model_validate(request)
        for request in requests
    ]
    transfer_ids = [request.transfer_id for request in parsed]
    if len(transfer_ids) != len(set(transfer_ids)):
        raise ValueError("occupant transfer IDs must be unique")

    projected_occupancy = {shelter_id: shelter.occupancy for shelter_id, shelter in shelters.items()}
    results: list[OccupantTransferProposal] = []
    for request in sorted(parsed, key=lambda item: item.transfer_id):
        if request.source_shelter_id not in shelters:
            raise ValueError(f"unknown source shelter {request.source_shelter_id!r}")
        if request.destination_shelter_id not in shelters:
            raise ValueError(f"unknown destination shelter {request.destination_shelter_id!r}")
        source = shelters[request.source_shelter_id]
        destination = shelters[request.destination_shelter_id]
        route = request.route
        if not route.reachable:
            results.append(
                OccupantTransferProposal(
                    transfer_id=request.transfer_id,
                    source_shelter_id=source.id,
                    destination_shelter_id=destination.id,
                    requested_quantity=request.quantity,
                    proposed_quantity=0,
                    route_road_ids=[],
                    status="unavailable",
                    reason=route.failure_reason or "no validated route is available",
                )
            )
            continue
        if not _route_matches_locations(route, source.location, destination.location):
            raise ValueError(
                f"route for transfer {request.transfer_id!r} does not match the "
                "source and destination shelter locations"
            )

        available_people = projected_occupancy[source.id]
        remaining_capacity = destination.capacity - projected_occupancy[destination.id]
        proposed = min(request.quantity, available_people, remaining_capacity)
        if proposed == 0:
            results.append(
                OccupantTransferProposal(
                    transfer_id=request.transfer_id,
                    source_shelter_id=source.id,
                    destination_shelter_id=destination.id,
                    requested_quantity=request.quantity,
                    proposed_quantity=0,
                    route_road_ids=[],
                    status="unavailable",
                    reason="source occupancy or destination capacity prevents transfer",
                )
            )
            continue

        projected_occupancy[source.id] -= proposed
        projected_occupancy[destination.id] += proposed
        reason = (
            "Transfer is bounded by source occupancy and remaining destination capacity."
            if proposed < request.quantity
            else "Requested transfer fits source occupancy and remaining destination capacity."
        )
        results.append(
            OccupantTransferProposal(
                transfer_id=request.transfer_id,
                source_shelter_id=source.id,
                destination_shelter_id=destination.id,
                requested_quantity=request.quantity,
                proposed_quantity=proposed,
                route_road_ids=route.road_ids,
                status="proposed",
                reason=reason,
            )
        )
    return results


def allocate_resources(
    evaluation_id: str,
    relief_units: Sequence[UnitData],
    shelters: Sequence[ShelterData],
    routes: Mapping[tuple[str, str], RouteData],
    reservations: Sequence[ReservationData] = (),
    config: PriorityConfig | None = None,
    occupant_transfers: Sequence[TransferData] = (),
) -> AllocationResult:
    """Create stable greedy proposals without mutating inventory or commitments.

    Priority uses available explicit priority and risk scores, population
    relative to the largest supplied population, unmet demand relative to the
    largest remaining shelter demand, and whether any unit has a reachable
    route. Missing explicit/risk/population inputs are omitted and reported;
    the observed priority weights are renormalized. The fallback score is only
    used if configuration gives zero total weight to every available factor.
    """
    if not evaluation_id.strip():
        raise ValueError("evaluation_id cannot be blank")
    selected_config = config or PriorityConfig()
    units = _parse_models(relief_units, ReliefUnitContract, "relief unit")
    shelter_list = _parse_models(shelters, ShelterContract, "shelter")
    shelter_by_id = {shelter.id: shelter for shelter in shelter_list}
    unit_by_id = {unit.id: unit for unit in units}
    routes_by_pair: dict[tuple[str, str], RouteResult] = {}
    for pair, route in routes.items():
        if len(pair) != 2:
            raise ValueError("route keys must be (unit_id, shelter_id) pairs")
        if pair[0] not in unit_by_id or pair[1] not in shelter_by_id:
            raise ValueError(f"route references unknown unit or shelter: {pair!r}")
        parsed_route = _route_model(route)
        if parsed_route.reachable and not _route_matches_locations(
            parsed_route, unit_by_id[pair[0]].location, shelter_by_id[pair[1]].location
        ):
            raise ValueError(
                f"route for {pair[0]!r} to {pair[1]!r} does not match the "
                "unit and shelter locations"
            )
        routes_by_pair[pair] = parsed_route

    reservation_list = [
        reservation
        if isinstance(reservation, ReservationContract)
        else ReservationContract.model_validate(reservation)
        for reservation in reservations
    ]
    unique_reservations: dict[str, ReservationContract] = {}
    for reservation in reservation_list:
        previous = unique_reservations.get(reservation.allocation_id)
        if previous is not None and previous != reservation:
            raise ValueError(
                f"conflicting existing commitments share allocation ID {reservation.allocation_id!r}"
            )
        unique_reservations[reservation.allocation_id] = reservation

    available_inventory: dict[str, dict[str, int | None]] = {
        unit.id: dict(unit.inventory) for unit in units
    }
    demand_remaining: dict[str, dict[str, int]] = {
        shelter.id: dict(shelter.demand) for shelter in shelter_list
    }
    for reservation in unique_reservations.values():
        if reservation.unit_id not in unit_by_id:
            raise ValueError(f"commitment references unknown relief unit {reservation.unit_id!r}")
        if reservation.shelter_id not in shelter_by_id:
            raise ValueError(f"commitment references unknown shelter {reservation.shelter_id!r}")
        unit_inventory = available_inventory[reservation.unit_id]
        current_inventory = unit_inventory.get(reservation.item)
        if current_inventory is None or current_inventory < reservation.quantity:
            raise ValueError(
                f"commitment {reservation.allocation_id!r} exceeds known inventory for "
                f"{reservation.unit_id}/{reservation.item}"
            )
        current_demand = demand_remaining[reservation.shelter_id].get(reservation.item, 0)
        if current_demand < reservation.quantity:
            raise ValueError(
                f"commitment {reservation.allocation_id!r} exceeds remaining demand for "
                f"{reservation.shelter_id}/{reservation.item}"
            )
        unit_inventory[reservation.item] = current_inventory - reservation.quantity
        demand_remaining[reservation.shelter_id][reservation.item] = (
            current_demand - reservation.quantity
        )

    priorities = _shelter_priorities(
        shelter_list,
        demand_remaining,
        routes_by_pair,
        [unit.id for unit in units],
        selected_config,
    )
    proposals: list[AllocationProposal] = []
    for priority in priorities:
        shelter_id = priority.shelter_id
        for item in sorted(demand_remaining[shelter_id]):
            outstanding = demand_remaining[shelter_id][item]
            candidates: list[tuple[float, str, RouteResult]] = []
            for unit in units:
                route = _route_for_pair(routes_by_pair, unit.id, shelter_id)
                if route is not None and route.reachable:
                    candidates.append((route.total_cost or 0.0, unit.id, route))
            candidates.sort(key=lambda item_route: (item_route[0], item_route[1]))

            for _, unit_id, route in candidates:
                inventory = available_inventory[unit_id].get(item)
                if inventory is None or inventory <= 0 or outstanding <= 0:
                    continue
                quantity = min(inventory, outstanding)
                if quantity <= 0:
                    continue
                available_inventory[unit_id][item] = inventory - quantity
                outstanding -= quantity
                proposals.append(
                    AllocationProposal(
                        allocation_id=_stable_allocation_id(
                            evaluation_id, unit_id, shelter_id, item
                        ),
                        unit_id=unit_id,
                        shelter_id=shelter_id,
                        item=item,
                        quantity=quantity,
                        route_road_ids=route.road_ids,
                        reason=(
                            f"Reachable route selected; proposed {quantity} unit(s) "
                            f"against remaining {item} demand."
                        ),
                    )
                )
            demand_remaining[shelter_id][item] = outstanding

    missing_inventory: dict[str, list[str]] = {}
    for unit in units:
        relevant_items = {
            item
            for shelter in shelter_list
            for item, quantity in shelter.demand.items()
            if quantity > 0
        }
        missing = sorted(
            item
            for item in relevant_items
            if item not in available_inventory[unit.id]
            or available_inventory[unit.id][item] is None
        )
        if missing:
            missing_inventory[unit.id] = missing
        for item in relevant_items:
            available_inventory[unit.id].setdefault(item, None)

    unmet: list[UnmetDemand] = []
    for shelter_id in sorted(demand_remaining):
        for item in sorted(demand_remaining[shelter_id]):
            quantity = demand_remaining[shelter_id][item]
            if quantity <= 0:
                continue
            missing_units = sorted(
                unit_id
                for unit_id, unit_inventory in available_inventory.items()
                if item not in unit_inventory or unit_inventory[item] is None
            )
            reason = (
                "Demand remains because required inventory is unknown for some units."
                if missing_units
                else "Demand remains because available inventory or reachable routes were insufficient."
            )
            unmet.append(
                UnmetDemand(
                    shelter_id=shelter_id,
                    item=item,
                    quantity=quantity,
                    missing_inventory_units=missing_units,
                    reason=reason,
                )
            )

    transfer_results = _allocate_occupant_transfers(occupant_transfers, shelter_by_id)
    return AllocationResult(
        evaluation_id=evaluation_id,
        shelter_priorities=priorities,
        proposals=sorted(
            proposals,
            key=lambda proposal: (proposal.shelter_id, proposal.item, proposal.unit_id),
        ),
        unmet_demand=unmet,
        inventory_remaining=available_inventory,
        missing_inventory=missing_inventory,
        occupant_transfers=transfer_results,
    )
