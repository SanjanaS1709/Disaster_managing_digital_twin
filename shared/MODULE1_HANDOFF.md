# Module 1 handoff

The backend exposes versioned TwinState, directed GraphML/GeoJSON roads,
simulation events, decision proposals, local persistence, and WebSocket updates.
The active road data is explicitly `synthetic_demo` with 3 nodes and 4 directed
edges. See `backend/data/manifest.json`.

Member 2 should read `GET /api/simulation/snapshot?revision=N` and use its
revision as the optimistic concurrency token. GraphML edge `road_id`, GeoJSON
feature IDs, and state road IDs match. Blocked edges are unavailable; restricted
edges remain present for policy evaluation. Submit route proposals with
`POST /api/decisions?expected_revision=N`; the backend rejects unknown, blocked,
disconnected, duplicate, or stale actions. Approve or reject using
`/api/decisions/{id}/approve` or `/reject`. Resource transfers are validated but
remain proposal-only until an explicit state event defines delivery semantics.

Member 3 should start the service with
`C:\Python313\python.exe -m uvicorn app.main:app --reload`, fetch
`/api/twin/state` and `/api/twin/geojson`, and send events to
`/api/simulation/events`. GeoJSON coordinates are `[longitude, latitude]`.
Connect to `ws://127.0.0.1:8000/ws/updates`: the first message is `SYNC`; accepted
changes produce `STATE_CHANGED` with event ID, event type, revision, timestamp,
affected IDs, and a `refetch` URL. Delivery is best-effort; refetch state after
reconnect or revision gaps.

The application persists atomically to `SIMULATION_DB_PATH`, defaulting to
`backend/data/simulation_state.json`. Use one Uvicorn worker. Corrupt data fails
loudly and is never replaced with synthetic state. Firebase is optional and not
live-verified. For contemporary OSM, obtain a bounded extract from
[BBBike](https://extract.bbbike.org/) and run
`script/import_osm.py --input data/raw/chennai.osm --area t_nagar`, then
`script/validate_data.py`. The checked-in fixture is not Chennai data or a
historical Michaung reconstruction.
