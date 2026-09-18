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
  c.htmlSeguro = String;
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
test('sin operación activa no se muestran horas ni historial',()=>{
  const {c,e}=setup();
  c.bcPintar({activa:false,can_start:false,nodos:[{node_id:1,installed_version:current,available_version:current,comparison:'current',online:true,direct:true}]});
  assert.equal(e['bc-estado'].innerHTML,'');
  assert.equal(e['bc-lanzar'].hidden,true);
  assert.doesNotMatch(e.table.innerHTML,/<button/);
  assert.match(e.table.innerHTML,/Actualizado/);
});
test('recibir el firmware no se presenta como instalación confirmada',()=>{
  const {c,e}=setup();
  c.bcPintar({activa:false,nodos:[{node_id:1,comparison:'unknown',online:true,direct:true,received:true,can_install:true}]});
  assert.match(e.table.innerHTML,/Listo para instalar/);
  assert.match(e.table.innerHTML,/class="bc-instalar"/);
  assert.doesNotMatch(e.table.innerHTML,/Instalación confirmada/);
});

test('no muestra hashes ni animación cuando falla la lectura',()=>{
  const {c,e,document}=setup();
  assert.equal(c.fwVersionTexto(current),'0.0.58');
  const el=document.getElementById('warning');
  c.fwMensaje(el,'No se pudo leer la versión instalada.','advertencia');
  assert.match(el.className,/advertencia/);
  assert.doesNotMatch(el.className,/exito|progreso/);
});
test('solo una versión posterior habilita actualizar',()=>{
  const {c}=setup(), btn={};
  c.fwAccion(btn,'0.0.58-difusion-red','0.0.59+abcdef123456',true);
  assert.equal(btn.hidden,false); assert.equal(btn.disabled,false);
  assert.equal(btn.textContent,'Actualizar a 0.0.59');
  c.fwAccion(btn,'0.0.60','0.0.59+abcdef123456',true);
  assert.equal(btn.hidden,true);
});
