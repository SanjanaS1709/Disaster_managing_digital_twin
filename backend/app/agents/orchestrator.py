"""LangGraph orchestration for safe, non-mutating disaster decision proposals."""

from __future__ import annotations

import copy
import operator
from collections.abc import Collection, Mapping, Sequence
from typing import Annotated, TypedDict

from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.agents import allocation as allocation_agent
from app.agents import escalation as escalation_agent
from app.agents import perception as perception_agent
from app.agents import risk as risk_agent
from app.agents import routing as routing_agent
from app.agents.decision_explainer import explain_validated_decision
from app.schemas.contracts import (
    AllocationResult,
    AllocationProposal,
    DisasterEvent,
    EscalationPolicy,
    EscalationRequest,
    EscalationResult,
    PerceptionResult,
    PriorityConfig,
    RiskAssessment,
    RiskConfig,
    ReservationContract,
    ReliefUnitContract,
    RouteResult,
    TwinStateContract,
)


class AgentTraceEntry(BaseModel):
    """One concise node execution or explicit skip record."""

    model_config = ConfigDict(extra="forbid")

    agent: str
    status: str
    summary: str
    event_id: str | None = None
    simulation_id: str | None = None
    error: str | None = None


class WorkflowAgentResult(BaseModel):
    """Per-agent status surfaced to the API owner and dashboard."""

    status: str
    reason: str


class WorkflowRoute(BaseModel):
    """A route result tied to the unit and destination it plans for."""

    unit_id: str
    shelter_id: str
    result: RouteResult


class WorkflowResponse(BaseModel):
    """Serialized proposal evaluation; completion does not mean dispatch or application."""

    model_config = ConfigDict(extra="forbid")

    simulation_id: str | None
    event_id: str | None
    state_revision: int | None
    current_revision: int | None
    workflow_status: str
    outcome: str
    agents: dict[str, WorkflowAgentResult]
    perception_result: PerceptionResult | None = None
    risk_result: RiskAssessment | None = None
    routing_result: list[WorkflowRoute] = Field(default_factory=list)
    allocation_result: AllocationResult | None = None
    proposed_actions: list[AllocationProposal] = Field(default_factory=list)
    escalation_result: EscalationResult | None = None
    requires_approval: bool = False
    rejection_reasons: list[str] = Field(default_factory=list)
    rule_ids: list[str] = Field(default_factory=list)
    missing_data_warnings: list[str] = Field(default_factory=list)
    agent_trace: list[AgentTraceEntry] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    explanation: str
    state_applied: bool = False


class WorkflowState(TypedDict, total=False):
    """Typed LangGraph state; each node writes only its owned fields."""

    simulation_id: str
    event_id: str | None
    state_revision: int | None
    current_revision: int | None
    twin_state: object
    incoming_event: object
    event_history: object | None
    seen_event_ids: Collection[str]
    reservations: Sequence[object]
    risk_config: RiskConfig
    priority_config: PriorityConfig
    escalation_policy: EscalationPolicy
    routing_risk_weight: float
    evidence_verified: bool
    simulation_only: bool
    reversible: bool
    use_llm_explanation: bool
    explanation_client: object | None
    report_client: object | None
    groq_api_key: str | None
    groq_model: str | None
    perception_result: PerceptionResult
    validated_twin: TwinStateContract
    risk_result: RiskAssessment
    routing_result: list[WorkflowRoute]
    allocation_result: AllocationResult
    escalation_result: EscalationResult
    errors: Annotated[list[str], operator.add]
    agent_trace: Annotated[list[AgentTraceEntry], operator.add]
    final_response: WorkflowResponse
    report_source: str


def _trace(
    state: WorkflowState,
    agent: str,
    status: str,
    summary: str,
    error: str | None = None,
) -> list[AgentTraceEntry]:
    return [
        AgentTraceEntry(
            agent=agent,
            status=status,
            summary=summary,
            event_id=state.get("event_id"),
            simulation_id=state.get("simulation_id"),
            error=error,
        )
    ]


