const assert=require('assert');const fs=require('fs');
const {chromium}=require(process.env.FOREST_PLAYWRIGHT_MODULE||'../apps/web/node_modules/@playwright/test');
(async()=>{
 const browser=await chromium.launch({headless:true,channel:process.env.FOREST_BROWSER_CHANNEL||'chrome'});
 const page=await browser.newPage({viewport:{width:1512,height:982}});const errors=[]; const httpErrors=[];
 page.on('pageerror',e=>errors.push({url:page.url(),error:e.message}));page.on('response',r=>{if(r.status()>=400&&r.url().includes('/api/'))httpErrors.push({url:r.url(),status:r.status()})});
 const base=process.env.FOREST_WEB_URL||'http://127.0.0.1:5173';const pid=JSON.parse(fs.readFileSync('var/demo.json')).project_id; const report=[];
 for(const route of ['overview','workspace','library','ideas','theory','experiments','data','figures','paper','files']){
  await page.goto(`${base}/projects/${pid}/${route}`);await page.waitForSelector('.sidebar');await page.waitForTimeout(route==='paper'?2200:750);
  const text=await page.locator('body').innerText();assert(text.length>150,route+' blank');
  const overflow=await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+2);
  report.push({route,text_length:text.length,overflow});
  if(['overview','workspace','figures','paper','data'].includes(route))await page.screenshot({path:`var/qa/${route}-desktop.png`,fullPage:true});
 }
 await page.goto(`${base}/settings`);await page.waitForTimeout(750);report.push({route:'settings',text_length:(await page.locator('body').innerText()).length});
 await page.setViewportSize({width:390,height:844});await page.goto(`${base}/projects/${pid}/workspace`);await page.waitForTimeout(800);await page.screenshot({path:'var/qa/workspace-mobile.png',fullPage:true});
 report.push({route:'mobile',overflow:await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+2)});
 fs.writeFileSync('var/qa/browser-routes.json',JSON.stringify({report,errors,httpErrors},null,2)); console.log(JSON.stringify({report,errors,httpErrors},null,2));
 await browser.close();assert(errors.length===0,'Browser runtime errors');assert(!report.some(r=>r.overflow),'Unintended page overflow');
})();
