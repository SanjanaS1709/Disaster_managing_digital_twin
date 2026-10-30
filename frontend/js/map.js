const roadColors = { open: '#71827e', restricted: '#d0a24d', blocked: '#c7666d' };
let map;
let roadsLayer;
let shelterLayer;
let unitsLayer;
let rainfallLayer;
let routeLayer;
let activeRouteRoadIds = [];
let activeRouteStartLocation;
let activeRouteCoordinates;
let latestGeoJSON;
let roadLayersById = new Map();

function roadStyle(feature) {
  const status = feature.properties.status || 'open';
  return { color: roadColors[status] || '#71827e', weight: status === 'open' ? 1.4 : 2.2, opacity: status === 'open' ? .42 : .78 };
}

function updateRoadLayer(geojson) {
  const nextIds = new Set(geojson.features.map(feature => feature.properties?.id || feature.id));
  const sameNetwork = nextIds.size === roadLayersById.size && [...nextIds].every(id => roadLayersById.has(id));
  if (!sameNetwork) {
    roadsLayer.clearLayers();
    roadsLayer.addData(geojson);
    roadLayersById = new Map();
    roadsLayer.eachLayer(layer => roadLayersById.set(layer.feature.properties?.id || layer.feature.id, layer));
    return;
  }
  for (const feature of geojson.features) {
    const id = feature.properties?.id || feature.id;
    const layer = roadLayersById.get(id);
    const previous = layer.feature.properties;
    if (previous.status === feature.properties.status && previous.risk_score === feature.properties.risk_score) continue;
    layer.feature.properties = feature.properties;
    layer.setStyle(roadStyle(feature));
    layer.bindPopup(popup(feature.properties.name || id, [
      ['Road ID', id], ['Status', feature.properties.status], ['Risk', feature.properties.risk_score],
    ]));
  }
}

function rainfallImpact(value, unit) {
  const intensity = unit === 'mm/hour' ? value : value / 2;
  const severity = intensity < 15 ? .25 : intensity < 30 ? .5 : intensity < 50 ? .75 : 1;
  if (severity <= .25) return { blocked: 0, restricted: 250 };
  if (severity <= .5) return { blocked: 250, restricted: 800 };
  if (severity <= .75) return { blocked: 600, restricted: 1500 };
  return { blocked: 1000, restricted: 2500 };
}

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
    style: roadStyle,
    onEachFeature: (feature, layer) => layer.bindPopup(popup(feature.properties.name || feature.properties.id, [
      ['Road ID', feature.properties.id], ['Status', feature.properties.status], ['Risk', feature.properties.risk_score],
    ])),
  }).addTo(map);
  shelterLayer = L.layerGroup().addTo(map);
  unitsLayer = L.layerGroup().addTo(map);
  rainfallLayer = L.layerGroup().addTo(map);
  routeLayer = L.layerGroup().addTo(map);
  map.on('click', event => {
    if (!map._routePickHandler) return;
    const handler = map._routePickHandler;
    map._routePickHandler = null;
    map.getContainer().classList.remove('route-pick-cursor');
    handler([event.latlng.lng, event.latlng.lat]);
  });
  L.control.layers(null, { 'Road network': roadsLayer, 'Suggested route': routeLayer, 'Shelters': shelterLayer, 'Relief units': unitsLayer, 'Simulated rainfall': rainfallLayer }, { collapsed: true }).addTo(map);
  document.getElementById('map-reset').addEventListener('click', () => fitLiveBounds());
  return map;
}

export function pickPointOnMap(onPick) {
  if (!map) throw new Error('Map is not ready.');
  map._routePickHandler = onPick;
  map.getContainer().classList.add('route-pick-cursor');
}

function fitLiveBounds() {
  if (!map) return;
  if (routeLayer?._routeBounds?.isValid()) {
    map.fitBounds(routeLayer._routeBounds.pad(.12), { maxZoom: 16 });
    return;
  }
  const layers = [shelterLayer, unitsLayer, rainfallLayer].flatMap(layer => layer.getLayers());
  if (layers.length) map.fitBounds(L.featureGroup(layers).getBounds().pad(.08), { maxZoom: 14 });
}

export function renderTwinMap(geojson, state) {
  if (!map || !geojson) return;
  latestGeoJSON = geojson;
  shelterLayer.clearLayers();
  unitsLayer.clearLayers();
  rainfallLayer.clearLayers();
  updateRoadLayer(geojson);
  renderActiveRoute(activeRouteStartLocation);
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
    if (zone.rainfall.length > 1) {
      const impact = rainfallImpact(reading.value, reading.unit);
      L.circle([zone.location[1], zone.location[0]], {
        radius: impact.restricted, color: '#d0a24d', weight: 1, dashArray: '5 5', fillColor: '#d0a24d', fillOpacity: .07,
      }).bindPopup(popup(`Simulated restricted area · ${zone.id}`, [['Radius', `${impact.restricted} m`], ['Rainfall', `${reading.value} ${reading.unit}`]])).addTo(rainfallLayer);
      if (impact.blocked) L.circle([zone.location[1], zone.location[0]], {
        radius: impact.blocked, color: '#c7666d', weight: 1, fillColor: '#c7666d', fillOpacity: .12,
      }).bindPopup(popup(`Simulated blocked area · ${zone.id}`, [['Radius', `${impact.blocked} m`], ['Rainfall', `${reading.value} ${reading.unit}`]])).addTo(rainfallLayer);
    }
    L.circleMarker([zone.location[1], zone.location[0]], { radius: 12 + reading.value / 10, color, weight: 2, fillColor: color, fillOpacity: .2 })
      .bindPopup(popup(`Rainfall zone ${zone.id}`, [['Rainfall', `${reading.value} ${reading.unit}`], ['Data', 'Simulated scenario']]))
      .addTo(rainfallLayer);
  }
  fitLiveBounds();
}

