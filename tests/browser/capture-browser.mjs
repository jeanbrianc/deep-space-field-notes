import assert from 'node:assert/strict';
import { mkdir, writeFile } from 'node:fs/promises';
import { createRequire } from 'node:module';
import identities from '../../app/capture-identities.json' with { type: 'json' };
const require=createRequire(import.meta.url);
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const base=process.env.PREVIEW_URL||'http://localhost:5173';
const artifacts=process.env.BROWSER_ARTIFACT_DIR||'outputs/capture-browser';
await mkdir(artifacts,{recursive:true});
const browser=await chromium.launch({headless:true,...(process.env.CHROME_PATH?{executablePath:process.env.CHROME_PATH}:{})});
const errors=[];
async function selected(page,id){ await page.waitForFunction(id=>new URL(location.href).searchParams.get('capture')===id&&!!document.querySelector('.capture-frame img')&&document.querySelector('.portal-stage')?.getAttribute('aria-busy')==='false',id); }
async function open(page,prints=false){await page.getByRole('button',{name:prints?'Browse prints':'Browse captures',exact:true}).click();await page.getByRole('dialog').waitFor();assert.ok(await page.getByLabel('Search captures',{exact:true}).evaluate(element=>document.activeElement===element));}
async function close(page){await page.getByRole('button',{name:'Close capture browser'}).click();await page.getByRole('dialog').waitFor({state:'hidden'});}
try{
 for(const viewport of [{width:1440,height:1000},{width:390,height:844},{width:320,height:740}]){
  const context=await browser.newContext({viewport,reducedMotion:'reduce'});const page=await context.newPage();page.on('pageerror',error=>errors.push(error.message));
  const requests=[];page.on('request',request=>{if(request.resourceType()==='image')requests.push(new URL(request.url()).pathname)});
  await page.goto(base);await page.getByRole('button',{name:'Browse captures',exact:true}).waitFor();assert.equal(await page.locator('.capture-frame').count(),0);
  await page.waitForTimeout(300);const initialImages=requests.length;
  await open(page);const dialog=page.getByRole('dialog');const search=page.getByLabel('Search captures',{exact:true});
  await page.waitForFunction(()=>document.querySelectorAll('.browser-thumbnail img').length>0);await page.waitForTimeout(200);
  assert.equal(await dialog.locator('.browser-card').count(),33);const visibleThumbs=await dialog.locator('.browser-thumbnail img').count();assert.ok(visibleThumbs>0&&visibleThumbs<33,`bounded initial loading: ${visibleThumbs}`);
  assert.ok(requests.slice(initialImages).every(path=>path.startsWith('/capture-thumbnails/')),JSON.stringify(requests.slice(initialImages)));
  for(const{id}of identities){const card=dialog.locator(`[data-capture-id="${id}"]`);assert.equal(await card.getByRole('link',{name:/Open capture/}).getAttribute('href'),`/?capture=${id}`);assert.equal(await card.getByRole('link',{name:/View print/}).getAttribute('href'),`/prints/${id}`);assert.ok(await card.locator('.browser-context').innerText());}
  await page.screenshot({path:`${artifacts}/all-${viewport.width}.png`});
  assert.ok(await dialog.evaluate(element=>element.scrollWidth<=element.clientWidth));assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  await search.fill('  AnDRomeda   Galaxy  ');assert.equal(await dialog.locator('.browser-card').count(),2);assert.match(await dialog.locator('#capture-browser-count').innerText(),/^2 captures/);
  assert.match(await dialog.locator('.browser-object').nth(1).innerText(),/Mosaic/);
  await search.fill('M31');assert.equal(await dialog.locator('.browser-card').count(),2);
  await search.fill('M 3');assert.equal(await dialog.locator('.browser-card').count(),1);assert.match(await dialog.locator('.browser-object').innerText(),/^M 3$/);
  await search.fill('NGC6960');assert.equal(await dialog.locator('.browser-card').count(),2);
  await search.fill('Caldwell 27');assert.equal(await dialog.locator('.browser-card').count(),2);
  await search.fill('missing object');await page.getByRole('heading',{name:'No captures found'}).waitFor();assert.match(await dialog.locator('#capture-browser-count').innerText(),/^0 captures/);await page.screenshot({path:`${artifacts}/empty-${viewport.width}.png`});
  await page.getByRole('button',{name:'Clear search'}).click();assert.equal(await dialog.locator('.browser-card').count(),33);assert.ok(await search.evaluate(element=>document.activeElement===element));
  await search.press('ArrowRight');assert.equal(new URL(page.url()).search,'');
  // Native modal keyboard focus trap and Escape restore the exact opener.
  await page.keyboard.press('Shift+Tab');assert.equal(await page.evaluate(()=>document.activeElement.getAttribute('aria-label')),'Close capture browser');
  await page.keyboard.press('Shift+Tab');assert.ok(await page.evaluate(()=>!!document.activeElement.closest('dialog')));
  await page.keyboard.press('Escape');await dialog.waitFor({state:'hidden'});assert.equal(await page.evaluate(()=>document.activeElement.textContent),'Browse captures');assert.equal(await page.locator('.capture-frame').count(),0);
  for(let i=0;i<3;i++){await open(page);await close(page);assert.equal(new URL(page.url()).search,'');}
  await open(page,true);assert.ok(await page.getByLabel('Available prints only').isChecked());assert.equal(await dialog.locator('.browser-card').count(),33);await search.fill('no such print');await page.getByRole('heading',{name:'No captures found'}).waitFor();await page.getByRole('button',{name:'Clear search'}).click();
  await page.getByLabel('Available prints only').uncheck();assert.match(await dialog.locator('#capture-browser-count').innerText(),/^33 captures$/);await page.getByLabel('Available prints only').check();
  await search.fill('Pelican');await dialog.getByRole('link',{name:/View print/}).click();await page.waitForURL('**/prints/pelican-nebula-ic-5070');assert.equal(await page.getByRole('link',{name:'Back to the observatory'}).getAttribute('href'),'/?capture=pelican-nebula-ic-5070');await page.getByRole('link',{name:'Back to the observatory'}).click();await selected(page,'pelican-nebula-ic-5070');
  const original=page.url();const image=await page.locator('.capture-frame img').getAttribute('src');await open(page);await search.fill('Orion');await page.keyboard.press('Escape');await dialog.waitFor({state:'hidden'});assert.equal(page.url(),original);assert.equal(await page.locator('.capture-frame img').getAttribute('src'),image);
  await open(page);await search.fill('Orion');await search.press('Tab');await page.keyboard.press('Tab');await page.keyboard.press('Tab');assert.match(await page.evaluate(()=>document.activeElement.textContent),/Open capture/);await page.keyboard.press('Enter');await selected(page,'orion-nebula');
  await page.goBack();await selected(page,'pelican-nebula-ic-5070');await page.goForward();await selected(page,'orion-nebula');await open(page);await page.goBack();await selected(page,'pelican-nebula-ic-5070');await page.getByRole('dialog').waitFor({state:'hidden'});await page.goForward();await selected(page,'orion-nebula');
  console.log(JSON.stringify({viewport,cards:33,initialThumbnails:visibleThumbs,searchAliases:true,emptyClear:true,printFilter:true,contextReturn:true,focusKeyboard:true,repeatedCancelHistory:true}));await context.close();
 }
 const normal=await browser.newPage({viewport:{width:1440,height:1000},reducedMotion:'no-preference'});normal.on('pageerror',error=>errors.push(error.message));
 await normal.goto(`${base}/?capture=pelican-nebula-ic-5070`);await selected(normal,'pelican-nebula-ic-5070');
 await normal.getByRole('button',{name:'Next capture',exact:true}).click();await normal.waitForTimeout(100);await open(normal);await normal.getByLabel('Search captures',{exact:true}).fill('missing');await normal.getByRole('dialog').evaluate(element=>element.focus());await normal.keyboard.press('ArrowRight');await close(normal);await normal.waitForTimeout(3100);await selected(normal,'pelican-nebula-ic-5070');
 await normal.getByRole('button',{name:'Next capture',exact:true}).click();await normal.waitForFunction(()=>new URL(location.href).searchParams.get('capture')!=='pelican-nebula-ic-5070');const interruptedId=new URL(normal.url()).searchParams.get('capture');await open(normal);await close(normal);await normal.waitForTimeout(3100);await selected(normal,interruptedId);console.log(JSON.stringify({normalMotionOpenCloseCancellation:true,backgroundKeyboardIsolation:true}));await normal.close();
 // Representative step/time comparison, reduced-motion local UI, not sales evidence.
 const page=await browser.newPage({viewport:{width:1440,height:1000},reducedMotion:'reduce'});page.on('pageerror',error=>errors.push(error.message));await page.goto(base);await open(page);const order=await page.locator('.browser-card').evaluateAll(cards=>cards.map(card=>card.getAttribute('data-capture-id')));await close(page);
 const measurements=[];
 for(const[id,query]of [['pelican-nebula-ic-5070','Pelican'],['orion-nebula','Orion'],['triangulum-galaxy-m-33','Triangulum'],['andromeda-galaxy-mosaic-m-31-mosaic','Mosaic']]){
  await page.goto(base);let start=performance.now();await page.getByRole('button',{name:'Open the telescope on the Andromeda Galaxy'}).click();await selected(page,'andromeda-galaxy');let steps=1;const targetIndex=order.indexOf(id);const initialIndex=order.indexOf('andromeda-galaxy');const distance=(targetIndex-initialIndex+order.length)%order.length;for(let i=1;i<=distance;i++){await page.getByRole('button',{name:'Next capture',exact:true}).click();await selected(page,order[(initialIndex+i)%order.length]);steps++}const sequentialMs=Math.round(performance.now()-start);
  await page.goto(base);start=performance.now();await open(page);await page.getByLabel('Search captures',{exact:true}).fill(query);await page.locator(`[data-capture-id="${id}"]`).getByRole('link',{name:/Open capture/}).click();await selected(page,id);const browseMs=Math.round(performance.now()-start);measurements.push({id,sequentialSteps:steps,browseSteps:3,sequentialMs,browseMs});
 }
 await writeFile(`${artifacts}/discovery-measurements.json`,`${JSON.stringify({method:'Local desktop Chromium, reduced motion, start after root navigation; automated interaction wall time. One run per route; not a production or revenue benchmark.',measurements},null,2)}\n`);console.log(JSON.stringify({measurements,pageErrors:errors}));assert.deepEqual(errors,[]);await page.close();
}finally{await browser.close()}
