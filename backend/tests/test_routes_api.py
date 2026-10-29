from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.api.routes as routes_api


def client():
    app = FastAPI()
    app.include_router(routes_api.router)
    return TestClient(app)


def route_payload(code="Ok"):
    return {
        "code": code,
        "routes": [{
            "distance": 4200.5,
            "duration": 730,
            "geometry": {"type": "LineString", "coordinates": [[80.2, 13.0], [80.21, 13.01], [80.22, 13.02]]},
            "legs": [{"steps": [
                {"name": "Anna Salai", "distance": 350, "duration": 60, "maneuver": {"type": "depart"}},
                {"name": "Beach Road", "distance": 3850, "duration": 670, "maneuver": {"type": "turn", "modifier": "right"}},
                {"name": "", "distance": 0.5, "duration": 0, "maneuver": {"type": "arrive"}},
            ]}],
        }],
    }


class StubResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


def test_route_uses_lon_lat_and_returns_osrm_geometry_and_directions(monkeypatch):
    seen = {}

    class StubClient:
        def __init__(self, **kwargs):
            seen["timeout"] = kwargs["timeout"].read

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def get(self, url, params):
            seen.update(url=url, params=params)
            return StubResponse(route_payload())

    monkeypatch.setattr(routes_api.httpx, "AsyncClient", StubClient)
    response = client().post("/api/routes/calculate", json={
        "origin": {"label": "Origin", "coordinates": [80.2, 13.0]},
        "destination": {"label": "Shelter", "coordinates": [80.22, 13.02]},
    })
    assert response.status_code == 200
    data = response.json()
    assert seen["url"].endswith("/route/v1/driving/80.2,13.0;80.22,13.02")
    assert seen["params"] == {"overview": "full", "geometries": "geojson", "steps": "true", "alternatives": "2"}
    assert data["route"]["geometry"]["coordinates"] == route_payload()["routes"][0]["geometry"]["coordinates"]
    assert data["route"]["distance_m"] == 4200.5
    assert data["route"]["steps"][1]["instruction"] == "Turn right onto Beach Road"
    assert data["safety_status"] == "not_verified"


def test_route_rejects_same_or_invalid_points_without_calling_service(monkeypatch):
    def unexpected_client(**kwargs):
        raise AssertionError("Invalid points must not call OSRM")

    monkeypatch.setattr(routes_api.httpx, "AsyncClient", unexpected_client)
    same = {"origin": {"coordinates": [80.2, 13]}, "destination": {"coordinates": [80.2, 13]}}
    assert client().post("/api/routes/calculate", json=same).status_code == 422
    invalid = {"origin": {"coordinates": [220, 13]}, "destination": {"coordinates": [80.22, 13.02]}}
    assert client().post("/api/routes/calculate", json=invalid).status_code == 422


def test_route_reports_when_osrm_has_no_route(monkeypatch):
    class StubClient:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def get(self, *_args, **_kwargs):
            return StubResponse({"code": "NoRoute", "routes": []})

    monkeypatch.setattr(routes_api.httpx, "AsyncClient", StubClient)
    response = client().post("/api/routes/calculate", json={
        "origin": {"coordinates": [80.2, 13]},
        "destination": {"coordinates": [80.22, 13.02]},
    })
    assert response.status_code == 404
    assert "No road route" in response.json()["detail"]


def test_route_reports_timeout_without_leaking_provider_details(monkeypatch):
    class StubClient:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def get(self, *_args, **_kwargs):
            raise routes_api.httpx.ReadTimeout("private connection details")

    monkeypatch.setattr(routes_api.httpx, "AsyncClient", StubClient)
    response = client().post("/api/routes/calculate", json={
        "origin": {"coordinates": [80.2, 13]},
        "destination": {"coordinates": [80.22, 13.02]},
    })
    assert response.status_code == 504
    assert response.json()["detail"] == "The routing service timed out. Please try again."


def test_compare_ranks_reachable_destinations_by_distance_and_time(monkeypatch):
    seen = {}

    class StubClient:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def get(self, url, params):
            seen.update(url=url, params=params)
            return StubResponse({
                "code": "Ok",
                "distances": [[10000, 4000]],
                "durations": [[500, 700]],
            })

    monkeypatch.setattr(routes_api.httpx, "AsyncClient", StubClient)
    response = client().post("/api/routes/compare", json={
        "origin": {"label": "Incident", "coordinates": [80.2, 13]},
        "destinations": [
            {"label": "Far shelter", "coordinates": [80.22, 13.02]},
            {"label": "Near shelter", "coordinates": [80.21, 13.01]},
        ],
    })
    assert response.status_code == 200
    data = response.json()
    assert seen["url"].endswith("/table/v1/driving/80.2,13.0;80.22,13.02;80.21,13.01")
    assert seen["params"] == {"sources": "0", "destinations": "1;2", "annotations": "distance,duration"}
    assert [item["destination"]["label"] for item in data["candidates"]] == ["Near shelter", "Far shelter"]
    assert data["safety_status"] == "not_verified"
