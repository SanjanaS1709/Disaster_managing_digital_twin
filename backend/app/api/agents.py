"""Agent evaluation and trace APIs mounted by the shared FastAPI application."""

import logging

from fastapi import APIRouter, HTTPException

from app.agents.orchestrator import WorkflowResponse, run_workflow
from app.schemas.contracts import AgentEvaluationRequest, DisasterEvent
from app.state_manager import state_manager

router = APIRouter(prefix="/api/agents", tags=["agents"])
logger = logging.getLogger(__name__)


@router.post("/evaluate", response_model=WorkflowResponse)
def evaluate_agents(request: AgentEvaluationRequest | DisasterEvent) -> WorkflowResponse:
    """Evaluate against live state; full caller-supplied snapshots remain supported for adapters."""
    try:
        if isinstance(request, AgentEvaluationRequest):
            return run_workflow(
                request.twin_state,
                request.event,
                current_revision=request.current_revision,
                seen_event_ids=request.seen_event_ids,
                reservations=request.reservations,
            )
        event = request
        state = state_manager.get_state()
        # The agent contract distinguishes graph node IDs from road provenance.
        # The shared twin's u/v fields are its authoritative directed endpoints.
        twin = {
            "simulation_id": state.simulation_id,
            "timestamp": state.timestamp.isoformat(),
            "revision": state.revision,
            "roads": [
                {
                    "id": road.id,
                    "source": road.u or f"{road.coordinates[0][0]},{road.coordinates[0][1]}",
                    "target": road.v or f"{road.coordinates[-1][0]},{road.coordinates[-1][1]}",
                    "status": road.status,
                    "risk_score": road.risk_score,
                    "geometry": {"type": "LineString", "coordinates": road.coordinates},
                    "distance_m": road.length_m,
                }
                for road in state.roads
            ],
            "shelters": [
                {"id": shelter.id, "name": shelter.name, "location": shelter.location,
                 "capacity": shelter.capacity, "occupancy": shelter.occupancy,
                 "demand": shelter.demand}
                for shelter in state.shelters
            ],
            "relief_units": [
                {"id": unit.id, "location": unit.location, "inventory": unit.inventory}
                for unit in state.relief_units
            ],
            "zones": [
                {"id": zone.id, "risk_score": zone.risk_score,
                 "affected_population": zone.affected_population}
                for zone in state.zones
            ],
        }
        seen_event_ids = [accepted.event_id for accepted in state_manager.get_events()]
        return run_workflow(
            twin,
            event,
            current_revision=state.revision,
            seen_event_ids=seen_event_ids,
        )
    except Exception as error:
        logger.exception("Agent workflow evaluation failed")
        raise HTTPException(
            status_code=500,
            detail={
                "code": "workflow_error",
                "message": "Agent workflow evaluation failed.",
            },
        ) from error
