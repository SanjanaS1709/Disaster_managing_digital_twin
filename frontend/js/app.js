import { getEvents, getGeoJSON, getStatus, getTwin, postJSON, websocketURL } from './api.js';
import { renderDashboard, setConnection } from './dashboard.js';
import { renderAgentEvaluation, resetAgentPanel } from './agents.js';
import { initMap, renderTwinMap } from './map.js';
import { configureEventForm, mountOperationsPanel, readEventPayload, renderEventHistory, renderSimulationStatus, showIntegrationNotice } from './simulation.js';

let twinState;
let simulationStatus;
let pendingEvent;
let pendingRevision;
let mapReady = false;
let socket;
let refreshTimer;
let heartbeat;
let formSignature;

const setText = (selector, value) => {
  const node = document.querySelector(selector);
  if (node) node.textContent = value;
};
const formatError = error => error instanceof Error ? error.message : String(error);

function updateClock() {
  setText('#header-time', `${new Intl.DateTimeFormat('en-IN', { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false, timeZone: 'Asia/Kolkata' }).format(new Date())} IST`);
}

async function refreshDashboard() {
  try {
    const [state, status, events, geojson] = await Promise.all([getTwin(), getStatus(), getEvents(), getGeoJSON()]);
    twinState = state;
    simulationStatus = status;
    renderDashboard(state, status, events);
    renderSimulationStatus(status, state);
    renderEventHistory(events, state);
    const nextFormSignature = `${state.revision}:${document.getElementById('live-event-type').value}`;
    if (formSignature !== nextFormSignature) {
      if (pendingEvent && pendingRevision !== state.revision) {
        pendingEvent = undefined;
        pendingRevision = undefined;
        document.getElementById('apply-event').disabled = true;
        showIntegrationNotice('Twin state changed after evaluation. Run the agents again before applying an event.');
      }
      configureEventForm(state);
      formSignature = nextFormSignature;
    }
    try {
      if (mapReady) renderTwinMap(geojson, state);
    } catch (error) {
      const mapNode = document.getElementById('map');
      mapNode.classList.add('map-error');
      mapNode.textContent = `Map unavailable: ${formatError(error)}`;
    }
    setConnection(true);
    document.documentElement.dataset.mode = 'live';
    return true;
  } catch (error) {
    setConnection(false, 'Retrying connection');
    showIntegrationNotice(formatError(error), true);
    if (!refreshTimer) refreshTimer = window.setTimeout(() => { refreshTimer = null; refreshDashboard(); }, 5000);
    return false;
  }
}

function buildEvent() {
  if (!twinState) throw new Error('Twin state has not loaded.');
  const type = document.getElementById('live-event-type').value;
  return {
    event_id: `fieldops-${crypto.randomUUID()}`,
    simulation_id: twinState.simulation_id,
    type,
    timestamp: twinState.timestamp,
    target_id: document.getElementById('live-event-target').value,
    payload: readEventPayload(twinState),
    source: 'simulation',
  };
}

