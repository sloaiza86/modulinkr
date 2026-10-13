// Comprueba las operaciones reales con respuestas de la API y lecturas simuladas.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');
const staticDir = path.resolve(__dirname, '../static');
(async () => {
  const browser = await chromium.launch({ headless: true, channel: 'chrome' });
  try {
    const page = await browser.newPage({ viewport: { width: 1000, height: 1000 } });
    const errors = []; page.on('pageerror', e => errors.push(e.message));
    await page.setContent('<html lang="es"><head><meta charset="utf-8"></head><body><main style="margin:24px"><div id="dashboard"></div><modulinkr-node-control data-origin="1"></modulinkr-node-control></main><dialog id="operacion-dialogo" aria-labelledby="operacion-titulo"><modulinkr-operation-editor></modulinkr-operation-editor></dialog></body></html>');
    await page.addStyleTag({ path: path.join(staticDir, 'style.css') });
    await page.evaluate(() => {
      window.htmlSeguro = s => String(s).replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;');
      window.cerrarDetalle = () => {};
      window.iconoMdi = () => '';
      window.data = { origin:1, unavailable:'', operations:[], actions:[
        {id:'tc06',name:'Corrección de temperatura',unit:'°C',device:0,device_name:'Sensor',signature:1,function:'write_single_register',type:'int16',count:1,scale:.1,offset:0,read_channels:[0],ui:{min:-10,max:10,step:.1},observed:{value:0,captured:1000}},
        {id:'tc10',name:'Corrección (0x10)',unit:'°C',device:0,device_name:'Sensor',signature:2,function:'write_multiple_registers',type:'int16',count:1,scale:.1,read_channels:[0],ui:{advanced:true},observed:{value:0,captured:1000}},
        {id:'relay',name:'Ventilación',device:1,device_name:'Relés',signature:3,function:'write_single_coil',count:1,read_channels:[],observed:{value:false,captured:1000}},
        {id:'bank',name:'Banco',device:1,device_name:'Relés',signature:4,function:'write_multiple_coils',count:4,read_channels:[1,2,3,4],ui:{labels:['Ventilación','Luz','Bomba','Alarma']},observed:{value:[false,true,false,false],captured:1000}},
        {id:'mode',name:'Modo',device:2,signature:5,function:'write_single_register',count:1,type:'uint16',ui:{kind:'select',options:[{value:0,label:'Automático'},{value:1,label:'Manual'}]},observed:{value:0,captured:1000}},
        {id:'flow',name:'Caudal',device:2,signature:6,function:'write_multiple_registers',count:2,type:'float32',unit:'L/min',ui:{min:0,max:100,step:.25},observed:{value:7.5,captured:1000}},
        {id:'position',name:'Posición',device:2,signature:7,function:'write_multiple_registers',count:2,type:'int32',observed:{value:-1500,captured:1000}},
        {id:'counter',name:'Contador',device:2,signature:8,function:'write_multiple_registers',count:2,type:'uint32',observed:{value:16777217,captured:1000}},
        {id:'reset',name:'Poner a cero',device:2,signature:9,function:'write_multiple_registers',count:2,type:'uint32',ui:{kind:'action',value:0},observed:{value:16777217,captured:1000}},
      ] };
      window.posts=[];
      window.fetchApi = async (url, options) => {
        if (options?.method === 'POST') {
          if(window.writeFailure)return {ok:false,json:async()=>({detail:'Escritura rechazada en prueba'})};
          const post=JSON.parse(options.body); window.posts.push(post); window.headers=options.headers;
          window.data.operations=[{id:window.posts.length,device:post.device,action:post.action,value:post.value,state:'queued',created:1000,transport:'',detail:'',verification:'pending'}];
          return {ok:true,json:async()=>({id:window.posts.length,state:'queued'})};
        }
        return {ok:true,json:async()=>structuredClone(window.data)};
      };
    });
    const app=fs.readFileSync(path.join(staticDir,'app.js'),'utf8');
    await page.addScriptTag({content:app.slice(app.indexOf('function operacionesNodo('),app.indexOf('function tarjetaNodo('))});
    await page.addScriptTag({content:app.slice(app.indexOf('document.addEventListener("modulinkr-control-load"'))});
    await page.addScriptTag({path:path.join(staticDir,'components.js')});
    await page.waitForSelector('.control-acciones button');
    assert(!await page.locator('modulinkr-node-control').textContent().then(s=>s.includes('respaldo automático')));
    assert.equal(await page.evaluate(()=>ModulinkrOperations.visible(data.actions).length),8);
    await page.evaluate(()=>{
      const cat={writes:data.actions,reads:[{id:'ctemp',name:'Corrección de temperatura'}]};
      document.getElementById('dashboard').innerHTML=tarjetaOperacion(1,data.actions[0],{channels:[{value:0}]},cat);
    });
    await page.locator('[data-operation-id="tc06"]').click();
    await page.waitForSelector('.operacion-form input');
    assert.equal(await page.locator('.operacion-lectura').textContent(),'0 °C');
    await page.locator('.operacion-form input').fill('1');
    await page.evaluate(()=>document.querySelector('modulinkr-operation-editor').refresh());
    assert.equal(await page.locator('.operacion-form input').inputValue(),'1');
    await page.locator('.operacion-aplicar').click();
    await page.waitForFunction(()=>posts.length===1);
    assert.deepEqual(await page.evaluate(()=>posts[0]),{device:0,action:'tc06',value:1});
    assert.equal(await page.evaluate(()=>headers['X-Modulinkr-Command']),'1');
    assert.equal(await page.locator('.operacion-lectura').textContent(),'0 °C');
    assert.equal(await page.locator('.operacion-aplicar').isDisabled(),true);
    await page.evaluate(()=>{data.operations[0].state='confirmed';data.operations[0].transport='nbiot';document.querySelector('modulinkr-operation-editor').refresh();});
    await page.waitForFunction(()=>document.querySelector('.operacion-estado').textContent.includes('Esperando lectura'));
    assert.equal(await page.locator('.operacion-lectura').textContent(),'0 °C');
    await page.evaluate(()=>{data.actions[0].observed={value:1,captured:1001};data.operations[0].verification='verified';document.querySelector('modulinkr-operation-editor').refresh();});
    await page.waitForFunction(()=>document.querySelector('.operacion-estado').textContent.includes('Lectura verificada'));
    assert.equal(await page.locator('.operacion-lectura').textContent(),'1 °C');
    assert.equal(await page.locator('.operacion-aplicar').isDisabled(),true);
    await page.locator('.operacion-cerrar').click();
    assert.equal(await page.evaluate(()=>document.getElementById('operacion-dialogo').open),false);
    async function open(id){await page.evaluate(id=>{data.operations=[]; const a=data.actions.find(a=>a.id===id); abrirOperacion(1,a.device,id);},id);await page.waitForFunction(id=>document.querySelector('modulinkr-operation-editor')._action?.id===id,id);}
    for(const [id,value] of [['flow','8.25'],['position','-1800'],['counter','4294967295']]){
      await open(id);await page.locator('.operacion-form input').fill(value);await page.locator('.operacion-aplicar').click();await page.waitForFunction(id=>posts.at(-1).action===id,id);assert.equal(await page.evaluate(()=>posts.at(-1).value),Number(value));
    }
    await open('mode');await page.locator('.operacion-form select').selectOption('1');await page.locator('.operacion-aplicar').click();await page.waitForFunction(()=>posts.at(-1).action==='mode');assert.equal(await page.evaluate(()=>posts.at(-1).value),1);
    await open('relay');await page.locator('.operacion-form input').check();await page.locator('.operacion-aplicar').click();await page.waitForFunction(()=>posts.at(-1).action==='relay');assert.equal(await page.evaluate(()=>posts.at(-1).value),true);
    await open('bank');await page.locator('.operacion-form input').nth(0).check();await page.locator('.operacion-aplicar').click();await page.waitForFunction(()=>posts.at(-1).action==='bank');assert.deepEqual(await page.evaluate(()=>posts.at(-1).value),[true,true,false,false]);
    await open('reset');const count=await page.evaluate(()=>posts.length);await page.locator('.operacion-aplicar').click();assert.equal(await page.evaluate(()=>posts.length),count);assert.equal(await page.locator('.operacion-aplicar').textContent(),'Confirmar acción');await page.locator('.operacion-aplicar').click();await page.waitForFunction(()=>posts.at(-1).action==='reset');assert.equal(await page.evaluate(()=>posts.at(-1).value),0);
    await open('tc06');await page.locator('.operacion-form input').fill('10.1');assert.equal(await page.locator('.operacion-aplicar').isDisabled(),true);await page.locator('.operacion-form input').fill('0.15');assert.equal(await page.locator('.operacion-aplicar').isDisabled(),true);
    await page.setViewportSize({width:320,height:750});
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
    assert.equal(await page.evaluate(()=>{const d=document.getElementById('operacion-dialogo');return d.scrollWidth<=d.clientWidth;}),true);
    const screenshot=process.env.MODULINKR_CONTROL_SCREENSHOT;if(screenshot)await page.screenshot({path:screenshot,fullPage:true});
    await page.evaluate(()=>{data.operations=[];window.writeFailure=true;document.querySelector('modulinkr-operation-editor').refresh();});
    await page.locator('.operacion-form input').fill('7');await page.locator('.operacion-aplicar').click();
    await page.waitForFunction(()=>document.querySelector('.operacion-estado').textContent.includes('Escritura rechazada en prueba'));
    await page.evaluate(()=>document.querySelector('modulinkr-operation-editor').refresh());
    assert.match(await page.locator('.operacion-estado').textContent(),/Escritura rechazada en prueba/);
    await page.evaluate(()=>{window.writeFailure=false;});await page.locator('.operacion-form input').fill('5');
    await page.evaluate(()=>{data.unavailable='Servicio no disponible';document.querySelector('modulinkr-operation-editor').refresh();});
    await page.waitForFunction(()=>document.querySelector('.operacion-estado').textContent.includes('Servicio no disponible'));
    assert.equal(await page.locator('.operacion-aplicar').isDisabled(),true);
    await page.evaluate(()=>{data.actions=[];data.unavailable='';document.querySelector('modulinkr-operation-editor').refresh();});
    await page.waitForFunction(()=>document.querySelector('.operacion-estado').textContent.includes('ya no está declarada'));
    assert.equal(await page.locator('.operacion-aplicar').isDisabled(),true);
    await page.locator('.operacion-cerrar').click();
    const index=fs.readFileSync(path.join(staticDir,'index.html'),'utf8');
    const detail=index.slice(index.indexOf('<div id="detalle-fondo"'),index.indexOf('<dialog id="operacion-dialogo"'));
    await page.evaluate(markup=>document.body.insertAdjacentHTML('beforeend',markup),detail);
    await page.evaluate(()=>{
      data.unavailable='';data.actions=[{id:'tc06',name:'Corrección (06)',unit:'°C',device:0,signature:1,function:'write_single_register',count:1,type:'int16',scale:.1,read_channels:[2],observed:{value:0}}];
      window.cacheEstado={service_online:true,online_s:60,nodes:[{origin:1,name:'Nodo Sensor Ambiental',delivery_online:true,fw_version:'0.0.81',rssi:-18,snr:12,hop_count:1,transport:'lora'}]};
      window.cacheUltimos={nodes:[{origin:1,channels:[{read_id:'temp',value:30.2,unit:'C'},{read_id:'hum',value:44.5,unit:'%RH'},{read_id:'ctemp',value:0,unit:'°C'}]}]};
      window.cacheCatalogosRed=[{origin:1,writes:data.actions,reads:[{id:'temp',name:'Temperatura'},{id:'hum',name:'Humedad'},{id:'ctemp',name:'Corrección de temperatura'}]}];
      window.catalogo=null;window.detalleOrigen=1;window.redAhora=1000;
      window.esSupernodo=()=>false;window.chipsNodo=()=>[];window.chipConexion=()=>'';window.iconoMedida=()=>'';window.chipEstado=()=>({cls:'verde',txt:'En línea'});
      window.actualizarContenido=(el,markup)=>{el.innerHTML=markup;};window.filaDet=(name,value)=>'<p>'+name+': '+value+'</p>';
      window.fwVersionTexto=v=>v;window.fmtNum=v=>String(v);window.fmtValor=v=>String(v);window.unidad=v=>v;window.chipDuty=()=>'';
      window.edadEnVivo=()=>'';window.nombreNodoRuta=()=>'';
    });
    await page.addScriptTag({content:app.slice(app.indexOf('function nombreCanalDetalle('),app.indexOf('async function verNodoEnDatos('))});
    await page.addScriptTag({content:app.slice(app.indexOf('document.getElementById("detalle-cuerpo").addEventListener("click"'),app.indexOf('document.getElementById("detalle-acciones").addEventListener("click"'))});
    await page.evaluate(()=>{pintarDetalle(1);document.getElementById('detalle').show();});
    await page.waitForFunction(()=>document.getElementById('detalle').classList.contains('abierto'));
    assert.equal(await page.locator('#detalle-pane-device .detalle-medida').count(),2);
    assert.equal(await page.locator('#detalle-pane-device [data-operation-id]').count(),1);
    assert.equal(await page.locator('#detalle-cuerpo').evaluate(el=>el.scrollWidth<=el.clientWidth),true);
    assert.equal(await page.locator('#detalle-tab-diagnostic').evaluate(el=>{const range=document.createRange();range.selectNodeContents(el);return range.getClientRects().length;}),1);
    await page.locator('#detalle-tab-device').focus();await page.keyboard.press('ArrowRight');
    await page.waitForFunction(()=>document.getElementById('detalle-tab-activity').getAttribute('aria-selected')==='true');
    await page.keyboard.press('ArrowRight');
    await page.waitForFunction(()=>document.getElementById('detalle-tab-diagnostic').getAttribute('aria-selected')==='true');
    assert.match(await page.locator('#detalle-pane-diagnostic').textContent(),/RSSI/);
    await page.locator('#detalle-tab-device').click();
    const last=page.locator('#detalle-acciones button');await last.focus();await page.keyboard.press('Tab');
    assert.equal(await page.evaluate(()=>document.activeElement.id),'detalle-cerrar');
    if(screenshot)await page.screenshot({path:screenshot.replace('.png','-device.png'),fullPage:true});
    assert.deepEqual(errors,[]);
    console.log('OK: operaciones numéricas, selector, bobinas, grupo, acción, límites, borrador conservado, envío directo, lectura separada, verificación, catálogo sustituido y ancho de 320 px');
  }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1);});
