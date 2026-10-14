# FieldOps · disaster response dashboard

A static frontend prototype for the Cyclone Michaung digital twin, styled as a dark operations analytics dashboard. It includes a Leaflet map, synthetic road network, shelter and relief-unit panels, local demo metrics, and agent connection states. No backend is connected; no live emergency data or real AI-agent output is shown.

## Run on Windows

From this directory in PowerShell:

```powershell
py -m http.server 5500
```

If `py` is unavailable, use `python -m http.server 5500`. Open <http://localhost:5500> and stop the server with `Ctrl+C`. Leaflet and OpenStreetMap tiles require internet access.

The included sample values are: 8 roads (4 open, 2 restricted, 2 blocked), 3 shelters (494 of 740 places occupied), 2 relief units, and 2 risk overlays. All are synthetic demo data.
