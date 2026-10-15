from app.agents.escalation import evaluate_escalation
from app.schemas.contracts import AllocationResult

SIMULATION_ID = "SIM-1"


def road(road_id="R1", source="N1", target="N2", status="open"):
    return {
        "id": road_id,
        "source": source,
        "target": target,
        "status": status,
        "risk_score": 0.1,
        "geometry": {
            "type": "LineString",
            "coordinates": [[80.0, 13.0], [80.01, 13.0]],
        },
    }


def route(road_ids=None, nodes=None, reachable=True, failure_reason=None):
    road_ids = road_ids if road_ids is not None else ["R1"]
    nodes = nodes if nodes is not None else ["N1", "N2"]
    return {
        "reachable": reachable,
        "source": "N1",
        "target": "N2",
        "road_ids": road_ids,
        "nodes": nodes,
        "route_geometry": {
            "type": "LineString",
            "coordinates": [[80.0, 13.0], [80.01, 13.0]],
        }
        if reachable and road_ids
        else None,
        "total_cost": 100.0 if reachable else None,
        "failure_reason": failure_reason,
    }


def request(**overrides):
    data = {
        "decision_id": "DEC-1",
        "simulation_id": SIMULATION_ID,
        "action_kind": "route",
        "expected_revision": 4,
        "current_revision": 4,
        "risk_score": 0.1,
        "route": route(),
        "roads": [road()],
        "evidence_complete": True,
        "reversible": True,
        "simulation_only": True,
    }
    data.update(overrides)
    return data


def allocation_result(proposals=None, transfers=None):
    return AllocationResult(
        evaluation_id="EVAL-1",
        shelter_priorities=[],
        proposals=proposals or [],
        unmet_demand=[],
        inventory_remaining={},
        missing_inventory={},
        occupant_transfers=transfers or [],
    )


def test_valid_low_risk_reversible_simulation_action_auto_approved():
    result = evaluate_escalation(request())

    assert result.status == "AUTO_APPROVED"
    assert result.requires_approval is False
    assert "AUTO_APPROVAL_LOW_RISK_REVERSIBLE_SIMULATION" in result.rule_ids
    assert result.state_applied is False


def test_critical_risk_action_requires_human_approval():
    result = evaluate_escalation(request(risk_score=0.75))

    assert result.status == "PENDING_APPROVAL"
    assert result.requires_approval is True
    assert "APPROVAL_CRITICAL_RISK" in result.rule_ids


def test_high_impact_allocation_requires_approval():
    result = evaluate_escalation(
        request(
            action_kind="allocation",
            route=None,
            allocation_result=allocation_result(
                [
                    {
                        "allocation_id": "A1",
                        "unit_id": "U1",
                        "shelter_id": "S1",
                        "item": "water",
                        "quantity": 5,
                        "route_road_ids": ["R1"],
                        "reason": "test proposal",
                    }
                ]
            ),
            relief_units=[
                {
                    "id": "U1",
                    "location": "N1",
                    "inventory": {"water": 20},
                }
            ],
            shelters=[
                {
                    "id": "S1",
                    "name": "Shelter",
                    "location": "N2",
                    "capacity": 100,
                    "occupancy": 20,
                    "demand": {"water": 5},
                }
            ],
        )
    )

    assert result.status == "PENDING_APPROVAL"
    assert "APPROVAL_HIGH_IMPACT_ALLOCATION" in result.rule_ids


def test_per_item_quantity_above_auto_approval_cap_requires_approval():
    result = evaluate_escalation(
        request(
            action_kind="allocation",
            route=None,
            allocation_result=allocation_result(
                [
                    {
                        "allocation_id": "A1",
                        "unit_id": "U1",
                        "shelter_id": "S1",
                        "item": "water",
                        "quantity": 26,
                        "route_road_ids": ["R1"],
                        "reason": "test proposal",
                    }
                ]
            ),
            relief_units=[
                {"id": "U1", "location": "N1", "inventory": {"water": 500}}
            ],
            shelters=[
                {
                    "id": "S1",
                    "name": "Shelter",
                    "location": "N2",
                    "capacity": 100,
                    "occupancy": 10,
                    "demand": {"water": 26},
                }
            ],
        )
    )

    assert result.status == "PENDING_APPROVAL"
    assert "APPROVAL_HIGH_IMPACT_ALLOCATION" in result.rule_ids


def test_non_simulation_or_irreversible_action_requires_approval():
    result = evaluate_escalation(request(simulation_only=False))

    assert result.status == "PENDING_APPROVAL"
    assert "APPROVAL_NOT_AUTO_ELIGIBLE" in result.rule_ids


def test_stale_revision_requires_approval():
    result = evaluate_escalation(request(expected_revision=3, current_revision=4))

    assert result.status == "PENDING_APPROVAL"
    assert "APPROVAL_REVISION_MISMATCH" in result.rule_ids
    assert "state manager must revalidate" in result.explanation


def test_missing_evidence_requires_approval():
    result = evaluate_escalation(request(evidence_complete=False))

    assert result.status == "PENDING_APPROVAL"
    assert "APPROVAL_UNCERTAIN_EVIDENCE" in result.rule_ids


