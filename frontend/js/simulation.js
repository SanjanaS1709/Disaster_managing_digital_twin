export function mountOperationsPanel() {
  const content = document.querySelector('.content-wrap');
  const footer = content.querySelector('.page-footer');
  const section = document.createElement('section');
  section.className = 'integration-grid';
  section.innerHTML = `
    <article class="panel operations-panel">
      <div class="panel-header"><div><div class="panel-title"><span class="panel-icon orange-icon">◷</span><h2>Simulation controls</h2></div><p id="simulation-summary">Loading live simulation status…</p></div><button class="panel-menu" id="simulation-reset" type="button">New run</button></div>
      <div class="operation-buttons simulation-actions" aria-label="Simulation controls"><button type="button" data-sim-action="start">Begin run</button><button type="button" data-sim-action="pause">Pause</button><button type="button" data-sim-action="resume">Continue</button><button type="button" data-sim-action="advance">Move time forward 1 minute</button></div>
      <p class="simulation-help" id="simulation-help" aria-live="polite">Loading simulation controls…</p>
      <h3>Recent events</h3><div id="event-history" class="event-history"><p>Loading event history…</p></div>
    </article>
    <article class="panel operations-panel event-panel">
      <div class="panel-header"><div><div class="panel-title"><span class="panel-icon purple-icon">✳</span><h2>Decision support</h2></div><p>Choose an event → Review the plain-language summary → Apply the event to the simulation if appropriate.</p></div></div>
      <form id="live-event-form" class="live-event-form">
        <label>Event type<select id="live-event-type"><option value="ROAD_FLOODED">Road flooded</option><option value="ROAD_REOPENED">Road reopened</option><option value="SHELTER_OVER_CAPACITY">Shelter occupancy</option><option value="RESOURCE_SHORTAGE">Resource shortage</option><option value="RAINFALL_INCREASE">Rainfall increase</option></select></label>
        <label>Affected place or resource<select id="live-event-target" required></select></label>
        <fieldset class="event-details"><legend>Event details</legend><div id="event-fields"></div></fieldset>
        <div class="operation-buttons"><button type="submit" id="evaluate-event">Get clear review</button><button type="button" id="apply-event" disabled>Update simulation</button></div>
      </form>
      <div id="integration-notice" class="integration-notice" role="status" hidden></div>
    </article>`;
  content.insertBefore(section, footer);
  return section;
}

export function renderSimulationStatus(status, state) {
  const summary = document.getElementById('simulation-summary');
  const phase = (status.phase || 'unknown').toLowerCase();
  if (summary) {
    const phaseLabel = phase === 'running' ? 'Run is active' : phase === 'paused' ? 'Run is paused' : phase === 'ready' ? 'Ready to begin' : `Run status: ${phase}`;
    summary.textContent = `${phaseLabel} · ${status.accepted_events} events recorded · ${status.scheduled_events} planned`;
    summary.dataset.phase = phase;
  }
  const phaseActions = {
    ready: new Set(['start']),
    running: new Set(['pause', 'advance']),
    paused: new Set(['resume', 'advance']),
  };
  document.querySelectorAll('[data-sim-action]').forEach(button => {
    button.disabled = !phaseActions[phase]?.has(button.dataset.simAction) || button.dataset.pending === 'true';
  });
  const startButton = document.querySelector('[data-sim-action="start"]');
  if (startButton) startButton.textContent = phase === 'running' ? 'Run is active' : 'Begin run';
  const pauseButton = document.querySelector('[data-sim-action="pause"]');
  if (pauseButton) pauseButton.textContent = 'Pause run';
  const resumeButton = document.querySelector('[data-sim-action="resume"]');
  if (resumeButton) resumeButton.textContent = 'Continue run';
  const advanceButton = document.querySelector('[data-sim-action="advance"]');
  if (advanceButton) advanceButton.textContent = 'Advance time by 1 minute';
  const help = document.getElementById('simulation-help');
  if (help) {
    const messages = {
      ready: 'The run has not started. Select Begin run; then advance time manually to progress the scenario.',
      running: 'Run is active. Time does not pass automatically. Select Advance time by 1 minute to progress and process scheduled events.',
      paused: 'Run is paused. Select Continue run to resume, or advance time by one minute while paused.',
    };
    help.textContent = messages[phase] || `Simulation phase: ${phase}.`;
  }
}

export function renderEventHistory(events, state) {
  const root = document.getElementById('event-history');
  if (!root) return;
  root.replaceChildren();
  if (!events.length) { root.textContent = 'No events have been recorded in this run.'; return; }
  const names = { ROAD_FLOODED: 'Road flooding reported', ROAD_REOPENED: 'Road reopened', SHELTER_OVER_CAPACITY: 'Shelter occupancy updated', RESOURCE_SHORTAGE: 'Supply shortage recorded', RAINFALL_INCREASE: 'Rainfall increase recorded' };
  const entities = [...(state?.roads || []), ...(state?.shelters || []), ...(state?.relief_units || []), ...(state?.zones || [])];
  for (const event of events.slice().reverse().slice(0, 8)) {
    const row = document.createElement('div'); row.className = 'event-history-row';
    const name = document.createElement('b'); name.textContent = names[event.type] || 'Event recorded';
    const entity = entities.find(item => item.id === event.target_id);
    const when = event.timestamp ? new Intl.DateTimeFormat('en-IN', { hour: '2-digit', minute: '2-digit', hour12: true, timeZone: 'Asia/Kolkata' }).format(new Date(event.timestamp)) : '';
    const target = document.createElement('span'); target.textContent = [entity?.name || 'Simulation event', when].filter(Boolean).join(' · ');
    row.append(name, target); root.append(row);
  }
}

