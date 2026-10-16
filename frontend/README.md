# FieldOps · disaster response dashboard

A static frontend dashboard for the disaster digital twin. It reads the live development twin from the shared FastAPI backend, draws its road GeoJSON and asset locations on Leaflet, and exposes simulation controls, event history, and five-agent proposal evaluation. The backend's current baseline is synthetic demo data and is not an operational emergency feed.

## Run on Windows

Start the backend first from the repository root:

```powershell
..\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --app-dir backend
```

Then, from this directory in PowerShell:

```powershell
py -m http.server 5500
```

If `py` is unavailable, use `python -m http.server 5500`. Open <http://localhost:5500> and stop the server with `Ctrl+C`. Leaflet and OpenStreetMap tiles require internet access.

Open <http://127.0.0.1:5500>. By default the UI connects to the API on port 8000 at the same hostname used for the frontend. Set `window.FIELDOPS_API_URL` before `js/app.js` to override it. Leaflet and OpenStreetMap tiles require internet access. Event evaluation is proposal-only; applying an event is a separate simulation request.
