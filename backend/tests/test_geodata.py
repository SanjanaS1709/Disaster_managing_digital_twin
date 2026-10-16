import csv
import json
from pathlib import Path
import tempfile
import unittest

import networkx as nx
import osmnx as ox

from app.geodata import import_facilities, import_osm_xml, validate_geo_layer
from app.road_loader import save_network


class GeodataTests(unittest.TestCase):
    def setUp(self):
        self.fixture = Path(__file__).parents[1] / "data" / "raw" / "osm_fixture.osm"

    def test_osm_fixture_preserves_oneway_and_intersections(self):
        graph = import_osm_xml(self.fixture, (80.20, 13.02, 80.27, 13.07))
        self.assertEqual(graph.number_of_nodes(), 4)
        self.assertEqual(graph.number_of_edges(), 4)
        self.assertTrue(graph.has_edge("1", "2"))
        self.assertTrue(graph.has_edge("2", "1"))
        self.assertTrue(graph.has_edge("2", "3"))
        self.assertFalse(graph.has_edge("3", "2"))
        self.assertTrue(graph.has_edge("3", "4"))
        self.assertFalse(graph.has_edge("4", "3"))
        self.assertEqual(graph.graph["source"], "contemporary_osm")
        self.assertEqual(graph.graph["crs"], "EPSG:4326")

    def test_osm_import_omits_unreferenced_nodes(self):
        content = '''<osm generator="test"><node id="1" lon="80.23" lat="13.04"/><node id="2" lon="80.24" lat="13.04"/><node id="99" lon="80.25" lat="13.05"/><way id="10"><nd ref="1"/><nd ref="2"/><tag k="highway" v="residential"/></way></osm>'''
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "roads.osm"
            path.write_text(content, encoding="utf-8")
            graph = import_osm_xml(path)
        self.assertEqual(set(graph.nodes), {"1", "2"})
        self.assertEqual(graph.number_of_edges(), 2)

    def test_osm_outputs_match(self):
        graph = import_osm_xml(self.fixture)
        with tempfile.TemporaryDirectory() as directory:
            save_network(graph, directory)
            saved = ox.io.load_graphml(Path(directory) / "chennai_roads.graphml")
            geojson = json.loads((Path(directory) / "chennai_roads.geojson").read_text())
            self.assertEqual({e["road_id"] for *_, e in saved.edges(keys=True, data=True)}, {f["id"] for f in geojson["features"]})
            self.assertTrue(all(feature["geometry"]["coordinates"][0][0] == 80.2341 for feature in geojson["features"] if feature["properties"]["u"] == "1"))

    def test_facility_csv_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "facilities.csv"
            path.write_text("id,name,longitude,latitude,type,source,observation_date,capacity\nS1,One,80.23,13.04,shelter,local,2023-12-04,100\n", encoding="utf-8")
            records = import_facilities(path, (80.20, 13.02, 80.27, 13.07))
            self.assertEqual(records[0]["capacity"], 100)
            path.write_text("id,name,longitude,latitude,type,source\nS1,One,80.23,13.04,shelter,local\nS1,Two,80.24,13.05,shelter,local\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Duplicate"):
                import_facilities(path)

    def test_invalid_layer_and_facility_bounds(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "layer.geojson"
            path.write_text(json.dumps({"type": "FeatureCollection", "features": [{"type": "Feature", "geometry": None}]}), encoding="utf-8")
            with self.assertRaises(ValueError):
                validate_geo_layer(path, "flood")
            csv_path = Path(directory) / "facilities.csv"
            csv_path.write_text("id,name,longitude,latitude,type,source\nS1,One,181,13,shelter,local\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                import_facilities(csv_path)
