"""HTTP contracts and transaction/replay regression tests for the simulation core."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from pydantic import ValidationError
from app.main import app
from app.models import DisasterEvent, Road, Shelter, TwinState
from app import road_loader
from app.sample_data import create_initial_state
from app.state_manager import StateManager


class SimulationTests(unittest.TestCase):
    def setUp(self):
        # Simulation contracts deliberately use the small fixture, so they do
        # not read or mutate an operator's installed road network.
        fixture_roads = [dict(feature['properties'], coordinates=feature['geometry']['coordinates'])
                         for feature in road_loader.feature_collection(road_loader.synthetic_graph())['features']]
        self.manager = StateManager(roads_loader=lambda: fixture_roads)
        self.override = patch('app.main.state_manager', self.manager)
        self.override.start()
        self.addCleanup(self.override.stop)
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        self.initial = self.state()
        self.road = self.initial['roads'][0]['id']

    def state(self):
        return self.client.get('/api/twin/state').json()

    def event(self, kind='ROAD_FLOODED', target=None, payload=None, **overrides):
        targets = {'ROAD_FLOODED': self.road, 'ROAD_REOPENED': self.road,
                   'SHELTER_OVER_CAPACITY': 'S001', 'RESOURCE_SHORTAGE': 'U001', 'RAINFALL_INCREASE': 'Z001'}
        result = dict(event_id='event-1', simulation_id=self.initial['simulation_id'], type=kind,
                      target_id=target or targets[kind], timestamp='2023-12-04T10:01:00+05:30', payload=payload or {})
        result.update(overrides)
        return result

    def post_event(self, event):
        return self.client.post('/api/simulation/events', json=event)

    def test_flood_geojson_revision(self):
        response = self.post_event(self.event(payload={'risk_score': .9}))
        self.assertEqual(response.status_code, 200, response.text)
        state = response.json()
        self.assertEqual(state['revision'], 2)
        self.assertEqual(state['roads'][0]['status'], 'blocked')
        self.assertEqual(state['roads'][0]['risk_score'], .9)
        feature = self.client.get('/api/twin/geojson').json()['features'][0]
        self.assertEqual(feature['properties']['status'], 'blocked')
        self.assertEqual(feature['id'], self.road)
        self.assertEqual(feature['geometry']['coordinates'], state['roads'][0]['coordinates'])

    def test_reopen_default_and_restricted(self):
        self.post_event(self.event())
        response = self.post_event(self.event('ROAD_REOPENED', event_id='reopen'))
        self.assertEqual(response.json()['roads'][0]['status'], 'open')
        self.assertEqual(response.json()['roads'][0]['risk_score'], .2)
        response = self.post_event(self.event('ROAD_REOPENED', payload={'status': 'restricted', 'risk_score': .4}, event_id='restricted'))
        self.assertEqual(response.json()['roads'][0]['status'], 'restricted')
        self.assertEqual(response.json()['revision'], 4)

    def test_shelter_overcapacity_and_recovery(self):
        response = self.post_event(self.event('SHELTER_OVER_CAPACITY', payload={'occupancy': 230}))
        shelter = response.json()['shelters'][0]
        self.assertEqual(shelter['occupancy'], 230)
        self.assertTrue(shelter['over_capacity'])
        self.assertEqual(shelter['excess_occupancy'], 30)
        response = self.post_event(self.event('SHELTER_OVER_CAPACITY', payload={'occupancy': 100}, event_id='recover'))
        self.assertFalse(response.json()['shelters'][0]['over_capacity'])

    def test_inventory_shortage_and_duplicate(self):
        event = self.event('RESOURCE_SHORTAGE', payload={'item': 'water', 'quantity': 50, 'unit': 'litres'})
        self.assertEqual(self.post_event(event).json()['relief_units'][0]['inventory']['water'], 350)
        self.assertEqual(self.post_event(event).status_code, 400)
        self.assertEqual(self.state()['relief_units'][0]['inventory']['water'], 350)
        self.assertEqual(len(self.client.get('/api/simulation/events').json()), 1)

    def test_inventory_wrong_unit_item_and_insufficient_stock(self):
        for payload in ({'item': 'water', 'quantity': 500, 'unit': 'litres'},
                        {'item': 'water', 'quantity': 1, 'unit': 'kg'},
                        {'item': 'medicine', 'quantity': 1, 'unit': 'packs'}):
            with self.subTest(payload=payload):
                self.assertEqual(self.post_event(self.event('RESOURCE_SHORTAGE', payload=payload)).status_code, 400)
                self.assertEqual(self.state(), self.initial)

    def test_rainfall_measurements_provenance_and_units(self):
        event = self.event('RAINFALL_INCREASE', payload={'value': 25, 'unit': 'mm/hour'}, observed_at='2023-12-04T10:00:30+05:30')
        response = self.post_event(event)
        self.assertEqual(response.status_code, 200, response.text)
        measurement = response.json()['zones'][0]['rainfall'][0]
        self.assertEqual(measurement, dict(value=25.0, unit='mm/hour', timestamp=event['observed_at'], source='simulation'))
        event.update(event_id='lower', payload={'value': 20, 'unit': 'mm/hour'})
        self.assertEqual(self.post_event(event).status_code, 400)
        event.update(event_id='accumulation', payload={'value': 5, 'unit': 'mm'})
        self.assertEqual(self.post_event(event).status_code, 200)
        event.update(event_id='false-provenance', source='IMD')
        self.assertEqual(self.post_event(event).status_code, 422)

    def test_unknown_entity_ids(self):
        for kind, payload in [('ROAD_FLOODED', {}), ('ROAD_REOPENED', {}),
                              ('SHELTER_OVER_CAPACITY', {'occupancy': 220}),
                              ('RESOURCE_SHORTAGE', {'item': 'food', 'quantity': 1, 'unit': 'packs'}),
                              ('RAINFALL_INCREASE', {'value': 30, 'unit': 'mm'})]:
            with self.subTest(kind=kind):
                response = self.post_event(self.event(kind, target='MISSING', payload=payload))
                self.assertEqual(response.status_code, 400)
                self.assertIn('Unknown target', response.json()['detail'])
                self.assertEqual(self.state(), self.initial)

    def test_invalid_payloads_are_atomic(self):
        cases = [('SHELTER_OVER_CAPACITY', {'occupancy': -1}),
                 ('SHELTER_OVER_CAPACITY', {'occupancy': 1.5}),
                 ('RESOURCE_SHORTAGE', {'item': 'water', 'quantity': -1, 'unit': 'litres'}),
                 ('RESOURCE_SHORTAGE', {'item': 'water', 'quantity': True, 'unit': 'litres'}),
                 ('RESOURCE_SHORTAGE', {'item': '', 'quantity': 1, 'unit': 'litres'}),
                 ('RESOURCE_SHORTAGE', {'item': 'water', 'quantity': 1}),
                 ('RAINFALL_INCREASE', {'value': -1, 'unit': 'mm'}),
                 ('RAINFALL_INCREASE', {'value': 1, 'unit': 'inches'}),
                 ('ROAD_FLOODED', {'risk_score': 1.1}),
                 ('ROAD_REOPENED', {'status': 'blocked'}),
                 ('ROAD_FLOODED', {'unexpected': 1})]
        for kind, payload in cases:
            with self.subTest(kind=kind, payload=payload):
                response = self.post_event(self.event(kind, payload=payload))
                self.assertEqual(response.status_code, 422, response.text)
                self.assertEqual(self.state(), self.initial)
        self.assertEqual(self.client.get('/api/simulation/events').json(), [])

    def test_timestamps_and_simulation_id(self):
        for override, status in [({'timestamp': '2023-12-04T09:00:00+05:30'}, 400),
                                 ({'timestamp': '2023-12-04T11:00:00'}, 422),
                                 ({'observed_at': '2023-12-05T10:00:00Z'}, 422),
                                 ({'simulation_id': 'other'}, 400)]:
            self.assertEqual(self.post_event(self.event(**override)).status_code, status)
            self.assertEqual(self.state(), self.initial)
        self.assertEqual(self.post_event(self.event()).status_code, 200)
        self.assertEqual(self.post_event(self.event(event_id='older', timestamp=self.initial['timestamp'])).status_code, 400)

    def test_start_pause_resume_and_manual_clock(self):
        initial_time = self.initial['timestamp']
        self.assertEqual(self.client.post('/api/simulation/pause').status_code, 400)
        self.assertEqual(self.client.post('/api/simulation/advance', json={'seconds': 1}).status_code, 400)
        self.assertEqual(self.client.post('/api/simulation/start').json()['phase'], 'running')
        self.assertEqual(self.client.post('/api/simulation/start').status_code, 400)
        self.assertEqual(self.client.post('/api/simulation/pause').json()['phase'], 'paused')
        self.assertEqual(self.client.post('/api/simulation/pause').status_code, 400)
        self.assertEqual(self.client.get('/api/simulation/status').json()['timestamp'], initial_time)
        self.assertEqual(self.client.post('/api/simulation/resume').json()['phase'], 'running')
        self.assertEqual(self.client.post('/api/simulation/resume').status_code, 400)
        self.assertEqual(self.state()['revision'], 1)

    def test_advance_and_step_while_paused(self):
        self.client.post('/api/simulation/start')
        self.client.post('/api/simulation/pause')
        response = self.client.post('/api/simulation/advance', json={'seconds': 60})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['timestamp'], '2023-12-04T10:01:00+05:30')
        self.assertEqual(response.json()['revision'], 2)
        for seconds in (0, -1, 31536001):
            self.assertEqual(self.client.post('/api/simulation/advance', json={'seconds': seconds}).status_code, 422)
        self.assertEqual(self.state()['revision'], 2)

    def test_chronological_schedule_and_equal_timestamp_order(self):
        later = self.event('ROAD_REOPENED', event_id='later', timestamp='2023-12-04T10:02:00+05:30')
        earlier = self.event(event_id='earlier')
        tied = self.event('ROAD_REOPENED', event_id='tied', payload={'status': 'restricted'})
        for event in (later, earlier, tied):
            self.assertEqual(self.client.post('/api/simulation/schedule', json=event).status_code, 200)
        self.assertEqual(self.state(), self.initial)
        self.assertEqual(self.client.get('/api/simulation/events').json(), [])
        self.client.post('/api/simulation/start')
        status = self.client.post('/api/simulation/advance', json={'seconds': 60}).json()
        self.assertEqual(status['accepted_events'], 2)
        self.assertEqual(status['scheduled_events'], 1)
        self.assertEqual(self.state()['roads'][0]['status'], 'restricted')
        self.assertEqual(self.state()['revision'], 3)
        self.assertEqual([e['event_id'] for e in self.client.get('/api/simulation/events').json()], ['earlier', 'tied'])
        self.client.post('/api/simulation/advance', json={'seconds': 90})
        self.assertEqual(self.state()['roads'][0]['status'], 'open')
        self.assertEqual(self.state()['revision'], 5)  # last event + clock-only move
        self.assertEqual(self.client.post('/api/simulation/replay', json={}).json()['state'], self.state())

    def test_schedule_validation_reserved_ids_and_direct_injection(self):
        event = self.event()
        self.assertEqual(self.client.post('/api/simulation/schedule', json=event).status_code, 200)
        self.assertEqual(self.client.post('/api/simulation/schedule', json=event).status_code, 400)
        self.assertEqual(self.post_event(event).status_code, 400)
        self.assertEqual(self.post_event(self.event(event_id='skip-queue')).status_code, 400)
        for bad in (self.event(event_id='past', timestamp=self.initial['timestamp']),
                    self.event(event_id='unknown', target='unknown')):
            self.assertEqual(self.client.post('/api/simulation/schedule', json=bad).status_code, 400)
        self.assertEqual(self.client.get('/api/simulation/status').json()['scheduled_events'], 1)

    def test_advance_batch_atomicity_when_inventory_changes(self):
        flood = self.event(event_id='batch-flood', timestamp='2023-12-04T10:00:30+05:30')
        self.assertEqual(self.client.post('/api/simulation/schedule', json=flood).status_code, 200)
        event = self.event('RESOURCE_SHORTAGE', payload={'item': 'water', 'quantity': 300, 'unit': 'litres'})
        self.assertEqual(self.client.post('/api/simulation/schedule', json=event).status_code, 200)
        self.post_event(self.event('RESOURCE_SHORTAGE', payload={'item': 'water', 'quantity': 200, 'unit': 'litres'}, event_id='consume', timestamp=self.initial['timestamp']))
        self.client.post('/api/simulation/start')
        before_state, before_history = self.state(), self.client.get('/api/simulation/history').json()
        response = self.client.post('/api/simulation/advance', json={'seconds': 120})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.state(), before_state)
        self.assertEqual(self.client.get('/api/simulation/history').json(), before_history)

    def test_projected_queue_shortage_rejected(self):
        payload = {'item': 'water', 'quantity': 300, 'unit': 'litres'}
        first = self.event('RESOURCE_SHORTAGE', payload=payload)
        self.assertEqual(self.client.post('/api/simulation/schedule', json=first).status_code, 200)
        second = self.event('RESOURCE_SHORTAGE', payload=payload, event_id='second', timestamp='2023-12-04T10:02:00+05:30')
        self.assertEqual(self.client.post('/api/simulation/schedule', json=second).status_code, 400)
        self.assertEqual(self.client.get('/api/simulation/status').json()['scheduled_events'], 1)

    def test_replay_determinism_all_event_types_and_clock(self):
        events = [self.event(), self.event('ROAD_REOPENED', event_id='reopen'),
                  self.event('SHELTER_OVER_CAPACITY', payload={'occupancy': 220}, event_id='shelter'),
                  self.event('RESOURCE_SHORTAGE', payload={'item': 'food', 'quantity': 10, 'unit': 'packs'}, event_id='stock'),
                  self.event('RAINFALL_INCREASE', payload={'value': 50, 'unit': 'mm'}, event_id='rain')]
        for event in events:
            self.assertEqual(self.post_event(event).status_code, 200)
        self.client.post('/api/simulation/start')
        self.client.post('/api/simulation/advance', json={'seconds': 60})
        before = self.state()
        history = self.client.get('/api/simulation/history').json()
        for _ in range(2):
            response = self.client.post('/api/simulation/replay', json={})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()['state'], before)
        self.assertEqual(self.state(), before)
        self.assertEqual(self.client.get('/api/simulation/history').json(), history)
        self.assertEqual(self.client.get('/api/simulation/snapshot', params={'revision': 1}).json()['state'], self.initial)
        self.assertEqual(self.client.get('/api/simulation/snapshot', params={'revision': 2}).json()['state']['roads'][0]['status'], 'blocked')
        self.assertEqual(self.client.get('/api/simulation/snapshot', params={'revision': 999}).status_code, 400)

    def test_reset_archives_history_and_replay(self):
        run_id = self.client.get('/api/simulation/status').json()['run_id']
        self.post_event(self.event())
        previous = self.state()
        self.client.post('/api/simulation/schedule', json=self.event('ROAD_REOPENED', event_id='pending', timestamp='2023-12-04T10:02:00+05:30'))
        self.assertEqual(self.client.post('/api/simulation/reset').json(), self.initial)
        self.assertEqual(self.client.get('/api/simulation/events').json(), [])
        self.assertEqual(self.client.get('/api/simulation/status').json()['phase'], 'ready')
        self.assertEqual(self.client.post('/api/simulation/replay', json={'run_id': run_id}).json()['state'], previous)
        archived = self.client.get('/api/simulation/history', params={'run_id': run_id}).json()
        self.assertEqual(len(archived['accepted']), 1)
        self.assertEqual(len(archived['scheduled']), 1)
        self.assertIn(run_id, self.client.get('/api/simulation/runs').json())
        self.assertEqual(self.client.get('/api/simulation/history', params={'run_id': 'missing'}).status_code, 400)
        self.assertEqual(self.post_event(self.event()).status_code, 200)  # IDs scoped to run

    def test_history_ingestion_and_defensive_copies(self):
        self.post_event(self.event())
        record = self.client.get('/api/simulation/history').json()['accepted'][0]
        self.assertEqual(record['status'], 'accepted')
        self.assertEqual(record['revision'], 2)
        self.assertIsNotNone(datetime.fromisoformat(record['ingested_at']).tzinfo)
        self.assertNotEqual(record['ingested_at'], record['event']['timestamp'])
        state = self.manager.get_state()
        state.roads[0].status = 'open'
        history = self.manager.history()
        history.accepted[0].event.payload['risk_score'] = 0
        self.assertEqual(self.state()['roads'][0]['status'], 'blocked')
        self.assertEqual(self.manager.snapshot().state.roads[0].risk_score, 1)

    def test_thread_safe_duplicate_http_requests(self):
        event = self.event('RESOURCE_SHORTAGE', payload={'item': 'water', 'quantity': 100, 'unit': 'litres'})
        def send(_):
            with TestClient(app) as client:
                return client.post('/api/simulation/events', json=event).status_code
        with ThreadPoolExecutor(max_workers=4) as pool:
            codes = list(pool.map(send, range(8)))
        self.assertEqual(codes.count(200), 1)
        self.assertEqual(codes.count(400), 7)
        self.assertEqual(self.state()['relief_units'][0]['inventory']['water'], 300)
        self.assertEqual(self.state()['revision'], 2)

    def test_rejected_id_can_be_corrected(self):
        event = self.event('SHELTER_OVER_CAPACITY', payload={'occupancy': -1})
        self.assertEqual(self.post_event(event).status_code, 422)
        event['payload']['occupancy'] = 210
        self.assertEqual(self.post_event(event).status_code, 200)
        self.assertEqual(self.state()['revision'], 2)

    def test_rainfall_older_observation_is_rejected(self):
        event = self.event('RAINFALL_INCREASE', payload={'value': 20, 'unit': 'mm'})
        self.assertEqual(self.post_event(event).status_code, 200)
        event.update(event_id='older-observation', timestamp='2023-12-04T10:02:00+05:30', observed_at=self.initial['timestamp'])
        event['payload']['value'] = 30
        before = self.state()
        self.assertEqual(self.post_event(event).status_code, 400)
        self.assertEqual(self.state(), before)

    def test_reset_load_failure_and_replay_baseline_isolation(self):
        self.post_event(self.event())
        before = self.state()
        with patch.object(self.manager, '_roads_loader', side_effect=OSError('unreadable graph')):
            with self.assertRaisesRegex(OSError, 'unreadable graph'):
                self.client.post('/api/simulation/reset')
            self.assertEqual(self.client.post('/api/simulation/replay', json={}).json()['state'], before)
        self.assertEqual(self.state(), before)

    def test_persistence_rejects_roads_from_a_replaced_network(self):
        fixture_roads = [dict(feature['properties'], coordinates=feature['geometry']['coordinates'])
                         for feature in road_loader.feature_collection(road_loader.synthetic_graph())['features']]
        replacement_roads = [dict(road) for road in fixture_roads]
        replacement_roads[0]['id'] = 'R_replaced_0'
        with tempfile.TemporaryDirectory() as directory:
            storage = str(Path(directory) / 'simulation.json')
            original = StateManager(storage, roads_loader=lambda: fixture_roads)
            original.apply_event(DisasterEvent.model_validate(self.event(target=fixture_roads[0]['id'])))
            restored = StateManager(storage, roads_loader=lambda: replacement_roads)
            self.assertEqual([road.id for road in restored.get_state().roads],
                             [road['id'] for road in replacement_roads])
            self.assertEqual(restored.get_events(), [])

    def test_shared_samples_and_openapi(self):
        shared = Path(__file__).resolve().parents[2] / 'shared'
        TwinState.model_validate_json((shared / 'sample_twin_state.json').read_text(encoding='utf-8'))
        DisasterEvent.model_validate_json((shared / 'sample_event.json').read_text(encoding='utf-8'))
        # Shared event IDs intentionally target the synthetic demo graph.  Keep
        # their contract test independent of whichever GIS dataset is installed.
        fixture_roads = [dict(feature['properties'], coordinates=feature['geometry']['coordinates'])
                         for feature in road_loader.feature_collection(road_loader.synthetic_graph())['features']]
        fixture_manager = StateManager(roads_loader=lambda: fixture_roads)
        with patch('app.main.state_manager', fixture_manager), TestClient(app) as client:
            for event in json.loads((shared / 'sample_events.json').read_text(encoding='utf-8')):
                self.assertEqual(client.post('/api/simulation/events', json=event).status_code, 200)
            response = client.get('/openapi.json')
            self.assertEqual(response.status_code, 200)
            self.assertIn('/api/simulation/replay', response.json()['paths'])

    def test_schema_coordinates_quantities_timezone_and_unique_ids(self):
        for coordinate in ([181, 13], [80, 91], [80], [float('nan'), 13]):
            with self.assertRaises(ValidationError):
                Road(id='R', coordinates=[coordinate, [80, 13]])
        raw = create_initial_state().model_dump()
        raw['roads'][1]['id'] = raw['roads'][0]['id']
        with self.assertRaises(ValidationError):
            TwinState.model_validate(raw)
        raw = create_initial_state().model_dump()
        raw['timestamp'] = datetime(2023, 12, 4)
        with self.assertRaises(ValidationError):
            TwinState.model_validate(raw)
        for field in ('capacity', 'occupancy'):
            shelter = self.initial['shelters'][0].copy()
            shelter[field] = -1
            with self.assertRaises(ValidationError):
                Shelter.model_validate(shelter)
        raw = create_initial_state().model_dump()
        raw['shelters'][0]['demand']['food'] = -1
        with self.assertRaises(ValidationError):
            TwinState.model_validate(raw)
        raw = create_initial_state().model_dump()
        raw['relief_units'][0]['inventory']['food'] = -1
        with self.assertRaises(ValidationError):
            TwinState.model_validate(raw)


if __name__ == '__main__':
    unittest.main()
