import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.agents.allocation import allocate_resources
from app.schemas.contracts import RouteResult


SCENARIO_PATH = Path(__file__).parents[1] / "data" / "sample" / "synthetic_routing_scenario.json"


def reachable_route(cost=10):
    return RouteResult(
        reachable=True,
        source="N001",
        target="N002",
        road_ids=["R001"],
        nodes=["N001", "N002"],
        route_geometry={"type": "LineString", "coordinates": [[80, 13], [80.01, 13]]},
        total_cost=cost,
    )


def unreachable_route():
    return RouteResult(
        reachable=False,
        source="N001",
        target="N002",
        failure_reason="blocked",
    )


def unit(unit_id="U1", inventory=None):
    return {"id": unit_id, "location": "N001", "inventory": inventory or {}}


def shelter(
    shelter_id="S1",
    demand=None,
    priority=None,
    risk_score=None,
    affected_population=None,
    occupancy=0,
    capacity=100,
    location="N002",
):
    return {
        "id": shelter_id,
        "name": shelter_id,
        "location": location,
        "capacity": capacity,
        "occupancy": occupancy,
        "demand": demand or {},
        "priority": priority,
        "risk_score": risk_score,
        "affected_population": affected_population,
    }


def test_sufficient_inventory_fulfills_demand_and_returns_remainder():
    result = allocate_resources(
        "eval-1",
        [unit(inventory={"water": 20})],
        [shelter(demand={"water": 12})],
        {("U1", "S1"): reachable_route()},
    )

    assert [(p.item, p.quantity) for p in result.proposals] == [("water", 12)]
    assert result.inventory_remaining["U1"]["water"] == 8
    assert result.unmet_demand == []


def test_inventory_shortage_reports_partial_allocation_and_unmet_demand():
    result = allocate_resources(
        "eval-1",
        [unit(inventory={"water": 4})],
        [shelter(demand={"water": 10})],
        {("U1", "S1"): reachable_route()},
    )

    assert result.proposals[0].quantity == 4
    assert result.unmet_demand[0].quantity == 6


def test_shelter_priority_controls_greedy_allocation_order():
    shelters = [
        shelter("S1", {"water": 10}, priority=0.9),
        shelter("S2", {"water": 10}, priority=0.1),
    ]
    result = allocate_resources(
        "eval-1",
        [unit(inventory={"water": 12})],
        shelters,
        {("U1", "S1"): reachable_route(10), ("U1", "S2"): reachable_route(20)},
    )

    assert result.shelter_priorities[0].shelter_id == "S1"
    quantities = {(p.shelter_id, p.quantity) for p in result.proposals}
    assert quantities == {("S1", 10), ("S2", 2)}
    assert [(item.shelter_id, item.quantity) for item in result.unmet_demand] == [("S2", 8)]


def test_each_item_type_is_allocated_independently():
    result = allocate_resources(
        "eval-1",
        [unit(inventory={"water": 4, "food": 2})],
        [shelter(demand={"water": 4, "food": 2})],
        {("U1", "S1"): reachable_route()},
    )

    assert {(p.item, p.quantity) for p in result.proposals} == {("water", 4), ("food", 2)}


def test_unreachable_shelter_receives_no_proposed_delivery():
    result = allocate_resources(
        "eval-1",
        [unit(inventory={"water": 10})],
        [shelter(demand={"water": 5})],
        {("U1", "S1"): unreachable_route()},
    )

    assert result.proposals == []
    assert result.unmet_demand[0].quantity == 5


def test_repeated_evaluation_has_stable_ids_and_no_duplicate_lines():
    args = (
        "same-evaluation",
        [unit(inventory={"water": 5})],
        [shelter(demand={"water": 5})],
        {("U1", "S1"): reachable_route()},
    )
    first = allocate_resources(*args)
    repeated = allocate_resources(*args)

    assert first.model_dump() == repeated.model_dump()
    assert len({proposal.allocation_id for proposal in first.proposals}) == 1


def test_existing_reservations_deduct_inventory_and_demand():
    result = allocate_resources(
        "eval-2",
        [unit(inventory={"water": 15})],
        [shelter(demand={"water": 10})],
        {("U1", "S1"): reachable_route()},
        reservations=[
            {
                "allocation_id": "already-committed",
                "unit_id": "U1",
                "shelter_id": "S1",
                "item": "water",
                "quantity": 4,
            }
        ],
    )

    assert result.proposals[0].quantity == 6
    assert result.inventory_remaining["U1"]["water"] == 5
    assert result.unmet_demand == []


def test_duplicate_existing_commitment_is_counted_once():
    commitment = {
        "allocation_id": "same",
        "unit_id": "U1",
        "shelter_id": "S1",
        "item": "water",
        "quantity": 4,
    }
    result = allocate_resources(
        "eval-2",
        [unit(inventory={"water": 10})],
        [shelter(demand={"water": 10})],
        {("U1", "S1"): reachable_route()},
        reservations=[commitment, commitment],
    )

    assert result.proposals[0].quantity == 6


