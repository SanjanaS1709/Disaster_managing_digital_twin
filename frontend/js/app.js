import { DEMO_MODE, IST_OFFSET_MINUTES } from './config.js';
import { demoRoads, demoShelters, demoUnits, demoZones, initMap } from './map.js';

const fmt = n => new Intl.NumberFormat('en-IN').format(n);
function updateClock() {
  const now = new Date(Date.now() + IST_OFFSET_MINUTES * 60_000);
  const time = new Intl.DateTimeFormat('en-IN',{timeZone:'UTC',hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false}).format(now);
  document.getElementById('header-time').textContent = `${time} IST`;
}

// Metrics are derived from the same local synthetic dataset rendered on the map.
function renderMetrics() {
  const blocked = demoRoads.filter(r=>r.status==='blocked').length;
  const open = demoRoads.filter(r=>r.status==='open').length;
  const restricted = demoRoads.filter(r=>r.status==='restricted').length;
  const occupied = demoShelters.reduce((sum,s)=>sum+s.occupancy,0);
  const capacity = demoShelters.reduce((sum,s)=>sum+s.capacity,0);
  const values = { 'metric-roads':fmt(demoRoads.length), 'metric-blocked':fmt(blocked), 'metric-restricted':fmt(restricted), 'metric-shelters':fmt(demoShelters.length), 'metric-occupancy':`${Math.round(occupied/capacity*100)}% occupancy`, 'metric-capacity':fmt(capacity-occupied), 'metric-units':fmt(demoUnits.length), 'metric-supplies':`${new Set(demoUnits.flatMap(u=>Object.keys(u.inventory))).size} supply categories`, 'metric-zones':fmt(demoZones.length), 'shelter-count':demoShelters.length, 'unit-count':demoUnits.length };
  for (const [id,value] of Object.entries(values)) { const el=document.getElementById(id); if(el)el.textContent=value; }
  document.getElementById('chart-road-total').textContent = fmt(demoRoads.length);
  document.getElementById('road-open').textContent = fmt(open);
  document.getElementById('road-restricted').textContent = fmt(restricted);
  document.getElementById('road-blocked').textContent = fmt(blocked);
  document.querySelector('.status-donut').style.setProperty('--open-end', `${open/demoRoads.length*100}%`);
  document.querySelector('.status-donut').style.setProperty('--restricted-end', `${(open+restricted)/demoRoads.length*100}%`);
}
function addText(parent,tag,className,value){const el=document.createElement(tag);if(className)el.className=className;el.textContent=value;parent.append(el);return el;}
function renderShelters(){
  const root=document.getElementById('shelter-list');
  demoShelters.forEach(s=>{const item=document.createElement('div');item.className='shelter-row';const icon=addText(item,'span','shelter-row-icon','⌂');icon.setAttribute('aria-hidden','true');const info=document.createElement('div');info.className='shelter-info';addText(info,'b','',s.name.replace('Demo · ',''));addText(info,'span','',`${s.id} · ${s.type}`);item.append(info);const stats=document.createElement('div');stats.className='shelter-stats';const top=document.createElement('div');top.className='shelter-stat-line';addText(top,'b','',`${Math.round(s.occupancy/s.capacity*100)}%`);addText(top,'span','',`${s.occupancy} / ${s.capacity}`);stats.append(top);const bar=document.createElement('div');bar.className='capacity-track';const fill=document.createElement('span');fill.style.width=`${Math.min(100,s.occupancy/s.capacity*100)}%`;if(s.occupancy/s.capacity>.75)fill.classList.add('high');bar.append(fill);stats.append(bar);item.append(stats);root.append(item);});
}
function renderUnits(){
  const root=document.getElementById('unit-list');
  demoUnits.forEach(unit=>{const item=document.createElement('div');item.className='unit-row';const icon=addText(item,'span','unit-row-icon','✚');icon.setAttribute('aria-hidden','true');const info=document.createElement('div');info.className='unit-info';addText(info,'b','',unit.name.replace('Demo · ',''));addText(info,'span','',`${unit.id} · ${unit.type}`);item.append(info);const supplies=document.createElement('div');supplies.className='supply-list';Object.entries(unit.inventory).forEach(([kind,count])=>{const chip=document.createElement('span');chip.className='supply-chip';addText(chip,'b','',fmt(count));addText(chip,'small','',kind);supplies.append(chip);});item.append(supplies);root.append(item);});
}
function init(){
  updateClock();setInterval(updateClock,1000);renderMetrics();renderShelters();renderUnits();if(DEMO_MODE)document.documentElement.dataset.mode='demo';
  try{initMap();}catch(error){const map=document.getElementById('map');map.classList.add('map-error');const message=document.createElement('div');message.className='map-error-message';message.textContent=error.message;map.append(message);}
  document.querySelectorAll('.rail-link[href^="#"]').forEach(link=>link.addEventListener('click',()=>{document.querySelectorAll('.rail-link').forEach(item=>item.classList.remove('active'));link.classList.add('active');}));
  document.querySelectorAll('.queue-item').forEach(item=>item.addEventListener('click',()=>{document.querySelectorAll('.queue-item').forEach(entry=>entry.classList.remove('selected'));item.classList.add('selected');}));
}
init();
