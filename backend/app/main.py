
import logging

from fastapi import FastAPI, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.exceptions import RequestValidationError
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .models import (AdvanceRequest, DisasterEvent, EventHistory, ReplayRequest,
                     ScheduledEvent, SimulationStatus, StateSnapshot, TwinState)
from .state_manager import state_manager
from .decisions import decision_manager
from .models import AgentResponse, ProposedAction, DecisionStatus
from .live import live_clients
from .api.agents import router as agents_router
from .api.assistant import router as assistant_router
from .api.routes import router as routes_router


app = FastAPI(
    title="Agentic Disaster Digital Twin API",
    description="Digital twin backend for disaster relief logistics",
    version="0.1.0"
)
app.include_router(agents_router)
app.include_router(assistant_router)
app.include_router(routes_router)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5500", "http://127.0.0.1:5500"],
    allow_credentials=False,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Content-Type"]
)


@app.exception_handler(ValueError)
async def simulation_error(request: Request, error: ValueError) -> JSONResponse:
    logging.getLogger(__name__).warning('Rejected %s: %s', request.url.path, error)
    return JSONResponse(status_code=400, content={"detail": str(error)})


@app.exception_handler(RequestValidationError)
async def schema_error(request: Request, error: RequestValidationError) -> JSONResponse:
    logging.getLogger(__name__).warning('Rejected invalid request schema: %s', request.url.path)
    return await request_validation_exception_handler(request, error)


@app.get("/")
def home() -> dict[str, str]:
    return {
        "message": "Disaster Digital Twin API is running",
        "status": "active"
    }


@app.get("/api/twin/state", response_model=TwinState)
def get_twin_state() -> TwinState:
    return state_manager.get_state()


@app.post("/api/simulation/events", response_model=TwinState)
async def inject_event(event: DisasterEvent) -> TwinState:
    result = state_manager.apply_event(event)
    # Notification occurs only after StateManager persistence completes.
    await live_clients.broadcast({'type': 'STATE_CHANGED', 'simulation_id': result.simulation_id, 'event_id': event.event_id, 'event_type': event.type, 'revision': result.revision, 'timestamp': result.timestamp.isoformat(), 'affected_entity_ids': [event.target_id], 'refetch': '/api/twin/state'})
    return result


@app.get("/api/twin/geojson")
def get_twin_geojson() -> dict:
    return {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "id": road.id,
             "geometry": {"type": "LineString", "coordinates": road.coordinates},
             "properties": road.model_dump(exclude={"coordinates"})}
            for road in state_manager.get_state().roads
        ]
    }


@app.get("/api/simulation/events", response_model=list[DisasterEvent])
def get_event_history() -> list[DisasterEvent]:
    return state_manager.get_events()


@app.post("/api/simulation/reset", response_model=TwinState)
def reset_simulation() -> TwinState:
    return state_manager.reset()


@app.get('/api/simulation/status', response_model=SimulationStatus)
def simulation_status() -> SimulationStatus:
    return state_manager.status()


@app.post('/api/simulation/start', response_model=SimulationStatus)
def start_simulation() -> SimulationStatus:
    return state_manager.transition('start')


@app.post('/api/simulation/pause', response_model=SimulationStatus)
def pause_simulation() -> SimulationStatus:
    return state_manager.transition('pause')


@app.post('/api/simulation/resume', response_model=SimulationStatus)
def resume_simulation() -> SimulationStatus:
    return state_manager.transition('resume')


@app.post('/api/simulation/advance', response_model=SimulationStatus)
def advance_simulation(request: AdvanceRequest) -> SimulationStatus:
    return state_manager.advance(request.seconds)


@app.post('/api/simulation/schedule', response_model=ScheduledEvent)
def schedule_event(event: DisasterEvent) -> ScheduledEvent:
    return state_manager.schedule(event)


@app.get('/api/simulation/history', response_model=EventHistory)
def simulation_history(run_id: str | None = None) -> EventHistory:
    return state_manager.history(run_id)


@app.get('/api/simulation/runs', response_model=list[str])
def simulation_runs() -> list[str]:
    return state_manager.runs()


@app.get('/api/simulation/snapshot', response_model=StateSnapshot)
def simulation_snapshot(revision: int | None = Query(default=None, ge=1), run_id: str | None = None) -> StateSnapshot:
    return state_manager.snapshot(revision, run_id)


@app.post('/api/simulation/replay', response_model=StateSnapshot)
def replay_simulation(request: ReplayRequest) -> StateSnapshot:
    return state_manager.snapshot(request.revision, request.run_id)


@app.websocket('/ws/updates')
async def websocket_updates(websocket: WebSocket):
    await live_clients.connect(websocket, state_manager.status().model_dump(mode='json'))
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        live_clients.disconnect(websocket)
    except Exception:
        live_clients.disconnect(websocket)


@app.post('/api/decisions', response_model=DecisionStatus)
def propose_decision(action: ProposedAction, expected_revision: int = Query(..., ge=1)):
    return decision_manager.propose(action, state_manager.get_state(), expected_revision)


@app.get('/api/decisions', response_model=list[DecisionStatus])
def list_decisions():
    return decision_manager.list()


@app.get('/api/decisions/{action_id}', response_model=DecisionStatus)
def get_decision(action_id: str):
    return decision_manager.get(action_id)


@app.post('/api/decisions/{action_id}/approve', response_model=DecisionStatus)
def approve_decision(action_id: str, actor: str = Query('development_operator')):
    return decision_manager.decide(action_id, True, state_manager.get_state(), actor)


@app.post('/api/decisions/{action_id}/reject', response_model=DecisionStatus)
def reject_decision(action_id: str, actor: str = Query('development_operator')):
    return decision_manager.decide(action_id, False, state_manager.get_state(), actor)
