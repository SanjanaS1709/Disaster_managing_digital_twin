import { getEvents, getStatus, getTwin, postJSON, websocketURL } from './api.js';
import { renderDashboard, setConnection } from './dashboard.js';
import { renderAgentEvaluation, resetAgentPanel } from './agents.js?v=assistant-firestore-20261030-1';
import { clearRoute, initMap, pickPointOnMap, renderTwinMap, showRoute, showSuggestedRoutes } from './map.js?v=assistant-firestore-20261030-1';
import { initAssistant } from './assistant.js?v=assistant-firestore-20261030-1';
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
let refreshInFlight;
let refreshQueued = false;

function stateGeoJSON(state) {
  return {
    type: 'FeatureCollection',
    features: (state.roads || []).map(road => ({
      type: 'Feature', id: road.id,
      geometry: { type: 'LineString', coordinates: road.coordinates },
      properties: { id: road.id, name: road.name, status: road.status, risk_score: road.risk_score, source: road.source },
    })),
  };
}

const setText = (selector, value) => {
  const node = document.querySelector(selector);
  if (node) node.textContent = value;
};
const formatError = error => error instanceof Error ? error.message : String(error);

function updateClock() {
  setText('#header-time', `${new Intl.DateTimeFormat('en-IN', { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false, timeZone: 'Asia/Kolkata' }).format(new Date())} IST`);
}

function routePoint(label, coordinates) {
  return { label, coordinates };
}

function readRoutePoint(select) {
  if (!select.value) throw new Error('Choose both a starting point and a destination.');
  const point = JSON.parse(select.value);
  if (!Array.isArray(point.coordinates) || point.coordinates.length !== 2 || !point.coordinates.every(Number.isFinite)
    || Math.abs(point.coordinates[0]) > 180 || Math.abs(point.coordinates[1]) > 90) {
    throw new Error('The selected location has invalid coordinates.');
  }
  return point;
}

function renderRouteChoices(state) {
  const origin = document.getElementById('route-origin');
  const destination = document.getElementById('route-destination');
  if (!origin || !destination) return;
  const priorOrigin = origin.value;
  const priorDestination = destination.value;
  const populate = (select, placeholder, items, fallback) => {
    const previousOption = [...select.options].find(option => option.value === fallback);
    select.replaceChildren(new Option(placeholder, ''));
    for (const item of items) {
      if (!Array.isArray(item.location) || item.location.length !== 2) continue;
      const value = JSON.stringify(routePoint(item.name, item.location));
      const option = new Option(item.name, value);
      select.add(option);
    }
    const match = [...select.options].find(option => option.value === fallback);
    if (match) select.value = fallback;
    else if (fallback && fallback.includes('Map selected') && previousOption) {
        select.add(new Option(previousOption.textContent, fallback));
        select.value = fallback;
    } else if (select.options.length > 1) select.selectedIndex = 1;
  };
  populate(origin, 'Choose an incident / start', [
    ...(state.zones || []).filter(zone => zone.location).map(zone => ({ name: `Incident zone ${zone.id}`, location: zone.location })),
    ...(state.relief_units || []).map(unit => ({ name: `Relief unit ${unit.id}`, location: unit.location })),
  ], priorOrigin || '');
  populate(destination, 'Choose a shelter', (state.shelters || []).map(shelter => ({ name: shelter.name, location: shelter.location })), priorDestination || '');
}

function showRouteState(message, kind = '') {
  const result = document.getElementById('route-result');
  result.replaceChildren();
  result.hidden = false;
  result.className = `route-result${kind ? ` ${kind}` : ''}`;
  const paragraph = document.createElement('p');
  paragraph.textContent = message;
  result.append(paragraph);
}

function formatDistance(meters) {
  return meters >= 1000 ? `${(meters / 1000).toFixed(1)} km` : `${Math.round(meters)} m`;
}

function formatDuration(seconds) {
  const minutes = Math.max(1, Math.round(seconds / 60));
  return minutes >= 60 ? `${Math.floor(minutes / 60)} hr ${minutes % 60} min` : `${minutes} min`;
}

function renderRouteResult(data) {
  const result = document.getElementById('route-result');
  result.replaceChildren();
  result.hidden = false;
  result.className = 'route-result success';
  const heading = document.createElement('p');
  heading.className = 'route-summary';
  heading.textContent = `${formatDistance(data.route.distance_m)} · ${formatDuration(data.route.duration_s)} estimated`;
  const notice = document.createElement('p');
  notice.className = 'route-safety';
  notice.textContent = data.safety_message;
  result.append(heading, notice);
  if (data.alternatives_count > 0) {
    const alternatives = document.createElement('p');
    alternatives.textContent = `Selected the quickest of ${data.alternatives_count + 1} mapped route options.`;
    result.append(alternatives);
  }
  if (data.route.steps?.length) {
    const details = document.createElement('details');
    const summary = document.createElement('summary');
    summary.textContent = 'Turn-by-turn directions';
    const list = document.createElement('ol');
    for (const step of data.route.steps) {
      const item = document.createElement('li');
      item.textContent = `${step.instruction} · ${formatDistance(step.distance_m)}`;
      list.append(item);
    }
    details.append(summary, list);
    result.append(details);
  }
}