def perception_node(state: WorkflowState) -> dict[str, object]:
    """Validate the twin snapshot/event or invoke optional untrusted report extraction."""
    try:
        twin = (
            state["twin_state"]
            if isinstance(state["twin_state"], TwinStateContract)
            else TwinStateContract.model_validate(state["twin_state"])
        )
    except (ValidationError, KeyError, TypeError) as error:
        message = f"Twin snapshot validation failed: {error}"
        return {
            "perception_result": PerceptionResult(status="rejected", errors=[message]),
            "errors": [message],
            "agent_trace": _trace(state, "perception", "REJECTED", message, message),
        }

    incoming = state["incoming_event"]
    try:
        if isinstance(incoming, Mapping) and isinstance(incoming.get("report_text"), str):
            report_text = incoming["report_text"]
            event_id = state.get("event_id") or incoming.get("event_id")
            timestamp = incoming.get("timestamp")
            source = incoming.get("source", state.get("report_source", "unstructured_report"))
            if not isinstance(event_id, str) or not isinstance(timestamp, str):
                result = PerceptionResult(
                    status="uncertain",
                    uncertainties=[
                        "Unstructured report requires caller-supplied event_id and timestamp."
                    ],
                )
            else:
                result = perception_agent.interpret_report(
                    report_text,
                    event_id=event_id,
                    simulation_id=twin.simulation_id,
                    timestamp=timestamp,
                    source=str(source),
                    known_entities=twin,
                    event_history=state.get("event_history"),
                    seen_event_ids=state.get("seen_event_ids", ()),
                    api_key=state.get("groq_api_key"),
                    model=state.get("groq_model"),
                    client=state.get("report_client"),
                )
        else:
            result = perception_agent.validate_event(
                incoming,
                expected_simulation_id=twin.simulation_id,
                known_entities=twin,
                event_history=state.get("event_history"),
                seen_event_ids=state.get("seen_event_ids", ()),
                evidence_verified=state.get("evidence_verified", False),
            )
    except Exception as error:
        message = f"Perception agent failed: {type(error).__name__}: {error}"
        return {
            "validated_twin": twin,
            "perception_result": PerceptionResult(status="rejected", errors=[message]),
            "errors": [message],
            "agent_trace": _trace(state, "perception", "ERROR", message, message),
        }

    statuses = {
        "validated": "SUCCESS",
        "duplicate": "DUPLICATE",
        "rejected": "REJECTED",
        "uncertain": "UNCERTAIN",
    }
    summary = (
        f"Event validation {result.status}; "
        f"{len(result.change_records)} proposed change record(s)."
    )
    errors = result.errors if result.status == "rejected" else []
    return {
        "validated_twin": twin,
        "perception_result": result,
        "errors": errors,
        "agent_trace": _trace(
            state,
            "perception",
            statuses[result.status],
            summary,
            "; ".join(errors) if errors else None,
        ),
    }


def _risk_inputs(event: DisasterEvent) -> Mapping[str, float | None]:
    indicators = event.payload.get("risk_indicators", {})
    if not isinstance(indicators, Mapping):
        raise ValueError("event payload risk_indicators must be an object")
    return indicators


def risk_node(state: WorkflowState) -> dict[str, object]:
    """Run the deterministic Risk Agent only on explicitly supplied indicators."""
    result = state["perception_result"]
    assert result.event is not None
    try:
        risk_result = risk_agent.assess_risk(
            target_id=result.event.target_id or result.event.simulation_id,
            indicators=_risk_inputs(result.event),
            config=state.get("risk_config"),
        )
    except Exception as error:
        message = f"Risk agent failed: {type(error).__name__}: {error}"
        return {
            "errors": [message],
            "agent_trace": _trace(state, "risk", "ERROR", message, message),
        }
    if risk_result.level == "UNAVAILABLE":
        status = "UNCERTAIN"
        summary = "Risk inputs were unavailable; no numerical score was fabricated."
    elif risk_result.complete:
        status = "SUCCESS"
        summary = f"Complete risk assessment: {risk_result.level}."
    else:
        status = "UNCERTAIN"
        summary = f"Partial risk assessment: {risk_result.level}; indicators are missing."
    return {
        "risk_result": risk_result,
        "agent_trace": _trace(state, "risk", status, summary),
    }


def _planning_roads(
    twin: TwinStateContract,
    event: DisasterEvent,
    risk_result: RiskAssessment,
    evidence_verified: bool,
) -> list[dict[str, object]]:
    """Create a local planning projection; never mutate the supplied twin snapshot."""
    projected = [copy.deepcopy(road.model_dump()) for road in twin.roads]
    if event.type == "ROAD_REOPENED" and not evidence_verified:
        return projected
    if event.type not in {"ROAD_FLOODED", "ROAD_REOPENED"}:
        return projected
    for road in projected:
        if road["id"] == event.target_id:
            road["status"] = "blocked" if event.type == "ROAD_FLOODED" else "open"
            if risk_result.risk_score is not None:
                road["risk_score"] = risk_result.risk_score
            break
    return projected


