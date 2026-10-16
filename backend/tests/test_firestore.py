"""Offline Firestore contract tests: immutable snapshots and conditional writes."""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import AsyncMock, patch
import tempfile

from app.storage import FirestoreStore, LocalStore, configured_store
from app.state_manager import StateManager
from app.decisions import DecisionManager
from app.models import DisasterEvent, ProposedAction
from app.road_loader import synthetic_graph, feature_collection


class Reference:
    def __init__(self, client, path):
        self.client, self.path = client, path

    def collection(self, name):
        return Reference(self.client, self.path + '/' + name)

    def document(self, name):
        return Reference(self.client, self.path + '/' + name)

    def get(self):
        if self.client.fail_read:
            raise OSError('read failed')
        value = self.client.data.get(self.path)
        return SimpleNamespace(exists=value is not None, update_time=value[1] if value else None,
                               to_dict=lambda: deepcopy(value[0]) if value else None)


class Batch:
    def __init__(self, client):
        self.client, self.writes = client, []

    def create(self, reference, data):
        self.writes.append(('create', reference.path, deepcopy(data), None))

    def update(self, reference, data, option):
        self.writes.append(('update', reference.path, deepcopy(data), option._last_update_time))

    def commit(self):
        client = self.client
        client.calls += 1
        if client.fail_at == client.calls:
            raise OSError('commit failed')
        for kind, path, data, expected in self.writes:
            current = client.data.get(path)
            if kind == 'create' and current is not None:
                raise RuntimeError('already exists')
            if kind == 'update' and (current is None or current[1] != expected):
                raise RuntimeError('stale writer')
        client.version += 1
        for _, path, data, _ in self.writes:
            client.data[path] = (data, client.version)
        return [SimpleNamespace(update_time=client.version) for _ in self.writes]


class FakeFirestore:
    def __init__(self):
        self.data, self.version, self.calls = {}, 0, 0
        self.fail_at, self.fail_read = None, False

    def collection(self, name):
        return Reference(self, name)

    def batch(self):
        return Batch(self)


def fixture_roads():
    return [dict(f['properties'], coordinates=f['geometry']['coordinates'])
            for f in feature_collection(synthetic_graph())['features']]


