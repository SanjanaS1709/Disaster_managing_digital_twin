import json
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

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


def test_evaluate_endpoint_uses_member1_inputs_for_duplicate_event_rejection():
    payload = request_body()
    payload["seen_event_ids"] = ["EVT-REOPENED"]

    response = client().post("/api/agents/evaluate", json=payload)

    assert response.status_code == 200
    body = response.json()
    assert body["outcome"] == "DUPLICATE"
    assert body["proposed_actions"] == []
    assert body["agents"]["routing"]["status"] == "SKIPPED"


def test_evaluate_endpoint_requires_revision_history_and_reservation_inputs():
    payload = request_body()
    del payload["current_revision"]
    del payload["seen_event_ids"]
    del payload["reservations"]

    response = client().post("/api/agents/evaluate", json=payload)

    assert response.status_code == 422
