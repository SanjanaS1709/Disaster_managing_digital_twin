"""Offline geospatial import and validation helpers.

Optional PBF support uses pyosmium when installed. XML .osm is parsed with the
standard library so the development workflow remains usable without Overpass.
"""
from __future__ import annotations

import csv
import json
from datetime import date
from pathlib import Path
import xml.etree.ElementTree as ET

import networkx as nx
from pyproj import Transformer

from .road_loader import save_network, prepare_graph

DRIVABLE = {"motorway", "trunk", "primary", "secondary", "tertiary", "unclassified", "residential", "living_street", "service", "motorway_link", "trunk_link", "primary_link", "secondary_link", "tertiary_link"}


def _bounds(bounds: tuple[float, float, float, float] | None):
    return lambda x, y: bounds is None or bounds[0] <= x <= bounds[2] and bounds[1] <= y <= bounds[3]


def import_osm_xml(path: str | Path, bounds: tuple[float, float, float, float] | None = None) -> nx.MultiDiGraph:
    """Import an OSM XML extract, preserving actual OSM way-node topology."""
    root = ET.parse(path).getroot()
    nodes = {}
    inside = _bounds(bounds)
    for item in root.findall("node"):
        node_id = item.attrib["id"]
        lon, lat = float(item.attrib["lon"]), float(item.attrib["lat"])
        if inside(lon, lat):
            nodes[node_id] = (lon, lat)
    graph = nx.MultiDiGraph(
        crs="EPSG:4326", source="contemporary_osm", source_file=str(Path(path).resolve()),
        source_generator=root.attrib.get("generator", "unknown"),
        source_bounds=dict(root.find("bounds").attrib) if root.find("bounds") is not None else {},
        dataset_type="road_network",
    )
    for way in root.findall("way"):
        tags = {tag.attrib.get("k"): tag.attrib.get("v", "") for tag in way.findall("tag")}
        if tags.get("highway") not in DRIVABLE or tags.get("area") == "yes":
            continue
        refs = [nd.attrib["ref"] for nd in way.findall("nd")]
        if len(refs) < 2 or any(ref not in nodes for ref in refs):
            continue
        oneway_tag = tags.get("oneway", "").lower()
        oneway = oneway_tag in {"yes", "true", "1", "-1"}
        reverse = oneway_tag == "-1"
        attrs = {"osmid": way.attrib.get("id"), "name": tags.get("name", "Unnamed Road"), "highway": tags["highway"], "oneway": oneway, "maxspeed": tags.get("maxspeed", "")}
        for start, end in zip(refs, refs[1:]):
            coords = [nodes[start], nodes[end]]
            # Do not retain every unrelated OSM point in the bounding box.
            # A node is needed by routing exactly when a retained road segment
            # references it; adding only these nodes preserves every edge and
            # OSM intersection while avoiding tens of thousands of isolates.
            for node_id, (lon, lat) in zip((start, end), coords):
                if node_id not in graph:
                    graph.add_node(node_id, x=lon, y=lat)
            # XML fixtures often omit ways' explicit lengths; calculate a stable
            # approximate metre length without pretending it is surveyed data.
            length = ((coords[1][0] - coords[0][0]) ** 2 + (coords[1][1] - coords[0][1]) ** 2) ** 0.5 * 111_320
            graph.add_edge(end if reverse else start, start if reverse else end, length=length, geometry=None, **attrs)
            if not oneway:
                graph.add_edge(end, start, length=length, geometry=None, **attrs)
    if not graph.number_of_edges():
        raise ValueError("OSM input contains no drivable roads in the selected bounds")
    return prepare_graph(graph)


def import_osm(path: str | Path, bounds=None) -> nx.MultiDiGraph:
    path = Path(path)
    if path.suffix.lower() in {".osm", ".xml"}:
        return import_osm_xml(path, bounds)
    if path.suffix.lower() in {".pbf", ".osm.pbf"}:
        try:
            import osmium
        except ImportError as error:
            raise RuntimeError("PBF import requires pyosmium; install it with `python -m pip install pyosmium` or supply .osm XML") from error
        raise RuntimeError("PBF parsing is unavailable in this build: install pyosmium and use the documented osmium extract workflow, or convert the PBF to .osm XML")
    raise ValueError("Input must be .osm, .xml, or .osm.pbf")


def import_facilities(path: str | Path, bounds=None) -> list[dict]:
    """Read facility CSV or GeoJSON, transforming GeoJSON coordinates to WGS84."""
    path = Path(path)
    records = []
    if path.suffix.lower() == ".csv":
        with path.open(newline="", encoding="utf-8-sig") as handle:
            records = list(csv.DictReader(handle))
    elif path.suffix.lower() in {".json", ".geojson"}:
        raw = json.loads(path.read_text(encoding="utf-8"))
        records = [{**feature.get("properties", {}), "longitude": feature["geometry"]["coordinates"][0], "latitude": feature["geometry"]["coordinates"][1]} for feature in raw.get("features", [])]
    else:
        raise ValueError("Facilities input must be CSV or GeoJSON")
    required = {"id", "name", "longitude", "latitude", "type", "source"}
    seen = set()
    result = []
    inside = _bounds(bounds)
    for record in records:
        missing = required - set(record)
        if missing:
            raise ValueError(f"Facility missing fields: {sorted(missing)}")
        ident = str(record["id"])
        if ident in seen:
            raise ValueError(f"Duplicate facility ID: {ident}")
        seen.add(ident)
        lon, lat = float(record["longitude"]), float(record["latitude"])
        if not (-180 <= lon <= 180 and -90 <= lat <= 90) or not inside(lon, lat):
            raise ValueError(f"Facility {ident} is outside valid coordinates or study bounds")
        item = {"id": ident, "name": str(record["name"]), "longitude": lon, "latitude": lat, "type": str(record["type"]), "source": str(record["source"])}
        if record.get("observation_date"):
            date.fromisoformat(str(record["observation_date"]))
            item["observation_date"] = str(record["observation_date"])
        if record.get("capacity") not in (None, ""):
            capacity = int(record["capacity"])
            if capacity < 0:
                raise ValueError(f"Negative capacity for facility {ident}")
            item["capacity"] = capacity
        result.append(item)
    return result


def validate_geo_layer(path: str | Path, kind: str) -> dict:
    """Validate optional rainfall/flood/boundary GeoJSON without inventing data."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if raw.get("type") != "FeatureCollection":
        raise ValueError(f"{kind} must be a GeoJSON FeatureCollection")
    for feature in raw.get("features", []):
        if "geometry" not in feature or feature["geometry"] is None:
            raise ValueError(f"{kind} contains a feature without geometry")
    return raw