function bindControls() {
  const section = mountOperationsPanel();
  section.querySelector('#live-event-type').addEventListener('change', () => {
    configureEventForm(twinState);
    formSignature = `${twinState?.revision}:${document.getElementById('live-event-type').value}`;
  });
  section.querySelector('#live-event-target').addEventListener('change', () => configureEventForm(twinState));
  const eventForm = section.querySelector('#live-event-form');
  const invalidateEvaluation = () => {
    if (!pendingEvent) return;
    pendingEvent = undefined;
    pendingRevision = undefined;
    section.querySelector('#apply-event').disabled = true;
    showIntegrationNotice('Event details changed. Run the agents again to evaluate this version.');
  };
  eventForm.addEventListener('input', invalidateEvaluation);
  eventForm.addEventListener('change', invalidateEvaluation);
  eventForm.addEventListener('submit', async event => {
    event.preventDefault();
    const button = section.querySelector('#evaluate-event');
    const applyButton = section.querySelector('#apply-event');
    button.disabled = true;
    button.textContent = 'Reviewing event…';
    applyButton.disabled = true;
    try {
      pendingEvent = buildEvent();
      pendingRevision = twinState.revision;
      const result = await postJSON('/api/agents/evaluate', pendingEvent);
      renderAgentEvaluation(result, twinState);
      applyButton.disabled = false;
      showIntegrationNotice('Review complete. Read the plain-language summary in Decision support before applying the event.');
    } catch (error) {
      pendingEvent = undefined;
      pendingRevision = undefined;
      showIntegrationNotice(formatError(error), true);
    } finally {
      button.disabled = false;
      button.textContent = 'Get clear review';
    }
  });
  section.querySelector('#apply-event').addEventListener('click', async event => {
    if (!pendingEvent) return;
    const button = event.currentTarget;
    button.disabled = true;
    try {
      await postJSON('/api/simulation/events', pendingEvent);
      pendingEvent = undefined;
      pendingRevision = undefined;
      resetAgentPanel();
      showIntegrationNotice('Event added to the simulation. The dashboard has been updated.');
      await refreshDashboard();
    } catch (error) {
      button.disabled = false;
      showIntegrationNotice(formatError(error), true);
    }
  });
  section.querySelectorAll('[data-sim-action]').forEach(button => button.addEventListener('click', async () => {
    const action = button.dataset.simAction;
    const routes = { start: '/api/simulation/start', pause: '/api/simulation/pause', resume: '/api/simulation/resume' };
    let actionError;
    section.querySelectorAll('[data-sim-action]').forEach(control => {
      control.dataset.pending = 'true';
      control.disabled = true;
    });
    try {
      if (action === 'advance') await postJSON('/api/simulation/advance', { seconds: 60 });
      else await postJSON(routes[action]);
      await refreshDashboard();
    } catch (error) {
      actionError = formatError(error);
      showIntegrationNotice(`Could not ${action} the simulation: ${formatError(error)}`, true);
      await refreshDashboard();
    } finally {
      section.querySelectorAll('[data-sim-action]').forEach(control => delete control.dataset.pending);
      if (simulationStatus && twinState) renderSimulationStatus(simulationStatus, twinState);
      if (actionError) {
        const help = section.querySelector('#simulation-help');
        if (help) help.textContent = `That action was not completed: ${actionError}. Current run status: ${simulationStatus?.phase || 'unknown'}.`;
      }
    }
  }));
  section.querySelector('#simulation-reset').addEventListener('click', async () => {
    if (!window.confirm('Start a new run? The current run will be archived.')) return;
    try {
      await postJSON('/api/simulation/reset');
      pendingEvent = undefined;
      resetAgentPanel();
      await refreshDashboard();
    } catch (error) { showIntegrationNotice(formatError(error), true); }
  });
}

function connectLiveUpdates() {
  try {
    socket = new WebSocket(websocketURL());
    socket.addEventListener('open', () => setConnection(true));
    socket.addEventListener('message', event => {
      try {
        const message = JSON.parse(event.data);
        if (message.type === 'STATE_CHANGED' || message.type === 'SYNC') refreshDashboard();
      } catch { /* Ignore malformed update messages and keep the REST view. */ }
    });
    socket.addEventListener('close', () => {
      setConnection(false, 'REST polling active');
      window.setTimeout(connectLiveUpdates, 4000);
    });
    socket.addEventListener('error', () => socket.close());
    if (!heartbeat) heartbeat = window.setInterval(() => {
      if (socket?.readyState === WebSocket.OPEN) socket.send('ping');
    }, 25000);
  } catch {
    setConnection(false, 'REST polling active');
  }
}

function start() {
  bindControls();
  resetAgentPanel();
  try { initMap(); mapReady = true; }
  catch (error) {
    const mapNode = document.getElementById('map');
    mapNode.classList.add('map-error');
    mapNode.textContent = `Map unavailable: ${formatError(error)}`;
  }
  updateClock();
  window.setInterval(updateClock, 1000);
  document.querySelectorAll('.nav-link[href^="#"]').forEach(link => link.addEventListener('click', () => {
    document.querySelectorAll('.nav-link').forEach(item => item.classList.remove('active'));
    link.classList.add('active');
  }));
  refreshDashboard();
  connectLiveUpdates();
  window.addEventListener('beforeunload', () => { if (heartbeat) window.clearInterval(heartbeat); socket?.close(); }, { once: true });
}

start();
