const COLORS = { open: '#45c99a', restricted: '#f0b84b', blocked: '#f0646b' };

// All features here are clearly synthetic and use GeoJSON [longitude, latitude].
export const demoRoads = [
  { id: 'D-R01', name: 'Demo · Chennai–Tiruvallur corridor', status: 'open', coords: [[80.2707,13.0827],[80.245,13.11],[80.22,13.145],[80.19,13.18],[80.16,13.22]] },
  { id: 'D-R02', name: 'Demo · Poonamallee High Road', status: 'restricted', coords: [[80.2707,13.0827],[80.245,13.075],[80.215,13.07],[80.18,13.065],[80.145,13.06]] },
  { id: 'D-R03', name: 'Demo · Inner Ring Road', status: 'open', coords: [[80.22,13.145],[80.235,13.12],[80.245,13.095],[80.245,13.075],[80.255,13.05]] },
  { id: 'D-R04', name: 'Demo · Ennore coastal link', status: 'blocked', coords: [[80.2707,13.0827],[80.29,13.11],[80.31,13.145],[80.33,13.18]] },
  { id: 'D-R05', name: 'Demo · GST Road', status: 'open', coords: [[80.2707,13.0827],[80.25,13.055],[80.23,13.025],[80.21,12.995]] },
  { id: 'D-R06', name: 'Demo · North Chennai connector', status: 'restricted', coords: [[80.22,13.145],[80.255,13.15],[80.29,13.155],[80.31,13.17]] },
  { id: 'D-R07', name: 'Demo · Ambattur link', status: 'blocked', coords: [[80.19,13.18],[80.205,13.155],[80.215,13.125],[80.215,13.095]] },
  { id: 'D-R08', name: 'Demo · OMR connector', status: 'open', coords: [[80.255,13.05],[80.275,13.025],[80.295,13.0],[80.315,12.975]] },
];

export const demoShelters = [
  { id:'D-S01', name:'Demo · Perambur Relief Centre', type:'Community hall', location:[80.233,13.115], capacity:240, occupancy:156 },
  { id:'D-S02', name:'Demo · Ambattur School Shelter', type:'Public school', location:[80.195,13.145], capacity:180, occupancy:137 },
  { id:'D-S03', name:'Demo · Triplicane Support Hub', type:'Relief centre', location:[80.275,13.06], capacity:320, occupancy:201 },
];

export const demoUnits = [
  { id:'D-U01', name:'Demo · Response Unit 01', type:'Supply vehicle', location:[80.245,13.085], inventory:{ Food:180, Water:240, Medical:36 } },
  { id:'D-U02', name:'Demo · Response Unit 02', type:'Field team', location:[80.21,13.105], inventory:{ Food:120, Water:180, Medical:24 } },
];

export const demoZones = [
  { id:'D-Z01', name:'Demo · North Chennai flood-risk area', coords:[[80.28,13.13],[80.32,13.14],[80.34,13.18],[80.31,13.20],[80.275,13.18],[80.28,13.13]] },
  { id:'D-Z02', name:'Demo · Central basin flood-risk area', coords:[[80.25,13.04],[80.29,13.035],[80.31,13.065],[80.29,13.09],[80.255,13.085],[80.25,13.04]] },
];

const popup = (title, rows) => {
  const box = document.createElement('div'); box.className = 'map-popup';
  const heading = document.createElement('strong'); heading.textContent = title; box.append(heading);
  for (const [label, value] of rows) { const row = document.createElement('div'); row.className = 'popup-row'; const key = document.createElement('span'); key.textContent = label; const val = document.createElement('b'); val.textContent = value; row.append(key, val); box.append(row); }
  const tag = document.createElement('small'); tag.textContent = 'SYNTHETIC DEMO DATA'; box.append(tag); return box;
};

export function initMap() {
  if (!window.L) throw new Error('Leaflet did not load. Check the internet connection and reload.');
  const map = L.map('map', { zoomControl:false, scrollWheelZoom:true }).setView([13.105,80.255], 11);
  L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', { maxZoom:19, attribution:'&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors' }).addTo(map);
  L.control.zoom({ position:'topright' }).addTo(map);

  const roadsLayer = L.layerGroup();
  demoRoads.forEach(road => {
    const line = L.polyline(road.coords.map(([lng,lat])=>[lat,lng]), { color:'#071522', weight:8, opacity:.82, lineCap:'round' }).addTo(roadsLayer);
    L.polyline(road.coords.map(([lng,lat])=>[lat,lng]), { color:COLORS[road.status], weight:4, opacity:.95, lineCap:'round' }).addTo(roadsLayer);
    line.bindPopup(popup(road.name, [['Road ID',road.id],['Status',road.status.toUpperCase()]]));
    roadsLayer.getLayers().at(-1).bindPopup(popup(road.name, [['Road ID',road.id],['Status',road.status.toUpperCase()]]));
  }); roadsLayer.addTo(map);

  const zonesLayer = L.layerGroup();
  demoZones.forEach(zone => L.polygon(zone.coords.map(([lng,lat])=>[lat,lng]), { color:'#ef7781', weight:1.5, fillColor:'#ef5968', fillOpacity:.16, dashArray:'5 5' }).bindPopup(popup(zone.name,[['Zone ID',zone.id],['Overlay','Flood risk (demo)']])).addTo(zonesLayer));
  zonesLayer.addTo(map);

  const sheltersLayer = L.layerGroup();
  demoShelters.forEach(s => L.circleMarker(s.location,{radius:8,color:'#071522',weight:3,fillColor:'#39b8c7',fillOpacity:1}).bindPopup(popup(s.name,[['Shelter ID',s.id],['Occupancy',`${s.occupancy} / ${s.capacity}`],['Available',`${s.capacity-s.occupancy} places`]])).addTo(sheltersLayer)); sheltersLayer.addTo(map);
  const unitsLayer = L.layerGroup();
  demoUnits.forEach(u => L.marker(u.location,{icon:L.divIcon({className:'unit-map-icon',html:'✚',iconSize:[27,27],iconAnchor:[13,13]})}).bindPopup(popup(u.name,[['Unit ID',u.id],['Type',u.type],['Status','Available (demo)']])).addTo(unitsLayer)); unitsLayer.addTo(map);
  L.control.layers(null, { 'Synthetic road network':roadsLayer, 'Flood-risk zones (demo)':zonesLayer, 'Demo shelters':sheltersLayer, 'Demo relief units':unitsLayer }, { position:'topleft', collapsed:true }).addTo(map);
  const bounds = L.featureGroup([roadsLayer,zonesLayer,sheltersLayer,unitsLayer]); map.fitBounds(bounds.getBounds().pad(.08));
  document.getElementById('map-reset').addEventListener('click',()=>map.fitBounds(bounds.getBounds().pad(.08)));
  return map;
}
