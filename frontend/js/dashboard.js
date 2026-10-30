const number = value => new Intl.NumberFormat('en-IN').format(value || 0);
const $ = selector => document.querySelector(selector);

export function setConnection(connected, detail = '') {
  document.querySelectorAll('.connection-status, .page-footer > span:nth-child(2)').forEach(node => {
    node.classList.toggle('connected', connected);
    if (node.lastChild) node.lastChild.textContent = connected ? ' Backend connected' : ` ${detail || 'Backend disconnected'}`;
  });
  const health = document.querySelector('.sidebar-health');
  if (health) {
    health.querySelector('b').textContent = connected ? 'Backend connected' : 'Backend disconnected';
    health.querySelector('small').textContent = connected ? 'Live twin API' : detail || 'Retrying connection';
    health.querySelector('.health-indicator').classList.toggle('connected', connected);
  }
  document.querySelector('.footer-led')?.classList.toggle('connected', connected);
}

export function renderDashboard(state, status, events) {
  const roads = state.roads || [];
  const shelters = state.shelters || [];
  const units = state.relief_units || [];
  const zones = state.zones || [];
  const open = roads.filter(road => road.status === 'open').length;
  const restricted = roads.filter(road => road.status === 'restricted').length;
  const blocked = roads.filter(road => road.status === 'blocked').length;
  const occupied = shelters.reduce((sum, shelter) => sum + shelter.occupancy, 0);
  const capacity = shelters.reduce((sum, shelter) => sum + shelter.capacity, 0);
  const inventory = units.reduce((totals, unit) => {
    for (const [item, amount] of Object.entries(unit.inventory || {})) totals[item] = (totals[item] || 0) + amount;
    return totals;
  }, {});
  const demand = shelters.reduce((totals, shelter) => {
    for (const [item, amount] of Object.entries(shelter.demand || {})) totals[item] = (totals[item] || 0) + amount;
    return totals;
  }, {});
  const shortages = Object.entries(demand).map(([item, amount]) => [item, Math.max(0, amount - (inventory[item] || 0))]).filter(([, amount]) => amount > 0);
  const values = {
    'metric-roads': number(roads.length), 'metric-blocked': `${number(blocked)} blocked`,
    'metric-restricted': `${number(restricted)} restricted`, 'metric-shelters': number(shelters.length),
    'metric-occupancy': `${capacity ? Math.round(occupied / capacity * 100) : 0}% occupied`,
    'metric-capacity': `${number(Math.max(0, capacity - occupied))} places available`,
    'metric-units': number(units.length), 'metric-zones': number(zones.length),
    'shelter-count': number(shelters.length), 'unit-count': number(units.length),
    'chart-road-total': number(roads.length), 'road-open': number(open),
    'road-restricted': number(restricted), 'road-blocked': number(blocked),
  };
  for (const [id, value] of Object.entries(values)) {
    const node = document.getElementById(id);
    if (node) node.textContent = value;
  }
  const donut = $('.status-donut');
  if (donut) {
    donut.style.setProperty('--open-end', `${roads.length ? open / roads.length * 100 : 0}%`);
    donut.style.setProperty('--restricted-end', `${roads.length ? (open + restricted) / roads.length * 100 : 0}%`);
  }
  renderShelters(shelters);
  renderUnits(units);
  renderRainfall(zones);
  const unitFoot = document.querySelector('#resources .panel-foot');
  if (unitFoot) unitFoot.textContent = shortages.length
    ? `Simulated gaps vs shelter demand: ${shortages.map(([item, amount]) => `${item} short by ${number(amount)}`).join(' · ')}. No dispatch has occurred.`
    : 'Example inventory meets listed shelter demand. No dispatch has occurred.';
  renderStatus(state, status, events);
}

