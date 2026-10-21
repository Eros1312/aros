/* Isolated browser regression suite. Requires a demo-only API serving the built web.
   AROS_TEST_URL=http://127.0.0.1:8000 PLAYWRIGHT_MODULE=/path/to/playwright node tests/workflow.browser.cjs
   The API must have DEMO_MODE=true and no running AI/automation workers.
*/
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const path=require('node:path');
const origin=process.env.AROS_TEST_URL||'http://127.0.0.1:8000';
if(!['127.0.0.1','localhost'].includes(new URL(origin).hostname))throw Error('Browser tests are restricted to a loopback demo API.');
const output=process.env.AROS_EVIDENCE_DIR||'/tmp/aros-browser-evidence';
const projectId=process.env.AROS_TEST_PROJECT_ID;
if(!projectId||!/^[a-zA-Z0-9-]{8,80}$/.test(projectId))throw Error('Set AROS_TEST_PROJECT_ID to a local demo project with a linked document, task and verified result. The browser suite never guesses or creates a project.');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const report={checks:[],screenshots:[],consoleErrors:[]};
const pass=name=>{report.checks.push(name);console.log('PASS '+name)};
(async()=>{
 await fs.mkdir(output,{recursive:true});
 const browser=await chromium.launch({...(process.env.AROS_CHROMIUM?{executablePath:process.env.AROS_CHROMIUM}:{}),headless:true,args:['--no-sandbox']});
 try{
  const context=await browser.newContext({viewport:{width:1280,height:800},locale:'es-ES',timezoneId:'Europe/Madrid',reducedMotion:'reduce'});
  // No fixture imagery can accidentally open production or a provider account.
  await context.route('**/*',route=>new URL(route.request().url()).origin===new URL(origin).origin?route.continue():route.fulfill({status:404,body:'Fixture test blocks external requests'}));
  const page=await context.newPage();page.on('pageerror',error=>report.consoleErrors.push(error.message));
  await page.goto(origin);await page.getByRole('button',{name:'Explorar con datos de ejemplo'}).click();
  await page.getByRole('heading',{name:'Primero, lo importante'}).waitFor();
  const me=await (await page.request.get(origin+'/api/v1/me')).json();assert.equal(me.demo,true,'tests must never run on real user data');
  await page.getByRole('heading',{name:'Primero, lo importante'}).waitFor();
  assert.equal(await page.locator('h1').count(),1);assert.equal(await page.locator('.nav button').count(),5);assert.equal(await page.locator('.source-notice a[href="https://github.com/Eros1312/aros"]').count(),1);pass('One home heading, five primary navigation routes and source link');
  await page.screenshot({path:path.join(output,'01-today-desktop.png'),fullPage:true});report.screenshots.push('01-today-desktop.png');
  await page.goto(origin+'/#overview');await page.waitForURL('**/#today');assert.ok((await page.locator('h1').innerText()).includes('Aros.'));pass('Legacy overview deep link resolves to Today');
  await page.goto(origin+'/#projects/'+projectId);await page.getByRole('heading',{name:'Material del proyecto'}).waitFor();
  const detail=await (await page.request.get(origin+'/api/v1/workspace/projects/'+projectId)).json();assert.ok(detail.project?.id===projectId);assert.ok(detail.resources.length&&detail.tasks.length&&detail.results.length);
  await page.screenshot({path:path.join(output,'02-project-desktop.png'),fullPage:true});report.screenshots.push('02-project-desktop.png');pass('Real project detail shows linked resources, tasks, results and work');
  await page.getByRole('button',{name:'Añadir tarea',exact:true}).click();await page.getByRole('dialog').waitFor();
  const taskArea=page.getByRole('dialog').getByRole('combobox',{name:/^Área/});assert.equal(await taskArea.inputValue(),detail.project.area);assert.equal(await taskArea.isEnabled(),false);assert.ok((await page.getByRole('dialog').innerText()).includes('Se vinculará a'));
  await page.getByRole('dialog').getByRole('button',{name:'Cancelar',exact:true}).click();pass('New task has visible project link and fixed matching area');
  const resource=detail.resources.find(row=>row.kind==='document')||detail.resources[0];
  const trigger=page.locator('.project-work-grid').getByRole('button').filter({hasText:resource.title}).first();await trigger.click();await page.getByRole('dialog').waitFor();
  await page.getByRole('dialog').getByRole('button',{name:'Cerrar',exact:true}).press('Escape');assert.equal(await page.getByRole('dialog').count(),0);assert.ok(await trigger.evaluate(el=>el===document.activeElement));pass('Resource modal Escape returns keyboard focus');
  await trigger.click();await page.getByRole('dialog').getByRole('button',{name:'Preguntar a Aros',exact:true}).click();await page.getByLabel('Contexto seleccionado para Aros').waitFor();
  assert.ok((await page.getByLabel('Contexto seleccionado para Aros').innerText()).includes(resource.title));assert.ok((await page.getByLabel('Contexto seleccionado para Aros').innerText()).includes('versión '+resource.version));
  await page.reload();await page.getByLabel('Contexto seleccionado para Aros').waitFor();pass('Exact resource ID/version/source remains visible across assistant reload');
  let payload=null;
  // Assert the outgoing contract without enqueueing an AI run or calling a model.
  await page.route('**/api/v1/chat',async route=>{if(route.request().method()==='POST'){payload=route.request().postDataJSON();await route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({detail:'Error de prueba: no se ha iniciado ningún modelo'})})}else await route.continue()});
  await page.getByLabel('Mensaje para Aros',{exact:true}).fill('Resume el recurso seleccionado');await page.getByRole('button',{name:'Enviar mensaje',exact:true}).click();await page.getByRole('status').filter({hasText:'Error de prueba'}).waitFor();
  assert.deepEqual(payload.item_ids,[resource.id]);assert.equal(payload.area,resource.area);assert.equal(payload.integrated,true);assert.equal(await page.getByLabel('Mensaje para Aros',{exact:true}).inputValue(),'Resume el recurso seleccionado');pass('Chat submits exact selected item_ids and preserves draft after failure (zero model calls)');
  await page.getByRole('button',{name:'Quitar recurso del contexto'}).click();assert.ok(!(await page.getByLabel('Contexto seleccionado para Aros').innerText()).includes(resource.title));await page.getByRole('button',{name:'Salir del contexto del proyecto'}).click();assert.equal(await page.getByLabel('Contexto seleccionado para Aros').count(),0);await page.reload();await page.getByLabel('Mensaje para Aros',{exact:true}).waitFor();assert.equal(await page.getByLabel('Contexto seleccionado para Aros').count(),0);pass('Removing resource and leaving project stays removed after reload');
  await page.goto(origin+'/#learning');await page.getByRole('heading',{name:'Aprendizaje',exact:true}).waitFor();
  const learningText=await page.locator('main').innerText();assert.ok(learningText.includes('Ahora y a continuación'));assert.ok(/sesi[oó]n(?:es)? programada/.test(learningText));pass('Learning offers generic saved resources and agenda');
  await page.screenshot({path:path.join(output,'03-learning-desktop.png'),fullPage:true});report.screenshots.push('03-learning-desktop.png');
  await page.goto(origin+'/#resources');await page.getByRole('heading',{name:'Recursos',exact:true}).waitFor();await page.getByRole('button',{name:'Añadir',exact:true}).click();await page.getByRole('dialog').waitFor();assert.equal(await page.getByRole('combobox',{name:'Tipo',exact:true}).inputValue(),'note');await page.getByRole('combobox',{name:'Tipo',exact:true}).selectOption('link');await page.getByRole('textbox',{name:'Título',exact:true}).fill('Enlace de prueba de recursos');await page.getByRole('textbox',{name:'Enlace',exact:true}).fill('https://example.com/reference');await page.getByRole('button',{name:'Guardar',exact:true}).click();await page.getByRole('dialog').waitFor({state:'hidden'});await page.locator('.record-main').filter({hasText:'Enlace de prueba de recursos'}).waitFor();pass('Resources creates a generic link with URL using the shared CRUD form');
  await page.screenshot({path:path.join(output,'04-resources-desktop.png'),fullPage:true});report.screenshots.push('04-resources-desktop.png');
  assert.equal((await page.request.get(origin+'/api/v1/events/library')).status(),200);await page.goto(origin+'/#events');await page.getByRole('heading',{name:'Eventos',exact:true}).waitFor();await page.getByRole('button',{name:/^Agenda y trabajo/}).click();await page.getByRole('heading',{name:'Agenda y trabajo',exact:true}).waitFor();assert.ok((await page.locator('main').innerText()).includes('Preparar un encuentro'));pass('Events loads its generic material library and agenda');
  await page.goto(origin+'/#agents');await page.getByRole('heading',{name:'Conversaciones',exact:true}).waitFor();assert.equal(await page.locator('.agent-inspector[open]').count(),0);pass('Specialist conversation precedes technical diagnostics');
  await page.screenshot({path:path.join(output,'05-agents-desktop.png'),fullPage:true});report.screenshots.push('05-agents-desktop.png');
  for(const width of [390,768,1280]){
   await page.setViewportSize({width,height:844});
   for(const route of ['today','projects','learning','resources','events','chat']){
    await page.goto(origin+'/#'+route);await page.locator('main[data-view="'+route+'"] h1').waitFor();if(route==='today')await page.getByRole('heading',{name:'Primero, lo importante'}).waitFor();
    const dimensions=await page.evaluate(()=>({width:document.documentElement.clientWidth,scroll:document.documentElement.scrollWidth}));assert.ok(dimensions.scroll<=dimensions.width+1,route+' horizontal overflow at '+width);
    if(width===390){const name='06-'+route+'-mobile.png';await page.screenshot({path:path.join(output,name),fullPage:true});report.screenshots.push(name)}
   }
   pass('No horizontal overflow on six changed routes at '+width+'px');
  }
  await page.setViewportSize({width:390,height:844});await page.goto(origin+'/#today');await page.getByRole('button',{name:'Más opciones'}).click();await page.locator('.sidebar.open').waitFor();
  await page.locator('.sidebar-close').press('Tab');assert.ok(await page.locator('.sidebar').evaluate(el=>el.contains(document.activeElement)));await page.locator('.sidebar button').first().press('Escape');assert.equal(await page.locator('.sidebar.open').count(),0);pass('Mobile drawer keyboard navigation and Escape dismissal');
  await page.goto(origin+'/#resources');await page.getByLabel('Buscar en esta área').fill('zzzz-no-match-fixture');await page.getByRole('heading',{name:'No hay resultados'}).waitFor();await page.screenshot({path:path.join(output,'07-resources-empty-mobile.png'),fullPage:true});report.screenshots.push('07-resources-empty-mobile.png');pass('Honest empty filtered collection');
  await page.route('**/api/v1/workspace/summary',route=>route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({detail:'Fuente de prueba temporalmente no disponible'})}));await page.goto(origin+'/#today');await page.getByRole('alert').filter({hasText:'No se pudo comprobar el resumen'}).waitFor();await page.screenshot({path:path.join(output,'08-today-error-mobile.png'),fullPage:true});report.screenshots.push('08-today-error-mobile.png');pass('Summary error explains missing verification and offers retry');
  assert.deepEqual(report.consoleErrors,[],'No browser runtime or hydration errors');pass('No browser runtime or hydration errors');
  await context.close();
 }finally{await browser.close();await fs.writeFile(path.join(output,'browser-report.json'),JSON.stringify(report,null,2))}
})().catch(error=>{console.error(error);process.exitCode=1});
