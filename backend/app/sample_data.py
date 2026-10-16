"""Deterministic illustrative facilities and weather for the local demo."""
from datetime import datetime
from zoneinfo import ZoneInfo

from .models import TwinState


_SHELTER_POINTS = [
    (80.2258, 13.0352), (80.2306, 13.0403), (80.2353, 13.0442),
    (80.2401, 13.0386), (80.2454, 13.0337), (80.2491, 13.0410),
    (80.2208, 13.0441), (80.2322, 13.0279), (80.2437, 13.0470),
    (80.2511, 13.0298), (80.2163, 13.0324), (80.2378, 13.0208),
]
_UNIT_POINTS = [
    (80.2271, 13.0373), (80.2330, 13.0418), (80.2394, 13.0449),
    (80.2463, 13.0372), (80.2504, 13.0434), (80.2224, 13.0409),
    (80.2292, 13.0298), (80.2357, 13.0332), (80.2421, 13.0287),
    (80.2488, 13.0322), (80.2182, 13.0461), (80.2395, 13.0226),
]
_CAPACITIES = [240, 180, 320, 200, 280, 160, 220, 300, 190, 260, 150, 210]
_OCCUPANCY_PCT = [65, 82, 54, 91, 73, 48, 77, 61, 88, 57, 69, 42]
_RAINFALL = [18, 24, 31, 42, 56, 68, 74, 49, 36]


def create_initial_state() -> TwinState:
    timestamp = datetime(2023, 12, 4, 10, 0, tzinfo=ZoneInfo("Asia/Kolkata"))
    shelters = []
    for i, (lon, lat) in enumerate(_SHELTER_POINTS, start=1):
        capacity = _CAPACITIES[i - 1]
        occupancy = round(capacity * _OCCUPANCY_PCT[i - 1] / 100)
        shelters.append({
            "id": f"S{i:03d}", "name": f"Demo Shelter {i:02d}",
            "location": [lon, lat], "capacity": capacity, "occupancy": occupancy,
            "demand": {"food": round(capacity * 1.2), "water": round(capacity * 3.0),
                       "medical": round(capacity * .25), "blankets": round(capacity * .5)},
            "source": "synthetic_demo",
        })

    relief_units = []
    for i, (lon, lat) in enumerate(_UNIT_POINTS, start=1):
        relief_units.append({
            "id": f"U{i:03d}", "location": [lon, lat], "source": "synthetic_demo",
            "inventory": {
                "food": [180, 120, 240, 90, 160, 210, 75, 130, 190, 110, 80, 150][i - 1],
                "water": [240, 360, 180, 120, 300, 210, 150, 270, 90, 330, 180, 240][i - 1],
                "medical": [24, 18, 36, 12, 30, 20, 10, 28, 16, 22, 8, 26][i - 1],
                "blankets": [40, 25, 60, 20, 45, 35, 18, 50, 30, 42, 15, 38][i - 1],
            },
            "inventory_units": {"food": "packs", "water": "litres", "medical": "kits", "blankets": "pieces"},
        })

    zones = []
    for i, (lon, lat) in enumerate(_SHELTER_POINTS[::2], start=1):
        rainfall = _RAINFALL[i - 1]
        zones.append({
            "id": f"Z{i:03d}", "location": [lon, lat],
            "risk_score": min(.95, .25 + rainfall / 100),
            "affected_population": 250 + i * 115,
            "rainfall": [{"value": rainfall, "unit": "mm/hour", "timestamp": timestamp,
                          "source": "simulation"}],
            "source": "synthetic_demo",
        })

    return TwinState(
        simulation_id="michaung_demo_001", timestamp=timestamp, revision=1,
        roads=[], shelters=shelters, relief_units=relief_units, zones=zones,
    )
