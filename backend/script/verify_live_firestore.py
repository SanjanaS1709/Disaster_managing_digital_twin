"""Run isolated live Firestore verification without touching application data.

Run with ``backend/.venv/Scripts/python.exe script/verify_live_firestore.py``.
The process exits non-zero on any failed check and removes only the uniquely named
verification collection it created.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4


def require_environment() -> tuple[str, str]:
    required = ("GOOGLE_APPLICATION_CREDENTIALS", "FIREBASE_PROJECT_ID", "SIMULATION_STORAGE_BACKEND")
    missing = [name for name in required if not os.getenv(name)]
    if missing:
        raise RuntimeError("Missing required environment variables: " + ", ".join(missing))

    credentials_file = Path(os.environ["GOOGLE_APPLICATION_CREDENTIALS"])
    if not credentials_file.is_file():
        raise RuntimeError("GOOGLE_APPLICATION_CREDENTIALS does not name an existing file")
    if os.environ["SIMULATION_STORAGE_BACKEND"].lower() != "firestore":
        raise RuntimeError("SIMULATION_STORAGE_BACKEND must be firestore for live verification")
    return os.environ["FIREBASE_PROJECT_ID"], str(credentials_file)


def fixture_roads():
    from app.road_loader import feature_collection, synthetic_graph

    return [dict(feature["properties"], coordinates=feature["geometry"]["coordinates"])
            for feature in feature_collection(synthetic_graph())["features"]]


def fixture_routing_graph(_state):
    from app.road_loader import synthetic_graph

    return synthetic_graph()


def delete_tree(reference) -> None:
    for collection in reference.collections():
        for document in collection.stream():
            delete_tree(document.reference)
    reference.delete()


def main() -> None:
    project_id, _ = require_environment()
    import firebase_admin
    from firebase_admin import credentials, firestore

    original_environment = {name: os.environ.get(name) for name in (
        "SIMULATION_STORAGE_BACKEND", "SIMULATION_DB_PATH", "DECISION_DB_PATH"
    )}
    with TemporaryDirectory() as import_directory:
        os.environ["SIMULATION_STORAGE_BACKEND"] = "local"
        os.environ["SIMULATION_DB_PATH"] = str(Path(import_directory) / "simulation.json")
        os.environ["DECISION_DB_PATH"] = str(Path(import_directory) / "decisions.json")
        from app.decisions import DecisionManager
        from app.models import DisasterEvent, ProposedAction
        from app.state_manager import StateManager
        from app.storage import FirestoreStore
    for name, value in original_environment.items():
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value

    verification_id = uuid4().hex
    collection = "__firestore_verification_" + verification_id
    app_name = "live-verification-" + verification_id
    app = firebase_admin.initialize_app(credentials.ApplicationDefault(), {"projectId": project_id}, name=app_name)
    client = firestore.client(app=app)
    created_roots = []

    try:
        print("environment variables: available")
        print("service account file: exists")
        print("Firebase Admin SDK: initialized")

        crud = client.collection(collection).document("crud")
        created_roots.append(crud)
        crud.set({"phase": "written", "marker": verification_id})
        if crud.get().to_dict() != {"phase": "written", "marker": verification_id}:
            raise RuntimeError("Firestore write/read round trip did not match")
        crud.update({"phase": "updated"})
        if crud.get().to_dict().get("phase") != "updated":
            raise RuntimeError("Firestore update did not persist")
        crud.delete()
        if crud.get().exists:
            raise RuntimeError("Firestore delete did not persist")
        print("Firestore CRUD: write/read/update/delete succeeded")

        simulation_namespace = "simulation-" + verification_id
        simulation_store = FirestoreStore(collection=collection, namespace=simulation_namespace, client=client)
        created_roots.append(client.collection(collection).document(simulation_namespace))
        manager = StateManager(store=simulation_store, roads_loader=fixture_roads)
        state = manager.get_state()
        event = DisasterEvent(
            event_id="verification-event-" + verification_id,
            simulation_id=state.simulation_id,
            target_id=state.roads[0].id,
            type="ROAD_FLOODED",
            timestamp=datetime(2023, 12, 4, 10, 1).astimezone(),
        )
        manager.apply_event(event)
        restored = StateManager(
            store=FirestoreStore(collection=collection, namespace=simulation_namespace, client=client),
            roads_loader=fixture_roads,
        )
        if restored.get_events()[0].event_id != event.event_id or restored.get_state().roads[0].status != "blocked":
            raise RuntimeError("Firestore restart recovery did not restore the event state")
        try:
            restored.apply_event(event)
        except ValueError as error:
            if "already processed" not in str(error):
                raise
        else:
            raise RuntimeError("Duplicate event was accepted after restart")
        print("simulation persistence, restart recovery, and duplicate-event protection: succeeded")

        decision_namespace = "decisions-" + verification_id
        decision_store = FirestoreStore(collection=collection, namespace=decision_namespace, client=client)
        created_roots.append(client.collection(collection).document(decision_namespace))
        decisions = DecisionManager(decision_store, graph_builder=fixture_routing_graph)
        action = ProposedAction(
            action_id="verification-action-" + verification_id,
            type="ROUTE_ASSIGNMENT",
            target_id="S001",
            route_road_ids=[state.roads[1].id],
            reason="Isolated Firestore verification",
        )
        decisions.propose(action, restored.get_state(), restored.get_state().revision)
        decisions.decide(action.action_id, True, restored.get_state(), actor="firestore-verifier")
        recovered_decision = DecisionManager(
            FirestoreStore(collection=collection, namespace=decision_namespace, client=client),
            graph_builder=fixture_routing_graph,
        ).get(action.action_id)
        if recovered_decision.status != "applied" or recovered_decision.decided_by != "firestore-verifier":
            raise RuntimeError("Decision approval was not recovered from Firestore")
        print("decision approval persistence: succeeded")
    finally:
        cleanup_errors = []
        for root in reversed(created_roots):
            try:
                delete_tree(root)
            except Exception as error:  # Report cleanup failure without hiding a verification failure.
                cleanup_errors.append(type(error).__name__)
        firebase_admin.delete_app(app)
        if cleanup_errors:
            raise RuntimeError("Temporary Firestore verification data cleanup failed: " + ", ".join(cleanup_errors))
        print("temporary verification collection: deleted")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print("LIVE FIRESTORE VERIFICATION FAILED: " + type(error).__name__ + ": " + str(error), file=sys.stderr)
        raise
