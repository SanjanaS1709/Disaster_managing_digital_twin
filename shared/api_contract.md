# Module 1 shared API contract

Base URL: `http://127.0.0.1:8000`. OpenAPI: `/openapi.json`; interactive docs: `/docs`.
This is a single-process, in-memory, manually stepped development simulation.
It is inspired by Cyclone Michaung; demo entities and simulated measurements are
not verified December 2023 observations. Road downloader and GraphML contracts are
unchanged; see `backend/ROAD_DATA.md`.

## State and identity

`TwinState` preserves `simulation_id`, timezone-aware `timestamp`, positive
`revision`, `roads`, `shelters`, `relief_units`, and `zones`. IDs are nonblank and
unique across all entity collections in a state. Revisions are scoped to a run.
Use `(run_id, revision)` to identify a historical version, not revision alone.

| Schema | Fields and validation |
| --- | --- |
| Road | `id`, `name`, `source`, `status` (`open`, `restricted`, `blocked`), `risk_score` [0,1], `coordinates` (at least two positions); optional `u`, `v`, `key`, positive finite `length_m` |
| Shelter | `id`, `name`, `location`, nonnegative integer `capacity` and `occupancy`, `demand` mapping item names to nonnegative integers, `source`; derived read-only `over_capacity` and `excess_occupancy` |
| ReliefUnit | `id`, `location`, `inventory` mapping item names to nonnegative integers, `inventory_units` mapping the same item names to unit strings, `source` |
| DisasterZone | `id`, `risk_score` [0,1], nonnegative integer `affected_population`, `source`, `rainfall` measurement list |
| RainfallMeasurement | finite nonnegative `value`, `unit` (`mm` or `mm/hour`), aware `timestamp`, `source="simulation"` |
| SimulationStatus | `simulation_id`, `run_id`, `phase`, `timestamp`, `revision`, `accepted_events`, `scheduled_events`, `mode="manual"` |
| EventHistory | `run_id`, `accepted` records, and pending `scheduled` records |
| StateSnapshot | `run_id`, `revision`, `timestamp`, complete `state` |

Coordinates are EPSG:4326 **[longitude, latitude]**: longitude [-180,180], latitude
[-90,90], exactly two finite numbers. Validating ranges cannot detect a swapped
pair when both numbers fall within both ranges. Leaflet's direct `LatLng` API uses
latitude first: reverse the pair there; pass GeoJSON to Leaflet unchanged.

Road IDs remain `R_{u}_{v}_{key}` for directed GraphML edges, with `road_id` as the
GraphML edge attribute and the same ID in GeoJSON. Opposite directions have
different IDs. `routing_graph(state)` removes blocked edges from a routing copy;
restricted edges remain available with status/risk attributes for Module 2 to
interpret. IDs are stable within a saved network, not guaranteed across OSM edits.

All demo shelters, units and zones use `source="synthetic_demo"`. Road provenance
comes from the installed GraphML: the checked-in T. Nagar network is a local OSM
XML import labelled `contemporary_osm`; synthetic fixture roads are used only by
tests and explicit offline-demo workflows.
Rainfall always has simulated provenance. No IMD observations or AI predictions
are fabricated. Road source does not imply that simulated road status is observed.

## DisasterEvent envelope

```json
{
  "event_id": "flood-001",
  "simulation_id": "michaung_demo_001",
  "type": "ROAD_FLOODED",
  "timestamp": "2023-12-04T10:01:00+05:30",
  "observed_at": "2023-12-04T10:00:30+05:30",
  "target_id": "R_0_1_0",
  "payload": {"risk_score": 1.0},
  "source": "simulation"
}
```

`timestamp` is the simulation effective time. Optional `observed_at` is the
simulated observation time, must be aware and no later than effective time.
`source` defaults to and must equal `simulation`. Accepted/scheduled records have
server-generated UTC `ingested_at`; accepted records also contain `accepted_at`.
These wall-clock audit fields never drive simulation time or revision order.

Event IDs are unique across accepted and pending scheduled events within a run.
Rejected requests do not reserve an ID. New runs may reuse old IDs. Payloads reject
unknown keys, missing required fields, nonfinite values and invalid quantities.
Integer quantities reject booleans, fractions and numeric strings.

