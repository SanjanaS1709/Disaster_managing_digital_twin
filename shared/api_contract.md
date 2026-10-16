# Shared API contract

Member 1 owns the single shared FastAPI application and all authoritative twin
mutations. Member 2's agent API router is intended to be mounted into that
application, not run as a second production backend.

| Endpoint | Owner | Consumer |
|---|---|---|
| `GET /api/twin/state` | Member 1 | Members 2 and 3 |
| `GET /api/twin/geojson` | Member 1 | Member 3 |
| `POST /api/simulation/start` | Member 1 | Member 3 |
| `POST /api/simulation/pause` | Member 1 | Member 3 |
| `POST /api/simulation/reset` | Member 1 | Member 3 |
| `POST /api/simulation/events` | Member 1 | Members 2 and 3 |
| `POST /api/agents/evaluate` | Member 2 | Member 1 |
| `GET /api/agents/trace` | Member 2 | Member 3 |
| `GET /api/decisions` | Member 2 | Member 3 |
| `POST /api/decisions/{id}/approve` | Member 2 | Member 3 |
| `POST /api/decisions/{id}/reject` | Member 2 | Member 3 |
| `WS /ws/updates` | Member 1 | Member 3 |

Member 2 returns proposed decisions; it does not directly mutate authoritative
twin state. Any eventual action application must go through the agreed Member
1 state-manager interface, with revision and idempotency checks.

## `POST /api/agents/evaluate`

The route is implemented by Member 2's `backend/app/api/agents.py` router and
must be mounted by Member 1 in the shared application. It validates the body
using the existing `AgentEvaluationRequest`, `TwinStateContract`,
`DisasterEvent`, and `ReservationContract` Pydantic models, then calls the
existing LangGraph `run_workflow()` function. The route does not load,
persist, approve, or apply state. The caller must supply authoritative,
read-only inputs.

### Request schema

The body is an object with exactly these required properties; undeclared
properties are rejected:

| Property | JSON type | Contract |
|---|---|---|
| `twin_state` | object | `TwinStateContract` below |
| `event` | object | `DisasterEvent` below |
| `current_revision` | integer | Strict integer, minimum `0`; authoritative current revision observed by the caller |
| `seen_event_ids` | array of strings | Existing event IDs from the caller's event history |
| `reservations` | array of objects | Each item is `{ "allocation_id": string, "unit_id": string, "shelter_id": string, "item": string, "quantity": integer }`; strings are non-empty and `quantity` is a strict integer greater than `0` |

`TwinStateContract` has exactly these required properties:

| Property | JSON type | Constraints |
|---|---|---|
| `simulation_id` | string | Non-empty |
| `timestamp` | string | Non-empty |
| `revision` | integer | Strict integer, minimum `0` |
| `roads` | array of objects | Road contracts below |
| `shelters` | array of objects | Shelter contracts below |
| `relief_units` | array of objects | Relief-unit contracts below |
| `zones` | array of objects | Zone contracts below |

Each road object has required `id`, `source`, `status`, `risk_score`, and
`geometry` properties. `id`, `source`, and `status` are non-empty strings;
`risk_score` is a finite number in `[0, 1]`; `geometry` is a GeoJSON
`LineString` with at least two `[longitude, latitude]` coordinate pairs.
Coordinates must be finite, longitude in `[-180, 180]`, and latitude in
`[-90, 90]`.
Optional properties are `target` (non-empty string or `null`, default `null`),
`distance_m` (finite number greater than `0` or `null`, default `null`), and
`bidirectional` (boolean, default `false`).

Each shelter object has required `id`, `name`, `location`, `capacity`, and
`occupancy` properties. `id` and `name` are non-empty strings; `location` is a
graph-node string or a coordinate pair; `capacity` and `occupancy` are strict
non-negative integers, and occupancy cannot exceed capacity. Optional
properties are `demand` (object mapping non-empty item names to strict
non-negative integers, default `{}`), `priority` and `risk_score` (number in
`[0, 1]` or `null`, default `null`), and `affected_population` (non-negative
integer or `null`, default `null`).

