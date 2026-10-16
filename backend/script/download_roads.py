"""Bounded OSMnx downloads with explicit offline fallback."""
import argparse
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--endpoint', action='append')
    p.add_argument('--attempts', type=int, default=2)
    p.add_argument('--timeout', type=int, default=60)
    p.add_argument('--attempt-timeout', type=int, default=120)
    p.add_argument('--backoff', type=float, default=5)
    p.add_argument('--distance', type=int, default=500)
    p.add_argument('--output-dir', type=Path, default=BASE_DIR / 'data' / 'processed')
    p.add_argument('--local-graph', type=Path)
    p.add_argument('--offline-demo', action='store_true')
    p.add_argument('--refresh', action='store_true')
    p.add_argument('--worker', type=Path, help=argparse.SUPPRESS)
    a = p.parse_args()
    if min(a.attempts, a.timeout, a.attempt_timeout, a.distance) <= 0 or a.backoff < 0:
        p.error('Attempts, timeouts and distance must be positive; backoff must be nonnegative')
    import osmnx as ox
    from app.road_loader import prepare_graph, save_network, synthetic_graph
    endpoints = a.endpoint or os.environ.get('OVERPASS_ENDPOINTS', 'https://overpass-api.de/api,https://overpass.kumi.systems/api').split(',')
    endpoints = [e.strip().rstrip('/') for e in endpoints if e.strip()]
    if not endpoints or any(not e.startswith(('https://', 'http://')) for e in endpoints):
        p.error('Provide Overpass HTTP(S) base URLs ending in /api')
    if a.worker:
        ox.settings.use_cache = True
        ox.settings.log_console = True
        ox.settings.cache_folder = BASE_DIR / 'data' / 'cache'
        ox.settings.requests_timeout = a.timeout
        ox.settings.overpass_url = endpoints[0]
        g = ox.graph.graph_from_point((13.0418, 80.2341), dist=a.distance, network_type='drive', simplify=True)
        g.graph.update(source='openstreetmap', attribution='OpenStreetMap contributors', overpass_endpoint=endpoints[0])
        ox.io.save_graphml(prepare_graph(g), filepath=a.worker)
        return 0
    cached = a.local_graph or a.output_dir / 'chennai_roads.graphml'
    g = None
    if not a.offline_demo and cached.exists():
        try:
            g = prepare_graph(ox.io.load_graphml(cached))
            print(f'Validated local network: {cached}', flush=True)
        except Exception as error:
            print(f'Cannot reuse {cached}: {error}', file=sys.stderr)
            if a.local_graph:
                return 1
    elif a.local_graph and not a.offline_demo:
        print(f'Missing local GraphML: {cached}', file=sys.stderr)
        return 1
    if a.offline_demo:
        g = synthetic_graph()
    elif g is None or a.refresh:
        for attempt in range(a.attempts):
            endpoint = endpoints[attempt % len(endpoints)]
            print(f'Download attempt {attempt + 1}/{a.attempts}: {endpoint}', flush=True)
            with tempfile.TemporaryDirectory() as temp:
                target = Path(temp) / 'network.graphml'
                command = [sys.executable, str(Path(__file__).resolve()), '--worker', str(target), '--endpoint', endpoint, '--timeout', str(a.timeout), '--distance', str(a.distance)]
                try:
                    subprocess.run(command, check=True, timeout=a.attempt_timeout)
                    g = prepare_graph(ox.io.load_graphml(target))
                    print('Live OpenStreetMap download succeeded.', flush=True)
                    break
                except (subprocess.SubprocessError, ValueError, OSError) as error:
                    print(f'Attempt failed: {error}. Check server capacity/connectivity and worker error above.', file=sys.stderr)
            if attempt + 1 < a.attempts:
                time.sleep(min(a.backoff * 2 ** attempt, 60))
        else:
            if g is None:
                print('No usable network. Retry later, use --local-graph FILE, or --offline-demo.', file=sys.stderr)
                return 1
            print('Download unavailable; reusing validated local network.')
    save_network(g, a.output_dir)
    print(f"Saved {g.number_of_nodes()} nodes / {g.number_of_edges()} directed edges; source={g.graph['source']}\n{a.output_dir.resolve()}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
