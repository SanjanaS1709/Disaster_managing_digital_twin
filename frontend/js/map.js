const roadColors = { open: '#45c99a', restricted: '#f0b84b', blocked: '#f0646b' };
let map;
let roadsLayer;
let shelterLayer;
let unitsLayer;
let rainfallLayer;

function popup(title, rows) {
  const box = document.createElement('div');
  const heading = document.createElement('strong');
  heading.textContent = title;
  box.append(heading);
  for (const [label, value] of rows) {
    const row = document.createElement('div');
    const key = document.createElement('span');
    const text = document.createElement('b');
    key.textContent = label;
    text.textContent = String(value);
    row.append(key, text);
    box.append(row);
  }
  return box;
}

export function initMap() {
  if (!window.L) throw new Error('Leaflet did not load. Check the internet connection and reload.');
  map = L.map('map', { zoomControl: false, scrollWheelZoom: true }).setView([13.08, 80.27], 11);
  L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
    maxZoom: 19,
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
  }).addTo(map);
  L.control.zoom({ position: 'topright' }).addTo(map);
  roadsLayer = L.geoJSON(null, {
    style: feature => ({ color: roadColors[feature.properties.status] || '#a18af7', weight: 4, opacity: .94 }),
    onEachFeature: (feature, layer) => layer.bindPopup(popup(feature.properties.name || feature.properties.id, [
      ['Road ID', feature.properties.id], ['Status', feature.properties.status], ['Risk', feature.properties.risk_score],
    ])),
  }).addTo(map);
  shelterLayer = L.layerGroup().addTo(map);
  unitsLayer = L.layerGroup().addTo(map);
  rainfallLayer = L.layerGroup().addTo(map);
  L.control.layers(null, { 'Road network': roadsLayer, 'Shelters': shelterLayer, 'Relief units': unitsLayer, 'Simulated rainfall': rainfallLayer }, { collapsed: true }).addTo(map);
  document.getElementById('map-reset').addEventListener('click', () => fitLiveBounds());
  return map;
}

function fitLiveBounds() {
  if (!map) return;
  const layers = [roadsLayer, shelterLayer, unitsLayer, rainfallLayer].flatMap(layer => layer.getLayers());
  if (layers.length) map.fitBounds(L.featureGroup(layers).getBounds().pad(.08), { maxZoom: 14 });
}

export function renderTwinMap(geojson, state) {
  if (!map || !geojson) return;
  roadsLayer.clearLayers();
  shelterLayer.clearLayers();
  unitsLayer.clearLayers();
  rainfallLayer.clearLayers();
  roadsLayer.addData(geojson);
  for (const shelter of state.shelters || []) {
    L.circleMarker([shelter.location[1], shelter.location[0]], { radius: 8, color: '#071522', weight: 3, fillColor: '#39b8c7', fillOpacity: 1 })
      .bindPopup(popup(shelter.name, [['Shelter ID', shelter.id], ['Occupancy', `${shelter.occupancy} / ${shelter.capacity}`], ['Available', Math.max(0, shelter.capacity - shelter.occupancy)]]))
      .addTo(shelterLayer);
  }
  for (const unit of state.relief_units || []) {
    L.marker([unit.location[1], unit.location[0]], { icon: L.divIcon({ className: 'unit-map-icon', html: '&#10010;', iconSize: [27, 27], iconAnchor: [13, 13] }) })
      .bindPopup(popup(unit.id, [['Inventory', Object.entries(unit.inventory || {}).map(([item, qty]) => `${item}: ${qty}`).join(', ') || 'Unknown']]))
      .addTo(unitsLayer);
  }
  for (const zone of state.zones || []) {
    const reading = zone.rainfall?.at(-1);
    if (!zone.location || !reading) continue;
    const color = reading.value >= 60 ? '#ed777d' : reading.value >= 35 ? '#e8ba6f' : '#62c6d1';
    L.circleMarker([zone.location[1], zone.location[0]], { radius: 12 + reading.value / 10, color, weight: 2, fillColor: color, fillOpacity: .2 })
      .bindPopup(popup(`Rainfall zone ${zone.id}`, [['Rainfall', `${reading.value} ${reading.unit}`], ['Data', 'Simulated scenario']]))
      .addTo(rainfallLayer);
  }
  fitLiveBounds();
}
