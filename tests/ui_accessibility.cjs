const {chromium}=require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const AxeBuilder=require(process.env.AXE_MODULE || '@axe-core/playwright').default;
const fs=require('fs');
(async()=>{
 const browser=await chromium.launch({headless:true});const context=await browser.newContext({viewport:{width:1512,height:1050}});const page=await context.newPage();
 await page.goto(process.env.AERODROP_UI_TEST_URL || 'http://localhost:18000');await page.waitForTimeout(2000);
 const results=[];
 for(const view of ['overview','history','systems']){
  await page.locator(`[data-view="${view}"]`).first().click();await page.waitForTimeout(200);
  const result=await new AxeBuilder({page}).withTags(['wcag2a','wcag2aa','wcag21a','wcag21aa']).analyze();
  results.push({view,violations:result.violations});
  console.log(JSON.stringify({view,issues:result.violations.map(x=>({id:x.id,impact:x.impact,count:x.nodes.length,nodes:x.nodes.slice(0,10).map(n=>({target:n.target,summary:n.failureSummary}))}))}));
 }
 const output=process.env.AERODROP_TEST_OUTPUT || 'test-artifacts';fs.mkdirSync(output,{recursive:true});fs.writeFileSync(output+'/accessibility.json',JSON.stringify(results,null,2));
 await browser.close();if(results.some(r=>r.violations.length))process.exitCode=1;
})().catch(e=>{console.error(e.stack);process.exit(1)});
