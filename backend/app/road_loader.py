"""GraphML is authoritative; every directed edge has one stable road ID."""
import json
import math
import os
from pathlib import Path
import tempfile
import networkx as nx
import osmnx as ox
from pyproj import CRS
from shapely.geometry import LineString, mapping

DATA_DIR = Path(__file__).resolve().parents[1] / 'data' / 'processed'


def prepare_graph(g):
    if not isinstance(g, nx.MultiDiGraph) or not g.number_of_edges():
        raise ValueError('Expected a nonempty directed MultiDiGraph')
    if CRS.from_user_input(g.graph.get('crs')) != CRS.from_epsg(4326):
        g = ox.projection.project_graph(g, to_crs='EPSG:4326')
    g.graph.setdefault('source', 'local_graphml_unverified')
    for _, node in g.nodes(data=True):
        x, y = float(node['x']), float(node['y'])
        if not (-180 <= x <= 180 and -90 <= y <= 90):
            raise ValueError('Invalid longitude/latitude')
    for u, v, k, e in g.edges(keys=True, data=True):
        e['road_id'] = f'R_{u}_{v}_{k}'
        length = float(e['length'])
        if not math.isfinite(length) or length <= 0:
            raise ValueError(f'Invalid length: {e["road_id"]}')
        e['length'] = length
        geom = e.get('geometry')
        if geom is None:
            geom = LineString([(g.nodes[n]['x'], g.nodes[n]['y']) for n in (u, v)])
        if geom.geom_type != 'LineString' or geom.is_empty or not geom.is_valid:
            raise ValueError('Expected valid LineString geometry')
        if any(not (-180 <= x <= 180 and -90 <= y <= 90) for x, y in geom.coords):
            raise ValueError('Geometry must use longitude/latitude')
        e['geometry'] = geom
    return g


def feature_collection(g):
    return {'type': 'FeatureCollection', 'features': [
        {'type': 'Feature', 'id': e['road_id'], 'geometry': mapping(e['geometry']),
         'properties': {'id': e['road_id'], 'source': g.graph['source'],
                        'name': str(e.get('name', 'Unnamed Road')), 'u': str(u), 'v': str(v),
                        'key': int(k), 'length_m': e['length'], 'status': 'open', 'risk_score': 0.0}}
        for u, v, k, e in g.edges(keys=True, data=True)]}


def save_network(g, directory):
    g = prepare_graph(g)
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    # Stage and round-trip before replacing. API derives roads from canonical
    # GraphML if interrupted between these two individually atomic replacements.
    with tempfile.TemporaryDirectory(dir=directory) as temp:
        gp, jp = Path(temp) / 'roads.graphml', Path(temp) / 'roads.geojson'
        ox.io.save_graphml(g, filepath=gp)
        loaded = prepare_graph(ox.io.load_graphml(gp))
        jp.write_text(json.dumps(feature_collection(loaded), allow_nan=False, indent=2), encoding='utf-8')
        json.loads(jp.read_text(encoding='utf-8'))
        os.replace(gp, directory / 'chennai_roads.graphml')
        os.replace(jp, directory / 'chennai_roads.geojson')


def synthetic_graph():
    g = nx.MultiDiGraph(crs='EPSG:4326', source='synthetic_demo', attribution='Synthetic development fixture; not OpenStreetMap roads')
    for n, (x, y) in enumerate([(80.2341, 13.0418), (80.2351, 13.0418), (80.2351, 13.0428)]):
        g.add_node(n, x=x, y=y)
    for u, v in [(0, 1), (1, 0), (1, 2), (2, 1)]:
        g.add_edge(u, v, length=110.0, name='Synthetic demo road')
    return prepare_graph(g)


def load_roads():
    path = DATA_DIR / 'chennai_roads.graphml'
    if not path.exists():
        raise FileNotFoundError(
            f'Road network data is missing: {path}. '
            'Add the real GraphML road network or import a real OSM extract before starting the API.'
        )
    g = prepare_graph(ox.io.load_graphml(path))
    return [dict(f['properties'], coordinates=f['geometry']['coordinates']) for f in feature_collection(g)['features']]


def routing_graph(state):
    """Routing copy with blocked directed edges removed by stable road ID."""
    path = DATA_DIR / 'chennai_roads.graphml'
    if not path.exists():
        raise FileNotFoundError(
            f'Road network data is missing: {path}. '
            'Routing requires the real GraphML road network.'
        )
    g = prepare_graph(ox.io.load_graphml(path))
    roads = {r.id: r for r in state.roads}
    if set(roads) != {e['road_id'] for *_, e in g.edges(data=True, keys=True)}:
        raise ValueError('State and network differ; reset after replacing data')
    for u, v, k, e in list(g.edges(keys=True, data=True)):
        road = roads[e['road_id']]
        e.update(status=road.status, risk_score=road.risk_score)
        if road.status == 'blocked':
            g.remove_edge(u, v, k)
    return g
