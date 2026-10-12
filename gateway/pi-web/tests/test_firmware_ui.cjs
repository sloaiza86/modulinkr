const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../static/app.js'), 'utf8');
function section(a,b) { const start=source.indexOf(a); const end=source.indexOf(b,start); assert.ok(start>=0 && end>start); return source.slice(start,end); }
function setup() {
  const elements = {};
  const document = {getElementById(id) {return elements[id] ||= {dataset:{},hidden:false,disabled:false,textContent:'',className:'',innerHTML:'',querySelectorAll:()=>[],setAttribute(){},querySelector(){return this;}};},querySelector:()=>document.getElementById('table')};
  const c=vm.createContext({document,console});
  vm.runInContext(section('function fwNumero(', 'let fwPuerto = null;') + section('let bcSeleccion =', 'async function bcInstalar('), c);
  vm.runInContext(section('function htmlSeguro(', '\nfunction '), c);
  c.migDato = (a,b) => a + ':' + b;
  c.migDuracion = n => n+'s';
  c.BC_FASE = {sending:'enviando'};
  return {c,e:elements,document};
}
const current='0.0.58-difusion-red+010203040506';
test('la versión visible determina si hay una actualización',()=>{
  const {c}=setup();
  assert.equal(c.fwComparar(current,current),'current');
  assert.equal(c.fwComparar(current,current.slice(0,-1)+'7'),'current');
  assert.equal(c.fwComparar('0.0.58-difusion-red',current),'current');
  assert.equal(c.fwComparar('0.0.59+010203040506',current),'older');
});
test('el botón desaparece si el firmware ya está instalado',()=>{
  const {c}=setup(), btn={};
  c.fwAccion(btn,current,current,true); assert.equal(btn.hidden,true);
  c.fwAccion(btn,null,current,false); assert.equal(btn.hidden,true); assert.equal(btn.disabled,true);
  c.fwAccion(btn,null,current,true); assert.equal(btn.disabled,true);
});
test('la ficha final elimina la animación de consulta',()=>{
  const {c,e,document}=setup();
  document.getElementById('fw-bin-info').className='mensaje mensaje-progreso';
  c.fwFicha(current,current);
  assert.doesNotMatch(e['fw-bin-info'].className,/progreso/);
  assert.match(e['fw-bin-info'].textContent,/Actualizado/);
});
function row(extra={}) {
  return {node_id:2,job_id:4,name:'Supernodo',online:true,installed_version:'0.0.75',available_version:'0.0.76',
    summary:'Listo para instalar',primary_action:'install',can_install:true,can_send:false,
    operation:{id:4,version:'0.0.76',state:'ready',received:true,missing:0,verified:true,
      installation:'No solicitada',detail:'Imagen recibida'},history:[],...extra};
}
function render(n) { const env=setup(); env.c.bcPintar({nodos:[n],can_start:!!n.can_send}); return env; }

