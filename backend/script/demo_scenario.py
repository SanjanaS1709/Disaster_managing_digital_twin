"""Run an isolated, repeatable Module 1 scenario against FastAPI in-process."""
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
from app.main import app
from app.state_manager import StateManager


def main():
    with TemporaryDirectory(prefix='module1_demo_') as directory:
        manager = StateManager(str(Path(directory) / 'state.json'))
        with patch('app.main.state_manager', manager), TestClient(app) as client:
            state = client.post('/api/simulation/reset').json()
            simulation_id = state['simulation_id']
            events = [
                ('demo-rain', 'RAINFALL_INCREASE', state['zones'][0]['id'], {'value': 30, 'unit': 'mm/hour'}, '2023-12-04T10:01:00+05:30'),
                ('demo-flood', 'ROAD_FLOODED', state['roads'][0]['id'], {}, '2023-12-04T10:02:00+05:30'),
                ('demo-shelter', 'SHELTER_OVER_CAPACITY', state['shelters'][0]['id'], {'occupancy': 220}, '2023-12-04T10:03:00+05:30'),
                ('demo-shortage', 'RESOURCE_SHORTAGE', state['relief_units'][0]['id'], {'item': 'water', 'quantity': 25, 'unit': 'litres'}, '2023-12-04T10:04:00+05:30'),
            ]
            for event_id, kind, target, payload, timestamp in events:
                response = client.post('/api/simulation/events', json={'event_id': event_id, 'simulation_id': simulation_id, 'type': kind, 'timestamp': timestamp, 'target_id': target, 'payload': payload})
                assert response.status_code == 200, response.text
            current = client.get('/api/twin/state').json()
            print(f"source={current['roads'][0]['source']} revision={current['revision']} road_status={current['roads'][0]['status']}")
            print(f"shelter_over_capacity={current['shelters'][0]['over_capacity']} water={current['relief_units'][0]['inventory']['water']}")
            print('No route proposal fabricated: synthetic fallback has no verified relief-route semantics.')
            restarted = StateManager(str(Path(directory) / 'state.json'))
            assert restarted.get_state().revision == current['revision']
            assert len(restarted.get_events()) == 4
            print('restart recovery: passed')


if __name__ == '__main__':
    main()