def _available_relief_units(
    twin: TwinStateContract,
    reservations: Sequence[object],
) -> list[ReliefUnitContract]:
    """Subtract authoritative commitments before evaluating allocation impact."""
    parsed = [
        reservation
        if isinstance(reservation, ReservationContract)
        else ReservationContract.model_validate(reservation)
        for reservation in reservations
    ]
    remaining_units: list[ReliefUnitContract] = []
    for unit in twin.relief_units:
        inventory = dict(unit.inventory)
        for reservation in parsed:
            if reservation.unit_id != unit.id:
                continue
            available = inventory.get(reservation.item)
            if available is None or available < reservation.quantity:
                raise ValueError(
                    f"commitment {reservation.allocation_id!r} exceeds known inventory "
                    f"for {unit.id}/{reservation.item}"
                )
            inventory[reservation.item] = available - reservation.quantity
        remaining_units.append(
            ReliefUnitContract.model_validate(
                {**unit.model_dump(), "inventory": inventory}
            )
        )
    return remaining_units


def routing_node(state: WorkflowState) -> dict[str, object]:
    """Plan each relief-unit/shelter pair against the read-only hazard projection."""
    event = state["perception_result"].event
    assert event is not None
    try:
        roads = _planning_roads(
            state["validated_twin"],
            event,
            state["risk_result"],
            state.get("evidence_verified", False),
        )
        routes = [
            WorkflowRoute(
                unit_id=unit.id,
                shelter_id=shelter.id,
                result=routing_agent.route_between(
                    roads,
                    unit.location,
                    shelter.location,
                    risk_weight=state.get("routing_risk_weight", 5.0),
                ),
            )
            for unit in state["validated_twin"].relief_units
            for shelter in state["validated_twin"].shelters
        ]
    except Exception as error:
        message = f"Routing agent failed: {type(error).__name__}: {error}"
        return {
            "errors": [message],
            "agent_trace": _trace(state, "routing", "ERROR", message, message),
        }
    reachable = sum(route.result.reachable for route in routes)
    status = "SUCCESS" if reachable else "UNREACHABLE"
    summary = (
        f"Found {reachable} reachable route(s) across {len(routes)} unit/shelter pair(s)."
        if routes
        else "No relief-unit/shelter pairs exist in the supplied snapshot."
    )
    return {
        "routing_result": routes,
        "agent_trace": _trace(state, "routing", status, summary),
    }


def allocation_node(state: WorkflowState) -> dict[str, object]:
    """Propose allocations using only reachable routes and current supplied snapshots."""
    try:
        routes = {
            (candidate.unit_id, candidate.shelter_id): candidate.result
            for candidate in state["routing_result"]
            if candidate.result.reachable
        }
        result = allocation_agent.allocate_resources(
            evaluation_id=state.get("event_id") or "workflow-evaluation",
            relief_units=state["validated_twin"].relief_units,
            shelters=state["validated_twin"].shelters,
            routes=routes,
            reservations=state.get("reservations", ()),
            config=state.get("priority_config"),
        )
    except Exception as error:
        message = f"Allocation agent failed: {type(error).__name__}: {error}"
        return {
            "errors": [message],
            "agent_trace": _trace(state, "allocation", "ERROR", message, message),
        }
    status = "PROPOSED" if result.proposals or result.occupant_transfers else "COMPLETED"
    summary = (
        f"Created {len(result.proposals)} supply proposal(s) and "
        f"{len(result.occupant_transfers)} occupant-transfer proposal(s)."
    )
    return {
        "allocation_result": result,
        "agent_trace": _trace(state, "allocation", status, summary),
    }


