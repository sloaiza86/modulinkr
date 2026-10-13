// Comprueba continuidad de medidas y conservación de los canales originales.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const { chromium } = require('playwright');
const root = path.resolve(__dirname, '..');
const server = http.createServer((req, res) => {
  const relative = new URL(req.url, 'http://localhost').pathname;
  const file = path.join(root, relative);
  if ((!relative.startsWith('/static/') && relative !== '/tests/test_chart_dom.html') || !fs.existsSync(file)) {
    res.setHeader('Content-Type', 'text/html'); res.end('<html lang="es"><head></head><body></body></html>'); return;
  }
  res.setHeader('Content-Type', file.endsWith('.js') ? 'text/javascript' : file.endsWith('.css') ? 'text/css' : file.endsWith('.html') ? 'text/html' : 'application/json');
  res.end(fs.readFileSync(file));
});
(async () => {
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  let browser;
  try {
    browser = await chromium.launch({headless: true, channel: 'chrome'});
    const page = await browser.newPage({viewport: {width: 1400, height: 900}});
    const errors = []; page.on('pageerror', e => errors.push(e.message));
    const origin = `http://127.0.0.1:${server.address().port}`;
    await page.goto(origin);
    const html = fs.readFileSync(path.join(root, 'static/index.html'), 'utf8');
    await page.evaluate(html => {
      const doc = new DOMParser().parseFromString(html, 'text/html');
      const view = doc.getElementById('view-datos'); view.hidden = false;
      document.body.appendChild(view);
      document.documentElement.style.setProperty('--sb-ancho','0px');
    }, html);
    await page.addStyleTag({url: origin + '/static/style.css'});
    await page.addStyleTag({content:'body { display: block; } #view-datos { padding: 12px 12px 84px; }'});
    await page.addStyleTag({url: origin + '/static/vendor/webawesome-3.11.0/dist-cdn/styles/themes/default.css'});
    await page.addScriptTag({url: origin + '/static/vendor/echarts.min.js'});
    for (const name of ['tree-item', 'tree']) await page.addScriptTag({type:'module',url: origin + `/static/vendor/webawesome-3.11.0/dist-cdn/components/${name}/${name}.js`});
    await page.addScriptTag({url: origin + '/static/components.js'});
    await page.evaluate(() => {
      window.cacheEstado = {nodes:[]}; window.cacheUltimos = {nodes:[]}; window.esSupernodo = () => false;
      window.COLORES_GRAFICO = ['blue','purple','green']; window.COLOR = {dim:'#53668c',text:'#17234f',border:'#d9e2f2'};
      window.FUENTE_GRAFICO = 'system-ui'; window.unidad = x => x;
      window.opcHora = options => ({...options,timeZone:'Europe/Madrid'});
      window.fmtDia = d => d.toLocaleDateString('es-ES'); window.fmtHora = d => d.toLocaleTimeString('es-ES');
      window.fmtValor = String; window.fmtEje = String; window.htmlSeguro = String;
      window.toast = () => {}; window.requests = [];
      const cut = '2026-10-12T20:40:00+00:00';
      const channel = (id,previous,name='Temperatura') => ({channel_id:id,read_id:'temp',name,unit:'C',previous,
        active_from:previous?'1970-01-01T00:00:00+00:00':cut,active_to:previous?cut:null});
      window.catalogResponse = [{node_id:1,name:'Sensor ambiental en la base',previous:false,channels:[{...channel(11,false),channel_ids:[11,10],active_from:'1970-01-01T00:00:00+00:00'}]},
        {node_id:2,name:'Nodo anterior',previous:true,channels:[channel(20,true,'Temperatura retirada')]}];
      window.fetchApi = async url => {
        requests.push(url);
        if (url.startsWith('/api/datos/nodos')) return {ok:true,json:async()=>structuredClone(catalogResponse)};
        const ids = new URL(url, location.origin).searchParams.get('channels').split(',').map(Number);
        return {ok:true,json:async()=>({series:ids.map(id=>{
          const points = id === 11 ? Array.from({length:18},(_,i)=>[
            1791835200+(i<9?i*600:43200+(i-9)*600),30+Math.sin(i/3),1,0]) : [[1791828000,id,1,0]];
          return {channel_id:id,unit:'C',bucket_s:1,points,source_points:{lora:points,nbiot:[]}};
        })})};
      };
    });
    const source = fs.readFileSync(path.join(root, 'static/app.js'), 'utf8');
    await page.addScriptTag({content: source.slice(source.indexOf('let chart = null;'), source.indexOf('// ----- Vista Configuración:'))});
    await page.evaluate(() => cargarCatalogo());
    assert.match(await page.evaluate(()=>requests[0]), /incluir_anteriores=true&unificar=true/);
    assert.equal(await page.locator('.medidas-grupo').count(), 1);
    await page.locator('.medidas-grupo-toggle').click();
    assert.equal(await page.locator('wa-tree-item').count(),1);
    await page.locator('#mostrar-anteriores').check();
    assert.equal(await page.locator('.medidas-grupo').count(),2);
    await page.locator('.medidas-grupo-toggle').first().click();
    assert.equal(await page.locator('.medidas-grupo').first().locator('wa-tree-item').count(),1);
    assert.match(await page.locator('.medida-opcion-version').first().textContent(), /12\/10\/2026.*22:40:00/);
    await page.locator('.medidas-grupo-todas').first().click();
    await page.waitForFunction(()=>document.querySelectorAll('.grafico-leyenda-cuerpo [data-accion="serie"]').length===1);
    assert(!/Anterior/.test(await page.locator('#grafico-leyenda').textContent()));
    assert.equal(await page.evaluate(()=>new Set(chart.getOption().series.map(s=>s.name)).size),1);
    await page.evaluate(()=>{selectorPeriodo.value=customElements.get('modulinkr-period-selector').fromPreset('now-30d');});
    await page.evaluate(()=>graficar());
    assert.equal(await page.evaluate(()=>new URL(requests.at(-1),location.origin).searchParams.get('channels')), '11');
    assert.equal(await page.evaluate(()=>new URL(requests.at(-1),location.origin).searchParams.get('unificar')), 'true');
    assert.equal(await page.evaluate(()=>new Date(new URL(requests.at(-1),location.origin).searchParams.get('desde')) < new Date('2026-10-12T20:40:00Z')),true);
    await page.locator('#mostrar-anteriores').uncheck();
    assert.deepEqual(await page.evaluate(()=>[...seleccion]),[11]);
    assert.equal(await page.locator('.medidas-grupo').count(),1);
    const desktopScreenshot = process.env.MODULINKR_HISTORY_DESKTOP_SCREENSHOT;
    if (desktopScreenshot) {
      const toggle = page.locator('.medidas-grupo-toggle').first();
      if (await toggle.getAttribute('aria-expanded') !== 'true') await toggle.click();
      await page.waitForFunction(()=>document.getElementById('datos-vacio').hidden);
      assert.equal(await page.locator('wa-tree-item').count(),1);
      assert.equal(await page.evaluate(()=>chart.getOption().series.length),1);
      await page.screenshot({path:desktopScreenshot,fullPage:true});
    }
    await page.evaluate(()=>{
      localStorage.setItem('modulinkr_vistas',JSON.stringify({Anterior:{channels:[10,11],modo:'medida',anteriores:true}}));
      vistaAplicar('Anterior');
    });
    assert.equal(await page.locator('#mostrar-anteriores').isChecked(),true);
    assert.deepEqual(await page.evaluate(()=>[...seleccion]),[11]);
    await page.locator('.medidas-buscar').fill('retirada');
    assert.equal(await page.locator('wa-tree-item').count(),1);
    assert.equal(await page.locator('wa-tree-item').getAttribute('data-canal'),'20');
    await page.locator('.medidas-buscar').fill('');
    await page.evaluate(()=>cargarCatalogo());
    assert.deepEqual(await page.evaluate(()=>[...seleccion]),[11]);
    await page.locator('.medidas-buscar').fill('Anterior');
    assert.equal(await page.locator('wa-tree-item').count(),1);
    await page.setViewportSize({width:320,height:850});
    await page.locator('#btn-medidas').click();
    await page.waitForTimeout(150);
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
    assert.equal(await page.evaluate(()=>{const p=document.getElementById('selector-medidas');return p.scrollWidth<=p.clientWidth;}),true);
    assert.equal(await page.locator('#mostrar-anteriores').isVisible(),true);
    const screenshot = process.env.MODULINKR_HISTORY_SCREENSHOT;
    if (screenshot) await page.screenshot({path:screenshot,fullPage:true});
    assert.deepEqual(errors,[]);
    console.log('Histórico: una entrada por medida, consulta continua, alias de vistas anteriores, medidas retiradas y anchura móvil correctos');
    await page.setViewportSize({width:1400,height:900});
    await page.goto(origin + '/tests/test_chart_dom.html');
    await page.waitForFunction(()=>/correctas|FAIL/.test(document.getElementById('results').textContent));
    const result = await page.locator('#results').textContent();
    assert(!result.startsWith('FAIL'), result);
    assert.deepEqual(errors,[]);
    console.log(result.split(':')[0]);
  } finally {
    if(browser) await browser.close();
    await new Promise(resolve => server.close(resolve));
  }
})().catch(error=>{console.error(error);process.exitCode=1;});
