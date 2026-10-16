# Road data workflow

Run from `backend` with Python 3.13. The inspected working interpreter was
`C:\Python313\python.exe` (OSMnx 2.1.1). `backend\Scripts\python.exe` is an
incomplete environment without OSMnx; `backend\venv` contains no interpreter.
Do not edit application copies inside environment folders.

```powershell
python -m pip install -r requirements-dev.txt
python script/download_roads.py --refresh
python -m unittest discover -s tests -v
python -m uvicorn app.main:app --reload
```

Downloader defaults: 500 m bounding-box radius around T. Nagar, driving network,
two attempts rotating between Overpass endpoints, 60 s HTTP timeout, 120 s hard
limit per worker, 5 s exponential backoff capped at 60 s. OSMnx itself may retry
429/504 indefinitely; the worker deadline bounds this without private API patches.
A deadline includes imports, server rate-limit waits, and graph processing.
Set repeatable `--endpoint https://HOST/api` or comma-separated
`OVERPASS_ENDPOINTS`; also configurable: `--attempts`, `--timeout`,
`--attempt-timeout`, `--backoff`, `--distance`, and `--output-dir`.

Successful HTTP responses are cached under `data/cache`. An existing valid
GraphML is reused without networking unless `--refresh` is supplied. A failed
refresh retains the validated local network, including its original source label.
`--local-graph PATH` imports a local directed GraphML network. Unlabeled imports
are marked `local_graphml_unverified`, never assumed to be OpenStreetMap.

If Overpass is unavailable:

```powershell
python script/download_roads.py --offline-demo
```

This explicitly writes a **synthetic_demo** fixture (3 nodes, 4 directed edges)
to the normal output paths. It replaces any existing network there. Use
`--output-dir data/demo` to keep it separate. To replace demo outputs with real
roads later, run with `--refresh`. Without files, the API emits a warning and uses
the same labeled synthetic fixture in memory. Invalid existing GraphML fails
visibly rather than silently serving unrelated data.

Both outputs are staged and round-trip validated before individually atomic
replacement. GraphML is authoritative for API loading. An interruption between
the two replacements can leave an older GeoJSON alongside new GraphML; rerunning
the downloader without `--refresh` regenerates the GeoJSON from GraphML. This is
not a two-file filesystem transaction. Do not run concurrent download writers.

Road IDs are `R_{u}_{v}_{key}` in GraphML edge `road_id`, GeoJSON feature `id`, and
API road `id`. They identify directed edges within the saved network, not permanent
IDs across later OSM edits/simplification. Reverse edges have distinct IDs. Nodes
remain connected NetworkX intersections, with length in metres and LineString
geometry in EPSG:4326 longitude/latitude order. `routing_graph(state)` returns a
copy with blocked directed edges removed and risk/status applied. A flood targets
one directed road ID; blocking both directions requires events for both IDs.

GET `/api/twin/geojson` derives road features from current simulation state.
Flood/reopen events update this response immediately; static data files remain
the baseline. Restart or reset after replacing road data. State remains in-memory
and is unsuitable for multiple independent Uvicorn workers.

OSM attribution: OpenStreetMap contributors, https://www.openstreetmap.org/copyright
Live downloads represent current OSM, not a verified December 2023 snapshot.
The small T. Nagar extract does not cover all Chennai/Tiruvallur. No PBF importer
or historical road reconstruction is included.

## Verification in this workspace

Python 3.13.2 / OSMnx 2.1.1 / FastAPI 0.110.0 / Pydantic 2.10.6.
Five unittest cases passed, exercising all five requested API routes,
flood/reopen/reset, duplicate-event rejection, blocked routing edges, directed
connectivity, graph/GeoJSON IDs and geometry, projected imports, invalid data,
failed writes, bounded retries, and local fallback. Compile checks passed.
The installed older FastAPI/httpx combination emits a TestClient deprecation
warning; it did not fail tests. Tests use httpx via requirements-dev.txt.
The original reported 502 was an upstream capacity response with a non-JSON body.
A live attempt here timed out, and the primary server's status URL returned HTTP
406 even outside the sandbox. Thus server/network availability remains external
to this fix; no successful real OSM download is implied by passing offline tests.
The second endpoint (overpass.kumi.systems) returned HTTP 500 with HTML, reproducing the JSON parsing symptom. Both live attempts failed at that time. The user later supplied a BBBike XML extract, which is now the active road network (9,502 retained nodes and 18,987 directed edges; see `data/manifest.json`).

## Offline local GIS import

Use `script/import_osm.py` when Overpass is unavailable. The importer accepts a local `.osm` XML extract with no extra parser dependency and preserves actual OSM way/node topology, drivable highway tags, one-way tags, parallel edges, OSM IDs and EPSG:4326 coordinates. Crossing lines are not connected unless they share an OSM node. `.osm.pbf` input is recognized and gives an explicit `pyosmium` installation message; this environment does not have pyosmium, so no PBF has been claimed as imported.

Compressed OSM XML extracts (`.osm.gz`) can be imported directly:

```powershell
python script/import_osm.py --input data/raw/Chennai_TNagar_DisasterTwin.osm.gz --area t_nagar
```

```powershell
cd C:\Users\DELL\full_stack\agentic-disaster-twin\backend
C:\Python313\python.exe script/import_osm.py --input data/raw/chennai.osm --area t_nagar
C:\Python313\python.exe script/validate_data.py
C:\Python313\python.exe -m uvicorn app.main:app --reload
```

Download a small Chennai extract manually from [BBBike](https://extract.bbbike.org/) using a T. Nagar bounding box, then save it under `data/raw`. A larger current Southern Zone PBF is available from [Geofabrik](https://download.geofabrik.de/asia/india/southern-zone.html), but it is hundreds of MB and represents contemporary OSM, not December 2023. For PBF support install `C:\Python313\python.exe -m pip install pyosmium`, or convert/select an XML extract with an external Osmium workflow before importing.

The checked-in `data/raw/osm_fixture.osm` is a tiny structural test fixture only: it is explicitly not Chennai data. The active T. Nagar graph was imported from `data/raw/Chennai_TNagar_DisasterTwin.osm.gz`; its manifest records OSM XML provenance, bounds and counts. The importer retains only nodes referenced by retained drivable road segments, preserving road topology while dropping unrelated OSM points. Run `python -m unittest discover -s tests -v` to test one-way and bidirectional behavior, CRS, stable IDs, staged exports, and invalid-input handling. Facilities can be imported with `app.geodata.import_facilities` from CSV or GeoJSON; use `data/raw/facilities_template.csv`. The importer validates required fields, duplicate IDs, WGS84 bounds, study-area bounds, ISO observation dates and nonnegative capacities. No verified facility, rainfall, flood, DEM or boundary dataset is currently present. `data/manifest.json` records these classifications.

## Simulation core integration

The completed manual simulation contract and example requests are in
`../shared/api_contract.md`, `../shared/sample_twin_state.json`,
`../shared/sample_event.json`, and `../shared/sample_events.json`.
Reset now archives the previous run in memory before starting a fresh baseline;
the existing GET events endpoint still clears for the current run. Historical
snapshots/replay use each run's saved baseline, without reloading road files.

Verification after simulation-core changes: `python -m unittest discover -s tests
-v` ran 29 tests, all passed (including the five existing road tests).
`python script/verify_simulation.py` passed the complete HTTP timeline, and
`python -m compileall -q app script tests` completed successfully.
The installed test dependencies emitted TestClient deprecation and AnyIO resource
warnings; no test failures occurred. The road downloader and processed network
were not changed during this simulation-core task.