def escalation_node(state: WorkflowState) -> dict[str, object]:
    """Evaluate a proposal against hard constraints and configured approval policy."""
    try:
        twin = state["validated_twin"]
        risk_result = state.get("risk_result")
        risk_score = risk_result.risk_score if risk_result is not None else None
        allocation_result = state.get("allocation_result")
        reachable_routes = [
            candidate.result
            for candidate in state.get("routing_result", [])
            if candidate.result.reachable
        ]
        if allocation_result is not None and (
            allocation_result.proposals or allocation_result.occupant_transfers
        ):
            request = EscalationRequest(
                decision_id=state.get("event_id") or f"decision-{twin.revision}",
                simulation_id=twin.simulation_id,
                action_kind="allocation",
                expected_revision=twin.revision,
                current_revision=(
                    state["current_revision"]
                    if state.get("current_revision") is not None
                    else twin.revision
                ),
                risk_score=risk_score,
                allocation_result=allocation_result,
                roads=_planning_roads(
                    twin,
                    state["perception_result"].event,
                    risk_result,
                    state.get("evidence_verified", False),
                ),
                relief_units=_available_relief_units(
                    twin,
                    state.get("reservations", ()),
                ),
                shelters=twin.shelters,
                evidence_complete=(
                    state.get("evidence_verified", False)
                    and risk_result is not None
                    and risk_result.complete
                ),
                reversible=state.get("reversible", False),
                simulation_only=state.get("simulation_only", False),
            )
        elif reachable_routes:
            request = EscalationRequest(
                decision_id=state.get("event_id") or f"decision-{twin.revision}",
                simulation_id=twin.simulation_id,
                action_kind="route",
                expected_revision=twin.revision,
                current_revision=(
                    state["current_revision"]
                    if state.get("current_revision") is not None
                    else twin.revision
                ),
                risk_score=risk_score,
                route=reachable_routes[0],
                roads=_planning_roads(
                    twin,
                    state["perception_result"].event,
                    risk_result,
                    state.get("evidence_verified", False),
                ),
                evidence_complete=(
                    state.get("evidence_verified", False)
                    and risk_result is not None
                    and risk_result.complete
                ),
                reversible=state.get("reversible", False),
                simulation_only=state.get("simulation_only", False),
            )
        else:
            unreachable = next(
                (
                    candidate.result
                    for candidate in state.get("routing_result", [])
                    if not candidate.result.reachable
                ),
                None,
            )
            if unreachable is None:
                message = "Escalation skipped because no route or allocation candidate exists."
                return {
                    "agent_trace": _trace(
                        state,
                        "escalation",
                        "SKIPPED",
                        message,
                    )
                }
            request = EscalationRequest(
                decision_id=state.get("event_id") or f"decision-{twin.revision}",
                simulation_id=twin.simulation_id,
                action_kind="route",
                expected_revision=twin.revision,
                current_revision=(
                    state["current_revision"]
                    if state.get("current_revision") is not None
                    else twin.revision
                ),
                risk_score=risk_score,
                route=unreachable,
                roads=_planning_roads(
                    twin,
                    state["perception_result"].event,
                    risk_result,
                    state.get("evidence_verified", False),
                ),
                evidence_complete=False,
                simulation_only=state.get("simulation_only", False),
            )
        result = escalation_agent.evaluate_escalation(
            request,
            policy=state.get("escalation_policy"),
        )
    except Exception as error:
        message = f"Escalation engine failed: {type(error).__name__}: {error}"
        return {
            "errors": [message],
            "agent_trace": _trace(state, "escalation", "ERROR", message, message),
        }
    return {
        "escalation_result": result,
        "agent_trace": _trace(
            state,
            "escalation",
            result.status,
            result.explanation,
        ),
    }


def _conditional_after_perception(state: WorkflowState) -> str:
    result = state.get("perception_result")
    if result is None or result.status != "validated":
        return "finalize"
    return "risk"


def _conditional_after_risk(state: WorkflowState) -> str:
    return "routing" if state.get("risk_result") is not None else "finalize"


def _conditional_after_routing(state: WorkflowState) -> str:
    if state.get("routing_result") is None:
        return "finalize"
    if any(route.result.reachable for route in state["routing_result"]):
        return "allocation"
    if state["routing_result"]:
        return "escalation"
    return "finalize"


def _conditional_after_allocation(state: WorkflowState) -> str:
    return "escalation" if state.get("allocation_result") is not None else "finalize"