class FirestoreTests(TestCase):
    def setUp(self):
        self.client = FakeFirestore()

    def store(self, namespace='simulation'):
        return FirestoreStore(client=self.client, namespace=namespace)

    def manager(self):
        return StateManager(store=self.store(), roads_loader=fixture_roads)

    def event(self, manager, event_id='flood', kind='ROAD_FLOODED'):
        state = manager.get_state()
        return DisasterEvent(event_id=event_id, simulation_id=state.simulation_id,
                             target_id=state.roads[0].id, type=kind,
                             timestamp='2023-12-04T10:01:00+05:30')

    def test_chunked_roundtrip_over_document_limit(self):
        store = self.store()
        self.assertIsNone(store.load())
        document = {'events': ['தமிழ்' * 10000] * 12}
        store.save(document)
        self.assertEqual(self.store().load(), document)
        self.assertTrue(all(len(data['payload']) <= store.CHUNK_BYTES
                            for data, _ in self.client.data.values() if 'payload' in data))

    def test_partial_upload_and_publish_failure_keep_previous_snapshot(self):
        for offset in (1, 2):
            with self.subTest(offset=offset):
                store = self.store()
                store.load()
                store.save({'value': 'old'})
                self.client.fail_at = self.client.calls + offset
                with self.assertRaises(OSError):
                    store.save({'value': 'new'})
                self.assertEqual(self.store().load(), {'value': 'old'})

    def test_stale_writer_cannot_overwrite_new_state(self):
        first, second = self.store(), self.store()
        first.load()
        second.load()
        first.save({'revision': 1})
        with self.assertRaisesRegex(RuntimeError, 'already exists'):
            second.save({'revision': 2})
        second.load()
        first.save({'revision': 3})
        with self.assertRaisesRegex(RuntimeError, 'stale writer'):
            second.save({'revision': 4})
        self.assertEqual(self.store().load(), {'revision': 3})

    def test_read_failure_and_corrupt_chunks_fail_closed(self):
        store = self.store()
        self.client.fail_read = True
        with self.assertRaises(OSError):
            store.load()
        self.client.fail_read = False
        store.load()
        store.save({'value': 1})
        path = next(path for path in self.client.data if '/chunks/' in path)
        self.client.data[path][0]['payload'] = b'corrupt'
        with self.assertRaisesRegex(RuntimeError, 'checksum'):
            self.store().load()
        del self.client.data[path]
        with self.assertRaisesRegex(RuntimeError, 'incomplete'):
            self.store().load()

    def test_restart_recovers_events_schedule_archives_and_replay(self):
        manager = self.manager()
        event = self.event(manager)
        manager.apply_event(event)
        old_id = manager.status().run_id
        before = manager.snapshot().state
        manager.reset()
        manager.schedule(self.event(manager, 'scheduled'))
        manager.transition('start')
        restored = self.manager()
        self.assertEqual(restored.status(), manager.status())
        self.assertEqual(restored.history(), manager.history())
        self.assertEqual(restored.snapshot(run_id=old_id).state, before)
        restored.advance(60)
        self.assertEqual(restored.get_state().roads[0].status, 'blocked')
        with self.assertRaisesRegex(ValueError, 'already processed'):
            self.manager().apply_event(self.event(manager, 'scheduled'))
        stored = self.store().load()
        self.assertNotIn('roads', stored['state'])
        self.assertNotIn('coordinates', str(stored))
        self.assertEqual(len(stored['network']['sha256']), 64)

    def test_network_mismatch_preserves_committed_state(self):
        self.manager()
        before = deepcopy(self.client.data)
        def changed():
            roads = fixture_roads()
            roads[0]['coordinates'] = [list(point) for point in roads[0]['coordinates']]
            roads[0]['coordinates'][0][0] += 0.0001
            return roads
        with self.assertRaisesRegex(RuntimeError, 'network version'):
            StateManager(store=self.store(), roads_loader=changed)
        self.assertEqual(self.client.data, before)

    def test_failed_event_does_not_broadcast_or_change_state(self):
        from app.main import app
        from fastapi.testclient import TestClient
        manager = self.manager()
        before = manager.get_state()
        self.client.fail_at = self.client.calls + 2
        with patch('app.main.state_manager', manager), patch('app.main.live_clients.broadcast', new_callable=AsyncMock) as broadcast:
            with TestClient(app) as client:
                with self.assertRaises(OSError):
                    client.post('/api/simulation/events', json=self.event(manager).model_dump(mode='json'))
                broadcast.assert_not_awaited()
                self.assertEqual(manager.get_state(), before)
                self.assertEqual(manager.get_events(), [])
                self.client.fail_at = None
                self.assertEqual(client.post('/api/simulation/events', json=self.event(manager).model_dump(mode='json')).status_code, 200)
                broadcast.assert_awaited_once()

    def test_advance_and_reset_failure_roll_back_all_journals(self):
        manager = self.manager()
        manager.schedule(self.event(manager))
        manager.transition('start')
        before = manager._document()
        for operation in (lambda: manager.advance(60), manager.reset):
            self.client.fail_at = self.client.calls + 2
            with self.assertRaises(OSError):
                operation()
            self.assertEqual(manager._document(), before)

    def test_decisions_restart_failure_and_approval(self):
        manager = DecisionManager(self.store('decisions'))
        state = self.manager().get_state()
        action = ProposedAction(action_id='route/one', type='ROUTE_ASSIGNMENT', target_id='S001',
                                route_road_ids=[state.roads[0].id], reason='Test route')
        with patch('app.decisions.routing_graph', return_value=synthetic_graph()):
            manager.propose(action, state, state.revision)
        manager = DecisionManager(self.store('decisions'))
        self.assertEqual(manager.get(action.action_id).status, 'pending_approval')
        self.client.fail_at = self.client.calls + 2
        with self.assertRaises(OSError):
            manager.decide(action.action_id, True, state)
        self.assertEqual(manager.get(action.action_id).status, 'pending_approval')
        self.client.fail_at = None
        manager.decide(action.action_id, True, state, 'operator-test')
        restored = DecisionManager(self.store('decisions'))
        self.assertEqual(restored.get(action.action_id).status, 'applied')
        self.assertEqual(restored.get(action.action_id).decided_by, 'operator-test')
        with self.assertRaisesRegex(ValueError, 'already exists'):
            restored.propose(action, state, state.revision)

    def test_local_archives_and_decisions_recover(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / 'simulation.json')
            manager = StateManager(store=LocalStore(path), roads_loader=fixture_roads)
            old_id = manager.status().run_id
            manager.apply_event(self.event(manager))
            manager.reset()
            restored = StateManager(store=LocalStore(path), roads_loader=fixture_roads)
            self.assertEqual(restored.snapshot(run_id=old_id), manager.snapshot(run_id=old_id))
            decision_path = str(Path(directory) / 'decisions.json')
            decisions = DecisionManager(LocalStore(decision_path))
            state = manager.get_state()
            action = ProposedAction(action_id='transfer', type='RESOURCE_TRANSFER', target_id='S001',
                                    unit_id='U001', item='water', quantity=1, reason='test')
            decisions.propose(action, state, state.revision)
            with self.assertRaises(ValueError):
                decisions.decide('transfer', True, state)
            self.assertEqual(decisions.get('transfer').status, 'pending_approval')
            decisions.decide('transfer', False, state)
            self.assertEqual(DecisionManager(LocalStore(decision_path)).get('transfer').status, 'rejected')

    def test_backend_selection(self):
        with patch.dict('os.environ', {'SIMULATION_STORAGE_BACKEND': 'invalid'}):
            with self.assertRaises(RuntimeError):
                configured_store('unused.json')
        with patch.dict('os.environ', {'SIMULATION_STORAGE_BACKEND': 'firestore', 'FIREBASE_PROJECT_ID': 'demo-test'}):
            with patch('app.storage.FirestoreStore') as constructor:
                configured_store('unused.json', 'decisions')
                self.assertEqual(constructor.call_args.kwargs['namespace'], 'decisions')

    def test_emulator_initializes_without_cloud_credentials(self):
        with patch.dict('os.environ', {'FIRESTORE_EMULATOR_HOST': '127.0.0.1:8080'}):
            with patch('firebase_admin.get_app', side_effect=ValueError), \
                 patch('firebase_admin.initialize_app') as initialize, \
                 patch('firebase_admin.firestore.client') as client, \
                 patch('firebase_admin.credentials.ApplicationDefault') as adc:
                FirestoreStore(project_id='demo-disaster-twin')
                adc.assert_not_called()
                self.assertEqual(initialize.call_args.args[1], {'projectId': 'demo-disaster-twin'})
                self.assertEqual(initialize.call_args.args[0].get_credential().__class__.__name__, 'AnonymousCredentials')
                client.assert_called_once()

    def test_event_uniqueness_after_restart_and_failed_schedule(self):
        manager = self.manager()
        event = self.event(manager)
        self.client.fail_at = self.client.calls + 2
        with self.assertRaises(OSError):
            manager.schedule(event)
        self.assertEqual(manager.history().scheduled, [])
        self.client.fail_at = None
        manager.apply_event(event)
        with self.assertRaisesRegex(ValueError, 'already processed'):
            self.manager().apply_event(event)

    def test_failed_proposal_is_not_published(self):
        manager = DecisionManager(self.store('decisions'))
        state = self.manager().get_state()
        action = ProposedAction(action_id='proposal', type='RESOURCE_TRANSFER', target_id='S001',
                                unit_id='U001', item='water', quantity=1, reason='test')
        self.client.fail_at = self.client.calls + 2
        with self.assertRaises(OSError):
            manager.propose(action, state, state.revision)
        self.assertEqual(manager.list(), [])
        self.assertEqual(DecisionManager(self.store('decisions')).list(), [])