Each relief-unit object has required `id` and `location` properties. `id` is a
non-empty string; `location` is a graph-node string or coordinate pair.
Optional `inventory` maps non-empty item names to strict non-negative integers
or `null` (unknown), and defaults to `{}`. Each zone object has required
`id` (string), `risk_score` (finite number in `[0, 1]`), and
`affected_population` (strict non-negative integer).

`DisasterEvent` has required `event_id`, `simulation_id`, `type`, `timestamp`,
and `source`; optional `target_id` is a string or `null`, and `payload` is an
object defaulting to `{}`. IDs must match
`^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$`; `type` is one of
`ROAD_FLOODED`, `ROAD_REOPENED`, `SHELTER_OVER_CAPACITY`,
`RESOURCE_SHORTAGE`, `RAINFALL_INCREASE`, or `UNKNOWN`. Required strings must
be non-empty. `target_id`, when supplied, must match the same ID pattern.
`payload` is an arbitrary JSON object; the workflow validates event-specific
content. All request models reject undeclared properties.

`current_revision` is intentionally distinct from `twin_state.revision`: the
former is the caller's authoritative current revision, while the latter is
the snapshot revision used for planning. A mismatch is returned to escalation
policy; the caller must still check revision and idempotency before applying
any proposal.

### Response schema

A successful evaluation returns the existing `WorkflowResponse` object.
Properties with defaults are included in the JSON response:

| Property | JSON type | Description |
|---|---|---|
| `simulation_id`, `event_id` | string or `null` | Identifiers for the evaluated snapshot and event |
| `state_revision`, `current_revision` | integer or `null` | Snapshot and caller-supplied authoritative revisions |
| `workflow_status`, `outcome` | string | Workflow status and decision outcome |
| `agents` | object | Keys `perception`, `risk`, `routing`, `allocation`, and `escalation`; each value has required string `status` and `reason` |
| `perception_result` | object or `null` | `status` (`validated`, `duplicate`, `rejected`, or `uncertain`), nullable `event`, `change_records`, `reevaluate_agents`, `uncertainties`, and `errors` |
| `risk_result` | object or `null` | `target_id`, nullable `risk_score`, `level`, `contributing_factors`, `missing_indicators`, `explanation`, and `complete` |
| `routing_result` | array | Each item has `unit_id`, `shelter_id`, and `result` (a `RouteResult`) |
| `allocation_result` | object or `null` | Allocation ID, shelter priorities, proposals, unmet demand, remaining inventory, missing inventory, and occupant-transfer proposals |
| `proposed_actions` | array | `AllocationProposal` objects with `allocation_id`, `unit_id`, `shelter_id`, `item`, positive integer `quantity`, `route_road_ids`, literal `status: "proposed"`, and `reason` |
| `escalation_result` | object or `null` | Decision ID, simulation ID, status (`AUTO_APPROVED`, `PENDING_APPROVAL`, or `BLOCKED`), `requires_approval`, `rule_ids`, explanation, and literal `state_applied: false` |
| `requires_approval` | boolean | Whether human approval is required |
| `rejection_reasons`, `rule_ids`, `missing_data_warnings`, `errors` | arrays of strings | Policy explanations, rule identifiers, data warnings, and workflow errors |
| `agent_trace` | array | Each item has `agent`, `status`, `summary`, and optional nullable `event_id`, `simulation_id`, and `error` |
| `explanation` | string | Human-readable outcome explanation |
| `state_applied` | boolean | Always `false`; an approval outcome never applies state |

Nested objects retain the exact fields and validation rules of their existing
Pydantic result contracts (`PerceptionResult`, `RiskAssessment`, `WorkflowRoute`,
`RouteResult`, `AllocationResult`, `EscalationResult`, and
`AgentTraceEntry`), detailed here:

