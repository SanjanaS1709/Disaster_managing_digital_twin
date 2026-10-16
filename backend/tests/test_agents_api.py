import copy
import json
from pathlib import Path
from unittest.mock import Mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.api.agents as agents_api
from app.agents.orchestrator import WorkflowResponse
from app.api.agents import router

ROOT = Path(__file__).parents[2]
TWIN_PATH = ROOT / "shared" / "sample_twin_state.json"
EVENT_PATH = ROOT / "shared" / "sample_event.json"


def client():
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def request_body():
    twin = json.loads(TWIN_PATH.read_text(encoding="utf-8"))
    event = json.loads(EVENT_PATH.read_text(encoding="utf-8"))
    event.update(
        {
            "event_id": "EVT-REOPENED",
            "type": "ROAD_REOPENED",
            "timestamp": "2023-12-04T12:20:00Z",
        }
    )
    event["payload"] = {
        "reported_status": "open",
        "risk_indicators": {
            "flood_severity": 0.1,
            "rainfall_indicator": 0.1,
            "elevation_risk": 0.1,
            "infrastructure_vulnerability": 0.1,
            "population_exposure": 0.1,
        },
    }
    return {
        "twin_state": twin,
        "event": event,
        "current_revision": twin["revision"],
        "seen_event_ids": [],
        "reservations": [],
    }


def workflow_response(**overrides):
    response = {
        "simulation_id": "SIM-MICHAUNG-SAMPLE",
        "event_id": "EVT-REOPENED",
        "state_revision": 1,
        "current_revision": 1,
        "workflow_status": "COMPLETED",
        "outcome": "PENDING_APPROVAL",
        "agents": {},
        "explanation": "Mock workflow result.",
        "state_applied": False,
    }
    response.update(overrides)
    return WorkflowResponse(**response)


def test_evaluate_endpoint_returns_structured_proposal_revision_and_trace():
    response = client().post("/api/agents/evaluate", json=request_body())

    assert response.status_code == 200
    body = response.json()
    assert body["event_id"] == "EVT-REOPENED"
    assert body["simulation_id"] == "SIM-MICHAUNG-SAMPLE"
    assert body["state_revision"] == 1
    assert body["current_revision"] == 1
    assert body["outcome"] == "PENDING_APPROVAL"
    assert body["proposed_actions"]
    assert all(action["status"] == "proposed" for action in body["proposed_actions"])
    assert {entry["agent"] for entry in body["agent_trace"]} == {
        "perception",
        "risk",
        "routing",
        "allocation",
        "escalation",
    }
    assert body["state_applied"] is False


def test_evaluate_endpoint_passes_contract_inputs_to_mocked_orchestrator(monkeypatch):
    payload = request_body()
    original_twin_state = copy.deepcopy(payload["twin_state"])
    expected_response = workflow_response()
    run_workflow = Mock(return_value=expected_response)
    monkeypatch.setattr(agents_api, "run_workflow", run_workflow)

    response = client().post("/api/agents/evaluate", json=payload)

    assert response.status_code == 200
    assert response.json() == expected_response.model_dump(mode="json")
    run_workflow.assert_called_once()
    twin_state, event = run_workflow.call_args.args
    assert twin_state.model_dump(mode="json", exclude_unset=True) == original_twin_state
    assert event.event_id == payload["event"]["event_id"]
    assert run_workflow.call_args.kwargs == {
        "current_revision": payload["current_revision"],
        "seen_event_ids": payload["seen_event_ids"],
        "reservations": payload["reservations"],
    }
    assert payload["twin_state"] == original_twin_state


def test_evaluate_endpoint_preserves_twin_state_during_workflow(monkeypatch):
    payload = request_body()
    original_twin_state = copy.deepcopy(payload["twin_state"])
    real_run_workflow = agents_api.run_workflow

    def run_workflow_without_mutation(twin_state, event, **kwargs):
        state_before = twin_state.model_dump(mode="json")
        result = real_run_workflow(twin_state, event, **kwargs)
        assert twin_state.model_dump(mode="json") == state_before
        return result

    run_workflow = Mock(side_effect=run_workflow_without_mutation)
    monkeypatch.setattr(agents_api, "run_workflow", run_workflow)

    response = client().post("/api/agents/evaluate", json=payload)

    assert response.status_code == 200
    assert response.json()["state_applied"] is False
    assert payload["twin_state"] == original_twin_state
    run_workflow.assert_called_once()


def test_evaluate_endpoint_uses_member1_inputs_for_duplicate_event_rejection():
    payload = request_body()
    payload["seen_event_ids"] = ["EVT-REOPENED"]

    response = client().post("/api/agents/evaluate", json=payload)

    assert response.status_code == 200
    body = response.json()
    assert body["outcome"] == "DUPLICATE"
    assert body["proposed_actions"] == []
    assert body["agents"]["routing"]["status"] == "SKIPPED"


def test_evaluate_endpoint_returns_structured_workflow_errors(monkeypatch):
    expected_response = workflow_response(
        outcome="ERROR",
        errors=["Risk agent failed: RuntimeError: unavailable"],
        explanation="Workflow outcome: ERROR. Risk agent failed.",
    )
    monkeypatch.setattr(
        agents_api,
        "run_workflow",
        Mock(return_value=expected_response),
    )

    response = client().post("/api/agents/evaluate", json=request_body())

    assert response.status_code == 200
    assert response.json()["outcome"] == "ERROR"
    assert response.json()["errors"] == [
        "Risk agent failed: RuntimeError: unavailable"
    ]
    assert response.json()["state_applied"] is False


def test_evaluate_endpoint_returns_500_when_workflow_raises(monkeypatch):
    monkeypatch.setattr(
        agents_api,
        "run_workflow",
        Mock(side_effect=RuntimeError("workflow unavailable")),
    )

    response = client().post("/api/agents/evaluate", json=request_body())

    assert response.status_code == 500
    assert response.json() == {
        "detail": {
            "code": "workflow_error",
            "message": "Agent workflow evaluation failed.",
        }
    }


def test_evaluate_endpoint_requires_revision_history_and_reservation_inputs():
    payload = request_body()
    del payload["current_revision"]
    del payload["seen_event_ids"]
    del payload["reservations"]

    response = client().post("/api/agents/evaluate", json=payload)

    assert response.status_code == 422


def test_evaluate_endpoint_rejects_invalid_twin_and_event_contracts():
    invalid_twin = request_body()
    invalid_twin["twin_state"]["revision"] = -1
    invalid_twin_response = client().post(
        "/api/agents/evaluate",
        json=invalid_twin,
    )

    invalid_event = request_body()
    invalid_event["event"]["type"] = "NOT_A_SUPPORTED_EVENT"
    invalid_event_response = client().post(
        "/api/agents/evaluate",
        json=invalid_event,
    )

    assert invalid_twin_response.status_code == 422
    assert invalid_event_response.status_code == 422
