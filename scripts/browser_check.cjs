const { chromium } = require(process.env.FOREST_PLAYWRIGHT_MODULE || '../apps/web/node_modules/@playwright/test');
const fs=require('fs');
(async()=>{
 const browser=await chromium.launch({headless:true,channel:"chrome"}); const page=await browser.newPage({viewport:{width:1512,height:982}});
 const errors=[]; page.on('pageerror',e=>errors.push(e.message)); page.on('console',m=>{if(m.type()==='error')errors.push(m.text())});
 const pid=JSON.parse(fs.readFileSync('var/demo.json','utf8')).project_id;
 await page.goto(`http://127.0.0.1:5173/projects/${pid}/workspace`); await page.locator('.react-flow').waitFor({timeout:30000}); await page.waitForTimeout(1000);
 await page.screenshot({path:'var/qa/workspace-desktop.png',fullPage:true});
 console.log(JSON.stringify({title:await page.title(),text:(await page.locator('body').innerText()).slice(0,5500),errors},null,2));
 await browser.close();
})();
