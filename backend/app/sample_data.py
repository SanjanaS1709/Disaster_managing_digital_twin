
from datetime import datetime
from zoneinfo import ZoneInfo

from .models import TwinState


def create_initial_state() -> TwinState:
    return TwinState(
        simulation_id="michaung_demo_001",
        timestamp=datetime(
            2023, 12, 4, 10, 0,
            tzinfo=ZoneInfo("Asia/Kolkata")
        ),
        revision=1,

        roads=[
            {
                "id": "R001",
                "status": "open",
                "risk_score": 0.2,
                "coordinates": [
                    [80.23, 13.05],
                    [80.24, 13.06]
                ]
            },
            {
                "id": "R002",
                "status": "open",
                "risk_score": 0.4,
                "coordinates": [
                    [80.24, 13.06],
                    [80.25, 13.07]
                ]
            }
        ],

        shelters=[
            {
                "id": "S001",
                "name": "Demo Shelter A",
                "location": [80.24, 13.06],
                "capacity": 200,
                "occupancy": 120,
                "demand": {
                    "food": 100,
                    "water": 200
                }
            }
        ],

        relief_units=[
            {
                "id": "U001",
                "location": [80.23, 13.05],
                "inventory": {
                    "food": 250,
                    "water": 400
                },
                "inventory_units": {"food": "packs", "water": "litres"}
            }
        ],

        zones=[
            {
                "id": "Z001",
                "risk_score": 0.7,
                "affected_population": 500
            }
        ]
    )
