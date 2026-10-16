"""Import a local OSM XML/PBF extract without requiring Overpass."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.geodata import import_osm
from app.road_loader import save_network


AREAS = {"t_nagar": (80.21, 13.02, 80.27, 13.07), "chennai": (80.10, 12.90, 80.35, 13.25)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--area", choices=AREAS, default="t_nagar")
    parser.add_argument("--output-dir", type=Path, default=Path(__file__).resolve().parents[1] / "data" / "processed")
    args = parser.parse_args()
    graph = import_osm(args.input, AREAS[args.area])
    save_network(graph, args.output_dir)
    manifest = Path(args.output_dir).parent / "manifest.json" if args.output_dir.name == "processed" else args.output_dir / "manifest.json"
    document = json.loads(manifest.read_text(encoding="utf-8")) if manifest.exists() else {}
    document["roads"] = {
        "classification": "contemporary_osm",
        "source_organization": "OpenStreetMap contributors",
        "source_file": str(args.input.resolve()),
        "source_generator": graph.graph.get("source_generator"),
        "source_bounds": graph.graph.get("source_bounds"),
        "coverage": args.area,
        "crs": "EPSG:4326",
        "dataset_type": "road_network",
        "license": "Open Database License (ODbL); OpenStreetMap contributors",
        "validation": "passed",
        "nodes": graph.number_of_nodes(),
        "edges": graph.number_of_edges(),
        "processing_notes": "Imported from local OSM XML; only nodes referenced by retained drivable segments are kept.",
    }
    manifest.write_text(json.dumps(document, indent=2), encoding="utf-8")
    print(f"Imported {graph.number_of_nodes()} nodes / {graph.number_of_edges()} directed edges from {args.input}")


if __name__ == "__main__":
    raise SystemExit(main())
