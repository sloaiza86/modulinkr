// Comprueba la resolución visible con datos y respuestas locales.
const assert=require('node:assert/strict');
const path=require('node:path');
const {chromium}=require('playwright');
(async()=>{
 const browser=await chromium.launch({headless:true,channel:'chrome'});
 try {
  const page=await browser.newPage({viewport:{width:1100,height:900}});
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.setContent('<html lang="es"><body><div id="identidad-incidencias"></div></body></html>');
  await page.addStyleTag({path:path.resolve(__dirname,'../static/style.css')});
  await page.addScriptTag({path:path.resolve(__dirname,'../static/identity.js')});
  await page.evaluate(()=>ModulinkrIdentity.render({identity:{incidents:[
    {kind:'legacy_pending',origin:0,detail:'Datos de desarrollo'},
    {kind:'unverified',origin:1,detail:'Formato antiguo'}]}}));
  assert.equal(await page.locator('#identidad-incidencias').innerText(),'');
  await page.evaluate(()=>{
   window.posts=[];
   window.fetch=async(url,options)=>{posts.push({url,headers:options.headers,body:JSON.parse(options.body)});return {ok:true,json:async()=>({resolved:true})};};
   ModulinkrIdentity.render({deployment_id:'ffffffffffff',identity:{incidents:[{kind:'node_conflict',origin:1,detail:'Hay dos equipos con ID 1.'}],sightings:[{origin:1,device_uid:'010101010101',last_seen:1000},{origin:1,device_uid:'020202020202',last_seen:1001}]}});
  });
  await page.getByRole('button',{name:'Resolver conflicto'}).click();
  await page.locator('select').selectOption('010101010101');
  await page.locator('textarea').fill('Se desconectó el segundo equipo.');
  await page.getByRole('button',{name:'Confirmar resolución'}).click();
  assert.equal(await page.evaluate(()=>posts.length),0);
  await page.locator('input[type=checkbox]').check();
  await page.getByRole('button',{name:'Confirmar resolución'}).click();
  await page.waitForFunction(()=>posts.length===1);
  assert.equal(await page.evaluate(()=>posts[0].headers['X-Modulinkr-Identity']),'1');
  assert.equal(await page.evaluate(()=>posts[0].body.device_uid),'010101010101');
  await page.waitForFunction(()=>!document.querySelector('dialog'));
  await page.evaluate(()=>ModulinkrIdentity.render({deployment_id:'ffffffffffff',identity:{incidents:[{kind:'gateway_conflict',origin:255,detail:'Dos gateways.'}],sightings:[{origin:255,device_uid:'ffffffffffff',last_seen:1000},{origin:255,device_uid:'eeeeeeeeeeee',last_seen:1000}]}}));
  await page.getByRole('button',{name:'Resolver conflicto'}).click();
  assert.equal(await page.locator('select option').count(),2);
  assert.equal(await page.locator('select option').last().getAttribute('value'),'ffffffffffff');
  assert.deepEqual(errors,[]);
  console.log('OK: conflicto visible, confirmación obligatoria, envío y selección del gateway actual');
 } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
