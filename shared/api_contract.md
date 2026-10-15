# Shared API contract (planned)

This document records the planned integration surface; it does not define or
implement production endpoints. Member 1 owns the single shared FastAPI
application and all authoritative twin mutations. Member 2's agent APIs will
be mounted into that application, not run as a second production backend.

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

- Member 2 exposes the in-process `run_workflow` function for Member 1 to call
  from the shared application. The `/api/agents/evaluate` route is defined in
  Member 2's router, but it is not mounted until Member 1 includes that router
  in the shared application. No second server is created.
- The planned `POST /api/agents/evaluate` router accepts `twin_state`,
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