def test_blocked_road_is_hard_blocked_and_not_eligible_for_override():
    result = evaluate_escalation(request(roads=[road(status="blocked")]))

    assert result.status == "BLOCKED"
    assert result.requires_approval is False
    assert "HARD_INVALID_ROUTE" in result.rule_ids
    assert "cannot override" in result.explanation


def test_unreachable_route_is_hard_blocked():
    result = evaluate_escalation(
        request(
            route=route(
                road_ids=[],
                nodes=[],
                reachable=False,
                failure_reason="no feasible route",
            )
        )
    )

    assert result.status == "BLOCKED"
    assert "no feasible route" in result.explanation


def test_unknown_or_disconnected_road_route_is_hard_blocked():
    unknown = evaluate_escalation(request(route=route(["R404"], ["N1", "N2"])))
    disconnected = evaluate_escalation(
        request(
            route=route(["R1"], ["N1", "N2"]),
            roads=[road(source="N3", target="N2")],
        )
    )

    assert unknown.status == "BLOCKED"
    assert "unknown road" in unknown.explanation
    assert disconnected.status == "BLOCKED"
    assert "does not connect" in disconnected.explanation


def test_conflicting_road_node_coordinates_are_a_hard_safety_violation():
    result = evaluate_escalation(
        request(
            roads=[
                road(),
                {
                    **road("R2", source="N2", target="N3"),
                    "geometry": {
                        "type": "LineString",
                        "coordinates": [[81.0, 13.0], [81.01, 13.0]],
                    },
                },
            ]
        )
    )

    assert result.status == "BLOCKED"
    assert "HARD_INVALID_ROAD_SNAPSHOT" in result.rule_ids
    assert "conflicting endpoint coordinates" in result.explanation


def test_allocation_over_inventory_is_hard_blocked():
    result = evaluate_escalation(
        request(
            action_kind="allocation",
            route=None,
            allocation_result=allocation_result(
                [
                    {
                        "allocation_id": "A1",
                        "unit_id": "U1",
                        "shelter_id": "S1",
                        "item": "water",
                        "quantity": 8,
                        "route_road_ids": ["R1"],
                        "reason": "test proposal",
                    }
                ]
            ),
            relief_units=[
                {"id": "U1", "location": "N1", "inventory": {"water": 5}}
            ],
            shelters=[
                {
                    "id": "S1",
                    "name": "Shelter",
                    "location": "N2",
                    "capacity": 50,
                    "occupancy": 10,
                    "demand": {"water": 8},
                }
            ],
        )
    )

    assert result.status == "BLOCKED"
    assert "HARD_RESOURCE_OR_CAPACITY_CONSTRAINT" in result.rule_ids


def test_allocation_using_flooded_road_is_hard_blocked():
    result = evaluate_escalation(
        request(
            action_kind="allocation",
            route=None,
            allocation_result=allocation_result(
                [
                    {
                        "allocation_id": "A1",
                        "unit_id": "U1",
                        "shelter_id": "S1",
                        "item": "water",
                        "quantity": 1,
                        "route_road_ids": ["R1"],
                        "reason": "test proposal",
                    }
                ]
            ),
            relief_units=[
                {"id": "U1", "location": "N1", "inventory": {"water": 5}}
            ],
            shelters=[
                {
                    "id": "S1",
                    "name": "Shelter",
                    "location": "N2",
                    "capacity": 50,
                    "occupancy": 10,
                    "demand": {"water": 1},
                }
            ],
            roads=[road(status="flooded")],
        )
    )

    assert result.status == "BLOCKED"
    assert "non-traversable road" in result.explanation


def test_capacity_violation_is_hard_blocked():
    result = evaluate_escalation(
        request(
            action_kind="allocation",
            route=None,
            allocation_result=allocation_result(
                transfers=[
                    {
                        "transfer_id": "T1",
                        "source_shelter_id": "S1",
                        "destination_shelter_id": "S2",
                        "requested_quantity": 20,
                        "proposed_quantity": 20,
                        "route_road_ids": ["R1"],
                        "status": "proposed",
                        "reason": "test",
                    }
                ]
            ),
            shelters=[
                {
                    "id": "S1",
                    "name": "Source",
                    "location": "N1",
                    "capacity": 100,
                    "occupancy": 10,
                    "demand": {},
                },
                {
                    "id": "S2",
                    "name": "Destination",
                    "location": "N2",
                    "capacity": 10,
                    "occupancy": 8,
                    "demand": {},
                },
            ],
        )
    )

    assert result.status == "BLOCKED"
    assert "HARD_RESOURCE_OR_CAPACITY_CONSTRAINT" in result.rule_ids
    assert "exceed source occupancy" in result.explanation
    assert "exceed capacity" in result.explanation


def test_missing_risk_requires_approval_and_never_auto_approves():
    result = evaluate_escalation(request(risk_score=None))

    assert result.status == "PENDING_APPROVAL"
    assert "APPROVAL_MISSING_RISK" in result.rule_ids