function renderShelterRanking(data) {
  const result = document.getElementById('route-result');
  result.replaceChildren();
  result.hidden = false;
  result.className = 'route-result success';
  const intro = document.createElement('p');
  intro.className = 'route-summary';
  intro.textContent = 'Shelters ranked by road distance and estimated time';
  const safety = document.createElement('p');
  safety.className = 'route-safety';
  safety.textContent = data.safety_message;
  const list = document.createElement('ol');
  list.className = 'route-candidates';
  for (const candidate of data.candidates) {
    const item = document.createElement('li');
    const choose = document.createElement('button');
    choose.type = 'button';
    choose.textContent = `${candidate.destination.label} · ${formatDistance(candidate.distance_m)} · ${formatDuration(candidate.duration_s)}`;
    choose.addEventListener('click', () => {
      const select = document.getElementById('route-destination');
      const point = candidate.destination;
      const value = JSON.stringify(point);
      let option = [...select.options].find(entry => entry.value === value);
      if (!option) {
        option = new Option(point.label, value);
        select.add(option);
      }
      select.value = value;
      showRouteState(`${point.label} selected. Choose “Suggest route” to draw the road geometry.`, 'success');
    });
    item.append(choose);
    list.append(item);
  }
  result.append(intro, safety, list);
  if (data.unreachable_count) {
    const note = document.createElement('p');
    note.textContent = `${data.unreachable_count} destination${data.unreachable_count === 1 ? '' : 's'} could not be reached by road.`;
    result.append(note);
  }
}

async function loadAndRenderDashboard() {
  try {
    // The twin snapshot already contains road geometry and status. Derive the
    // map features from that same snapshot to avoid a second large, possibly
    // out-of-date GeoJSON download on every control action.
    const [state, status, events] = await Promise.all([getTwin(), getStatus(), getEvents()]);
    twinState = state;
    renderRouteChoices(state);
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
      if (mapReady) renderTwinMap(stateGeoJSON(state), state);
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

function refreshDashboard() {
  if (refreshInFlight) {
    refreshQueued = true;
    return refreshInFlight;
  }
  refreshInFlight = (async () => {
    do {
      refreshQueued = false;
      await loadAndRenderDashboard();
    } while (refreshQueued);
  })().finally(() => { refreshInFlight = undefined; });
  return refreshInFlight;
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
  const routeForm = document.getElementById('route-form');
  routeForm.addEventListener('submit', async event => {
    event.preventDefault();
    const button = document.getElementById('route-suggest');
    button.disabled = true;
    button.textContent = 'Finding route…';
    showRouteState('Requesting a road route…', 'loading');
    try {
      const origin = readRoutePoint(document.getElementById('route-origin'));
      const destination = readRoutePoint(document.getElementById('route-destination'));
      if (origin.coordinates[0] === destination.coordinates[0] && origin.coordinates[1] === destination.coordinates[1]) {
        throw new Error('Choose two different locations.');
      }
      const data = await postJSON('/api/routes/calculate', { origin, destination });
      const coordinates = data.route?.geometry?.coordinates;
      if (!Array.isArray(coordinates) || coordinates.length < 2) throw new Error('The routing service returned no usable road geometry.');
      showRoute([], origin.coordinates, coordinates);
      renderRouteResult(data);
    } catch (error) {
      clearRoute();
      showRouteState(formatError(error), 'error');
    } finally {
      button.disabled = false;
      button.textContent = 'Suggest route';
    }
  });
  const compareButton = document.getElementById('route-compare');
  compareButton.addEventListener('click', async () => {
    compareButton.disabled = true;
    compareButton.textContent = 'Ranking…';
    showRouteState('Comparing mapped road distance and travel time to shelters…', 'loading');
    try {
      const origin = readRoutePoint(document.getElementById('route-origin'));
      const destinations = [...document.getElementById('route-destination').options]
        .filter(option => option.value)
        .map(option => JSON.parse(option.value))
        .filter(point => point.coordinates[0] !== origin.coordinates[0] || point.coordinates[1] !== origin.coordinates[1]);
      if (!destinations.length) throw new Error('No distinct shelter destinations are available to compare.');
      const data = await postJSON('/api/routes/compare', { origin, destinations });
      renderShelterRanking(data);
    } catch (error) {
      showRouteState(formatError(error), 'error');
    } finally {
      compareButton.disabled = false;
      compareButton.textContent = 'Rank shelters';
    }
  });
  document.getElementById('route-clear').addEventListener('click', () => {
    clearRoute();
    document.getElementById('route-result').hidden = true;
  });
  document.querySelectorAll('[data-route-pick]').forEach(button => button.addEventListener('click', () => {
    const target = button.dataset.routePick;
    showRouteState(`Click the map to set the ${target}.`, 'loading');
    pickPointOnMap(coordinates => {
      const select = document.getElementById(`route-${target}`);
      const point = routePoint(target === 'origin' ? 'Map selected start' : 'Map selected destination', coordinates);
      const value = JSON.stringify(point);
      let option = [...select.options].find(item => item.value === value);
      if (!option) {
        option = new Option(`${point.label} (${coordinates[1].toFixed(4)}, ${coordinates[0].toFixed(4)})`, value);
        select.add(option);
      }
      select.value = value;
      showRouteState(`${point.label} selected.`, 'success');
    });
  }));

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
      clearRoute();
      renderAgentEvaluation(result, twinState, showRoute, showSuggestedRoutes);
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
      clearRoute();
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
      clearRoute();
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
  initAssistant();
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
