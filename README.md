# Agentic Digital Twin for Disaster Relief Logistics

A final-year project scaffold for exploring an agentic digital twin for
disaster-relief logistics, using the December 2023 Cyclone Michaung scenario in
Chennai and Tiruvallur as its motivating context. The sample files are
illustrative scaffolding only; they do not represent validated historical
measurements.

## Team ownership

- **Member 1:** Shared FastAPI backend, authoritative twin state, simulation
  engine, Firebase integration, and data layer.
- **Member 2:** Five AI agents, risk modelling, NetworkX routing, resource
  allocation, escalation, and LangGraph orchestration. Agent APIs will be
  mounted in Member 1's FastAPI application; there will not be a second
  production backend.
- **Member 3:** Lightweight HTML/CSS/JavaScript web dashboard, with Leaflet
  planned for mapping.

## Technology stack

- Python, FastAPI, and Uvicorn for the shared backend.
- Pydantic for data contracts and LangGraph for orchestration.
- NetworkX for graph-based routing.
- pytest and HTTPX for backend testing.
- Plain HTML, CSS, and JavaScript for the dashboard; Leaflet will be added in a
  later phase.
- Groq and python-dotenv support optional report interpretation and decision
  explanations. Firebase configuration is reserved for future integration;
  no database operations are implemented.

## Folder organization

- `backend/app/api/`: planned routers mounted by the shared FastAPI app.
- `backend/app/twin/`: planned authoritative state and simulation components.
- `backend/app/agents/`: planned agent modules and orchestration.
- `backend/app/services/`: planned Firestore and geospatial integrations.
- `backend/app/schemas/`: planned shared Pydantic contracts.
- `backend/data/`: reserved raw, processed, and sample data locations.
- `backend/tests/`: backend test location.
- `frontend/`: lightweight dashboard shell and future client scripts.
- `shared/`: illustrative state/event/agent-response JSON and the planned API
  contract.

## Planned implementation phases

1. Agree and implement shared schemas, road-graph semantics, and the
   state-manager interface.
2. Implement Member 1's authoritative twin state, simulation/event processing,
   persistence integration, and shared FastAPI application.
3. Integrate Member 2's existing agent workflow and proposed-decision APIs
   against the agreed contracts and Member 1's state-manager interface.
4. Implement Member 3's Leaflet dashboard against the shared API and update
   stream.
5. Integrate and test the end-to-end scenario, including revision,
   idempotency, and decision-approval behavior.

The Perception, Risk, Routing, Allocation, and Escalation modules are
implemented as testable components, with a compiled LangGraph workflow that
connects them. Groq is optional and limited to report extraction and
explanation of validated policy output; the explanation prompt lives
separately from Perception. The workflow creates proposals only: it does not
implement production API endpoints, authoritative state mutations, database
operations, or dashboard behavior.

Member 1 should invoke the workflow from the shared FastAPI application with a
validated twin snapshot, the active event, a read-only event-history adapter,
and the latest known revision. The returned `state_applied` field remains
false; any eventual application must be independently checked by Member 1's
state manager for revision, idempotency, and authoritative constraints.

## Local backend setup (Windows PowerShell)

From the project directory:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r backend\requirements.txt
```

The `.env.example` file contains placeholders only. Copy it to `.env` and set
locally managed `GROQ_API_KEY` and `GROQ_MODEL` values for optional agent
interpretation, decision explanations, and the AI Disaster Response Assistant.
The suggested Groq model is `openai/gpt-oss-120b`; set a model available to your
Groq account in `.env`. Never commit credentials.

The dashboard includes a chat assistant at **AI Disaster Response Assistant**.
It reads dashboard context directly from the backend and labels simulation data
as simulated. It cannot provide live alerts, confirm road safety, or dispatch
resources. The assistant endpoint is `POST /api/assistant/chat`; chat sessions
are kept in backend memory and reset when the backend restarts. No verified
document retrieval is configured yet, so the assistant does not return source
citations.

## Suggested road routes

The map's **Suggest route** control calls the backend OSRM integration, which
returns a GeoJSON road route, distance, estimated time, and turn instructions.
Use **Rank shelters** to compare candidate shelters by mapped road distance and
estimated travel time, then select a candidate and draw its route. Origins and
destinations can also be picked directly on the map. Coordinates sent to the
backend use `[longitude, latitude]` order.

The default routing host is the public OSRM demo service. Set `OSRM_BASE_URL`
in the repository `.env` file to use a compatible self-hosted service. The
current simulated road status and rainfall are not connected to OSRM, so every
route is marked as not verified for disaster safety.

To run the local dashboard, start FastAPI from `backend` with
`python -m uvicorn app.main:app --host 127.0.0.1 --port 8000`, then serve the
`frontend` directory on port `5500` (for example,
`python -m http.server 5500 --directory frontend` from the repository root).

## Deterministic agent tests

From the `backend` directory, run:

```powershell
python -m pytest
```

The routing fixture in `backend/data/sample/synthetic_routing_scenario.json` is
synthetic test data, not validated historical road or hazard data. The
escalation inventory thresholds are provisional examples, not operationally
validated policy.