- `PerceptionResult`: required `status` (literal `validated`, `duplicate`,
  `rejected`, or `uncertain`), nullable `event`, `change_records`,
  `reevaluate_agents` (strings from `risk`, `routing`, `allocation`,
  `escalation`), `uncertainties`, and `errors`. Each change record has
  `entity_type` (`road`, `shelter`, `relief_unit`, `zone`, or `simulation`),
  nullable `entity_id`, `field`, arbitrary JSON `reported_value`, and string
  array `evidence`.
- `RiskAssessment`: required `target_id`, nullable `risk_score` (finite number
  in `[0, 1]`), `level` (`LOW`, `MODERATE`, `HIGH`, `CRITICAL`, or
  `UNAVAILABLE`), `contributing_factors`, `missing_indicators`, `explanation`,
  and `complete`. Each contributing factor has `value`, `configured_weight`,
  `effective_weight`, and `weighted_contribution`, finite numbers in `[0, 1]`.
- `WorkflowRoute`: required `unit_id`, `shelter_id`, and `result`.
  `RouteResult` requires `reachable`; it also contains nullable `source`,
  `target`, `route_geometry`, `total_cost`, and `failure_reason`, plus
  `road_ids` and `nodes` string arrays. A reachable route must have endpoint
  nodes, a cost, and a complete path; an unreachable route must have a reason
  and no partial path.
- `AllocationResult`: required `evaluation_id`, `shelter_priorities`,
  `proposals`, `unmet_demand`, `inventory_remaining`, `missing_inventory`,
  and `occupant_transfers`. A shelter priority has `shelter_id`, `score` in
  `[0, 1]`, `contributing_factors`, `missing_inputs`, and `explanation`.
  Unmet demand has `shelter_id`, `item`, positive integer `quantity`,
  `missing_inventory_units`, and `reason`. Occupant-transfer proposals have
  `transfer_id`, `source_shelter_id`, `destination_shelter_id`,
  `requested_quantity`, non-negative `proposed_quantity`, `route_road_ids`,
  `status` (`proposed` or `unavailable`), and `reason`. Inventory maps unit
  IDs to item quantities, where each quantity is a non-negative integer or
  `null`.
- `EscalationResult`: required `decision_id`, `simulation_id`, `status`,
  `requires_approval`, `rule_ids`, `explanation`, and literal
  `state_applied: false`.
- Each `agent_trace` entry has required `agent`, `status`, and `summary`, and
  nullable `event_id`, `simulation_id`, and `error`.

A workflow-level failure reported in the response (`outcome: "ERROR"` with
`errors`) is still a structured `200` evaluation. Only an exception escaping
`run_workflow()` is an HTTP `500`.

### HTTP errors

| Status | Response | Meaning |
|---|---|---|
| `422 Unprocessable Entity` | `{"detail": [{"loc": [...], "msg": "...", "type": "..."}]}` | Request body fails Pydantic validation; `detail` contains FastAPI/Pydantic validation errors |
| `500 Internal Server Error` | `{"detail": {"code": "workflow_error", "message": "Agent workflow evaluation failed."}}` | `run_workflow()` raised an exception; details are logged server-side |

### Example request

```json
{
  "twin_state": {
    "simulation_id": "SIM-MICHAUNG-SAMPLE",
    "timestamp": "2023-12-04T12:00:00Z",
    "revision": 1,
    "roads": [
      {
        "id": "R001",
        "source": "N001",
        "target": "N002",
        "status": "open",
        "risk_score": 0.35,
        "geometry": {
          "type": "LineString",
          "coordinates": [[80.2707, 13.0827], [80.281, 13.091]]
        }
      }
    ],
    "shelters": [
      {
        "id": "S001",
        "name": "Sample Relief Shelter",
        "location": [80.281, 13.091],
        "capacity": 500,
        "occupancy": 120,
        "demand": {"water_liters": 800, "food_rations": 300}
      }
    ],
    "relief_units": [
      {
        "id": "U001",
        "location": [80.2707, 13.0827],
        "inventory": {"water_liters": 1000, "food_rations": 400}
      }
    ],
    "zones": [
      {"id": "Z001", "risk_score": 0.6, "affected_population": 2500}
    ]
  },
  "event": {
    "event_id": "EVT-REOPENED",
    "simulation_id": "SIM-MICHAUNG-SAMPLE",
    "type": "ROAD_REOPENED",
    "timestamp": "2023-12-04T12:20:00Z",
    "target_id": "R001",
    "payload": {
      "reported_status": "open",
      "risk_indicators": {
        "flood_severity": 0.1,
        "rainfall_indicator": 0.1,
        "elevation_risk": 0.1,
        "infrastructure_vulnerability": 0.1,
        "population_exposure": 0.1
      }
    },
    "source": "sample_operator_report"
  },
  "current_revision": 1,
  "seen_event_ids": [],
  "reservations": []
}
```

