import copy
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.agents.routing import build_road_graph, route_between


SCENARIO_PATH = Path(__file__).parents[1] / "data" / "sample" / "synthetic_routing_scenario.json"


@pytest.fixture
def scenario():
    return json.loads(SCENARIO_PATH.read_text(encoding="utf-8"))


@pytest.fixture
def roads(scenario):
    return copy.deepcopy(scenario["roads"])


def test_dijkstra_uses_lowest_total_cost_path(roads):
    direct = next(road for road in roads if road["id"] == "R001")
    direct["status"] = "open"
    direct["risk_score"] = 0.0

    result = route_between(roads, "N001", "N002")

    assert result.reachable is True
    assert result.road_ids == ["R001"]
    assert result.nodes == ["N001", "N002"]
    assert result.total_cost == 1000


def test_flooded_direct_road_uses_connected_alternative(roads):
    result = route_between(roads, "N001", "N002")

    assert result.reachable is True
    assert result.road_ids == ["R002", "R003"]
    assert result.nodes == ["N001", "N003", "N002"]
    assert result.route_geometry.coordinates == [
        (80.0, 13.0),
        (80.0, 13.01),
        (80.01, 13.0),
    ]


def test_blocked_roads_are_not_traversable(roads):
    for road in roads:
        if road["id"] in {"R002", "R003", "R004", "R005"}:
            road["status"] = "blocked"

    result = route_between(roads, "N001", "N002")

    assert result.reachable is False
    assert result.road_ids == []
    assert "inaccessible" in result.failure_reason


def test_no_feasible_path_returns_explicit_unreachable_result(roads):
    for road in roads:
        if road["id"] != "R006":
            road["status"] = "blocked"

    result = route_between(roads, "N001", "N005")

    assert result.reachable is False
    assert result.total_cost is None
    assert result.failure_reason


def test_unknown_source_or_destination_returns_unreachable(roads):
    unknown_source = route_between(roads, "NOT-A-NODE", "N002")
    unknown_target = route_between(roads, "N001", "NOT-A-NODE")

    assert unknown_source.reachable is False
    assert "absent" in unknown_source.failure_reason
    assert unknown_target.reachable is False
    assert "absent" in unknown_target.failure_reason


def test_roads_are_one_way_unless_bidirectional_is_explicit():
    road = {
        "id": "ONE",
        "source": "A",
        "target": "B",
        "status": "open",
        "risk_score": 0.0,
        "distance_m": 100,
        "geometry": {"type": "LineString", "coordinates": [[80, 13], [80.001, 13]]},
    }

    assert route_between([road], "B", "A").reachable is False
    road["bidirectional"] = True
    reverse = route_between([road], "B", "A")
    assert reverse.reachable is True
    assert reverse.road_ids == ["ONE"]
    assert reverse.route_geometry.coordinates == [(80.001, 13.0), (80.0, 13.0)]


def test_disconnected_edges_are_not_joined_by_proximity():
    roads = [
        {
            "id": "A",
            "source": "N1",
            "target": "N2",
            "status": "open",
            "risk_score": 0,
            "geometry": {"type": "LineString", "coordinates": [[80, 13], [80.01, 13]]},
        },
        {
            "id": "B",
            "source": "N3",
            "target": "N4",
            "status": "open",
            "risk_score": 0,
            "geometry": {"type": "LineString", "coordinates": [[80.010001, 13], [80.02, 13]]},
        },
    ]

    result = route_between(roads, "N1", "N4")

    assert result.reachable is False
    assert "disconnected" in result.failure_reason


def test_coordinate_locations_require_an_exact_endpoint_match(roads):
    assert route_between(roads, [80.0, 13.0], [80.01, 13.0]).reachable is True
    nearby_but_unmatched = route_between(roads, [80.000001, 13.0], "N002")

    assert nearby_but_unmatched.reachable is False
    assert "exact road-endpoint mapping" in nearby_but_unmatched.failure_reason


def test_road_status_is_normalized_and_closed_roads_are_excluded(roads):
    roads[0]["status"] = "OPEN"
    roads[1]["status"] = "BLOCKED"
    built = build_road_graph(roads)

    assert built.graph.has_edge("N001", "N002")
    assert ("R002", "road status 'blocked' is not traversable") in built.excluded_roads


def test_road_without_target_is_not_invented_as_an_edge():
    result = build_road_graph(
        [
            {
                "id": "NO-TARGET",
                "source": "N1",
                "status": "open",
                "risk_score": 0.1,
                "geometry": {"type": "LineString", "coordinates": [[80, 13], [80.01, 13]]},
            }
        ]
    )

    assert result.graph.number_of_edges() == 0
    assert "missing target" in result.excluded_roads[0][1]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("risk_score", 1.2),
        ("geometry", {"type": "LineString", "coordinates": [[80, 95], [80.01, 13]]}),
    ],
)
def test_invalid_road_risk_or_geometry_is_rejected(roads, field, value):
    roads[0][field] = value
    with pytest.raises(ValidationError):
        build_road_graph(roads)


def test_route_has_real_connected_road_ids_and_deduplicated_junctions(roads):
    result = route_between(roads, "N001", "N005")

    assert result.reachable is True
    edges = {(road["source"], road["target"], road["id"]) for road in roads}
    assert all(
        (result.nodes[index], result.nodes[index + 1], road_id) in edges
        for index, road_id in enumerate(result.road_ids)
    )
    assert len(result.route_geometry.coordinates) == 4
