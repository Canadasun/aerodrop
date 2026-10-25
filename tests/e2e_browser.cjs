const {chromium}=require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const fs=require('fs');
const output=process.env.AERODROP_TEST_OUTPUT || 'test-artifacts';
fs.mkdirSync(output,{recursive:true});
(async()=>{
 const browser=await chromium.launch({headless:!!process.env.HEADLESS});
 const page=await browser.newPage({viewport:{width:1280,height:900}});
 await page.addInitScript(()=>{const Native=window.WebSocket;window.__sockets=[];window.WebSocket=class extends Native{constructor(...args){super(...args);window.__sockets.push(this)}}});
 const errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.goto('http://localhost:8000');
 await page.waitForFunction(()=>['READY','COMPLETE','ABORTED'].includes(window.live?.state) && !document.querySelector('#startMission').disabled,{},{timeout:300000});
 // Short real PX4 flight, same controller and route API as the long demo.
 const results=[];
 for(const scenario of (process.env.AERODROP_SCENARIOS || 'delivery,low_battery,geofence,return,detour').split(',')){
  if(scenario==='detour'){
   // The preset selector plans the route itself, exactly as an operator would use it.
   await page.locator('#preset').selectOption('detour');
  }else{
   await page.locator('#destLat').fill('51.116');
   await page.locator('#destLon').fill('-114.109');
   await page.locator('#routeButton').click();
  }
  await page.waitForFunction(()=>!state.planning && !state.routeDirty);
  if(scenario==='detour'){
   const planned=await page.evaluate(()=>state.planned);
   if(!planned.bypassed_zones.includes('demo_zone_nose_hill')||planned.waypoints.length<4)throw Error('Detour not planned: '+JSON.stringify(planned));
   if(!(await page.locator('#details').innerText()).includes('Detouring around 1 synthetic restricted zone'))throw Error('Detour not shown in the route summary');
  }
  await page.locator('#startMission').click();
  const [response]=await Promise.all([page.waitForResponse(r=>r.url().endsWith('/mission') && r.request().method()==='POST'),page.locator('#dialogConfirm').click()]);
  const mission=await response.json();
  if(response.status()!==202)throw Error(JSON.stringify(mission));
  const states=[];let injected=false;let inspectedWaypoints=false;let finished=false;let maxAltitude=0;
  let finalTelemetry=null;
  const start=Date.now();
  while(Date.now()-start<(scenario==='detour'?600000:240000)){
   const t=await page.evaluate(()=>live);
   if(t.mission_id!==mission.id){await page.waitForTimeout(250);continue;}
   maxAltitude=Math.max(maxAltitude,t.altitude_m||0);
   if(!states.includes(t.state)){states.push(t.state);console.log(scenario,t.state,t.detail||'',t.altitude_m)}
   if(t.state==='NAVIGATING' && !inspectedWaypoints){
    await page.locator('#waypointsTabButton').click();
    const waypointText=await page.locator('#waypointList').innerText();
    if(waypointText.includes('NaN') || !/-?\d+\.\d{5}, -?\d+\.\d{5}/.test(waypointText))throw Error('Invalid live waypoint display: '+waypointText);
    await page.locator('#activityTabButton').click();inspectedWaypoints=true;
   }
   if(scenario==='detour' && t.state==='RTL_FAILSAFE')throw Error('The detour corridor tripped the live geofence: '+JSON.stringify(t));
   if(!['delivery','detour'].includes(scenario) && !injected && t.state==='NAVIGATING'){
    await page.locator(scenario==='low_battery'?'#batteryTest':scenario==='return'?'#returnMission':'#geofenceTest').click();await page.locator('#dialogConfirm').click();injected=true;
   }
   if(t.state==='ERROR')throw Error(JSON.stringify(t));
   if(t.mission_id && ['COMPLETE','ABORTED'].includes(t.state) && Date.now()-start>3000){
    if(t.state!==(scenario==='geofence'?'ABORTED':'COMPLETE'))throw Error(`Wrong terminal state for ${scenario}: `+JSON.stringify(t));
    if(t.armed || t.landed!==true || !Number.isFinite(t.altitude_m) || t.altitude_m>2)throw Error('Not landed/disarmed: '+JSON.stringify(t));
    if(['delivery','detour'].includes(scenario) && !states.includes('WINCH_DROP'))throw Error('Payload step missing');
    if(['low_battery','return'].includes(scenario) && states.includes('WINCH_DROP'))throw Error('Payload released during abort');
    if(scenario==='geofence' && !states.includes('RTL_FAILSAFE'))throw Error('RTL not exercised');
    if(!Number.isFinite(t.mission_started_unix)||!Number.isFinite(t.mission_finished_unix))throw Error('Mission timing missing');
    if(t.payload_status!==(['delivery','detour'].includes(scenario)?'RELEASED':'SKIPPED'))throw Error('Incorrect payload state '+JSON.stringify(t));
    if(!Number.isFinite(t.horizontal_speed_m_s)||!Number.isFinite(t.heading_deg))throw Error('Measured flight instruments missing');
    finalTelemetry=t;finished=true;break;
   }
   await page.waitForTimeout(250);
  }
  if(!finished)throw Error('Timed out '+scenario+' '+states.join(','));
  if(maxAltitude<25)throw Error(`${scenario} never reached cruise altitude (max ${maxAltitude} m)`);
  results.push({scenario,states,maxAltitude,inspectedWaypoints,finalTelemetry,passed:true});
  await page.evaluate(()=>window.scrollTo(0,0));
  await page.screenshot({path:`${output}/aerodrop-${scenario}.png`});
 }
 await page.evaluate(()=>window.__sockets.at(-1).close());
 await page.waitForFunction(()=>window.__sockets.length>=2 && window.__sockets.at(-1).readyState===1);
 await page.waitForFunction(()=>document.querySelector('#streamPill').textContent.includes('connected'));
 console.log('PASS telemetry reconnection');
 if(errors.length)throw Error(errors.join('\n'));
 fs.writeFileSync(`${output}/aerodrop-flight-results.json`,JSON.stringify(results,null,2));
 console.log('PASS',JSON.stringify(results));await browser.close();
})().catch(e=>{console.error(e.stack);process.exit(1)});