def test_empty_or_unknown_inventory_is_not_treated_as_unlimited():
    result = allocate_resources(
        "eval-1",
        [unit(inventory={}), unit("U2", {"water": None})],
        [shelter(demand={"water": 5})],
        {("U1", "S1"): reachable_route(), ("U2", "S1"): reachable_route()},
    )

    assert result.proposals == []
    assert result.missing_inventory == {"U1": ["water"], "U2": ["water"]}
    assert result.unmet_demand[0].missing_inventory_units == ["U1", "U2"]


def test_negative_inventory_and_over_capacity_shelter_are_rejected():
    with pytest.raises(ValidationError):
        allocate_resources(
            "eval-1",
            [unit(inventory={"water": -1})],
            [shelter(demand={"water": 1})],
            {("U1", "S1"): reachable_route()},
        )
    with pytest.raises(ValidationError):
        allocate_resources(
            "eval-1",
            [unit(inventory={"water": 1})],
            [shelter(demand={"water": 1}, occupancy=11, capacity=10)],
            {("U1", "S1"): reachable_route()},
        )


def test_route_cost_then_unit_id_provides_deterministic_unit_tie_breaking():
    result = allocate_resources(
        "eval-1",
        [unit("U2", {"water": 5}), unit("U1", {"water": 5})],
        [shelter(demand={"water": 8})],
        {
            ("U2", "S1"): reachable_route(10),
            ("U1", "S1"): reachable_route(10),
        },
    )

    assert [(proposal.unit_id, proposal.quantity) for proposal in result.proposals] == [
        ("U1", 5),
        ("U2", 3),
    ]


def test_identical_shelter_priority_uses_stable_shelter_id_tie_break():
    result = allocate_resources(
        "eval-1",
        [unit(inventory={"water": 2})],
        [shelter("S2", {"water": 5}), shelter("S1", {"water": 5})],
        {("U1", "S1"): reachable_route(), ("U1", "S2"): reachable_route()},
    )

    assert [priority.shelter_id for priority in result.shelter_priorities] == ["S1", "S2"]
    assert result.proposals[0].shelter_id == "S1"


def test_occupant_transfer_is_limited_by_source_and_destination_capacity():
    result = allocate_resources(
        "eval-1",
        [unit(inventory={})],
        [
            shelter("SOURCE", occupancy=8, capacity=10, location="N001"),
            shelter("DESTINATION", occupancy=8, capacity=10),
        ],
        {},
        occupant_transfers=[
            {
                "transfer_id": "T1",
                "source_shelter_id": "SOURCE",
                "destination_shelter_id": "DESTINATION",
                "quantity": 5,
                "route": reachable_route(),
            }
        ],
    )

    transfer = result.occupant_transfers[0]
    assert transfer.proposed_quantity == 2
    assert transfer.status == "proposed"
    assert "capacity" in transfer.reason


def test_occupant_transfer_with_full_destination_is_unavailable():
    result = allocate_resources(
        "eval-1",
        [unit()],
        [
            shelter("SOURCE", occupancy=8, location="N001"),
            shelter("DESTINATION", occupancy=10, capacity=10),
        ],
        {},
        occupant_transfers=[
            {
                "transfer_id": "T1",
                "source_shelter_id": "SOURCE",
                "destination_shelter_id": "DESTINATION",
                "quantity": 1,
                "route": reachable_route(),
            }
        ],
    )

    assert result.occupant_transfers[0].proposed_quantity == 0
    assert result.occupant_transfers[0].status == "unavailable"


def test_allocation_marks_missing_priority_inputs_without_zero_filling():
    result = allocate_resources(
        "eval-1",
        [unit(inventory={"water": 1})],
        [shelter(demand={"water": 1})],
        {("U1", "S1"): reachable_route()},
    )

    assert result.shelter_priorities[0].missing_inputs == [
        "explicit_priority",
        "risk_score",
        "affected_population",
    ]
    assert "renormalized" in result.shelter_priorities[0].explanation


def test_reachable_route_must_match_unit_and_shelter_endpoints():
    mismatched = RouteResult(
        reachable=True,
        source="OTHER-START",
        target="N002",
        road_ids=["R001"],
        nodes=["OTHER-START", "N002"],
        route_geometry={"type": "LineString", "coordinates": [[80, 13], [80.01, 13]]},
        total_cost=10,
    )
    with pytest.raises(ValueError, match="does not match"):
        allocate_resources(
            "eval-1",
            [unit(inventory={"water": 1})],
            [shelter(demand={"water": 1})],
            {("U1", "S1"): mismatched},
        )


def test_sample_fixture_is_labelled_synthetic_and_has_two_shelters():
    scenario = json.loads(SCENARIO_PATH.read_text(encoding="utf-8"))

    assert scenario["scenario_id"].startswith("SYNTHETIC")
    assert "not validated" in scenario["description"]
    assert len(scenario["shelters"]) >= 2