| Event type | Target | Payload and semantics |
| --- | --- | --- |
| ROAD_FLOODED | road ID | `{ "risk_score": 1.0 }`; optional score defaults to 1; blocks the directed edge |
| ROAD_REOPENED | road ID | `{ "status": "open", "risk_score": 0.2 }`; both optional; status can be open or restricted; sets these values, not prior pre-flood values |
| SHELTER_OVER_CAPACITY | shelter ID | `{ "occupancy": 230 }`; absolute occupancy, not delta; accepts values below capacity too for recovery; never clamps; overcapacity derived |
| RESOURCE_SHORTAGE | relief-unit ID | `{ "item": "water", "quantity": 50, "unit": "litres" }`; subtracts a strictly positive integer quantity exactly once; existing item and exact unit match required; insufficient stock rejected |
| RAINFALL_INCREASE | zone ID | `{ "value": 25, "unit": "mm/hour" }`; appends an absolute measurement, not a delta; value and observation time cannot decrease relative to last measurement in the same unit; units are separate series, never converted or added together |

The demo inventory defines `food` in `packs` and `water` in `litres`. Units are
case-sensitive and item-specific. Existing state schemas may omit inventory_units
for compatibility, but shortages require the targeted item to have a declared
unit. To flood both directions, submit one event per directed edge ID.

## Endpoints

All successful operations return HTTP 200. Bodies below are JSON.

| Method | Path | Request | Response |
| --- | --- | --- | --- |
| GET | `/api/twin/state` | none | TwinState |
| GET | `/api/twin/geojson` | none | Road FeatureCollection reflecting live statuses and risk |
| POST | `/api/simulation/events` | DisasterEvent | Updated TwinState; immediate application |
| GET | `/api/simulation/events` | none | Accepted DisasterEvent array for current run (existing contract) |
| POST | `/api/simulation/start` | none | SimulationStatus |
| POST | `/api/simulation/pause` | none | SimulationStatus |
| POST | `/api/simulation/resume` | none | SimulationStatus |
| POST | `/api/simulation/advance` | `{ "seconds": 60 }` | SimulationStatus |
| POST | `/api/simulation/reset` | none | New-run TwinState (existing contract) |
| GET | `/api/simulation/status` | none | SimulationStatus |
| POST | `/api/simulation/schedule` | DisasterEvent | ScheduledEvent record |
| GET | `/api/simulation/history` | optional `run_id` query | EventHistory |
| GET | `/api/simulation/runs` | none | Run ID array, archived runs then current |
| GET | `/api/simulation/snapshot` | optional `revision`, `run_id` queries | StateSnapshot reconstructed at that revision |
| POST | `/api/simulation/replay` | `{}` or `{ "revision": 2, "run_id": "..." }` | StateSnapshot; read-only replay |

A scheduled record contains `event`, monotonic insertion `sequence`, `ingested_at`
and `status="scheduled"`. An accepted record contains `event`, resulting
`revision`, `ingested_at`, `accepted_at`, and `status="accepted"`. Pending events
are not in the accepted event list. Rejected requests return errors and are logged;
they are not stored in the accepted history and have no state/revision effect.

## Lifecycle, clock and revisions

- A new run is `ready`, at baseline revision 1 and 2023-12-04 10:00 Asia/Kolkata.
- START: ready -> running. PAUSE: running -> paused. RESUME: paused -> running.
  Calling a transition from any other phase returns 400. These transitions do
  not change TwinState or its revision.
- No background clock runs. Running means ready for manual advances; PAUSE records
  the control state. Explicit ADVANCE also works while paused (single-step).
- ADVANCE requires running/paused and finite positive seconds <= 31536000, with
  at least one microsecond of effective movement. It processes scheduled events
  in `(old_clock, new_clock]`, ordered by `(timestamp, insertion sequence)`.
- Each applied event adds exactly one revision, even if it sets an existing value.
  If an advance ends after the last applied event, moving the clock to its target
  adds one further revision. A clock-only advance therefore adds one revision.
- Scheduling, status reads, pause/resume/start, snapshots and replay do not add
  revisions. Scheduling validates the projected queue but does not apply it.
- Immediate POST events remain available in every phase for compatibility. Their
  timestamp must be >= current clock; equal-time direct events use acceptance
  order. Future immediate events move the clock, but cannot skip a pending event
  at or before that timestamp: advance the queue first.
- State-manager mutations/read snapshots are protected by an RLock. Events and
  advance batches validate copies first. A failed batch publishes no state,
  history, queue removal, timestamp or revision changes.
- Immediate changes can invalidate an already queued resource event. ADVANCE
  then rejects the entire batch, keeps the queue and explains the error. There
  is no inventory replenishment/cancellation endpoint in this MVP; reset begins
  a clean run. Failed requests never partially consume inventory.

## History, reset and replay