function renderActiveRoute(startLocation, routeCoordinates) {
  routeLayer.clearLayers();
  if (Array.isArray(routeCoordinates) && routeCoordinates.length > 1) activeRouteCoordinates = routeCoordinates;
  if (activeRouteCoordinates?.length > 1) {
    drawRouteGeometry(activeRouteCoordinates);
    return;
  }
  if (!activeRouteRoadIds.length || !latestGeoJSON) return;
  const featureById = new Map(latestGeoJSON.features.map(feature => [feature.properties?.id || feature.id, feature]));
  const features = activeRouteRoadIds.map(id => featureById.get(id)).filter(Boolean);
  if (!features.length) return;
  const samePoint = (a, b) => Math.abs(a[0] - b[0]) < 1e-6 && Math.abs(a[1] - b[1]) < 1e-6;
  const coordinates = [];
  for (const feature of features) {
    let segment = [...feature.geometry.coordinates];
    if (coordinates.length) {
      if (samePoint(coordinates.at(-1), segment.at(-1))) segment.reverse();
      if (!samePoint(coordinates.at(-1), segment[0])) continue;
      segment = segment.slice(1);
    } else if (startLocation) {
      const start = [startLocation[0], startLocation[1]];
      const firstDistance = Math.hypot(segment[0][0] - start[0], segment[0][1] - start[1]);
      const lastDistance = Math.hypot(segment.at(-1)[0] - start[0], segment.at(-1)[1] - start[1]);
      if (lastDistance < firstDistance) segment.reverse();
    }
    coordinates.push(...segment);
  }
  if (coordinates.length < 2) return;
  drawRouteGeometry(coordinates);
}

function drawRouteGeometry(coordinates) {
  const routeFeature = { type: 'Feature', properties: {}, geometry: { type: 'LineString', coordinates } };
  const route = L.geoJSON(routeFeature);
  L.geoJSON(routeFeature, {
    style: { color: '#081b26', weight: 9, opacity: .9, lineCap: 'round', lineJoin: 'round' },
  }).addTo(routeLayer);
  L.geoJSON(routeFeature, {
    style: { color: '#31d6e5', weight: 5, opacity: 1, lineCap: 'round', lineJoin: 'round' },
  }).addTo(routeLayer);
  const bounds = route.getBounds();
  const start = coordinates[0];
  const end = coordinates.at(-1);
  L.circleMarker([start[1], start[0]], { radius: 6, color: '#071522', weight: 2, fillColor: '#67e2a9', fillOpacity: 1 }).bindTooltip('Route start').addTo(routeLayer);
  L.circleMarker([end[1], end[0]], { radius: 6, color: '#071522', weight: 2, fillColor: '#ffcf70', fillOpacity: 1 }).bindTooltip('Route destination').addTo(routeLayer);
  routeLayer.eachLayer(layer => { if (layer.getBounds) bounds.extend(layer.getBounds()); });
  routeLayer._routeBounds = bounds;
}

export function showRoute(roadIds, startLocation, routeCoordinates) {
  activeRouteRoadIds = Array.isArray(roadIds) ? roadIds : [];
  activeRouteStartLocation = startLocation;
  activeRouteCoordinates = Array.isArray(routeCoordinates) ? routeCoordinates : undefined;
  renderActiveRoute(activeRouteStartLocation, activeRouteCoordinates);
  if (routeLayer._routeBounds?.isValid()) map.fitBounds(routeLayer._routeBounds.pad(.12), { maxZoom: 16 });
}

export function showSuggestedRoutes(routes) {
  if (!map) return;
  routeLayer.clearLayers();
  routeLayer._routeBounds = null;
  const palette = ['#31d6e5', '#ffbf47', '#c58bff', '#55dfa1', '#ff7885', '#7ea6ff'];
  const bounds = L.latLngBounds();
  let drawn = 0;
  for (const [index, route] of routes.entries()) {
    const coordinates = route?.result?.route_geometry?.coordinates;
    if (!route?.result?.reachable || !Array.isArray(coordinates) || coordinates.length < 2) continue;
    const color = palette[index % palette.length];
    const feature = { type: 'Feature', properties: {}, geometry: { type: 'LineString', coordinates } };
    const layer = L.geoJSON(feature, {
      style: { color: '#081b26', weight: 9, opacity: .88, lineCap: 'round', lineJoin: 'round' },
    }).addTo(routeLayer);
    L.geoJSON(feature, {
      style: { color, weight: 5, opacity: 1, lineCap: 'round', lineJoin: 'round' },
    }).bindTooltip(`Relief unit ${route.unit_id} → shelter ${route.shelter_id}`, { sticky: true }).addTo(routeLayer);
    bounds.extend(layer.getBounds());
    drawn += 1;
  }
  if (drawn) {
    routeLayer._routeBounds = bounds;
    map.fitBounds(bounds.pad(.12), { maxZoom: 15 });
  }
}

export function clearRoute() {
  activeRouteRoadIds = [];
  activeRouteStartLocation = undefined;
  activeRouteCoordinates = undefined;
  routeLayer?.clearLayers();
  if (routeLayer) routeLayer._routeBounds = null;
}