### Mounting in the shared application

`backend/app/main.py` is reserved for Member 1's FastAPI application and is
intentionally unchanged. Member 1 should import and mount this router in the
existing application (do not instantiate another `FastAPI` app):

```python
from app.api.agents import router as agents_router

app.include_router(agents_router)
```

## Unresolved contract choices

- Routing requires an explicit road `target` node in addition to `source`.
  `target` is optional for backward-compatible reading of earlier records, but
  a road without it is not added to a graph. The first and last GeoJSON
  LineString coordinates are interpreted as the `source` and `target`
  endpoints, respectively.
- Roads are directed from `source` to `target` unless the optional
  `bidirectional` field is explicitly `true`. Confirm that this matches the
  eventual road-network source before using production data.
- Shelter and relief-unit `location` values are sample GeoJSON-style coordinate
  pairs in `[longitude, latitude]` order, or may be graph-node IDs. Coordinate
  locations map only when they exactly equal a validated road endpoint
  coordinate; proximity alone never creates a graph connection. Production
  data may require an explicit trusted location-to-node mapping.
- Road `distance_m`, when supplied, is used as the cost distance. Otherwise
  routing sums haversine distances along the GeoJSON LineString. The risk
  coefficient is an experimental default, not a calibrated value.
- Decision identifiers, approval/rejection payloads, concurrency behavior, and
  idempotency-key format still need agreement.

The road IDs in `sample_agent_response.json` illustrate a proposed-action
shape only. `R002` is absent from `sample_twin_state.json`, and no connectivity
is defined, so that example route is not a valid or verified route. Proposed
road IDs must be checked against real connected graph edges before use.

## Deterministic agent MVP assumptions

- Risk inputs are explicitly normalized to `[0, 1]` by their data provider;
  the Risk Agent does not derive or fabricate normalization from raw rainfall,
  elevation, infrastructure, or population measurements. The provisional
  weights are flood `0.35`, rainfall `0.25`, elevation `0.15`,
  infrastructure vulnerability `0.15`, and population exposure `0.10`.
  Missing indicators remain listed; available weights are renormalized. If
  none of the available indicators has positive configured weight, the result
  is `UNAVAILABLE`. Category thresholds (`0.25`, `0.50`, `0.75`) are
  provisional and need evaluation.
- Routing uses Dijkstra with `distance * (1 + 5.0 * risk_score)`. Explicit
  `distance_m` takes precedence; otherwise a haversine distance is calculated
  from each LineString segment. The coefficient is experimental, not
  calibrated. Only normalized status `open` is traversable; `blocked`,
  `flooded`, and unknown statuses are excluded.
- Allocation priority defaults to normalized weighted factors:
  explicit priority `0.40`, risk score `0.25`, affected population `0.15`,
  relative unmet demand `0.10`, and route feasibility `0.10`. Population and
  demand are normalized relative to the maximum among shelters in that
  evaluation. Missing explicit priority, risk, or population inputs are
  reported, not treated as observed zeroes; remaining available weights are
  renormalized. Allocation IDs are stable for the same evaluation ID, unit,
  shelter, and item. Callers must pass existing commitments to deduct them;
  persistence and authoritative reservation remain Member 1's responsibility.