def _format_response(state: WorkflowState) -> WorkflowResponse:
    result = state.get("perception_result")
    risk = state.get("risk_result")
    routes = state.get("routing_result", [])
    allocation = state.get("allocation_result")
    escalation = state.get("escalation_result")
    errors = list(state.get("errors", []))

    summaries: dict[str, WorkflowAgentResult] = {}
    known_agents = ("perception", "risk", "routing", "allocation", "escalation")
    trace_by_agent = {
        entry.agent: entry for entry in state.get("agent_trace", [])
    }
    skipped_reasons = {
        "perception": "Perception did not complete.",
        "risk": "No validated event was available.",
        "routing": "Risk assessment did not complete.",
        "allocation": "No feasible route was available or routing did not complete.",
        "escalation": "No valid proposal was available for policy evaluation.",
    }
    for agent in known_agents:
        entry = trace_by_agent.get(agent)
        if entry is None:
            summaries[agent] = WorkflowAgentResult(
                status="SKIPPED",
                reason=skipped_reasons[agent],
            )
        else:
            summaries[agent] = WorkflowAgentResult(
                status=entry.status,
                reason=entry.summary,
            )

    execution_error = any(
        entry.status == "ERROR" for entry in state.get("agent_trace", [])
    )
    if execution_error:
        outcome = "ERROR"
    elif result is None:
        outcome = "REJECTED"
    elif result.status == "duplicate":
        outcome = "DUPLICATE"
    elif result.status == "rejected":
        outcome = "REJECTED"
    elif result.status == "uncertain":
        outcome = "UNCERTAIN"
    elif escalation is not None:
        outcome = escalation.status
    elif routes and not any(route.result.reachable for route in routes):
        outcome = "UNREACHABLE"
    elif allocation is not None and (allocation.proposals or allocation.occupant_transfers):
        outcome = "PROPOSED"
    else:
        outcome = "COMPLETED"

    warnings: list[str] = []
    if result is not None:
        warnings.extend(result.uncertainties)
    if risk is not None:
        warnings.extend(
            f"Missing risk indicator: {indicator}"
            for indicator in risk.missing_indicators
        )
    if allocation is not None:
        for unit_id, items in allocation.missing_inventory.items():
            warnings.extend(
                f"Inventory for {item!r} is unknown on relief unit {unit_id}."
                for item in items
            )

    reasons = list(result.errors if result is not None else [])
    if escalation is not None and escalation.status == "BLOCKED":
        reasons.append(escalation.explanation)
    elif result is not None and result.status in {"rejected", "duplicate"}:
        reasons.extend(result.errors)

    if escalation is not None and state.get("use_llm_explanation", False):
        explanation = explain_validated_decision(
            escalation,
            client=state.get("explanation_client"),
        )
    elif escalation is not None:
        explanation = escalation.explanation
    elif outcome == "UNREACHABLE":
        explanation = (
            "Workflow evaluation completed, but no feasible route was found. "
            "No allocation or dispatch was produced."
        )
    elif outcome == "PROPOSED":
        explanation = (
            f"Workflow evaluation completed with {len(allocation.proposals)} proposed "
            "allocation(s). No state was changed and no delivery is confirmed."
        )
    elif outcome in {"REJECTED", "DUPLICATE", "UNCERTAIN", "ERROR"}:
        explanation = f"Workflow outcome: {outcome}. " + (
            "; ".join(errors or reasons or warnings) or "No operational action was proposed."
        )
    else:
        explanation = "Workflow evaluation completed. No operational action was applied."

    trace = list(state.get("agent_trace", []))
    for agent in known_agents:
        if agent not in trace_by_agent:
            trace.append(
                AgentTraceEntry(
                    agent=agent,
                    status="SKIPPED",
                    summary=skipped_reasons[agent],
                    event_id=state.get("event_id"),
                    simulation_id=state.get("simulation_id"),
                )
            )
    return WorkflowResponse(
        simulation_id=state.get("simulation_id"),
        event_id=state.get("event_id"),
        state_revision=state.get("state_revision"),
        current_revision=state.get("current_revision"),
        workflow_status="COMPLETED",
        outcome=outcome,
        agents=summaries,
        perception_result=result,
        risk_result=risk,
        routing_result=routes,
        allocation_result=allocation,
        proposed_actions=allocation.proposals if allocation is not None else [],
        escalation_result=escalation,
        requires_approval=escalation.requires_approval if escalation else False,
        rejection_reasons=list(dict.fromkeys(reasons)),
        rule_ids=escalation.rule_ids if escalation else [],
        missing_data_warnings=list(dict.fromkeys(warnings)),
        agent_trace=trace,
        errors=errors,
        explanation=explanation,
        state_applied=False,
    )


def finalize_node(state: WorkflowState) -> dict[str, object]:
    """Build the structured response and explicit skipped-node trace entries."""
    response = _format_response(state)
    existing = {entry.agent for entry in state.get("agent_trace", [])}
    skipped = [
        entry
        for entry in response.agent_trace
        if entry.agent not in existing
    ]
    return {
        "agent_trace": skipped,
        "final_response": response,
    }


