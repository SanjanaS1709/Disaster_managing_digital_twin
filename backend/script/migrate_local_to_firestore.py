"""Seed an empty Firestore collection with the current local twin history."""
import hashlib
import json
import os
from pathlib import Path

from app.storage import FirestoreStore


def compact_state(state: dict) -> dict:
    result = dict(state)
    roads = result.pop("roads")
    result["road_states"] = {
        road["id"]: {"status": road["status"], "risk_score": road["risk_score"]}
        for road in roads
        if road["status"] != "open" or road["risk_score"] != 0
    }
    return result


def compact_run(run: dict) -> dict:
    result = dict(run)
    result["state"] = compact_state(run["state"])
    result["baseline"] = compact_state(run["baseline"])
    result["archives"] = {key: compact_run(value) for key, value in run.get("archives", {}).items()}
    return result


def main() -> None:
    project_id = os.environ["FIREBASE_PROJECT_ID"]
    credential_path = Path(os.environ["FIREBASE_SERVICE_ACCOUNT_JSON"])
    credential = json.loads(credential_path.read_text(encoding="utf-8"))
    if credential.get("project_id") != project_id:
        raise RuntimeError("Service-account project does not match FIREBASE_PROJECT_ID")

    source = Path(os.getenv("SIMULATION_DB_PATH", "data/simulation_state.json"))
    local = json.loads(source.read_text(encoding="utf-8"))
    state = local["baseline"]
    static_roads = [
        {key: value for key, value in road.items() if key not in {"status", "risk_score"}}
        for road in state["roads"]
    ]
    current_hash = hashlib.sha256(json.dumps(
        sorted(static_roads, key=lambda road: road["id"]),
        sort_keys=True, separators=(",", ":"),
    ).encode()).hexdigest()
    if local["network"].get("sha256") != current_hash:
        raise RuntimeError("Local snapshot does not match its road-network fingerprint")

    store = FirestoreStore(
        project_id=project_id,
        service_account_json=str(credential_path),
        collection=os.getenv("FIRESTORE_COLLECTION", "agentic_disaster_twin"),
        namespace="simulation",
    )
    if store.load() is not None:
        raise RuntimeError("Firestore simulation namespace is not empty; refusing to overwrite it")
    store.save(compact_run(local))
    print(f"Migrated local run {local['run_id']} to Firestore project {project_id}.")
    print(f"Preserved {len(local.get('archives', {}))} archived run(s); road data remains local.")


if __name__ == "__main__":
    main()
