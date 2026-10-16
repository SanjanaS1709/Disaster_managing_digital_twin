"""Agent evaluation and trace APIs mounted by the shared FastAPI application."""

import logging

from fastapi import APIRouter, HTTPException

from app.agents.orchestrator import WorkflowResponse, run_workflow
from app.schemas.contracts import AgentEvaluationRequest

router = APIRouter(prefix="/api/agents", tags=["agents"])
logger = logging.getLogger(__name__)


@router.post("/evaluate", response_model=WorkflowResponse)
def evaluate_agents(request: AgentEvaluationRequest) -> WorkflowResponse:
    """Return a proposal from Member 1-supplied authoritative evaluation inputs.

    The shared-backend caller must source this snapshot, current revision,
    duplicate-event IDs, and reservations from its authoritative stores. This
    endpoint does not approve an action or apply any state change.
    """
    try:
        return run_workflow(
            request.twin_state,
            request.event,
            current_revision=request.current_revision,
            seen_event_ids=request.seen_event_ids,
            reservations=request.reservations,
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