export function configureEventForm(state) {
  const type = document.getElementById('live-event-type');
  const target = document.getElementById('live-event-target');
  if (!type || !target || !state) return;
  const eventType = type.value;
  const entities = eventType.startsWith('ROAD_') ? state.roads
    : eventType === 'SHELTER_OVER_CAPACITY' ? state.shelters
      : eventType === 'RESOURCE_SHORTAGE' ? state.relief_units : state.zones;
  const previousTarget = target.value;
  target.replaceChildren();
  for (const entity of entities) {
    const option = document.createElement('option'); option.value = entity.id; option.textContent = `${entity.name || (eventType.startsWith('ROAD_') ? 'Road' : 'Entity')} (${entity.id})`; target.append(option);
  }
  if (entities.some(entity => entity.id === previousTarget)) target.value = previousTarget;
  const selected = entities.find(entity => entity.id === target.value) || entities[0];
  const host = document.getElementById('event-fields');
  host.replaceChildren();
  const field = (labelText, control, helpText) => {
    const label = document.createElement('label'); label.append(document.createTextNode(labelText), control);
    if (helpText) { const help = document.createElement('small'); help.textContent = helpText; label.append(help); }
    host.append(label);
  };
  const select = (id, options) => {
    const control = document.createElement('select'); control.id = id;
    for (const [value, label] of options) { const option = document.createElement('option'); option.value = value; option.textContent = label; control.append(option); }
    return control;
  };
  const number = (id, value, min = 0, max) => {
    const control = document.createElement('input'); control.id = id; control.type = 'number'; control.min = String(min); control.step = '1'; control.value = String(value);
    if (max !== undefined) control.max = String(max);
    return control;
  };
  if (eventType === 'ROAD_FLOODED') {
    field('Reported severity', select('event-risk-level', [['0.25', 'Low'], ['0.5', 'Moderate'], ['0.75', 'High'], ['1', 'Very high']]), 'This records the road condition in the simulation. A separate danger estimate needs measured warning information.');
  } else if (eventType === 'ROAD_REOPENED') {
    field('Road condition', select('event-road-status', [['open', 'Open to traffic'], ['restricted', 'Limited access']]));
    field('Remaining danger level', select('event-risk-level', [['0.1', 'Low'], ['0.3', 'Moderate'], ['0.6', 'High']]), 'Choose the reported danger level after reopening.');
  } else if (eventType === 'SHELTER_OVER_CAPACITY') {
    field('People currently at the shelter', number('event-occupancy', selected?.occupancy || 0), 'Enter the current number of people. The simulation will compare it with shelter capacity.');
  } else if (eventType === 'RESOURCE_SHORTAGE') {
    const inventory = selected?.inventory || {};
    const items = Object.keys(inventory);
    const itemSelect = select('event-shortage-item', items.map(item => [item, item[0].toUpperCase() + item.slice(1)]));
    field('Supply in short supply', itemSelect, 'Choose a supply held by this relief unit.');
    const item = items[0];
    const unit = selected?.inventory_units?.[item] || '';
    field('Amount to record as used', number('event-shortage-quantity', 1, 1, inventory[item]), `${inventory[item] ?? 0} ${unit} currently listed for this supply.`);
    const unitNote = document.createElement('p'); unitNote.className = 'event-field-note'; unitNote.id = 'event-shortage-unit'; unitNote.textContent = `Amount is measured in ${unit || 'the listed unit'}.`; host.append(unitNote);
    itemSelect.addEventListener('change', () => {
      const chosen = itemSelect.value;
      const quantity = document.getElementById('event-shortage-quantity');
      const label = quantity.closest('label');
      quantity.max = String(inventory[chosen] ?? 0);
      quantity.value = String(Math.min(1, inventory[chosen] ?? 0));
      label.querySelector('small').textContent = `${inventory[chosen] ?? 0} ${selected?.inventory_units?.[chosen] || ''} currently listed for this supply.`;
      document.getElementById('event-shortage-unit').textContent = `Amount is measured in ${selected?.inventory_units?.[chosen] || 'the listed unit'}.`;
    });
  } else if (eventType === 'RAINFALL_INCREASE') {
    field('Reported rainfall', number('event-rainfall', 25), 'Enter the new measured rainfall. Use the same unit as the previous report for this zone.');
    field('Measurement unit', select('event-rainfall-unit', [['mm/hour', 'Millimetres per hour'], ['mm', 'Millimetres']]));
  }
}

export function readEventPayload(state) {
  const type = document.getElementById('live-event-type').value;
  const numberValue = id => Number(document.getElementById(id).value);
  if (type === 'ROAD_FLOODED') return { risk_score: numberValue('event-risk-level') };
  if (type === 'ROAD_REOPENED') return { status: document.getElementById('event-road-status').value, risk_score: numberValue('event-risk-level') };
  if (type === 'SHELTER_OVER_CAPACITY') return { occupancy: numberValue('event-occupancy') };
  if (type === 'RAINFALL_INCREASE') return { value: numberValue('event-rainfall'), unit: document.getElementById('event-rainfall-unit').value };
  const targetId = document.getElementById('live-event-target').value;
  const unit = (state.relief_units || []).find(item => item.id === targetId);
  const item = document.getElementById('event-shortage-item').value;
  return { item, quantity: numberValue('event-shortage-quantity'), unit: unit?.inventory_units?.[item] || '' };
}

export function showIntegrationNotice(message, isError = false) {
  const notice = document.getElementById('integration-notice');
  if (!notice) return;
  notice.hidden = false; notice.textContent = message;
  notice.classList.toggle('error', isError);
}