- Supply items and occupant transfers are separate. Occupant transfer
  requests are bounded by projected source occupancy and destination capacity
  and must carry a connected route matching the shelter endpoints. These are
  proposals only and do not update shelter occupancy.

## Perception and escalation integration

- Member 1 supplies a structured event, active `simulation_id`, known entity
  IDs (or a validated twin snapshot), and a read-only event-history adapter to
  the Perception Agent. The agent rejects malformed IDs, simulation mismatches,
  unknown or wrong-kind targets, malformed event payloads, and duplicate
  event IDs. The event-history implementation remains part of Member 1's
  authoritative event stream; the agent keeps no event database.
- A validated perception result contains proposed change records and
  downstream reevaluation names only. Member 1's state manager validates and
  applies any change using revision and idempotency checks. A reported event
  remains unverified until the system's verification process confirms it.
- Optional Groq report extraction loads `GROQ_API_KEY` and `GROQ_MODEL` from
  the repository-root `.env`. It is limited to extraction of factual claims;
  Pydantic validation and known-entity validation run before producing an
  event candidate. Failed or unconfigured interpretation returns uncertainty,
  not a fabricated event. Structured events can be validated deterministically
  without Groq.
- A separate `decision_explainer` prompt receives only a validated
  `EscalationResult`; deterministic policy results cannot be changed by the
  language model. When Groq is unconfigured or fails, the template explanation
  is returned.
- Escalation hard-blocks unreachable, disconnected, unknown-road, closed-road,
  over-inventory, and over-capacity proposals. Human approval cannot override
  those constraints. Otherwise critical risk (`>= 0.75`), high-impact
  allocations, revision mismatches, or incomplete evidence require human
  approval. Automatic approval is limited to low-risk, reversible,
  simulation-only actions with complete evidence and matching revisions.
- The example allocation policy flags an item as high impact if proposed
  quantity exceeds either 20% of supplied available inventory or 25 units.
  These thresholds, the critical-risk threshold, and the low-risk threshold are
  provisional examples, not operationally validated policy. The inventory
  snapshot supplied to escalation must represent currently available
  inventory after existing commitments.
- `AUTO_APPROVED` is an escalation policy outcome, not state application.
  Member 1's state manager must validate the current revision, idempotency,
  and authoritative constraints before applying any approved action.

## LangGraph orchestration integration

- Member 2 exposes the in-process `run_workflow` function through its
  `/api/agents/evaluate` router for Member 1 to include in the shared
  application. No second server is created.
- The `POST /api/agents/evaluate` route accepts `twin_state`,
  `event`, `current_revision`, `seen_event_ids`, and `reservations`. Member 1
  must source all five from authoritative state/event/commitment stores; the
  endpoint does not independently authenticate or fetch them.
- The workflow runs Perception, Risk, Routing, Allocation, and Escalation in
  order, with conditional skips for rejected, duplicate, uncertain, failed,
  or unreachable results. It returns structured per-agent status, outputs,
  warnings, rule identifiers, a trace, and an explanation.
- The caller supplies the current twin snapshot and event-history adapter.
  `state_revision` records the snapshot used for planning;
  `current_revision` may carry a newer revision observed by the caller so
  Escalation can flag stale proposals. Member 1 must still repeat revision and
  idempotency checks at application time to handle races.
- The workflow builds a temporary road projection for planning and never
  mutates the supplied TwinState. Unverified `ROAD_REOPENED` claims do not
  make a blocked road traversable; reported flooding is treated conservatively
  as blocked for planning. Route/allocation results are proposals;
  even `AUTO_APPROVED` does not apply an action or confirm dispatch.
- The escalation engine receives inventory after existing reservations, so
  its high-impact threshold is evaluated against currently available stock.
- Groq is not required for structured-event evaluation. Unstructured report
  interpretation is optional, and the separate explanation prompt receives
  only a validated escalation result; neither language-model call can override
  deterministic validation or policy.
