"""Validate active road exports and print a concise provenance report."""
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import osmnx as ox
from app.road_loader import prepare_graph


def main():
    root = Path(__file__).resolve().parents[1]
    data = root / "data" / "processed"
    graph = prepare_graph(ox.io.load_graphml(data / "chennai_roads.graphml"))
    geojson = json.loads((data / "chennai_roads.geojson").read_text(encoding="utf-8"))
    graph_ids = {edge["road_id"] for *_, edge in graph.edges(keys=True, data=True)}
    json_ids = {feature["id"] for feature in geojson["features"]}
    if graph_ids != json_ids:
        raise ValueError("GraphML and GeoJSON edge IDs differ")
    manifest = json.loads((root / "data" / "manifest.json").read_text(encoding="utf-8"))
    print(json.dumps({"source": manifest["roads"]["classification"], "graph_source": graph.graph.get("source"),
                      "nodes": graph.number_of_nodes(), "edges": graph.number_of_edges(),
                      "crs": graph.graph.get("crs"), "id_consistency": True}, indent=2))


if __name__ == "__main__":
    main()
