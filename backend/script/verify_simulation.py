"""Run a complete isolated manual timeline through FastAPI's HTTP test client."""
import json
from pathlib import Path
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
from app import road_loader
from app.main import app
from app.models import DisasterEvent, TwinState
from app.state_manager import StateManager


def main() -> None:
    shared = Path(__file__).resolve().parents[2] / 'shared'
    TwinState.model_validate_json((shared / 'sample_twin_state.json').read_text(encoding='utf-8'))
    DisasterEvent.model_validate_json((shared / 'sample_event.json').read_text(encoding='utf-8'))
    examples = json.loads((shared / 'sample_events.json').read_text(encoding='utf-8'))
    for example in examples:
        DisasterEvent.model_validate(example)
    fixture_roads = [dict(feature['properties'], coordinates=feature['geometry']['coordinates'])
                     for feature in road_loader.feature_collection(road_loader.synthetic_graph())['features']]
    manager = StateManager(roads_loader=lambda: fixture_roads)
    with patch('app.main.state_manager', manager), TestClient(app) as client:
        baseline = client.post('/api/simulation/reset').json()
        run_id = client.get('/api/simulation/status').json()['run_id']
        for event in examples:
            response = client.post('/api/simulation/schedule', json=event)
            assert response.status_code == 200, response.text
        assert client.get('/api/simulation/events').json() == []
        assert client.post('/api/simulation/start').json()['phase'] == 'running'
        assert client.post('/api/simulation/pause').json()['phase'] == 'paused'
        assert client.post('/api/simulation/advance', json={'seconds': 60}).status_code == 200
        assert client.get('/api/twin/geojson').json()['features'][0]['properties']['status'] == 'blocked'
        assert client.post('/api/simulation/resume').status_code == 200
        assert client.post('/api/simulation/advance', json={'seconds': 240}).status_code == 200
        current = client.get('/api/twin/state').json()
        assert current['revision'] == 6
        assert current['roads'][0]['status'] == 'open'
        assert current['shelters'][0]['over_capacity'] is True
        assert current['relief_units'][0]['inventory']['water'] == 350
        assert current['zones'][0]['rainfall'][0]['source'] == 'simulation'
        assert client.post('/api/simulation/replay', json={}).json()['state'] == current
        assert client.get('/api/simulation/snapshot', params={'revision': 1}).json()['state'] == baseline
        assert client.post('/api/simulation/reset').json() == baseline
        assert client.get('/api/simulation/events').json() == []
        assert client.post('/api/simulation/replay', json={'run_id': run_id}).json()['state'] == current
        print('PASS: shared schemas, five scheduled events, lifecycle, stepping, GeoJSON, replay, snapshots, reset and archived replay')
        print(f"Road provenance: {current['roads'][0]['source']}; final pre-reset revision: {current['revision']}")


if __name__ == '__main__':
    main()