test('disponible, operación y ejecución mantienen versiones independientes',()=>{
  const {e}=render(row({available_version:'0.0.77'}));
  assert.match(e.table.innerHTML,/0.0.75/);assert.match(e.table.innerHTML,/0.0.77/);assert.match(e.table.innerHTML,/Versión 0.0.76/);
  assert.match(e.table.innerHTML,/>Instalar<\/button>/);
  assert.doesNotMatch(e.table.innerHTML,/>Instalar 0/);
});
test('recepción verificada no se presenta como instalación confirmada',()=>{
  const {e}=render(row());
  assert.match(e.table.innerHTML,/Imagen verificada/);assert.match(e.table.innerHTML,/No solicitada/);
  assert.doesNotMatch(e.table.innerHTML,/Instalación confirmada/);
});
test('la consulta incierta ofrece comprobar sin instalar',()=>{
  const {e}=render(row({summary:'Recepción sin confirmar: falta respuesta del nodo',primary_action:'check',can_check:true,can_install:false}));
  assert.match(e.table.innerHTML,/>Comprobar recepción<\/button>/);
  assert.doesNotMatch(e.table.innerHTML,/data-action="install"/);
});
test('la instalación automática queda visible y permite desactivarla',()=>{
  const n=row({primary_action:null,can_install:false,can_disable_auto:true});n.operation.auto_install=true;
  const {e}=render(n);
  assert.match(e.table.innerHTML,/Instalación automática activada/);
  assert.match(e.table.innerHTML,/>Desactivar instalación automática<\/button>/);
});
test('la desconexión conserva el resultado de la operación',()=>{
  const {e}=render(row({online:false,can_install:false,primary_action:null,summary:'Instalación confirmada',blocked_reason:'Sin conexión; se conserva el último informe'}));
  assert.match(e.table.innerHTML,/Sin conexión/);assert.match(e.table.innerHTML,/Instalación confirmada/);
});
test('detalle e historial escapan contenido externo',()=>{
  const n=row({history:[{bcast_id:1,version:'0.0.75',source:'gateway',kind:'failed',detail:'Error <script>',ts:1}]});n.operation.detail='<fallo>';
  const {e}=render(n);assert.match(e.table.innerHTML,/&lt;fallo&gt;/);assert.match(e.table.innerHTML,/&lt;script&gt;/);
  assert.doesNotMatch(e.table.innerHTML,/<script>|<fallo>/);
});
test('la espera antes del regreso se explica y se anuncia el estado',()=>{
  const {c,e}=setup();c.bcPintar({waiting_for_restart:[1],nodos:[row({summary:'En ejecución; pendiente de confirmación de arranque'})]});
  assert.match(e['bc-estado'].textContent,/Esperando el regreso de: nodo 1/);
  assert.match(e['bc-progreso-accesible'].textContent,/pendiente de confirmación/);
});
test('el progreso distingue emisión de recepción',()=>{
  const n=row({summary:'Enviando: 100 % emitido; recepción pendiente',primary_action:null,can_install:false});n.operation.state='sending';n.operation.received=false;n.operation.verified=false;n.operation.missing=null;
  const {e}=render(n);assert.match(e.table.innerHTML,/100 % emitido; recepción pendiente/);
  assert.doesNotMatch(e.table.innerHTML,/data-action="install"/);
});
test('la reanudación se ofrece para la misma imagen disponible',()=>{
  const {e}=render(row({primary_action:'send',can_install:false,can_send:true}));
  assert.match(e.table.innerHTML,/>Reanudar envío<\/button>/);
  const other=render(row({available_version:'0.0.77',primary_action:'send',can_install:false,can_send:true}));
  assert.match(other.e.table.innerHTML,/>Enviar<\/button>/);
});
test('cada intento y el origen del diagnóstico se identifican',()=>{
  const {e}=render(row({history:[{bcast_id:4,version:'0.0.76',source:'node',kind:'report',phase:2,cause:15,ts:1}]}));
  assert.match(e.table.innerHTML,/Operación 4 · Nodo/);assert.match(e.table.innerHTML,/Verificación: Imagen verificada/);
});
test('el checkbox automático no está marcado por defecto',()=>{
  const html=fs.readFileSync(path.join(__dirname,'../static/index.html'),'utf8');
  const input=html.match(/<input[^>]*id="bc-auto-install"[^>]*>/)[0];assert.doesNotMatch(input,/checked/);
  assert.match(input,/aria-describedby/);
});
test('comprobar no invoca el endpoint de instalación',async()=>{
  const {c}=render(row({can_check:true,primary_action:'check',can_install:false}));
  const calls=[];c.fetchApi=async(url,options)=>{calls.push({url,body:JSON.parse(options.body)});return {ok:true,json:async()=>({ok:true})};};
  c.bcRefrescar=async()=>{};await c.bcAccion(2,'check');
  assert.equal(calls.length,1);assert.equal(calls[0].url,'/api/config/lora/firmware/comprobar');
  assert.equal(calls[0].body.id,4);
});