Accepted history is append-only per run. RESET archives the entire prior run
(including unapplied scheduled events), reloads current local road data into a new
baseline, creates a new run ID, clears the current event list/queue and returns
revision 1 in ready phase. The simulation ID remains the existing demo ID.
If baseline loading fails, reset leaves the previous run untouched.

Each run stores a deep-copied baseline and a journal of accepted events plus
clock-only movements. Snapshots/replay reconstruct the requested revision from
that baseline without rereading disk, consuming resources again, changing the
current run, applying pending events or executing agent actions. Archived pending
events remain inspectable but never run. Default replay returns the current run's
latest TwinState exactly, including timestamp and revision. Wall-clock ingestion
and acceptance metadata, run UUIDs, and lifecycle phase are not replayed state.
No mutation/rewind is implied by POST replay; use the returned snapshot to inspect
history. Configured local/Firestore storage restores runs and journals after
restart. In-memory test managers do not persist. Retained history grows over time.

## Errors

Domain failures (unknown entity/run, stale timestamp, duplicate ID, invalid
transition, insufficient stock, unit mismatch) return:

```json
{"detail": "Insufficient inventory for water"}
```

HTTP 400 preserves the existing event-error format. Schema/body/query validation
returns HTTP 422 with FastAPI's standard `detail` array (`loc`, `msg`, `type`, and
optional context). Neither form mutates the simulation. Unexpected I/O failures
are server errors and must be corrected rather than hidden with synthetic data.

## Example manual timeline

1. POST reset, then GET state/status to obtain current IDs and run ID.
2. POST schedule a ROAD_FLOODED event at 10:01 and ROAD_REOPENED at 10:02.
3. POST start, POST pause, POST advance `{"seconds":60}`: road blocked, revision 2.
4. POST resume, POST advance `{"seconds":60}`: road open, revision 3.
5. GET snapshot?revision=2 shows blocked road. POST replay `{}` equals live state
   without changing it. GET history distinguishes accepted from pending events.
6. POST reset clears current history. POST replay with old run_id still reconstructs
   the previous run while the process remains alive.

`sample_twin_state.json` is the synthetic demo baseline shape; `sample_event.json`
is a single valid event for its synthetic road `R_0_1_0`. `sample_events.json`
contains all five full request examples for that same fixture. These are not
universally valid runtime road IDs: obtain IDs from `GET /api/twin/state` after
loading a different OSM network. `backend/script/verify_simulation.py` exercises
a complete isolated FastAPI timeline with assertions and validates the shared
example files.

## Running on Windows

```powershell
cd C:\Users\DELL\full_stack\agentic-disaster-twin\backend
C:\Python313\python.exe -m unittest discover -s tests -v
C:\Python313\python.exe script/verify_simulation.py
C:\Python313\python.exe -m uvicorn app.main:app --reload
```

The verified global interpreter has all required libraries. For another machine,
install `requirements-dev.txt` using its chosen Python 3.13 interpreter. Do not
use `backend\Scripts\python.exe` (missing OSMnx in the inspected environment),
or edit app copies under `backend\venv`. Use one application worker. No Firebase,
WebSockets, frontend, LangGraph, historical data acquisition or auto-play is added.

## Persistence and integration boundary

The application manager persists its authoritative run to
`SIMULATION_DB_PATH` using an atomic JSON replacement. Direct `StateManager()`
instances remain isolated in-memory managers for tests. Use one Uvicorn worker.
Select `SIMULATION_STORAGE_BACKEND=local|firestore`. Firestore stores dynamic
road deltas, a GIS version/reference, event histories and archived runs; local
GraphML/GeoJSON must match the stored version on recovery. Decision proposals
and approval actors persist in a separate namespace (or `DECISION_DB_PATH` for
local storage). See `backend/FIRESTORE.md` for secure Admin SDK configuration,
emulator setup, conditional writes, restart behavior and retention limitations.
Cloud/emulator transport has not been live-verified; offline mock tests cover
storage failures and restart recovery. No credentials are stored in this repository.

`WS /ws/updates` sends `SYNC` on connection and best-effort `STATE_CHANGED`
messages after persistence succeeds. Messages include simulation ID, event ID,
event type, revision, timestamp, affected IDs, and a REST refetch path. Delivery
is not exactly once; clients compare revisions and refetch after reconnect/gaps.
Rejected events produce no state-change message.

`POST /api/decisions?expected_revision=N` accepts validated `ProposedAction`
contracts. Route assignments are checked against directed GraphML edges and
blocked-road status, then move through approval and application. Resource transfers
are validated but remain unapplied until an explicit state-event mapping is added.
This boundary contains no LangGraph or agent reasoning. The development operator
approval query parameter is not production authentication.
