/* AeroDrop operations console. Every flight command goes through the local API. */
'use strict';
const $ = id => document.getElementById(id);
const setText = (id,value) => {const node=$(id);if(node.textContent!==String(value))node.textContent=value;};
const icon = name => `<svg aria-hidden="true"><use href="#icon-${name}"/></svg>`;
const escapeHTML = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const finite = value => typeof value === 'number' && Number.isFinite(value);
const fmt = (value, digits = 1) => finite(value) ? value.toFixed(digits) : '—';
const seconds = value => { const n = Math.max(0, Math.floor(Number(value) || 0)); return `${String(Math.floor(n / 60)).padStart(2, '0')}:${String(n % 60).padStart(2, '0')}`; };
const distanceLabel = value => finite(value) ? value >= 1000 ? `${(value / 1000).toFixed(2)} km` : `${Math.round(value)} m` : '—';
const IDLE = new Set(['READY', 'COMPLETE', 'ABORTED', 'ERROR']);
const OPERABLE = new Set(['TAKEOFF', 'NAVIGATING', 'DELIVERY_HOVER', 'WINCH_DROP']);
const TERMINAL = new Set(['COMPLETE', 'ABORTED', 'ERROR']);
const PHASE = {ARMING:0, TAKEOFF:0, NAVIGATING:1, DELIVERY_HOVER:2, WINCH_DROP:2, RETURNING:3, RTL_FAILSAFE:3, LANDING:4, COMPLETE:5, ABORTED:5};
const TITLES = {WAITING_FOR_POSITION:'Waiting for position', READY:'Ready', ARMING:'Arming', TAKEOFF:'Taking off', NAVIGATING:'In transit', DELIVERY_HOVER:'At drop-off', WINCH_DROP:'Delivering', RETURNING:'Returning', LANDING:'Landing', COMPLETE:'Complete', ABORTED:'Returned safely', RTL_FAILSAFE:'Failsafe return', ERROR:'Needs attention'};
const NAV = {0:'Manual', 1:'Altitude control', 2:'Position control', 3:'Mission', 4:'Hold', 5:'Return to launch', 14:'Offboard', 17:'Auto takeoff', 18:'Auto land'};
let live = null;
window.live = null;
const state = {
  config:null, api:false, wsConnected:false, lastMessage:0, planned:null,
  plannedKey:null, planSequence:0, planning:false, pending:null, mission:null,
  events:[], history:loadHistory(), samples:[], trail:[], view:'overview', tab:'activity',
  picking:false, following:false, mapReady:false, routeDirty:true, previousState:null,
  previousMission:null, previousAck:null, previousDetail:null, snapshotRenderedAt:0,
  lastMissionFetch:0, dialogAction:null, launchIntent:null, controlsLocked:false,
};
let map, homeMarker, destinationMarker, aircraftMarker, socket, reconnectTimer, planTimer;
const emptyGeo = () => ({type:'FeatureCollection', features:[]});
function loadHistory() { try { const data = JSON.parse(localStorage.getItem('aerodrop.missions.v1') || '[]'); return Array.isArray(data) ? data.filter(x => x && typeof x.id === 'string').slice(0, 30) : []; } catch { return []; } }
function saveHistory() { try { localStorage.setItem('aerodrop.missions.v1', JSON.stringify(state.history.slice(0,30))); } catch { /* Private mode may disable local storage. */ } }
function age(key) { return live && finite(live[key]) ? Math.max(0, Date.now()/1000-live[key]) : Infinity; }
function positionFresh() { return age('position_timestamp_unix') < (state.config?.position_freshness_seconds || 3); }
function streamFresh() { return state.wsConnected && Date.now()-state.lastMessage < 3000; }
function controllerFresh() { return age('status_timestamp_unix') < 3; }
function missionControllerFresh() { return age('mission_state_timestamp_unix') < 3; }
// The same health conditions the API enforces for in-flight commands; rechecked when a dialog confirms.
function actionReady() { return !!state.api&&streamFresh()&&positionFresh()&&controllerFresh()&&missionControllerFresh()&&OPERABLE.has(live?.state); }
function idle() { return !!live && IDLE.has(live.state) && !live.armed; }
function isActive() { return !!live && !IDLE.has(live.state) && live.state !== 'WAITING_FOR_POSITION'; }
function payloadLabel(value) { return ({RELEASED:'Payload released', SKIPPED:'Delivery skipped', LOWERING:'Releasing payload', PENDING:'On board', UNKNOWN:'Unknown after controller restart'})[value] || 'Awaiting mission'; }
function coordinates() {
  const names = ['startLat','startLon','destLat','destLon'];
  const values = names.map(name => $(name).value.trim() === '' ? NaN : Number($(name).value));
  if (values.some(x => !Number.isFinite(x))) throw Error('Enter a valid number for each coordinate.');
  if (Math.abs(values[0]) > 90 || Math.abs(values[2]) > 90 || Math.abs(values[1]) > 180 || Math.abs(values[3]) > 180) throw Error('Latitude must be −90 to 90; longitude must be −180 to 180.');
  return {start:values.slice(0,2), destination:values.slice(2)};
}
function signature(body) { return JSON.stringify([body.start, body.destination]); }
function errorMessage(data, fallback) {
  if (typeof data?.detail === 'string') return data.detail;
  if (Array.isArray(data?.detail)) return data.detail.map(x => `${(x.loc || []).filter(v=>v!=='body').join('.')}: ${x.msg}`).join('; ');
  return fallback;
}
async function fetchJSON(path, options={}) {
  const abort = new AbortController(); const timeout = setTimeout(()=>abort.abort(),7000);
  try {
    const response = await fetch(path,{...options, signal:abort.signal,
      headers:options.body ? {'Content-Type':'application/json',...options.headers} : options.headers});
    let data; try { data = await response.json(); } catch { throw Error('The server returned an unreadable response.'); }
    if (!response.ok) throw Error(errorMessage(data,`Request failed (${response.status}).`));
    return data;
  } catch (error) { if (error.name==='AbortError') throw Error('The request timed out. Check the backend connection.'); throw error; }
  finally { clearTimeout(timeout); }
}
function toast(message, error=false) {
  const node = document.createElement('div'); node.className=`toast${error?' error':''}`; node.textContent=message;
  $('toastRegion').append(node); setTimeout(()=>node.remove(),6000);
}
function event(message, category='mission', error=false) {
  state.events.unshift({time:new Date().toISOString(),message,category,error,mission_id:live?.mission_id || null});
  state.events=state.events.slice(0,150); renderEvents();
}
function renderEvents() {
  $('eventCount').textContent=state.events.length;
  const filter=$('eventFilter').value;
  const entries=state.events.filter(item=>filter==='all'||item.category===filter);
  $('eventList').innerHTML=entries.length ? entries.slice(0,60).map(item=>`<div class="event-row${item.error?' error':''}"><span class="event-dot"></span><span>${escapeHTML(item.message)}</span><time datetime="${item.time}">${new Date(item.time).toLocaleTimeString([], {hour:'2-digit',minute:'2-digit',second:'2-digit'})}</time></div>`).join('') : '<div class="event-empty">No events to show yet.<br>Flight and connection updates will appear here.</div>';
}
function setView(view) {
  if (!['overview','history','systems'].includes(view)) return;
  state.view=view;
  for (const name of ['overview','history','systems']) $(`${name}View`).hidden=name!==view;
  document.querySelectorAll('.nav-button[data-view]').forEach(button=>{
    button.classList.toggle('active',button.dataset.view===view);
    if(button.dataset.view===view) button.setAttribute('aria-current','page'); else button.removeAttribute('aria-current');
  });
  const titles={overview:['Every delivery. In view.','Plan a route, dispatch your drone, and follow the journey home.'], history:['The story of every flight.','Review missions observed on this device and export your flight record.'], systems:['A clear signal. End to end.','Inspect the services and live aircraft data behind every delivery.']};
  $('pageTitle').textContent=titles[view][0]; $('pageSubtitle').textContent=titles[view][1];
  if(view==='overview') requestAnimationFrame(()=>map?.resize());
  if(view==='history') renderHistory();
  if(view==='systems') renderSystems();
}
function setTab(tab) {
  if(!['activity','telemetry','waypoints'].includes(tab))return;
  state.tab=tab;
  for(const name of ['activity','telemetry','waypoints']) {
    $(`${name}Tab`).hidden=name!==tab;
    $(`${name}TabButton`).setAttribute('aria-selected',String(name===tab));
    $(`${name}TabButton`).tabIndex=name===tab?0:-1;
  }
  if(tab==='telemetry') renderTelemetry();
  if(tab==='waypoints') renderWaypoints();
}
function download(name, data) {
  const blob=new Blob([JSON.stringify(data,null,2)],{type:'application/json'});
  const url=URL.createObjectURL(blob); const link=document.createElement('a'); link.href=url;link.download=name;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
  toast('Export downloaded.');
}
function snapshot() { return {exported_at:new Date().toISOString(),telemetry:live,mission:state.mission,planned_route:state.planned,configuration:state.config,events:state.events}; }
function polarPoint(lat,lon,north,east) { return [lon+east/(111320*Math.cos(lat*Math.PI/180)),lat+north/111320]; }
function distance(a,b) {
  const lat=(a[1]+b[1])/2*Math.PI/180;
  return Math.hypot((a[0]-b[0])*111320*Math.cos(lat),(a[1]-b[1])*111320);
}
function pointsDistance(points) { return points.slice(1).reduce((sum,p,i)=>sum+distance([points[i].longitude,points[i].latitude],[p.longitude,p.latitude]),0); }
function corridorFeature(points,halfWidth) {
  const features=[];
  for(let i=1;i<points.length;i++) {
    const a=points[i-1],b=points[i],scale=Math.cos((a.latitude+b.latitude)/2*Math.PI/180);
    const dx=(b.longitude-a.longitude)*111320*scale,dy=(b.latitude-a.latitude)*111320;
    const len=Math.hypot(dx,dy);if(len<.01)continue;
    const e=-dy/len*halfWidth,n=dx/len*halfWidth;
    const ring=[polarPoint(a.latitude,a.longitude,n,e),polarPoint(b.latitude,b.longitude,n,e),polarPoint(b.latitude,b.longitude,-n,-e),polarPoint(a.latitude,a.longitude,-n,-e)];ring.push(ring[0]);
    features.push({type:'Feature',properties:{},geometry:{type:'Polygon',coordinates:[ring]}});
  }
  return {type:'FeatureCollection',features};
}
function displayedRoute() {
  if(isActive() && state.mission?.id===live?.mission_id) return state.mission.waypoints || [];
  return state.planned?.waypoints || [];
}
function setGeo(name,data) { if(state.mapReady && map?.getSource(name)) map.getSource(name).setData(data); }
function drawRoute() {
  const points=displayedRoute();
  setGeo('route',points.length>1 ? {type:'Feature',properties:{},geometry:{type:'LineString',coordinates:points.map(p=>[p.longitude,p.latitude])}} : emptyGeo());
  setGeo('corridor',corridorFeature(points,state.config?.corridor_half_width_m || 30));
  setGeo('waypoint-points',{type:'FeatureCollection',features:points.map((p,i)=>({type:'Feature',properties:{index:i+1},geometry:{type:'Point',coordinates:[p.longitude,p.latitude]}}))});
  if(state.mapReady && map.getLayer('route-line')) map.setPaintProperty('route-line','line-opacity',state.routeDirty && !isActive() ? 0.4 : 1);
  renderWaypoints();
}
function markerElement(letter,label,type) {
  const node=document.createElement('div');node.className=`point-marker ${type}`;
  node.innerHTML=`<span class="marker-letter">${letter}</span><span class="marker-caption">${label}</span>`;
  node.setAttribute('role','img');node.setAttribute('aria-label',label);return node;
}
function updateMarkers() {
  if(!map || !window.maplibregl)return;
  let body;try{body=coordinates();}catch{return;}
  if(!homeMarker) homeMarker=new maplibregl.Marker({element:markerElement('A','LAUNCH','home'),anchor:'bottom'}).setLngLat([body.start[1],body.start[0]]).addTo(map);
  else homeMarker.setLngLat([body.start[1],body.start[0]]);
  const active=isActive() && state.mission?.id===live?.mission_id;
  const destination=active ? state.mission.waypoints?.at(-1) : {latitude:body.destination[0],longitude:body.destination[1]};
  if(!destination)return;
  if(!destinationMarker) {
    destinationMarker=new maplibregl.Marker({element:markerElement('B','DROP-OFF','destination'),draggable:true,anchor:'bottom'}).setLngLat([destination.longitude,destination.latitude]).addTo(map);
    destinationMarker.on('dragend',()=>{const p=destinationMarker.getLngLat();setDestination(p.lat,p.lng,true);});
  } else destinationMarker.setLngLat([destination.longitude,destination.latitude]);
  destinationMarker.setDraggable(!isActive());
}
function fitRoute() {
  const route=displayedRoute(); if(!map || route.length<2)return;
  const bounds=new maplibregl.LngLatBounds();route.forEach(p=>bounds.extend([p.longitude,p.latitude]));
  map.fitBounds(bounds,{padding:55,maxZoom:15,duration:700});
}
function setupMap() {
  if(map.getSource('route'))return;
  const sources={zones:state.config?.zones || emptyGeo(),buffers:state.config?.buffers || emptyGeo(),corridor:emptyGeo(),route:emptyGeo(),trail:emptyGeo(),'waypoint-points':emptyGeo()};
  Object.entries(sources).forEach(([id,data])=>map.addSource(id,{type:'geojson',data}));
  map.addLayer({id:'buffers-fill',type:'fill',source:'buffers',paint:{'fill-color':'#d9ab64','fill-opacity':.12}});
  map.addLayer({id:'buffers-line',type:'line',source:'buffers',paint:{'line-color':'#c69b59','line-width':1,'line-dasharray':[3,3],'line-opacity':.7}});
  map.addLayer({id:'zones-fill',type:'fill',source:'zones',paint:{'fill-color':'#c97662','fill-opacity':.22}});
  map.addLayer({id:'zones-line',type:'line',source:'zones',paint:{'line-color':'#bc705d','line-width':1.3}});
  map.addLayer({id:'corridor-fill',type:'fill',source:'corridor',paint:{'fill-color':'#8dab62','fill-opacity':.16}});
  map.addLayer({id:'route-outline',type:'line',source:'route',paint:{'line-color':'#f6faef','line-width':6,'line-opacity':.8}});
  map.addLayer({id:'route-line',type:'line',source:'route',paint:{'line-color':'#5d7e43','line-width':2.8}});
  map.addLayer({id:'trail-line',type:'line',source:'trail',paint:{'line-color':'#ad8c56','line-width':2,'line-dasharray':[2,2]}});
  map.addLayer({id:'waypoints-circles',type:'circle',source:'waypoint-points',paint:{'circle-radius':4,'circle-color':'#eff7e4','circle-stroke-width':1.5,'circle-stroke-color':'#789656'}});
  state.mapReady=true;$('mapLoading').hidden=true;updateMarkers();drawRoute();drawTrail();applyLayers();
  map.resize();
}
function initializeMap() {
  if(!window.maplibregl) {
    $('mapLoading').innerHTML='<span>Map renderer unavailable. You can still plan with coordinates.</span>';
    event('Map renderer unavailable; coordinate planning remains available.','connection',true);return;
  }
  try {
    map=new maplibregl.Map({container:'map',style:'https://tiles.openfreemap.org/styles/liberty',center:[-114.1055,51.119],zoom:12.8,attributionControl:true});
    map.addControl(new maplibregl.NavigationControl({showCompass:false}),'top-right');
    map.on('style.load',setupMap);
    let mapErrors=0;
    map.on('error',()=>{mapErrors++;if(mapErrors>2){$('mapNotice').textContent='Some basemap tiles are unavailable. Mission overlays remain active.';$('mapNotice').hidden=false;}});
    map.on('click',e=>{if(state.picking && !isActive()){setDestination(e.lngLat.lat,e.lngLat.lng,true);setPicking(false);}});
    map.on('mousemove',e=>{$('mapPosition').textContent=`${e.lngLat.lat.toFixed(5)}° / ${e.lngLat.lng.toFixed(5)}°`;});
    map.on('dragstart',()=>{if(state.following){state.following=false;$('followDrone').setAttribute('aria-pressed','false');}});
    map.on('click','zones-fill',e=>{if(!state.picking)toast(`Synthetic zone: ${e.features?.[0]?.properties?.id || 'restricted area'}. Includes a 100 m buffer.`);});
    setTimeout(()=>{if(!state.mapReady){$('mapNotice').textContent='Basemap unavailable. Route planning and mission overlays remain interactive.';$('mapNotice').hidden=false;map.setStyle({version:8,sources:{},layers:[{id:'background',type:'background',paint:{'background-color':'#e9eee0'}}]});}},9000);
  } catch(error) {$('mapLoading').textContent='This browser could not initialize the map. Coordinate controls remain available.';event(error.message,'connection',true);}
}
function applyLayers() {
  if(!state.mapReady)return;
  const groups={showZones:['zones-fill','zones-line'],showBuffers:['buffers-fill','buffers-line'],showCorridor:['corridor-fill','route-outline','route-line','waypoints-circles'],showTrail:['trail-line']};
  for(const [control,layers]of Object.entries(groups))for(const layer of layers)if(map.getLayer(layer))map.setLayoutProperty(layer,'visibility',$(control).checked?'visible':'none');
}
function drawTrail() { setGeo('trail',state.trail.length>1 ? {type:'Feature',properties:{},geometry:{type:'LineString',coordinates:state.trail}} : emptyGeo()); }
function setPicking(value) {
  state.picking=value&&!isActive();$('pickDestination').setAttribute('aria-pressed',String(state.picking));$('mapInstruction').hidden=!state.picking;
  if(map)map.getCanvas().style.cursor=state.picking?'crosshair':'';
  if(state.picking){setView('overview');$('map').scrollIntoView({behavior:'smooth',block:'center'});}
}
function markDirty() {
  state.routeDirty=true;state.planSequence++;state.planning=false;
  $('routeMessage').textContent='Route changed. Plan the corridor again before launch.';$('routeMessage').className='inline-message';
  updateMarkers();drawRoute();renderControls();
}
function setDestination(lat,lon,autoPlan=false) {
  if(isActive())return;
  $('destLat').value=lat.toFixed(6);$('destLon').value=lon.toFixed(6);$('preset').value='custom';markDirty();
  if(autoPlan){clearTimeout(planTimer);planTimer=setTimeout(()=>planRoute(false),350);}
}
async function planRoute(fit=true) {
  let body;try{body=coordinates();}catch(error){$('routeMessage').textContent=error.message;$('routeMessage').className='inline-message error';state.planned=null;state.routeDirty=true;drawRoute();renderControls();return;}
  if(isActive())return;
  const request=++state.planSequence;state.planning=true;renderControls();
  $('routeMessage').textContent='Checking restrictions and calculating a safe corridor…';$('routeMessage').className='inline-message';
  try {
    const route=await fetchJSON('/route',{method:'POST',body:JSON.stringify(body)});
    if(request!==state.planSequence)return;
    if(!Array.isArray(route.waypoints)||route.waypoints.length<2)throw Error('The planner did not return a complete route.');
    state.planned=route;state.plannedKey=signature(body);state.routeDirty=false;
    $('routeMessage').textContent='Corridor planned. Review the route, then launch when ready.';$('routeMessage').className='inline-message success';
    renderRouteSummary();drawRoute();updateMarkers();if(fit)fitRoute();
    event(`Corridor planned · ${distanceLabel(route.distance_m)} · ${route.waypoints.length} waypoints.`,'mission');
  }catch(error){if(request!==state.planSequence)return;state.planned=null;state.routeDirty=true;renderRouteSummary();drawRoute();$('routeMessage').textContent=error.message;$('routeMessage').className='inline-message error';event(`Route planning failed: ${error.message}`,'safety',true);}
  finally{if(request===state.planSequence){state.planning=false;renderControls();}}
}
function renderRouteSummary() {
  const route=state.planned;
  if(!route){$('details').innerHTML='<div class="empty-small">No valid corridor. Adjust the destination and plan again.</div>';return;}
  const bypassed=route.bypassed_zones || [];
  $('details').innerHTML=`<div class="summary-grid"><div class="summary-item"><span>ROUTE</span><strong>${escapeHTML(distanceLabel(route.distance_m))}</strong></div><div class="summary-item"><span>WAYPOINTS</span><strong>${route.waypoints.length}</strong></div><div class="summary-item"><span>ALTITUDE</span><strong>${fmt(route.waypoints[0].altitude_m,0)} <small>m</small></strong></div><div class="summary-item"><span>CORRIDOR</span><strong>${fmt(route.corridor_half_width_m*2,0)} <small>m wide</small></strong></div></div><div class="route-clearance${bypassed.length?' warning':''}">${icon('check')}<span>${bypassed.length?`Detouring around ${bypassed.length} synthetic restricted zone${bypassed.length>1?'s':''}.`:'Direct corridor clear of synthetic restrictions.'}</span></div>`;
}
function launchBlocker() {
  if(!state.api)return 'The backend is not responding.';
  if(!state.config)return 'Waiting for flight configuration from the backend.';
  if(!streamFresh())return 'Waiting for a live telemetry connection.';
  if(!positionFresh())return 'Waiting for a fresh aircraft position.';
  if(!controllerFresh())return 'Waiting for a fresh PX4 status report.';
  if(!missionControllerFresh())return 'Mission controller is not reporting. Check the aerodrop_ros_node container.';
  if(state.pending)return 'Waiting for the aircraft to acknowledge the command.';
  if(live?.attention_required)return live.detail||'Landing not confirmed. Operator attention required.';
  if(isActive())return 'A mission is in progress. Monitor the flight or request return.';
  if(live?.armed)return 'The aircraft must be disarmed before a new delivery.';
  if(live?.landed!==true)return 'Waiting for PX4 to confirm the aircraft is landed.';
  if(age('land_timestamp_unix')>=3)return 'Waiting for a fresh PX4 landing report.';
  if(age('battery_timestamp_unix')>=3)return 'Waiting for a fresh battery report.';
  if(live?.px4_failsafe)return 'PX4 reports an active failsafe. Resolve it before dispatch.';
  if(!idle())return isActive()?'A mission is already in progress.':'Waiting for the aircraft to become ready.';
  if(state.planning)return 'The route planner is calculating a corridor.';
  if(!state.planned||state.routeDirty)return 'Plan the corridor before launching.';
  try{if(signature(coordinates())!==state.plannedKey)return 'Coordinates changed. Plan the route again.';}catch(error){return error.message;}
  const launchBattery=live.low_battery_injected?live.px4_battery_pct:live.battery_pct;
  if(!finite(launchBattery))return 'Waiting for the aircraft battery report.';
  if(launchBattery<20)return 'Battery below 20%. Resolve the low-battery condition before dispatch.';
  return '';
}
function renderControls() {
  const blocker=launchBlocker();
  $('startMission').disabled=!!blocker;$('launchReason').textContent=blocker || (live?.preflight_checks_pass===false?'Ready for your review. PX4 preflight flag is not set; PX4 makes the final arming decision.':'Aircraft connected. Ready for your review.');
  const active=isActive();
  $('plannerFields').disabled=active;
  $('preset').disabled=active||!state.config;
  $('batterySchedule').disabled=active||!!state.pending;
  $('routeButton').disabled=state.planning||active||!state.api;
  $('routeButton').innerHTML=state.planning ? '<span class="spinner"></span>Planning corridor…' : `${icon('map')}Plan corridor${icon('arrow')}`;
  $('routeButton').querySelector('svg:last-child')?.classList.add('button-end');
  const operational=actionReady()&&!state.pending;
  for(const id of ['returnMission','batteryTest','geofenceTest'])$(id).disabled=!operational;
  $('useDrone').disabled=active||!positionFresh();$('pickDestination').disabled=active||!state.mapReady;
  $('fitRoute').disabled=!state.mapReady||displayedRoute().length<2;
  $('followDrone').disabled=!state.mapReady||!positionFresh();
  $('copyMissionId').disabled=!live?.mission_id;
  const checks=[['Backend',state.api],['Live telemetry',streamFresh()],['Position fix',positionFresh()],['Mission controller',missionControllerFresh()],['Disarmed',!!live&&!live.armed&&controllerFresh()]];
  $('preflightChecks').innerHTML=checks.map(([label,good])=>`<span class="check-item${good?' good':''}"><i></i>${label}</span>`).join('');
  if(active&&state.picking)setPicking(false);
  destinationMarker?.setDraggable(!active);
}
function renderConnection() {
  $('apiPill').className=`pill ${state.api?'good':'bad'}`;$('apiPill').innerHTML=`<i></i>${state.api?'API online':'API offline'}`;
  const fresh=streamFresh()&&positionFresh();
  $('streamPill').className=`pill ${fresh?'good':'bad'}`;$('streamPill').innerHTML=`<i></i>${fresh?'Aircraft connected':state.wsConnected?'Waiting for position':'Telemetry offline'}`;
  const message=!state.api?'The backend is unavailable. Reconnecting automatically; flight commands are disabled.':!streamFresh()?'Live telemetry is unavailable. Last readings are retained for reference; flight commands are disabled.':!positionFresh()?'Position data is stale. Waiting for the simulator’s next valid position.':'';
  $('connectionBanner').hidden=!message;setText('connectionBanner',message);
}
function renderMetrics() {
  const fresh=positionFresh()&&streamFresh();
  $('metricState').textContent=live?TITLES[live.state]||live.state:'Connecting';
  $('metricStateDetail').textContent=!fresh?'Waiting for fresh telemetry':!controllerFresh()?'Waiting for controller status':live?.armed?'Aircraft armed · '+(NAV[live.px4_nav_state]||`PX4 mode ${live.px4_nav_state}`):'Aircraft disarmed';
  $('altitudeValue').textContent=fmt(live?.altitude_m);$('speedValue').textContent=fmt(live?.horizontal_speed_m_s);
  $('verticalValue').textContent=finite(live?.vertical_speed_m_s)?`${Math.abs(live.vertical_speed_m_s)<.05?'Vertical':live.vertical_speed_m_s>=0?'Climbing':'Descending'} ${Math.abs(live.vertical_speed_m_s).toFixed(1)} m/s`:'Vertical speed not reported';
  $('batteryValue').textContent=fmt(live?.battery_pct,0);$('batteryNote').textContent=live?.low_battery_injected?`Simulated low battery · PX4 ${fmt(live.px4_battery_pct,0)}%`:finite(live?.battery_pct)?'Return threshold below 20%':'Waiting for battery report';
  $('batteryValue').closest('.metric').classList.toggle('low',finite(live?.battery_pct)&&live.battery_pct<20);
  $('headingValue').textContent=fmt(live?.heading_deg,0);
  $('headingNote').textContent=finite(live?.heading_deg)?['North','North-east','East','South-east','South','South-west','West','North-west'][Math.round(live.heading_deg/45)%8]:'Heading not reported';
  $('elapsedValue').textContent=seconds(live?.mission_elapsed_s);
  $('elapsedNote').textContent=live?.mission_id?TERMINAL.has(live.state)?'Mission ended':'Elapsed since dispatch':'No mission underway';
  $('armingState').textContent=live?live.armed?'Armed':'Disarmed':'Status unknown';
  $('telemetryAge').textContent=Number.isFinite(age('position_timestamp_unix'))?`${age('position_timestamp_unix')<1?'Live position':`Position ${age('position_timestamp_unix').toFixed(0)}s ago`}`:'No position fix';
  if(live&&finite(live.latitude)&&finite(live.longitude)&&map){
    if(!aircraftMarker&&positionFresh()){
      const node=document.createElement('div');node.className='aircraft-marker';node.innerHTML=icon('flight');node.setAttribute('role','img');node.setAttribute('aria-label','Live aircraft position');
      aircraftMarker=new maplibregl.Marker({element:node,rotationAlignment:'map',pitchAlignment:'map'}).setLngLat([live.longitude,live.latitude]).addTo(map);
    }
    if(aircraftMarker){aircraftMarker.setLngLat([live.longitude,live.latitude]);aircraftMarker.setRotation(finite(live.heading_deg)?live.heading_deg:0);aircraftMarker.getElement().style.opacity=fresh?'1':'.4';}
  }
}
function renderMission() {
  const code=live?.state;
  $('missionStateTag').textContent=code==='READY'?'READY':code==='COMPLETE'?'COMPLETE':code==='ABORTED'?'RETURNED':code==='WAITING_FOR_POSITION'||!code?'STANDBY':code.replaceAll('_',' ');
  $('missionStateTag').className=`state-tag${isActive()?' active':''}${code==='ERROR'||code==='RTL_FAILSAFE'?' error':''}`;
  $('missionId').textContent=live?.mission_id?live.mission_id.slice(0,8).toUpperCase():'Awaiting dispatch';$('missionId').title=live?.mission_id||'';
  $('payloadState').textContent=live?.mission_id?payloadLabel(live.payload_status):'Awaiting mission';
  $('payloadBadge').textContent=live?.payload_status==='RELEASED'?'DELIVERED':live?.payload_status==='SKIPPED'?'SKIPPED':live?.mission_id?'SIM':'—';
  setText('missionDetail',state.pending?'Command accepted. Waiting for the aircraft’s response…':live?.detail||({READY:'Your aircraft is ready. Plan a corridor to begin.',WAITING_FOR_POSITION:'Waiting for a valid simulator position.',NAVIGATING:`Following corridor · waypoint ${Math.min((live?.route_index||0)+1,live?.waypoint_count||0)} of ${live?.waypoint_count||'—'}.`,COMPLETE:'Aircraft landed and disarmed. Ready for another delivery.',ABORTED:'Aircraft returned, landed, and disarmed.'})[code]||'Flight status is updated by the mission executor.');
  $('missionDetail').classList.toggle('error',code==='ERROR'||code==='RTL_FAILSAFE');
  const phase=PHASE[code]??-1;
  document.querySelectorAll('#flightStages li').forEach((node,i)=>{
    const skipped=i===2 && live?.payload_status==='SKIPPED';
    node.classList.toggle('done',phase>i&&!skipped);
    node.classList.toggle('current',(phase===i||(code==='READY'&&i===0))&&!skipped);
    node.classList.toggle('skipped',skipped);
    node.querySelector('.stage-node').textContent=skipped?'–':phase>i?'✓':String(i+1);
  });
}
function renderWaypoints() {
  let points=displayedRoute();let current=live?.route_index;
  if(isActive()&&Array.isArray(live?.active_waypoints)&&live.active_waypoints.length)points=live.active_waypoints.map(p=>Array.isArray(p)?{latitude:p[0],longitude:p[1]}:p);
  $('waypointsDescription').textContent=points.length?`${points.length} waypoints · ${live?.state==='RETURNING'?'return corridor':'planned corridor'}`:'Plan a corridor to inspect waypoints.';
  $('waypointList').innerHTML=points.length?points.map((p,i)=>`<button class="waypoint-row${isActive()&&current===i?' current':''}" data-lat="${Number(p.latitude)}" data-lon="${Number(p.longitude)}" aria-label="Center map on waypoint ${i+1}"><span>${i+1}</span><span>${i===0?'Launch / start':i===points.length-1?'Final waypoint':'Corridor turn'}</span><code>${Number(p.latitude).toFixed(5)}, ${Number(p.longitude).toFixed(5)}</code></button>`).join(''):'<div class="event-empty">No route waypoints yet.</div>';
}
function renderTelemetry() {
  const metric=$('trendMetric').value;
  const samples=state.samples.filter(p=>finite(p[metric])&&Date.now()/1000-p.time<60);
  const units={altitude_m:'m',horizontal_speed_m_s:'m/s',battery_pct:'%'};
  $('trendValue').textContent=samples.length?`${fmt(samples.at(-1)[metric])} ${units[metric]}`:'Waiting for samples';
  $('sampleCount').textContent=`${samples.length} samples`;
  if(samples.length>1){
    const values=samples.map(x=>x[metric]);let min=Math.min(...values),max=Math.max(...values);if(max-min<1){min-=.5;max+=.5;}
    const t0=samples[0].time,t1=samples.at(-1).time;
    const coords=samples.map(p=>[t1===t0?0:(p.time-t0)/(t1-t0)*600,88-(p[metric]-min)/(max-min)*78]);
    const path=coords.map(([x,y],i)=>`${i?'L':'M'}${x.toFixed(1)},${y.toFixed(1)}`).join(' ');
    $('trendLine').setAttribute('d',path);$('trendArea').setAttribute('d',`${path} L600,95 L0,95 Z`);
  }else{$('trendLine').setAttribute('d','');$('trendArea').setAttribute('d','');}
  const fields=[['Position',live&&positionFresh()?`${fmt(live.latitude,5)}, ${fmt(live.longitude,5)}`:'No recent fix'],['PX4 mode',NAV[live?.px4_nav_state]|| (finite(live?.px4_nav_state)?`Mode ${live.px4_nav_state}`:'Not reported')],['Vehicle',live?live.armed?'Armed':live.landed===true?'Landed / disarmed':'Disarmed':'Unknown'],['Actual PX4 battery',finite(live?.px4_battery_pct)?`${fmt(live.px4_battery_pct,1)}%`:'Not reported'],['Last command',live?.command_ack?`${ackCommand(live.command_ack.command)} · ${ackResult(live.command_ack.result)}`:'No acknowledgement'],['Position age',Number.isFinite(age('position_timestamp_unix'))?`${fmt(age('position_timestamp_unix'),1)} s`:'No sample']];
  $('telemetryFields').innerHTML=fields.map(([label,value])=>`<div><dt>${label}</dt><dd title="${escapeHTML(value)}">${escapeHTML(value)}</dd></div>`).join('');
}
function renderHistory() {
  $('historyEmpty').hidden=state.history.length>0;
  $('missionHistory').innerHTML=state.history.map(item=>`<tr><td><code title="${escapeHTML(item.id)}">${escapeHTML(item.id.slice(0,8).toUpperCase())}</code></td><td>${item.timeSource==='mission'?'':'Observed '}${new Date(item.startedAt).toLocaleString([], {month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'})}</td><td><span class="state-tag${item.state==='ERROR'?' error':''}">${escapeHTML(TITLES[item.state]||item.state)}</span></td><td>${seconds(item.elapsed)}</td><td>${escapeHTML(payloadLabel(item.payload))}</td></tr>`).join('');
}
function renderSystems() {
  const positionAge=age('position_timestamp_unix');
  const systems=[
    ['system','Dispatch API',state.api?'Online':'Offline',state.api?'Route planning and mission commands are reachable.':'Waiting for a successful backend response.',state.api,'Health check: '+(state.api?'responding':'unavailable')],
    ['signal','Telemetry relay',streamFresh()?'Streaming':'No recent stream',state.wsConnected?'WebSocket connected to the local telemetry relay.':'Reconnecting automatically to the telemetry relay.',streamFresh(),state.lastMessage?`Last frame ${Math.max(0,(Date.now()-state.lastMessage)/1000).toFixed(1)} s ago`:'No frames received'],
    ['target','Position estimate',positionFresh()?'Fresh position':'Waiting',positionFresh()?'PX4 reports a valid horizontal and vertical position.':'No recent valid position. Launch remains disabled.',positionFresh(),Number.isFinite(positionAge)?`Sample age ${positionAge.toFixed(1)} s`:'No valid sample'],
    ['flight','Flight controller',controllerFresh()?'Connected':'Waiting',controllerFresh()?`${NAV[live?.px4_nav_state]||'PX4'} · ${live?.armed?'armed':'disarmed'}${live?.px4_failsafe?' · failsafe active':''}`:'Waiting for a recent PX4 vehicle-status message.',controllerFresh(),live?.preflight_checks_pass?'PX4 preflight checks pass':'PX4 preflight flag not set; PX4 makes the final arming decision'],
    ['system','Mission controller',missionControllerFresh()?(live?.recovered_after_restart?'Recovering':'Reporting'):'Not reporting',missionControllerFresh()?(live?.recovered_after_restart?'The executor restarted while the aircraft was armed and is recovering it (PX4 return-to-launch). The delivery will not resume.':'The ROS 2 mission executor is publishing its state.'):'No recent state from the ROS 2 mission executor. Launch and flight actions stay disabled.',missionControllerFresh(),Number.isFinite(age('mission_state_timestamp_unix'))?`Last report ${age('mission_state_timestamp_unix').toFixed(1)} s ago`:'No report received'],
    ['box','Payload release','Simulated','The mission executor performs a timed release; no winch hardware is connected.',false,payloadLabel(live?.payload_status)],
    ['layers','Airspace model','Synthetic zones',`${state.config?.zones?.features?.length??'—'} demo rectangles with ${state.config?.geofence_buffer_m??'—'} m safety buffers.`,!!state.config,'Not authoritative flight restrictions'],
  ];
  $('systemCards').innerHTML=systems.map(([glyph,name,label,description,good,readout])=>`<article class="card system-card"><div class="system-heading">${icon(glyph)}<span class="pill${good?' good':''}"><i></i>${label}</span></div><h3>${name}</h3><p>${escapeHTML(description)}</p><div class="system-readout">${escapeHTML(readout)}</div></article>`).join('');
  const capabilities=[['Route planning',state.config?.capabilities?.route_planning?'Connected':'Unavailable'],['Mission control',state.config?.capabilities?.mission_control?'Connected':'Unavailable'],['Live telemetry',state.config?.capabilities?.live_telemetry?'Supported':'Unavailable'],['Fault injection',state.config?.capabilities?.fault_injection?'Simulator controls':'Unavailable'],['Physical payload','Not connected'],['Order management','Not implemented'],['Mission history','Local to this browser']];
  $('capabilityList').innerHTML=capabilities.map(([label,value])=>`<div><dt>${label}</dt><dd>${value}</dd></div>`).join('');
  const config=state.config;
  $('configurationList').innerHTML=[['Cruise altitude',config?`${config.cruise_altitude_m} m`:'—'],['Corridor width',config?`${config.corridor_half_width_m*2} m`:'—'],['Geofence buffer',config?`${config.geofence_buffer_m} m`:'—'],['Low-battery return',config?`Below ${config.low_battery_threshold_pct}%`:'—'],['Position freshness',config?`Under ${config.position_freshness_seconds} seconds`:'—'],['Automatic battery test',state.mission?.simulate_low_battery_after_seconds>0?`After ${state.mission.simulate_low_battery_after_seconds} s`:'Off'],['Backend storage','One mission · in memory']].map(([label,value])=>`<div><dt>${label}</dt><dd>${escapeHTML(value)}</dd></div>`).join('');
  $('rawSnapshot').textContent=JSON.stringify({mission:state.mission,telemetry:live},null,2);
}
function ackCommand(code) {return ({176:'Flight mode',400:'Arming control',21:'Landing',20:'Return to launch'})[code]||`Command ${code}`;}
function ackResult(code) { return ({0:'accepted',1:'retry requested',2:'denied',3:'unsupported',4:'failed',5:'in progress',6:'cancelled'})[code]||`result ${code}`; }
function consumeTelemetry(data) {
  if(!data||!finite(data.latitude)||!finite(data.longitude))return;
  const previousState=state.previousState;
  live=data;window.live=data;
  if(data.mission_id&&data.mission_id!==state.previousMission) {
    state.previousMission=data.mission_id;state.trail=[];state.previousAck=null;
    event(`Observing mission ${data.mission_id.slice(0,8).toUpperCase()}.`,'mission');refreshMission();
  }
  if(data.state!==state.previousState) {
    event(`${TITLES[data.state]||data.state}${data.detail?' · '+data.detail:''}`,data.state==='RTL_FAILSAFE'||data.state==='ERROR'?'safety':'mission',data.state==='ERROR');
    state.previousState=data.state;state.previousDetail=data.detail;
    if(TERMINAL.has(data.state)){if(previousState&&!TERMINAL.has(previousState))toast(data.state==='COMPLETE'?'Mission complete. Aircraft landed and disarmed.':data.state==='ABORTED'?'Aircraft returned and landed safely.':data.detail||'Mission needs attention.',data.state==='ERROR');refreshMission();}
    drawRoute();updateMarkers();
  }else if(data.detail&&data.detail!==state.previousDetail){state.previousDetail=data.detail;event(data.detail,data.detail.includes('Battery')?'safety':'mission');}
  if(state.pending&&(data.mission_id===state.pending.id&&data.state!=='READY'||state.pending.action==='return'&&data.state==='RETURNING'||state.pending.action==='geofence'&&data.state==='RTL_FAILSAFE'||state.pending.action==='low_battery'&&data.low_battery_injected))state.pending=null;
  if(data.command_ack){
    const key=`${data.command_ack.command}:${data.command_ack.result}`;
    if(key!==state.previousAck){state.previousAck=key;event(`${ackCommand(data.command_ack.command)} ${ackResult(data.command_ack.result)} by PX4.`,data.command_ack.result===0?'command':'safety',![0,5].includes(data.command_ack.result));}
  }
  if(data.mission_id){
    let item=state.history.find(x=>x.id===data.mission_id);
    if(!item){item={id:data.mission_id,startedAt:finite(data.mission_started_unix)?data.mission_started_unix*1000:Date.now(),timeSource:finite(data.mission_started_unix)?'mission':'observed'};state.history.unshift(item);state.history=state.history.slice(0,30);}
    if(finite(data.mission_started_unix)){item.startedAt=data.mission_started_unix*1000;item.timeSource='mission';}
    Object.assign(item,{state:data.state,elapsed:data.mission_elapsed_s||0,payload:data.payload_status||null});
    if(TERMINAL.has(data.state))saveHistory();
  }
  const now=Date.now()/1000;
  if(!state.samples.length||now-state.samples.at(-1).time>.45){state.samples.push({time:now,altitude_m:data.altitude_m,horizontal_speed_m_s:data.horizontal_speed_m_s,battery_pct:data.battery_pct});state.samples=state.samples.filter(p=>now-p.time<=60);}
  if(positionFresh()&&data.armed){
    const point=[data.longitude,data.latitude];
    if(!state.trail.length||distance(state.trail.at(-1),point)>1){state.trail.push(point);state.trail=state.trail.slice(-1200);drawTrail();}
    if(state.following&&map)map.easeTo({center:point,duration:500});
  }
  renderMetrics();renderMission();renderControls();renderConnection();
  if(state.tab==='telemetry')renderTelemetry();if(state.tab==='waypoints')renderWaypoints();
  if(state.view==='history')renderHistory();if(state.view==='systems'&&Date.now()-state.snapshotRenderedAt>900){state.snapshotRenderedAt=Date.now();renderSystems();}
}
function connectTelemetry() {
  clearTimeout(reconnectTimer);
  const scheme=location.protocol==='https:'?'wss':'ws';
  socket=new WebSocket(`${scheme}://${location.host}/ws/telemetry/drone_01?role=viewer`);
  socket.addEventListener('open',()=>{state.wsConnected=true;event('Telemetry stream connected.','connection');renderConnection();renderControls();});
  socket.addEventListener('message',eventData=>{try{const data=JSON.parse(eventData.data);state.lastMessage=Date.now();consumeTelemetry(data);}catch{/* Ignore malformed frames without interrupting the stream. */}});
  socket.addEventListener('close',()=>{state.wsConnected=false;event('Telemetry disconnected. Reconnecting automatically.','connection',true);renderConnection();renderControls();reconnectTimer=setTimeout(connectTelemetry,1500);});
  socket.addEventListener('error',()=>socket.close());
}
async function refreshMission() {
  try{state.mission=await fetchJSON('/mission');drawRoute();updateMarkers();if(state.view==='systems')renderSystems();}catch{/* Health polling reports connection loss. */}
}
async function refreshHealth() {
  const before=state.api;
  try{await fetchJSON('/health');state.api=true;if(!state.config)await loadConfig();}catch{state.api=false;}
  if(before!==state.api)event(state.api?'Dispatch API connected.':'Dispatch API unavailable.','connection',!state.api);
  renderConnection();renderControls();if(state.view==='systems')renderSystems();
}
async function loadConfig() {
  state.config=await fetchJSON('/config');
  $('showBuffers').parentElement.lastChild.textContent=`${state.config.geofence_buffer_m} m safety buffer`;
  if(state.mapReady){setGeo('zones',state.config.zones);setGeo('buffers',state.config.buffers);}
}
async function postCommand(path,body,label) {
  const response=await fetchJSON(path,{method:'POST',body:JSON.stringify(body)});
  event(`${label} accepted by dispatch. Waiting for aircraft confirmation.`,'command');
  return response;
}
function dialogRows(rows) { return `<div class="dialog-summary">${rows.map(([label,value])=>`<div><span>${escapeHTML(label)}</span><strong>${escapeHTML(value)}</strong></div>`).join('')}</div>`; }
function openLaunch() {
  const blocker=launchBlocker();if(blocker){toast(blocker,true);return;}
  const body={...coordinates(),simulate_low_battery_after_seconds:Number($('batterySchedule').value)};
  state.launchIntent=body;state.dialogAction='launch';
  $('dialogEyebrow').textContent='MISSION CONFIRMATION';$('dialogTitle').textContent='Ready to dispatch?';
  $('dialogDescription').textContent='The simulator will arm, take off, deliver the payload, return to launch, land, and disarm.';
  $('dialogIcon').innerHTML=icon('flight');
  $('dialogDetails').innerHTML=dialogRows([['Aircraft','DRONE 01'],['Destination',`${body.destination[0].toFixed(5)}, ${body.destination[1].toFixed(5)}`],['Route',`${distanceLabel(state.planned.distance_m)} · ${state.planned.waypoints.length} waypoints`],['Cruise altitude',`${state.config.cruise_altitude_m} m`],['Payload','Timed simulation'],['Automatic battery test',body.simulate_low_battery_after_seconds>0?`After ${body.simulate_low_battery_after_seconds} seconds`:'Off']]);
  $('dialogConfirm').innerHTML=`Launch simulation ${icon('arrow')}`;$('dialogCancel').textContent='Keep planning';$('dialogError').hidden=true;$('dialogConfirm').disabled=false;
  $('actionDialog').showModal();
}
function openAction(action) {
  if(!state.api||!streamFresh()||!OPERABLE.has(live?.state)||state.pending)return;
  state.dialogAction=action;state.launchIntent=null;
  const copy={
    return:['OPERATOR COMMAND','Return and land?','Skip any remaining delivery steps and return along the reached corridor. The aircraft will land and disarm.','return','Confirm return'],
    low_battery:['SIMULATOR FAULT INJECTION','Simulate low battery?','Inject a 19% battery condition. The mission skips delivery and retraces the safe path home. The actual PX4 battery reading is retained.','battery','Inject low battery'],
    geofence:['SIMULATOR FAULT INJECTION','Test geofence return?','Inject a synthetic geofence breach. PX4 takes over with native return-to-launch, lands, and disarms. The mission ends as returned safely.','warning','Inject geofence fault'],
  }[action];
  if(!copy)return;
  $('dialogEyebrow').textContent=copy[0];$('dialogTitle').textContent=copy[1];$('dialogDescription').textContent=copy[2];$('dialogIcon').innerHTML=icon(copy[3]);
  $('dialogDetails').innerHTML=dialogRows([['Aircraft','DRONE 01'],['Current mission',live.mission_id?.slice(0,8).toUpperCase()||'—'],['Scope','Local flight simulation']]);
  $('dialogConfirm').textContent=copy[4];$('dialogCancel').textContent='Keep flying';$('dialogError').hidden=true;$('dialogConfirm').disabled=false;$('actionDialog').showModal();
}
async function confirmDialog() {
  const action=state.dialogAction;if(!action)return;
  $('dialogConfirm').disabled=true;$('dialogCancel').disabled=true;$('dialogError').hidden=true;
  try {
    if(action==='launch'){
      const blocker=launchBlocker();if(blocker)throw Error(blocker);
      if(signature(state.launchIntent)!==signature(coordinates()))throw Error('The destination changed. Close this dialog and review the new route.');
      const mission=await postCommand('/mission',state.launchIntent,'Launch command');
      state.mission=mission;state.pending={id:mission.id,time:Date.now()};state.trail=[];drawTrail();
    }else{
      if(!actionReady())throw Error('The aircraft or its controller is no longer in a state that accepts this command. Nothing was sent.');
      await postCommand('/mission/action',{name:action},({return:'Return command',low_battery:'Low-battery test',geofence:'Geofence test'})[action]);
      state.pending={action,time:Date.now()};
    }
    $('actionDialog').close();document.querySelector('.mission-panel').focus({preventScroll:true});toast(action==='launch'?'Launch queued. Waiting for the aircraft.':'Command queued. Watch the mission panel for confirmation.');renderControls();renderMission();
  }catch(error){$('dialogError').textContent=error.message;$('dialogError').hidden=false;event(`Command failed: ${error.message}`,'command',true);}
  finally{$('dialogConfirm').disabled=false;$('dialogCancel').disabled=false;}
}
function bindControls() {
  document.querySelectorAll('[data-view]').forEach(button=>button.addEventListener('click',()=>setView(button.dataset.view)));
  document.querySelector('.brand-mark').addEventListener('click',e=>{e.preventDefault();setView('overview');});
  document.querySelectorAll('[data-tab]').forEach(button=>{button.addEventListener('click',()=>setTab(button.dataset.tab));button.addEventListener('keydown',e=>{if(!['ArrowLeft','ArrowRight','Home','End'].includes(e.key))return;e.preventDefault();const names=['activity','telemetry','waypoints'];let i=names.indexOf(state.tab);i=e.key==='Home'?0:e.key==='End'?2:(i+(e.key==='ArrowRight'?1:2))%3;setTab(names[i]);$(`${names[i]}TabButton`).focus();});});
  $('routeForm').addEventListener('submit',e=>{e.preventDefault();planRoute();});
  for(const id of ['startLat','startLon','destLat','destLon'])$(id).addEventListener('input',()=>{markDirty();if(id.startsWith('dest'))$('preset').value='custom';});
  $('preset').addEventListener('change',()=>{const preset=state.config?.presets.find(x=>x.id===$('preset').value);if(!preset)return;$('destLat').value=preset.destination.latitude;$('destLon').value=preset.destination.longitude;markDirty();planRoute();});
  $('useDrone').addEventListener('click',()=>{if(!positionFresh())return;$('startLat').value=live.latitude.toFixed(6);$('startLon').value=live.longitude.toFixed(6);markDirty();toast('Launch point matched to the aircraft.');});
  $('pickDestination').addEventListener('click',()=>setPicking(!state.picking));$('cancelPick').addEventListener('click',()=>setPicking(false));
  $('fitRoute').addEventListener('click',fitRoute);
  $('followDrone').addEventListener('click',()=>{state.following=!state.following;$('followDrone').setAttribute('aria-pressed',String(state.following));if(state.following&&positionFresh())map.easeTo({center:[live.longitude,live.latitude],zoom:15,duration:700});});
  $('tiltMap').addEventListener('click',()=>{if(!map)return;const tilted=$('tiltMap').getAttribute('aria-pressed')!=='true';$('tiltMap').setAttribute('aria-pressed',String(tilted));map.easeTo({pitch:tilted?45:0,bearing:tilted?-15:0,duration:650});});
  $('layersButton').addEventListener('click',()=>{const open=$('layersPanel').hidden;$('layersPanel').hidden=!open;$('layersButton').setAttribute('aria-expanded',String(open));});
  for(const id of ['showZones','showBuffers','showCorridor','showTrail'])$(id).addEventListener('change',applyLayers);
  $('viewZones').addEventListener('click',()=>{if(!state.config||!map)return;const bounds=new maplibregl.LngLatBounds();state.config.buffers.features.forEach(f=>f.geometry.coordinates[0].forEach(p=>bounds.extend(p)));map.fitBounds(bounds,{padding:55,duration:700});$('layersPanel').hidden=true;$('layersButton').setAttribute('aria-expanded','false');});
  $('startMission').addEventListener('click',openLaunch);$('returnMission').addEventListener('click',()=>openAction('return'));$('batteryTest').addEventListener('click',()=>openAction('low_battery'));$('geofenceTest').addEventListener('click',()=>openAction('geofence'));
  $('dialogConfirm').addEventListener('click',confirmDialog);$('dialogCancel').addEventListener('click',()=>$('actionDialog').close());
  $('actionDialog').addEventListener('click',e=>{if(e.target===$('actionDialog')&&e.clientX){const r=$('actionDialog').getBoundingClientRect();if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)$('actionDialog').close();}});
  $('eventFilter').addEventListener('change',renderEvents);$('trendMetric').addEventListener('change',renderTelemetry);
  $('waypointList').addEventListener('click',e=>{const row=e.target.closest('[data-lat]');if(!row||!map)return;map.flyTo({center:[Number(row.dataset.lon),Number(row.dataset.lat)],zoom:15,duration:700});});
  $('exportEvents').addEventListener('click',()=>download('aerodrop-activity.json',state.events));$('exportSnapshot').addEventListener('click',()=>download('aerodrop-snapshot.json',snapshot()));$('exportHistory').addEventListener('click',()=>download('aerodrop-missions.json',state.history));
  $('copyMissionId').addEventListener('click',async()=>{try{await navigator.clipboard.writeText(live.mission_id);toast('Mission ID copied.');}catch{toast('Clipboard unavailable. The full mission ID is included in the snapshot export.',true);}});
  $('helpButton').addEventListener('click',()=>$('helpDialog').showModal());$('refreshSystems').addEventListener('click',async()=>{await refreshHealth();await refreshMission();renderSystems();toast('System status refreshed.');});
  document.addEventListener('keydown',e=>{if(e.key==='Escape'){setPicking(false);$('layersPanel').hidden=true;$('layersButton').setAttribute('aria-expanded','false');}});
  window.addEventListener('pagehide',saveHistory);
}
async function initialize() {
  bindControls();renderEvents();renderHistory();renderControls();renderConnection();renderMission();renderSystems();
  initializeMap();connectTelemetry();
  const initial=await Promise.allSettled([loadConfig(),fetchJSON('/status'),fetchJSON('/health')]);
  state.api=initial[2].status==='fulfilled';
  if(initial[1].status==='fulfilled'){state.mission=initial[1].value.mission;if(!live&&initial[1].value.telemetry)consumeTelemetry(initial[1].value.telemetry);}
  if(initial[0].status==='rejected')event('Flight configuration unavailable. Reconnecting to the backend.','connection',true);
  if(isActive()&&state.mission?.waypoints?.length){const points=state.mission.waypoints;state.planned={waypoints:points,distance_m:pointsDistance(points),corridor_half_width_m:state.config?.corridor_half_width_m||30,bypassed_zones:[]};state.routeDirty=false;renderRouteSummary();$('startLat').value=points[0].latitude;$('startLon').value=points[0].longitude;$('destLat').value=points.at(-1).latitude;$('destLon').value=points.at(-1).longitude;$('preset').value='custom';drawRoute();updateMarkers();setTimeout(fitRoute,500);}else if(state.api)planRoute(false);
  renderControls();renderConnection();
  setInterval(()=>{
    $('clock').textContent=new Date().toLocaleTimeString([], {hour:'2-digit',minute:'2-digit',second:'2-digit'});
    if(state.pending&&Date.now()-state.pending.time>12000){state.pending=null;toast('No mission response was observed. Check aircraft status before trying again.',true);event('Command acknowledgement timed out.','safety',true);}
    renderControls();renderConnection();renderMetrics();if(state.view==='systems')renderSystems();
  },1000);
  setInterval(refreshHealth,5000);setInterval(refreshMission,5000);
}
initialize().catch(error=>{event(`Dashboard initialization: ${error.message}`,'connection',true);toast(error.message,true);});
