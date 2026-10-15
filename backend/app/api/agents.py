"""Agent evaluation and trace APIs mounted by the shared FastAPI application."""

from fastapi import APIRouter

from app.agents.orchestrator import WorkflowResponse, run_workflow
from app.schemas.contracts import AgentEvaluationRequest

router = APIRouter(prefix="/api/agents", tags=["agents"])


@router.post("/evaluate", response_model=WorkflowResponse)
def evaluate_agents(request: AgentEvaluationRequest) -> WorkflowResponse:
    """Return a proposal from Member 1-supplied authoritative evaluation inputs.

    The shared-backend caller must source this snapshot, revision, event
    history, and reservation list from its authoritative stores. This endpoint
    does not approve an action or apply any state change.
    """
    return run_workflow(
        request.twin_state,
        request.event,
        current_revision=request.current_revision,
        seen_event_ids=request.seen_event_ids,
        reservations=request.reservations,
    )
