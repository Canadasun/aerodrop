/* Isolated browser tests. Run only against the preview backend on port 18000.
   Test telemetry is published to that isolated backend, never the flight stack. */
const {chromium}=require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const fs=require('fs');
const assert=require('assert/strict');
const origin=process.env.AERODROP_UI_TEST_URL || 'http://localhost:18000';
const output=process.env.AERODROP_TEST_OUTPUT || 'test-artifacts';
if(new URL(origin).port!=='18000')throw Error('Synthetic telemetry tests are restricted to the isolated preview on port 18000.');
fs.mkdirSync(output,{recursive:true});
const results=[];
let browser,page;
(async()=>{
 browser=await chromium.launch({headless:true});
 const context=await browser.newContext({viewport:{width:1512,height:1050},acceptDownloads:true});
 page=await context.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.addInitScript(()=>{const Native=WebSocket;window.__viewerSockets=[];window.WebSocket=class extends Native{constructor(...args){super(...args);if(String(args[0]).includes('role=viewer'))window.__viewerSockets.push(this);}}});
 async function check(name,fn){await fn();results.push(name);console.log('PASS',name);}
 async function seed(p=page){await p.evaluate(async()=>{
  window.__testFrame={drone_id:'drone_01',state:'READY',latitude:51.115,longitude:-114.110,altitude_m:0,battery_pct:82,px4_battery_pct:82,armed:false,landed:true,px4_nav_state:4,preflight_checks_pass:true,heading_deg:90,horizontal_speed_m_s:0,vertical_speed_m_s:0,mission_elapsed_s:0,mission_id:null,payload_status:'PENDING',route_index:0,waypoint_count:0};
  window.__producer=new WebSocket(`${location.protocol==='https:'?'wss':'ws'}://${location.host}/ws/telemetry/drone_01?role=drone`);
  await new Promise((resolve,reject)=>{window.__producer.onopen=resolve;window.__producer.onerror=reject});
  window.__testFrameTimer=setInterval(()=>{if(window.__producer.readyState===1){const now=Date.now()/1000;window.__producer.send(JSON.stringify({...window.__testFrame,timestamp_unix:now,position_timestamp_unix:window.__testFrame.stale?now-15:now,status_timestamp_unix:now}));}},200);
 });}
 async function frame(patch){await page.evaluate(value=>Object.assign(window.__testFrame,value),patch);await page.waitForTimeout(350);}
 await page.goto(origin);
 await page.waitForFunction(()=>state.api&&state.config);
 await check('Offline/unknown aircraft never appears ready',async()=>{assert.equal(await page.locator('#startMission').isDisabled(),true);assert.equal(await page.locator('#altitudeValue').innerText(),'—');});
 await seed();
 await page.waitForFunction(()=>!document.querySelector('#startMission').disabled);
 await check('Health, telemetry, route and all real preflight checks render',async()=>{
  assert.match(await page.locator('#apiPill').innerText(),/online/i);assert.match(await page.locator('#streamPill').innerText(),/connected/i);
  assert.equal(await page.locator('#batteryValue').innerText(),'82');assert.equal(await page.locator('#preflightChecks .good').count(),4);
  assert.match(await page.locator('#details').innerText(),/WAYPOINTS/);assert.equal(await page.locator('#returnMission').isDisabled(),true);
 });
 await check('Dirty coordinates block dispatch until replanned',async()=>{
  await page.locator('#destLat').fill('51.1162');assert.equal(await page.locator('#startMission').isDisabled(),true);
  await page.locator('#routeButton').click();await page.waitForFunction(()=>!state.routeDirty&&!state.planning);assert.equal(await page.locator('#startMission').isDisabled(),false);
 });
 await check('Unsafe endpoint produces readable error and cannot launch',async()=>{
  await page.locator('#destLat').fill('51.0447');await page.locator('#destLon').fill('-114.0719');await page.locator('#routeButton').click();
  await page.waitForFunction(()=>document.querySelector('#routeMessage').classList.contains('error'));
  assert.match(await page.locator('#routeMessage').innerText(),/safety buffer/);assert.equal(await page.locator('#startMission').isDisabled(),true);
  await page.locator('#preset').selectOption('quick');await page.waitForFunction(()=>!state.routeDirty&&!state.planning);
 });
 await page.waitForFunction(()=>state.mapReady,{},{timeout:25000});
 await check('Map click updates destination and replans the real corridor',async()=>{
  await page.locator('#pickDestination').click();await page.waitForTimeout(1100);
  const pixel=await page.evaluate(()=>{const p=map.project([-114.1093,51.1161]);const r=document.querySelector('#map').getBoundingClientRect();return{x:r.x+p.x,y:r.y+p.y};});
  await page.mouse.click(pixel.x,pixel.y);await page.waitForFunction(()=>!state.picking&&!state.routeDirty&&!state.planning);
  assert.ok(Math.abs(Number(await page.locator('#destLat').inputValue())-51.1161)<.00002,'Map click latitude: '+await page.locator('#destLat').inputValue());
 });
 await check('Draggable destination commits an updated route',async()=>{
  await page.evaluate(()=>{destinationMarker.setLngLat([-114.1092,51.1162]);destinationMarker.fire('dragend');});
  await page.waitForFunction(()=>!state.routeDirty&&!state.planning);assert.equal(await page.locator('#destLat').inputValue(),'51.116200');
 });
 await check('Layer controls and map focus are interactive',async()=>{
  await page.locator('#layersButton').click();await page.locator('#showBuffers').uncheck();assert.equal(await page.evaluate(()=>map.getLayoutProperty('buffers-fill','visibility')),'none');
  await page.locator('#showBuffers').check();await page.locator('#layersButton').click();await page.locator('#tiltMap').click();assert.equal(await page.locator('#tiltMap').getAttribute('aria-pressed'),'true');await page.locator('#tiltMap').click();await page.locator('#fitRoute').click();
 });
 await check('Launch is explicitly reviewed and cancel sends no command',async()=>{
  await page.locator('#startMission').click();assert.equal(await page.locator('#actionDialog').isVisible(),true);assert.match(await page.locator('#dialogDetails').innerText(),/Destination/);
  await page.locator('#dialogCancel').click();const response=await page.request.get(origin+'/mission');assert.deepEqual(await response.json(),{});
 });
 await page.evaluate(()=>window.scrollTo(0,0));await page.waitForTimeout(200);await page.screenshot({path:output+'/dashboard-desktop.png',fullPage:true});
 let mission;
 await check('Confirmed launch posts the exact route and scheduled test',async()=>{
  await page.locator('.schedule-details summary').click();await page.locator('#batterySchedule').selectOption('20');
  await page.locator('#startMission').click();const [response]=await Promise.all([page.waitForResponse(r=>r.url().endsWith('/mission')&&r.request().method()==='POST'),page.locator('#dialogConfirm').click()]);
  assert.equal(response.status(),202);mission=await response.json();assert.equal(mission.simulate_low_battery_after_seconds,20);assert.equal(mission.waypoints.at(-1).latitude,51.1162);
  assert.equal(await page.locator('#startMission').isDisabled(),true);
  await frame({state:'ARMING',mission_id:mission.id,waypoint_count:2,route_index:0,active_waypoints:mission.waypoints});
 });
 await check('Live speed, heading, waypoint and mission stages use telemetry',async()=>{
  await frame({state:'NAVIGATING',armed:true,landed:false,px4_nav_state:14,altitude_m:30,horizontal_speed_m_s:7.2,vertical_speed_m_s:.3,heading_deg:45,route_index:1,mission_elapsed_s:24});
  assert.equal(await page.locator('#speedValue').innerText(),'7.2');assert.equal(await page.locator('#headingValue').innerText(),'45');assert.equal(await page.locator('#elapsedValue').innerText(),'00:24');
  assert.equal(await page.locator('#destLat').isDisabled(),true);assert.equal(await page.locator('#returnMission').isEnabled(),true);assert.equal(await page.locator('#flightStages li.current').count(),1);
  await page.locator('#telemetryTabButton').click();assert.match(await page.locator('#telemetryFields').innerText(),/Offboard/);assert.ok((await page.locator('#trendLine').getAttribute('d')).length>0);
  await page.locator('#waypointsTabButton').click();assert.equal(await page.locator('.waypoint-row').count(),2);assert.doesNotMatch(await page.locator('#waypointList').innerText(),/NaN/);assert.match(await page.locator('#waypointList').innerText(),/51.11500/);await page.locator('.waypoint-row').last().click();await page.locator('#activityTabButton').click();
 });
 await check('Stale position disables active aircraft commands even with a live socket',async()=>{
  await frame({stale:true});await page.waitForFunction(()=>document.querySelector('#returnMission').disabled);assert.match(await page.locator('#connectionBanner').innerText(),/stale/i);
  await frame({stale:false});await page.waitForFunction(()=>!document.querySelector('#returnMission').disabled);
 });
 await check('Low-battery test has confirmation and posts the real action',async()=>{
  await page.locator('#batteryTest').click();assert.match(await page.locator('#dialogTitle').innerText(),/low battery/i);
  const [response]=await Promise.all([page.waitForResponse(r=>r.url().endsWith('/mission/action')),page.locator('#dialogConfirm').click()]);assert.equal(response.status(),202);assert.equal(response.request().postDataJSON().name,'low_battery');
  await frame({state:'RETURNING',low_battery_injected:true,battery_pct:19,payload_status:'SKIPPED',detail:'Battery below 20%; delivery skipped',route_index:0,waypoint_count:1,active_waypoints:[{latitude:51.115,longitude:-114.110,altitude_m:30}]});
  assert.match(await page.locator('#batteryNote').innerText(),/Simulated/);assert.match(await page.locator('#payloadState').innerText(),/skipped/i);assert.equal(await page.locator('#flightStages li.skipped').count(),1);
 });
 await check('Landed completion, browser history and real PX4 battery recovery',async()=>{
  await frame({state:'COMPLETE',armed:false,landed:true,altitude_m:0,horizontal_speed_m_s:0,vertical_speed_m_s:0,mission_elapsed_s:65,detail:'Landed and disarmed at home'});
  assert.match(await page.locator('#missionDetail').innerText(),/landed/i);assert.equal(await page.locator('#startMission').isEnabled(),true);
  await page.locator('[data-view="history"]').first().click();assert.equal(await page.locator('#missionHistory tr').count(),1);assert.match(await page.locator('#missionHistory').innerText(),/Complete/);
  const downloadEvent=page.waitForEvent('download');await page.locator('#exportHistory').click();assert.equal((await downloadEvent).suggestedFilename(),'aerodrop-missions.json');
 });
 await check('Systems, configuration and unsupported capabilities are honest',async()=>{
  await page.locator('[data-view="systems"]').first().click();assert.equal(await page.locator('.system-card').count(),6);assert.match(await page.locator('#capabilityList').innerText(),/Order management\s+Not implemented/);
  await page.locator('.diagnostics summary').click();assert.match(await page.locator('#rawSnapshot').innerText(),new RegExp(mission.id));await page.evaluate(()=>window.scrollTo(0,0));await page.waitForTimeout(200);await page.screenshot({path:output+'/dashboard-systems.png',fullPage:true});
 });
 await check('Telemetry reconnects automatically',async()=>{
  await page.evaluate(()=>window.__viewerSockets.at(-1).close());await page.waitForFunction(()=>window.__viewerSockets.length>=2&&window.__viewerSockets.at(-1).readyState===1);await page.waitForFunction(()=>document.querySelector('#streamPill').textContent.includes('connected'));
 });
 await check('Local mission history survives reload',async()=>{
  await page.reload();await page.waitForFunction(()=>state.config&&state.api);await page.locator('[data-view="history"]').first().click();assert.equal(await page.locator('#missionHistory tr').count(),1);
 });
 await check('Mobile layout has no horizontal overflow and keeps controls usable',async()=>{
  await page.setViewportSize({width:390,height:844});await page.locator('[data-view="overview"]').first().click();await page.waitForTimeout(500);
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth),true);
  await page.locator('#helpButton').click();assert.equal(await page.locator('#helpDialog').isVisible(),true);await page.locator('#helpDialog button[value="close"]').click();
  await page.evaluate(()=>window.scrollTo(0,0));await page.waitForTimeout(200);await page.screenshot({path:output+'/dashboard-mobile.png',fullPage:true});
 });
 await check('Basemap outage preserves route overlays and coordinate planning',async()=>{
  const offlineMap=await context.newPage();
  await offlineMap.route('https://tiles.openfreemap.org/**',route=>route.abort());
  await offlineMap.goto(origin);await offlineMap.waitForFunction(()=>state.mapReady,{},{timeout:20000});
  assert.match(await offlineMap.locator('#mapNotice').innerText(),/unavailable/i);
  assert.ok(await offlineMap.evaluate(()=>!!map.getSource('route')));
  await offlineMap.locator('#routeButton').click();await offlineMap.waitForFunction(()=>!state.planning&&!state.routeDirty);
  assert.match(await offlineMap.locator('#routeMessage').innerText(),/planned/i);
  await offlineMap.close();
 });
 assert.deepEqual(errors,[]);fs.writeFileSync(output+'/ui-results.json',JSON.stringify({passed:results.length,checks:results,pageErrors:errors},null,2));
 console.log('ALL UI CHECKS PASSED',results.length);await browser.close();
})().catch(async error=>{console.error(error.stack);if(page)await page.evaluate(()=>window.scrollTo(0,0));await page.waitForTimeout(200);await page.screenshot({path:output+'/failure.png',fullPage:true}).catch(()=>{});if(browser)await browser.close();process.exit(1)});