def build_workflow():
    """Build and compile the five-agent graph with safety-oriented branches."""
    graph = StateGraph(WorkflowState)
    graph.add_node("perception_node", perception_node)
    graph.add_node("risk_node", risk_node)
    graph.add_node("routing_node", routing_node)
    graph.add_node("allocation_node", allocation_node)
    graph.add_node("escalation_node", escalation_node)
    graph.add_node("finalize_node", finalize_node)

    graph.add_edge(START, "perception_node")
    graph.add_conditional_edges(
        "perception_node",
        _conditional_after_perception,
        {"risk": "risk_node", "finalize": "finalize_node"},
    )
    graph.add_conditional_edges(
        "risk_node",
        _conditional_after_risk,
        {"routing": "routing_node", "finalize": "finalize_node"},
    )
    graph.add_conditional_edges(
        "routing_node",
        _conditional_after_routing,
        {
            "allocation": "allocation_node",
            "escalation": "escalation_node",
            "finalize": "finalize_node",
        },
    )
    graph.add_conditional_edges(
        "allocation_node",
        _conditional_after_allocation,
        {"escalation": "escalation_node", "finalize": "finalize_node"},
    )
    graph.add_edge("escalation_node", "finalize_node")
    graph.add_edge("finalize_node", END)
    return graph.compile()


def run_workflow(
    twin_state: TwinStateContract | Mapping[str, object],
    incoming_event: DisasterEvent | Mapping[str, object],
    *,
    event_history: perception_agent.EventHistory | None = None,
    seen_event_ids: Collection[str] = (),
    reservations: Sequence[object] = (),
    risk_config: RiskConfig | None = None,
    priority_config: PriorityConfig | None = None,
    escalation_policy: EscalationPolicy | None = None,
    routing_risk_weight: float = 5.0,
    current_revision: int | None = None,
    evidence_verified: bool = False,
    simulation_only: bool = False,
    reversible: bool = False,
    use_llm_explanation: bool = False,
    explanation_client: object | None = None,
    report_client: object | None = None,
    groq_api_key: str | None = None,
    groq_model: str | None = None,
    report_source: str = "unstructured_report",
) -> WorkflowResponse:
    """Run one retry-safe evaluation and return proposal data without state mutation."""
    try:
        twin = (
            twin_state
            if isinstance(twin_state, TwinStateContract)
            else TwinStateContract.model_validate(twin_state)
        )
        initial_event_id = (
            incoming_event.event_id
            if isinstance(incoming_event, DisasterEvent)
            else incoming_event.get("event_id")
            if isinstance(incoming_event, Mapping)
            else None
        )
        initial_simulation_id = twin.simulation_id
        initial_revision: int | None = twin.revision
    except (ValidationError, AttributeError, TypeError):
        initial_event_id = (
            incoming_event.get("event_id")
            if isinstance(incoming_event, Mapping)
            else None
        )
        initial_simulation_id = (
            twin_state.get("simulation_id")
            if isinstance(twin_state, Mapping)
            else None
        )
        initial_revision = (
            twin_state.get("revision")
            if isinstance(twin_state, Mapping)
            and isinstance(twin_state.get("revision"), int)
            else None
        )

    initial: WorkflowState = {
        "simulation_id": initial_simulation_id,
        "event_id": initial_event_id,
        "state_revision": initial_revision,
        "current_revision": current_revision if current_revision is not None else initial_revision,
        "twin_state": twin_state,
        "incoming_event": incoming_event,
        "event_history": event_history,
        "seen_event_ids": seen_event_ids,
        "reservations": reservations,
        "risk_config": risk_config or RiskConfig(),
        "priority_config": priority_config or PriorityConfig(),
        "escalation_policy": escalation_policy or EscalationPolicy(),
        "routing_risk_weight": routing_risk_weight,
        "evidence_verified": evidence_verified,
        "simulation_only": simulation_only,
        "reversible": reversible,
        "use_llm_explanation": use_llm_explanation,
        "explanation_client": explanation_client,
        "report_client": report_client,
        "groq_api_key": groq_api_key,
        "groq_model": groq_model,
        "errors": [],
        "agent_trace": [],
        "report_source": report_source,
    }
    final_state = build_workflow().invoke(initial)
    response = final_state["final_response"]
    # Return a detached contract value so callers cannot mutate graph state.
    return WorkflowResponse.model_validate(response.model_dump())
