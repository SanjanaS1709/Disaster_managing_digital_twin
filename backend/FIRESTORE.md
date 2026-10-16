# Module 1 persistence

Use one API worker. `SIMULATION_STORAGE_BACKEND=local` (default) keeps atomic
JSON persistence at `SIMULATION_DB_PATH` and `DECISION_DB_PATH`. Both backends
recover active state, event IDs, accepted and pending events, manual clock,
phase, revisions, replay journals, archived runs, proposals and approval actors.
Bare `StateManager()` and `DecisionManager()` remain in memory for tests.

Install with `./.venv/Scripts/python.exe -m pip install -r requirements-dev.txt`.
The app reads process environment variables; it does not auto-load `.env` files.

For Firestore, enable a Firestore Native database in your Firebase project and set:

```powershell
$env:SIMULATION_STORAGE_BACKEND = 'firestore'
$env:FIREBASE_PROJECT_ID = 'your-project-id'
$env:FIRESTORE_COLLECTION = 'agentic_disaster_twin'
$env:FIREBASE_SERVICE_ACCOUNT_JSON = 'C:\secure\service-account.json'
.\.venv\Scripts\python.exe -m uvicorn app.main:app --workers 1
```

The credential variable accepts a file path, never inline credential JSON. Keep
the file outside the repository, restrict filesystem permissions, and never log
its contents. Alternatively omit it and use Application Default Credentials
(including `GOOGLE_APPLICATION_CREDENTIALS` or an attached workload identity).
The Admin SDK uses IAM; browser security rules do not protect these server writes.
Grant only the required database permissions, and do not expose these development
approval endpoints publicly without authentication.

For an already-running emulator, use a demo project and no cloud credentials:

```powershell
$env:SIMULATION_STORAGE_BACKEND = 'firestore'
$env:FIREBASE_PROJECT_ID = 'demo-disaster-twin'
$env:FIRESTORE_EMULATOR_HOST = '127.0.0.1:8080'
$env:FIREBASE_SERVICE_ACCOUNT_JSON = ''
.\.venv\Scripts\python.exe -m uvicorn app.main:app --workers 1
```

Firestore stores two independent namespaces, `simulation` and `decisions`, under
`FIRESTORE_COLLECTION`. Each has a head pointing to an immutable snapshot under
`generations/{uuid}/chunks/{index}`. Payload chunks are at most 200,000 bytes;
histories larger than one Firestore document are supported. Disable indexing on
the `payload` field for the `chunks` collection group in production. No queries
on that field are used.

All chunks are uploaded before the head is atomically published using its last
update-time precondition (create-if-absent for first startup). This protects
against stale writers and presents complete state/events/archives together.
Failed uploads remain unreferenced. Readers validate a SHA-256 checksum. On a
conflict or ambiguous commit failure, restart the service to reload authoritative
state; do not blindly retry the same operation. WebSocket event notifications are
sent only after the save succeeds. Local in-memory state rolls back on failures.

Road geometry, static attributes, GraphML and GeoJSON stay on disk. Firestore
contains dynamic status/risk deltas plus a SHA-256 of the static road records and
the dataset reference/source. Startup refuses a mismatched GIS version without
overwriting the persisted snapshot. Keep matching GIS exports available for all
archived runs. There is no automatic dataset migration or local-to-cloud upload:
switching backends selects that backend's existing history or initializes it.

Limitations: snapshots rewrite the retained dynamic history on each mutation;
this is suitable for the single-worker Module 1 workload, not high-throughput
multiwriter use. Previous and orphaned generations accumulate and need an
operator-managed retention policy. Do not delete generations referenced by a
head or being read. Decision and simulation namespaces commit independently;
resource transfers remain explicitly unsupported for application (as before).
Live cloud IAM, emulator transport and billing behavior require deployment-side
verification; automated tests use an in-memory Firestore fake with SDK write
preconditions, injected failures, chunking and restart recovery.

Run the full offline suite with the project interpreter. To prevent import-time
application singletons from reading/writing operator persistence during tests:

```powershell
.\.venv\Scripts\python.exe -c "import os, unittest; os.environ.update(SIMULATION_STORAGE_BACKEND='local', SIMULATION_DB_PATH='', DECISION_DB_PATH=''); unittest.main(module=None, argv=['unittest','discover','-s','tests','-v'])"
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe script/validate_data.py
```

The wrapper sets isolation variables inside Python only (older PowerShell
versions remove environment variables when assigned an empty string). It runs
the same full unittest discovery and does not alter the service environment.

Storage design references: [Firestore conditional writes and batches](https://firebase.google.com/docs/firestore/manage-data/transactions)
and [Firestore quotas](https://firebase.google.com/docs/firestore/quotas).
