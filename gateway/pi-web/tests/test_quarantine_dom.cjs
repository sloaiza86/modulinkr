// Comprueba navegación, consulta, detalle y pérdida de conexión en navegador.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const {chromium} = require('playwright');
const root = path.resolve(__dirname, '..');
const server = http.createServer((req, res) => {
  const name = new URL(req.url, 'http://localhost').pathname;
  const file = path.join(root, name);
  if (!name.startsWith('/static/') || !fs.existsSync(file)) {
    res.setHeader('Content-Type', 'text/html; charset=utf-8'); res.end('<html lang="es"><head><meta charset="utf-8"></head><body></body></html>'); return;
  }
  res.setHeader('Content-Type', (file.endsWith('.js') ? 'text/javascript' : file.endsWith('.css') ? 'text/css'
    : file.endsWith('.svg') ? 'image/svg+xml' : file.endsWith('.png') ? 'image/png' : 'application/json') + '; charset=utf-8');
  res.end(fs.readFileSync(file));
});
(async () => {
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  let browser;
  try {
    browser = await chromium.launch({headless: true, channel: 'chrome'});
    const page = await browser.newPage({viewport: {width: 1400, height: 1000}});
    const errors = []; page.on('pageerror', error => errors.push(error.message));
    const origin = `http://127.0.0.1:${server.address().port}`;
    await page.goto(origin);
    await page.evaluate(html => {
      const doc = new DOMParser().parseFromString(html, 'text/html');
      doc.querySelectorAll('script').forEach(script => script.remove());
      document.body.innerHTML = doc.body.innerHTML;
    }, fs.readFileSync(path.join(root, 'static/index.html'), 'utf8'));
    await page.addStyleTag({url: origin + '/static/style.css'});
    await page.addScriptTag({url: origin + '/static/components.js'});
    await page.evaluate(() => {
      for (const name of ['cfgLocalCerrar', 'migSondeoParar', 'debugStop', 'bcSondeoParar',
        'programarMasonryTarjetas', 'mqttCargar', 'cargarCatalogo']) window[name] = () => {};
      window.mantenimientoTimer = null;
      window.opcHora = options => ({...options, timeZone: 'Europe/Madrid'});
      window.htmlSeguro = value => String(value ?? '').replace(/[&<>"']/g, char =>
        ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
      window.requests = []; window.fail = false; window.delay = 0;
      window.textoCliente = value => value;
      window.canDelete = true; window.deletePlans = new Map(); window.planCounter = 0;
      window.confirmCalls = 0; window.confirmFailure = '';
      const item = (id, origin, reason) => ({id:String(id), origin, name:origin===1?'Sensor ambiental':null,
        reason, ts:'2025-01-01T10:00:00+00:00', seq:12, source:id%2?'lora':'nbiot',
        values:[23.5,null,61.2], received_at:'2026-10-13T11:00:00+00:00'});
      window.items = Array.from({length:53}, (_,i) => item(i+1, i===0?2:1, i===0?'unknown_node':i===1?'no_channels':'length_mismatch'));
      window.originalItems = structuredClone(items);
      window.fetchApi = async (url, opts) => {
        requests.push(url);
        if (url === '/api/db/estado') return {ok:true,json:async()=>({config:{host:'cloud.example'},password_set:true})};
        if (opts?.method === 'POST') {
          if (opts.headers['X-Modulinkr-Quarantine'] !== '1') throw new Error('missing header');
          const payload = JSON.parse(opts.body);
          if (url.endsWith('/preparar')) {
            const rows = items.filter(i => payload.scope === 'all' || payload.scope === 'selected' && payload.ids.includes(i.id)
              || payload.scope === 'filtered' && (!payload.origin || i.origin === payload.origin) && (!payload.motivo || i.reason === payload.motivo));
            const token = `review-${++planCounter}`;
            deletePlans.set(token, rows.map(i=>i.id));
            return {ok:true,json:async()=>({token,scope:payload.scope,count:rows.length,nodes:[...new Set(rows.map(i=>i.origin))]})};
          }
          confirmCalls++;
          const ids = deletePlans.get(payload.token);
          if (!ids) return {ok:false,json:async()=>({detail:'La revisión ha caducado o ya se utilizó.'})};
          deletePlans.delete(payload.token);
          const deleted = items.filter(i=>ids.includes(i.id)).length;
          items = items.filter(i=>!ids.includes(i.id));
          if (confirmFailure === 'network_after_commit') throw new Error('No hay conexión con el gateway.');
          return {ok:true,json:async()=>({deleted,reviewed:ids.length,skipped:ids.length-deleted})};
        }
        const failed = fail, wait = delay, snapshot = structuredClone(items);
        if (wait) await new Promise(resolve => setTimeout(resolve, wait));
        if (opts?.signal?.aborted) throw new Error('cancelled');
        if (failed) return {ok:false,json:async()=>({detail:'No se pudo consultar'})};
        const nodes = [...new Set(snapshot.map(i=>i.origin))].map(nid=>({origin:nid,
          name:snapshot.find(i=>i.origin===nid).name,count:snapshot.filter(i=>i.origin===nid).length}));
        const result = {total:snapshot.length,node_count:nodes.length,nodes,can_delete:canDelete,
          checked_at:'2026-10-13T11:00:00+00:00'};
        if (!url.endsWith('/resumen')) {
          const params = new URL(url, location.origin).searchParams;
          const nid = Number(params.get('origin')), reason = params.get('motivo');
          const filtered = snapshot.filter(i=>(!nid || i.origin===nid) && (!reason || i.reason===reason));
          result.filtered_count=filtered.length; result.limit=50;
          result.offset=filtered.length?Math.min(Number(params.get('offset')),Math.floor((filtered.length-1)/50)*50):0;
          result.items=filtered.slice(result.offset,result.offset+50);
        }
        return {ok:true,json:async()=>result};
      };
    });
    await page.addScriptTag({url: origin + '/static/quarantine.js'});
    const source = fs.readFileSync(path.join(root, 'static/app.js'), 'utf8');
    await page.addScriptTag({content: source.slice(source.indexOf('const TITULOS ='), source.indexOf('// ----- Vista de red:'))
      + source.slice(source.indexOf('let servidorTab ='), source.indexOf('const MANTENIMIENTO ='))
      + source.slice(source.indexOf('function bdEstadoHtml('), source.indexOf('function bdBody('))
      + source.slice(source.indexOf('const SPIN ='), source.indexOf('// Tras un CFG.PUT'))
      + source.slice(source.indexOf('document.getElementById("cfg-dialogo-cerrar").addEventListener("click", cfgDialogoCerrar);'),
        source.indexOf('// ----- Vista Configuración: radio LoRa'))});
    assert.deepEqual(errors, []);
    await page.evaluate(() => {fail=true;location.hash='#/configuracion/bd'; navegar();});
    await page.waitForFunction(()=>document.getElementById('quar-card-status').textContent.includes('No se pudo'));
    assert.equal(await page.locator('#quar-card-summary').textContent(), '');
    await page.evaluate(()=>{fail=false;servidorSetTab('bd');});
    await page.waitForFunction(()=>document.getElementById('quar-card-summary').textContent.includes('53'));
    assert.match(await page.locator('#quar-card-status').textContent(), /13\/10\/2026/);
    await page.evaluate(()=>{fail=true;});
    await page.locator('a[href="#/configuracion/servidor/cuarentena"]').click();
    await page.waitForFunction(()=>document.getElementById('quar-status').textContent.includes('No se pudo'));
    assert.equal(await page.locator('#quar-summary').textContent(), '');
    assert.equal(await page.locator('#quar-empty').isVisible(), false);
    await page.evaluate(()=>{fail=false;});
    await page.locator('#quar-refresh').click();
    await page.waitForFunction(()=>document.querySelectorAll('#quar-rows tr').length===50);
    assert.equal(await page.locator('#titulo-vista').textContent(), 'Datos en cuarentena');
    assert.equal(await page.locator('#quar-node option[value="2"]').count(), 1);
    assert.match(await page.locator('#quar-rows').textContent(), /01\/01\/2025/);
    await page.locator('[data-quar-id="1"]').click();
    assert.equal(await page.locator('#quar-detail-title').evaluate(el=>el===document.activeElement), true);
    assert.equal(await page.locator('.quar-values tbody tr').count(), 3);
    assert.match(await page.locator('.quar-values').textContent(), /Posición 2Sin valor/);
    await page.locator('#quar-refresh').click();
    await page.waitForFunction(()=>!document.getElementById('quar-refresh').disabled);
    assert.equal(await page.locator('#quar-detail').isVisible(), true);
    await page.locator('#quar-detail-close').click();
    assert.equal(await page.locator('[data-quar-id="1"]').evaluate(el=>el===document.activeElement), true);
    await page.locator('#quar-next').click();
    await page.waitForFunction(()=>document.getElementById('quar-page').textContent.startsWith('51'));
    assert.equal(await page.locator('#quar-rows tr').count(), 3);
    await page.evaluate(()=>{fail=true;});
    await page.locator('#quar-prev').click();
    await page.waitForFunction(()=>document.getElementById('quar-status').textContent.includes('No se pudo'));
    assert.match(await page.locator('#quar-page').textContent(), /51 a 53/);
    assert.match(await page.locator('#quar-summary').textContent(), /53/);
    await page.locator('#quar-node').selectOption('2');
    await page.waitForFunction(()=>!document.getElementById('quar-node').disabled);
    assert.equal(await page.locator('#quar-node').inputValue(), '0');
    assert.match(await page.locator('#quar-page').textContent(), /51 a 53/);
    await page.evaluate(()=>{fail=false;});
    await page.locator('#quar-node').selectOption('2');
    await page.waitForFunction(()=>document.querySelectorAll('#quar-rows tr').length===1);
    await page.locator('#quar-reason').selectOption('length_mismatch');
    await page.waitForFunction(()=>!document.getElementById('quar-empty').hidden);
    assert.equal(await page.locator('#quar-empty').textContent(), 'Sin resultados para estos filtros.');
    await page.locator('#quar-reason').selectOption('');
    await page.waitForFunction(()=>document.querySelectorAll('#quar-rows tr').length===1);
    await page.evaluate(()=>{items[0].name='<img src=x onerror="window.injected=true">';});
    await page.locator('#quar-refresh').click();
    await page.waitForFunction(()=>document.getElementById('quar-rows').textContent.includes('<img'));
    assert.equal(await page.locator('#quar-rows img').count(), 0);
    await page.locator('[data-quar-id="1"]').click();
    assert.equal(await page.locator('#quar-detail img').count(), 0);
    await page.evaluate(()=>{items[0].name=null;});
    await page.locator('#quar-refresh').click();
    await page.waitForFunction(()=>!document.getElementById('quar-refresh').disabled);
    await page.screenshot({path:process.env.MODULINKR_QUARANTINE_DESKTOP_SCREENSHOT || '/private/tmp/modulinkr-quarantine-desktop.png',fullPage:true});
    await page.setViewportSize({width:320,height:950});
    await page.waitForFunction(()=>getComputedStyle(document.getElementById('contenido')).marginLeft==='0px');
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=320), true,
      JSON.stringify(await page.evaluate(()=>[...document.querySelectorAll('#cfg-cuarentena *')]
        .filter(el=>el.getBoundingClientRect().right>320).map(el=>[el.tagName,el.id,el.className,el.getBoundingClientRect().right]))));
    await page.screenshot({path:process.env.MODULINKR_QUARANTINE_MOBILE_SCREENSHOT || '/private/tmp/modulinkr-quarantine-mobile.png',fullPage:true});
    await page.evaluate(()=>{items=[];});
    await page.locator('#quar-refresh').click();
    await page.waitForFunction(()=>document.getElementById('quar-empty').textContent==='Sin muestras en cuarentena.');
    assert.equal(await page.locator('#quar-detail').isVisible(), false);
    assert.equal(await page.locator('#quar-node').inputValue(), '2');
    await page.evaluate(()=>{delay=250;});
    await page.locator('#quar-refresh').click();
    await page.evaluate(()=>{location.hash='#/configuracion';});
    await page.waitForTimeout(400);
    assert.equal(await page.locator('#cfg-cuarentena').isVisible(), false);
    assert.equal(await page.locator('#quar-refresh').isDisabled(), false);
    await page.evaluate(()=>{fail=true;delay=0;location.hash='#/configuracion/bd';});
    await page.waitForFunction(()=>document.getElementById('quar-card-status').textContent.includes('No se pudo'));
    assert.match(await page.locator('#quar-card-summary').textContent(), /53/);
    assert.match(await page.locator('#quar-card-status').textContent(), /pueden haber cambiado/);
    await page.setViewportSize({width:1400,height:1000});
    await page.evaluate(()=>{fail=false;items=structuredClone(originalItems);
      document.getElementById('quar-node').value='0';document.getElementById('quar-reason').value='';
      location.hash='#/configuracion/servidor/cuarentena';});
    await page.waitForFunction(()=>document.querySelectorAll('#quar-rows tr').length===50 && !document.getElementById('quar-refresh').disabled);
    await page.locator('.quar-delete-tools > summary').click();
    assert.equal(await page.locator('#quar-delete-selected').isDisabled(), true);
    assert.equal(await page.locator('#quar-delete-filtered').isDisabled(), true);
    await page.locator('[data-quar-check="1"]').check();
    await page.locator('#quar-delete-selected').click();
    await page.waitForFunction(()=>!document.getElementById('cfg-dialogo-confirmar').hidden);
    assert.match(await page.locator('#cfg-dialogo-texto').textContent(), /1 registros/);
    await page.waitForFunction(()=>document.activeElement.id==='cfg-dialogo-cancelar');
    await page.keyboard.press('Escape');
    assert.equal(await page.evaluate(()=>confirmCalls), 0);
    assert.equal(await page.evaluate(()=>items.length), 53);
    await page.locator('#quar-delete-selected').click();
    await page.waitForFunction(()=>!document.getElementById('cfg-dialogo-confirmar').hidden);
    await page.evaluate(()=>{items.push({...originalItems[0],id:'100'});});
    await page.locator('#cfg-dialogo-confirmar').click();
    await page.waitForFunction(()=>document.getElementById('quar-delete-result').textContent.includes('1 registros borrados'));
    await page.waitForFunction(()=>!document.getElementById('quar-refresh').disabled);
    assert.equal(await page.evaluate(()=>items.some(i=>i.id==='100')), true);
    assert.equal(await page.evaluate(()=>items.some(i=>i.id==='1')), false);
    await page.locator('#quar-node').selectOption('1');
    await page.waitForFunction(()=>!document.getElementById('quar-refresh').disabled);
    await page.locator('#quar-reason').selectOption('length_mismatch');
    await page.waitForFunction(()=>!document.getElementById('quar-refresh').disabled);
    await page.locator('#quar-delete-filtered').click();
    await page.waitForFunction(()=>!document.getElementById('cfg-dialogo-confirmar').hidden);
    assert.match(await page.locator('#cfg-dialogo-texto').textContent(), /51 registros/);
    assert.match(await page.locator('#cfg-dialogo-texto').textContent(), /Sensor ambiental.*Cantidad de valores/);
    await page.evaluate(()=>{items=items.filter(i=>i.id!=='3');items.push({...originalItems[2],id:'101'});});
    await page.locator('#cfg-dialogo-confirmar').click();
    await page.waitForFunction(()=>document.getElementById('quar-delete-result').textContent.includes('50 registros borrados'));
    await page.waitForFunction(()=>!document.getElementById('quar-refresh').disabled);
    assert.match(await page.locator('#quar-delete-result').textContent(), /1 registros revisados ya no/);
    assert.deepEqual(await page.evaluate(()=>items.map(i=>i.id).sort()), ['100','101','2']);
    await page.locator('#quar-delete-all').click();
    await page.waitForFunction(()=>!document.getElementById('cfg-dialogo-confirmar').hidden);
    assert.match(await page.locator('#cfg-dialogo-texto').textContent(), /3 registros/);
    assert.match(await page.locator('#cfg-dialogo-texto').textContent(), /independientemente de los filtros/);
    await page.screenshot({path:'/private/tmp/modulinkr-quarantine-delete-desktop.png',fullPage:true});
    await page.setViewportSize({width:320,height:950});
    await page.waitForFunction(()=>getComputedStyle(document.getElementById('contenido')).marginLeft==='0px');
    await page.screenshot({path:'/private/tmp/modulinkr-quarantine-delete-mobile.png',fullPage:true});
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=320), true);
    await page.evaluate(()=>{items.push({...originalItems[0],id:'102'});});
    await page.locator('#cfg-dialogo-confirmar').click();
    await page.waitForFunction(()=>document.getElementById('quar-delete-result').textContent.includes('3 registros borrados'));
    await page.waitForFunction(()=>!document.getElementById('quar-refresh').disabled);
    assert.deepEqual(await page.evaluate(()=>items.map(i=>i.id)), ['102']);
    for (const width of [1400,320]) {
      await page.setViewportSize({width,height:950});
      await page.waitForFunction(()=>{
        const content = parseFloat(getComputedStyle(document.getElementById('contenido')).marginLeft);
        const sidebar = document.getElementById('sidebar');
        return innerWidth > 860 ? Math.abs(content - sidebar.getBoundingClientRect().width) < 1
          : content < 1 && getComputedStyle(sidebar).visibility === 'hidden';
      });
      await page.waitForFunction(()=>document.getElementById('quar-empty').getBoundingClientRect().top
        - document.querySelector('.quar-filters').getBoundingClientRect().bottom >= 12);
      const geometry = await page.evaluate(()=>({
        message:document.getElementById('quar-empty').getBoundingClientRect().toJSON(),
        selection:document.getElementById('quar-select-page').closest('.cfg-fila').getBoundingClientRect().toJSON()}));
      assert(geometry.selection.top - geometry.message.bottom >= 8);
      assert.equal(await page.locator('#quar-empty').textContent(), 'Sin resultados para estos filtros.');
      await page.screenshot({path:`/private/tmp/modulinkr-quarantine-empty-${width}.png`,fullPage:true});
    }
    await page.evaluate(()=>{canDelete=false;});
    await page.locator('#quar-refresh').click();
    await page.waitForFunction(()=>document.getElementById('quar-delete-permission').textContent.includes('no habilitado'));
    assert.equal(await page.locator('#quar-delete-all').isDisabled(), true);
    await page.evaluate(()=>{canDelete=true;});
    await page.locator('#quar-refresh').click();
    await page.waitForFunction(()=>!document.getElementById('quar-delete-all').disabled);
    await page.locator('#quar-delete-all').click();
    await page.waitForFunction(()=>!document.getElementById('cfg-dialogo-confirmar').hidden);
    await page.evaluate(()=>{items.push({...originalItems[0],id:'103'});confirmFailure='network_after_commit';});
    const before = await page.evaluate(()=>confirmCalls);
    await page.locator('#cfg-dialogo-confirmar').click();
    await page.waitForFunction(()=>document.getElementById('quar-delete-result').textContent.includes('No se pudo confirmar el resultado'));
    await page.waitForFunction(()=>!document.getElementById('quar-refresh').disabled);
    assert.equal(await page.evaluate(()=>confirmCalls), before+1);
    assert.deepEqual(await page.evaluate(()=>items.map(i=>i.id)), ['103']);
    await page.evaluate(()=>{confirmFailure='';});
    await page.locator('#quar-delete-all').click();
    await page.waitForFunction(()=>!document.getElementById('cfg-dialogo-confirmar').hidden);
    await page.evaluate(()=>deletePlans.clear());
    await page.locator('#cfg-dialogo-confirmar').click();
    await page.waitForFunction(()=>document.getElementById('quar-delete-result').textContent.includes('caducado'));
    assert.deepEqual(await page.evaluate(()=>items.map(i=>i.id)), ['103']);
    assert.deepEqual(errors, []);
    console.log('Cuarentena: consulta, selección, tres alcances, confirmación, cancelación, permisos, nuevas llegadas y fallo de respuesta comprobados.');
  } finally {
    if (browser) await browser.close();
    await new Promise(resolve => server.close(resolve));
  }
})().catch(error => {console.error(error); process.exitCode=1;});
