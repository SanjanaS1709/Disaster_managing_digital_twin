import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import networkx as nx
import osmnx as ox
from fastapi.testclient import TestClient
from app import road_loader
from app.main import app
from app.models import DisasterEvent, Road
from app.sample_data import create_initial_state
from app.simulation import reduce_event
from app.state_manager import StateManager
from script import download_roads


class RoadsTest(unittest.TestCase):
    def test_roundtrip_and_ids(self):
        with tempfile.TemporaryDirectory() as temp:
            road_loader.save_network(road_loader.synthetic_graph(), temp)
            g = ox.io.load_graphml(Path(temp) / 'chennai_roads.graphml')
            data = json.loads((Path(temp) / 'chennai_roads.geojson').read_text())
            self.assertEqual(g.number_of_nodes(), 3)
            self.assertEqual(g.number_of_edges(), 4)
            self.assertTrue(g.is_directed())
            self.assertEqual({e['road_id'] for *_, e in g.edges(keys=True, data=True)}, {f['id'] for f in data['features']})
            for f in data['features']:
                p = f['properties']
                e = g[int(p['u'])][int(p['v'])][p['key']]
                self.assertEqual(e['length'], p['length_m'])
                self.assertEqual(list(e['geometry'].coords), [tuple(c) for c in f['geometry']['coordinates']])
                self.assertEqual(p['source'], 'synthetic_demo')
            self.assertTrue(nx.has_path(g, 0, 2))

    def test_invalid_graph_preserves_files(self):
        with tempfile.TemporaryDirectory() as temp:
            road_loader.save_network(road_loader.synthetic_graph(), temp)
            before = {p.name: p.read_bytes() for p in Path(temp).iterdir()}
            g = road_loader.synthetic_graph()
            g[0][1][0]['length'] = float('nan')
            with self.assertRaises(ValueError):
                road_loader.save_network(g, temp)
            self.assertEqual(before, {p.name: p.read_bytes() for p in Path(temp).iterdir()})
            with self.assertRaises(ValueError):
                road_loader.prepare_graph(nx.MultiDiGraph(crs='EPSG:4326'))

    def test_api_flood_reset_routing(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(road_loader, 'DATA_DIR', Path(temp)):
            road_loader.save_network(road_loader.synthetic_graph(), temp)
            manager = StateManager()
            with patch('app.main.state_manager', manager), TestClient(app) as client:
                state = client.post('/api/simulation/reset').json()
                self.assertEqual(client.get('/api/twin/state').status_code, 200)
                road = state['roads'][0]
                event = dict(event_id='test-flood', simulation_id=state['simulation_id'], type='ROAD_FLOODED', timestamp='2023-12-04T11:00:00+05:30', target_id=road['id'])
                response = client.post('/api/simulation/events', json=event)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()['roads'][0]['status'], 'blocked')
                feature = client.get('/api/twin/geojson').json()['features'][0]
                self.assertEqual(feature['properties']['status'], 'blocked')
                self.assertEqual(len(client.get('/api/simulation/events').json()), 1)
                self.assertEqual(client.post('/api/simulation/events', json=event).status_code, 400)
                g = road_loader.routing_graph(manager.get_state())
                self.assertFalse(g.has_edge(int(road['u']), int(road['v']), road['key']))
                event.update(event_id='reopen', type='ROAD_REOPENED')
                self.assertEqual(client.post('/api/simulation/events', json=event).status_code, 200)
                self.assertEqual(road_loader.routing_graph(manager.get_state()).number_of_edges(), 4)
                self.assertEqual(client.post('/api/simulation/reset').json()['roads'][0]['status'], 'open')
                self.assertEqual(client.get('/api/simulation/events').json(), [])

    def test_active_network_loads_and_a_valid_road_can_be_flooded_and_reopened(self):
        """Exercise the installed graph read-only; never reset the application manager."""
        roads = road_loader.load_roads()
        self.assertGreater(len(roads), 0)
        road = Road(**roads[0])
        state = create_initial_state()
        state.roads = [road]
        flood = DisasterEvent(event_id='active-flood', simulation_id=state.simulation_id,
                              type='ROAD_FLOODED', timestamp='2023-12-04T10:01:00+05:30',
                              target_id=road.id, payload={'risk_score': 1.0})
        flooded = reduce_event(state, flood)
        self.assertEqual(next(item for item in flooded.roads if item.id == road.id).status, 'blocked')
        reopen = DisasterEvent.model_validate({**flood.model_dump(mode='json'),
                                               'event_id': 'active-reopen', 'type': 'ROAD_REOPENED',
                                               'timestamp': '2023-12-04T10:02:00+05:30',
                                               'payload': {'status': 'open', 'risk_score': 0.2}})
        reopened = reduce_event(flooded, reopen)
        self.assertEqual(next(item for item in reopened.roads if item.id == road.id).status, 'open')

    def test_projected_import_and_staging_failure(self):
        g = road_loader.synthetic_graph()
        g.graph['simplified'] = True
        projected = ox.projection.project_graph(g)
        restored = road_loader.prepare_graph(projected)
        self.assertAlmostEqual(restored.nodes[0]['x'], 80.2341, places=6)
        self.assertAlmostEqual(restored.nodes[0]['y'], 13.0418, places=6)
        with tempfile.TemporaryDirectory() as temp:
            road_loader.save_network(g, temp)
            before = {p.name: p.read_bytes() for p in Path(temp).iterdir()}
            with patch.object(road_loader.ox.io, 'save_graphml', side_effect=OSError('disk write failed')):
                with self.assertRaisesRegex(OSError, 'disk write failed'):
                    road_loader.save_network(g, temp)
            self.assertEqual(before, {p.name: p.read_bytes() for p in Path(temp).iterdir()})

    def test_bounded_retries_and_local_fallback(self):
        with tempfile.TemporaryDirectory() as temp:
            argv = ['download_roads', '--output-dir', temp, '--attempts', '2', '--backoff', '0', '--refresh']
            with patch('sys.argv', argv), patch.object(download_roads.subprocess, 'run', side_effect=subprocess.TimeoutExpired('worker', 1)) as run:
                self.assertEqual(download_roads.main(), 1)
                self.assertEqual(run.call_count, 2)
                self.assertFalse((Path(temp) / 'chennai_roads.graphml').exists())
                road_loader.save_network(road_loader.synthetic_graph(), temp)
                self.assertEqual(download_roads.main(), 0)
                self.assertEqual(run.call_count, 4)
            with patch('sys.argv', argv[:-1]), patch.object(download_roads.subprocess, 'run') as run:
                self.assertEqual(download_roads.main(), 0)
                run.assert_not_called()


if __name__ == '__main__':
    unittest.main()