function clear(node) { if (node) node.replaceChildren(); return node; }
function appendText(parent, tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  node.textContent = text;
  parent.append(node);
  return node;
}
function renderShelters(shelters) {
  const root = clear(document.getElementById('shelter-list'));
  for (const shelter of shelters) {
    const row = document.createElement('div'); row.className = 'shelter-row';
    appendText(row, 'span', 'shelter-row-icon', '⌂');
    const info = document.createElement('div'); info.className = 'shelter-info';
    appendText(info, 'b', '', shelter.name); appendText(info, 'span', '', shelter.id);
    row.append(info);
    const stats = document.createElement('div'); stats.className = 'shelter-stats';
    const line = document.createElement('div'); line.className = 'shelter-stat-line';
    appendText(line, 'b', '', `${shelter.occupancy}/${shelter.capacity}`);
    appendText(line, 'span', '', `${shelter.capacity ? Math.round(shelter.occupancy / shelter.capacity * 100) : 0}%`);
    stats.append(line);
    const track = document.createElement('div'); track.className = 'capacity-track';
    const fill = document.createElement('span'); fill.style.width = `${shelter.capacity ? Math.min(100, shelter.occupancy / shelter.capacity * 100) : 0}%`;
    if (shelter.capacity && shelter.occupancy / shelter.capacity >= .75) fill.classList.add('high');
    track.append(fill); stats.append(track); row.append(stats); root.append(row);
  }
  const foot = root?.parentElement.querySelector('.panel-foot');
  if (foot) foot.textContent = 'Shelter occupancy shown for this demo.';
}
function renderUnits(units) {
  const root = clear(document.getElementById('unit-list'));
  for (const unit of units) {
    const row = document.createElement('div'); row.className = 'unit-row';
    appendText(row, 'span', 'unit-row-icon', '✚');
    const info = document.createElement('div'); info.className = 'unit-info';
    appendText(info, 'b', '', unit.id); appendText(info, 'span', '', 'Relief unit'); row.append(info);
    const stock = document.createElement('div'); stock.className = 'supply-list';
    for (const [item, quantity] of Object.entries(unit.inventory || {})) {
      const chip = document.createElement('span'); chip.className = 'supply-chip';
      appendText(chip, 'b', '', number(quantity)); appendText(chip, 'small', '', item); stock.append(chip);
    }
    row.append(stock); root.append(row);
  }
  const foot = root?.parentElement.querySelector('.panel-foot');
  if (foot) foot.textContent = 'Example supply counts only. No deliveries are made.';
}
function renderRainfall(zones) {
  const root = clear(document.getElementById('rainfall-list'));
  const readings = (zones || []).map(zone => {
    const reading = zone.rainfall?.at(-1);
    return reading ? { id: zone.id, value: reading.value, unit: reading.unit } : null;
  }).filter(Boolean).sort((a, b) => b.value - a.value);
  if (!root) return;
  for (const reading of readings) {
    const row = document.createElement('div'); row.className = 'rainfall-row';
    const label = document.createElement('span'); label.textContent = reading.id;
    const track = document.createElement('i');
    const fill = document.createElement('b'); fill.style.width = `${Math.min(100, reading.value / 80 * 100)}%`;
    if (reading.value >= 60) fill.classList.add('heavy'); else if (reading.value >= 35) fill.classList.add('moderate');
    track.append(fill);
    const amount = document.createElement('strong'); amount.textContent = `${number(reading.value)} ${reading.unit}`;
    row.append(label, track, amount); root.append(row);
  }
  if (!readings.length) root.textContent = 'No rainfall readings available.';
}
function renderStatus(state, status, events) {
  const phase = status.phase.toUpperCase();
  const phaseLabel = status.phase === 'running' ? 'RUN ACTIVE' : status.phase === 'paused' ? 'RUN PAUSED' : 'READY';
  const scenario = $('.scenario-chip'); if (scenario) scenario.lastChild.textContent = ` DEMO · ${phaseLabel}`;
  const mapTag = document.querySelector('.map-panel .panel-tag'); if (mapTag) mapTag.textContent = 'OPENSTREETMAP';
  const mapLabel = document.querySelector('.map-label'); if (mapLabel) mapLabel.lastChild.textContent = ' OPENSTREETMAP NETWORK';
  const mapDescription = document.querySelector('.map-panel .panel-header p');
  if (mapDescription) mapDescription.textContent = 'OpenStreetMap road layout · simulated conditions';
  const hero = document.querySelector('.hero-status');
  if (hero) {
    hero.querySelector('b').textContent = status.phase === 'running' ? 'Run is active' : status.phase === 'paused' ? 'Run is paused' : 'Ready to begin';
    hero.querySelector(':scope > div:last-child > span').textContent = new Intl.DateTimeFormat('en-IN', { dateStyle: 'medium', timeStyle: 'short', timeZone: 'Asia/Kolkata' }).format(new Date(state.timestamp));
  }
  const labels = [document.querySelector('.status-panel .panel-header p'), document.querySelector('.status-note'), document.querySelector('.risk-summary > div:first-child b'), document.querySelector('.risk-summary > div:last-child b')];
  if (labels[0]) labels[0].textContent = `${events.length} events recorded · ${status.scheduled_events} planned`;
  if (labels[1]) labels[1].lastChild.textContent = ' Road layout is from OpenStreetMap; road conditions are simulated.';
  if (labels[2]) labels[2].textContent = 'Current simulation';
  if (labels[3]) labels[3].textContent = 'OpenStreetMap';
  const demoIndicators = document.querySelectorAll('.metric-bottom');
  if (demoIndicators[2]) demoIndicators[2].lastChild.textContent = ' Simulated stocks · gaps shown below';
  const zoneMetric = document.querySelector('.metric-green .metric-bottom span:last-child');
  if (zoneMetric) zoneMetric.textContent = 'Twin risk zones';
  const footer = document.querySelector('.page-footer > span:last-child'); if (footer) footer.textContent = 'OpenStreetMap roads · simulated conditions · not a live emergency feed';
}
