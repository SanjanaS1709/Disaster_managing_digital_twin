# Module 1 backend

Run from PowerShell:

```powershell
cd C:\Users\DELL\full_stack\agentic-disaster-twin\backend
C:\Python313\python.exe -m pip install -r requirements-dev.txt
C:\Python313\python.exe -m unittest discover -s tests -v
C:\Python313\python.exe script/demo_scenario.py
C:\Python313\python.exe -m uvicorn app.main:app --reload
```

The service uses one worker. `SIMULATION_STORAGE_BACKEND=local|firestore` selects
atomic local JSON or versioned Firestore snapshots. See [FIRESTORE.md](FIRESTORE.md)
for credentials, emulator setup, restart recovery, schema and limitations.
Active Chennai roads use `contemporary_osm` provenance and remain on disk.

Import local data with:

```powershell
C:\Python313\python.exe script/import_osm.py --input data/raw/chennai.osm --area t_nagar
C:\Python313\python.exe script/validate_data.py
```

See `ROAD_DATA.md` and `../shared/MODULE1_HANDOFF.md` for API, WebSocket,
decision, provenance and recovery contracts. No frontend or AI agent code is
implemented in this module.
