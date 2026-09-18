const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../static/app.js'), 'utf8');
function section(a,b) { const start=source.indexOf(a), end=source.indexOf(b,start); assert.ok(start>=0 && end>start); return source.slice(start,end); }
function deferred() { let resolve; const promise=new Promise(r=>resolve=r); return {promise,resolve}; }
function setup() {
  const elements={};
  const document={getElementById(id) {return elements[id] ||= {hidden:false, disabled:false, value:id==='fw-fuente'?'local':'',textContent:'',className:'',setAttribute(){},addEventListener(){},querySelector(){return this;}};}};
  const events=[], dialogs=[];
  const ports=[{name:'A',identity:{name:'Ambiental',node_id:1,version:'0.0.59'}},{name:'B',identity:{name:'Acelerómetro',node_id:2,version:'0.0.59'}}];
  const c=vm.createContext({document,console,Uint8Array,TextEncoder,TextDecoder,setTimeout:r=>{r();return 1;},
    htmlSeguro:String,textoError:e=>e.message, cfgDialogo:(...a)=>dialogs.push(a),navigator:{serial:{requestPort:async()=>ports[0]}},
    fetchApi:async()=>({ok:true, headers:{get:()=> '0.0.60+abcdef123456'}, arrayBuffer:async()=>new Uint8Array([1,2]).buffer})});
  c.LocalCfg=class {constructor(port){this.port=port;} async open(){events.push('open '+this.port.name); if(this.port.failOpen)throw new Error("Failed to execute 'open' on 'SerialPort': Failed to open serial port.");} async hello(){if(this.port.failRead)throw new Error('sin respuesta'); return this.port.identity;} async close(){events.push('close '+this.port.name); if(this.port.closing)await this.port.closing.promise;}};
  c.window={Transport:class {constructor(port){this.port=port;} async disconnect(){events.push('disconnect '+this.port.name);}},ESPLoader:class {constructor({transport}){this.port=transport.port;} async main(){} async writeFlash(){events.push('write '+this.port.name); this.port.identity={...this.port.identity,version:'0.0.60+abcdef123456'}; if(this.port.failAfter)this.port.failRead=true;} async hardReset(){events.push('reset '+this.port.name);}}};
  vm.runInContext(section('function fwNumero(', '// ----- Configurar nodo: formulario'),c);
  vm.runInContext('fwDisponible="0.0.60+abcdef123456"',c);
  return {c,e:elements,document,events,dialogs,ports};
}
test('la selección no se publica ni se abre otra operación antes del cierre USB',async()=>{
  const {c,ports,events,document}=setup(); ports[0].closing=deferred();
  const first=c.fwLocalBuscar(); await new Promise(setImmediate);
  assert.equal(document.getElementById('fw-local-buscar').disabled,true);
  c.navigator.serial.requestPort=async()=>ports[1];
  await c.fwLocalBuscar(); assert.equal(events.includes('open B'),false);
  assert.equal(document.getElementById('fw-ficha').hidden,true);
  ports[0].closing.resolve(); await first;
  assert.equal(document.getElementById('fw-nombre').textContent,'Ambiental');
  await c.fwLocalBuscar();
  assert.equal(document.getElementById('fw-nombre').textContent,'Acelerómetro');
  assert.deepEqual(events,['open A','close A','open B','close B']);
});
test('un fallo al cambiar de nodo elimina la ficha anterior y traduce el error',async()=>{
  const {c,ports,document}=setup(); await c.fwLocalBuscar();
  ports[1].failOpen=true; c.navigator.serial.requestPort=async()=>ports[1]; await c.fwLocalBuscar();
  assert.equal(document.getElementById('fw-ficha').hidden,true);
  assert.equal(document.getElementById('fw-local-flash').hidden,true);
  assert.match(document.getElementById('fw-local-aviso').textContent,/No se pudo abrir el USB/);
  assert.doesNotMatch(document.getElementById('fw-local-aviso').textContent,/Failed/);
});
test('el éxito exige comprobar el mismo nodo tras escribir y cerrar el cargador',async()=>{
  const {c,document,dialogs,events}=setup(); await c.fwLocalBuscar(); await c.fwLocalFlash();
  assert.match(document.getElementById('fw-resultado').textContent,/actualización completada.*0.0.60 confirmada/);
  assert.match(document.getElementById('fw-resultado').className,/exito/);
  assert.equal(dialogs.length,1); assert.equal(dialogs[0][2].cerrar,true);
  assert.equal(document.getElementById('fw-local-flash').hidden,true);
  assert.deepEqual(events.slice(-4),['reset A','disconnect A','open A','close A']);
  c.fwFuenteCtrls(); assert.match(document.getElementById('fw-resultado').textContent,/completada/);
});
test('escribir sin poder confirmar el arranque termina con advertencia persistente',async()=>{
  const {c,ports,document,dialogs}=setup(); await c.fwLocalBuscar(); ports[0].failAfter=true; await c.fwLocalFlash();
  assert.match(document.getElementById('fw-resultado').className,/advertencia/);
  assert.match(document.getElementById('fw-resultado').textContent,/no se pudo confirmar/);
  assert.doesNotMatch(document.getElementById('fw-resultado').textContent,/actualización completada/);
  assert.equal(dialogs.length,1);
});
test('un nodo distinto en el mismo puerto no recibe el firmware',async()=>{
  const {c,ports,document,events}=setup(); await c.fwLocalBuscar();
  ports[0].identity={node_id:2,version:'0.0.59',name:'Otro'}; await c.fwLocalFlash();
  assert.equal(events.some(e=>e.startsWith('write')),false);
  assert.match(document.getElementById('fw-resultado').textContent,/nodo conectado ha cambiado/);
});
test('una sesión que no pudo abrir el puerto no cierra el puerto de otra operación',async()=>{
  const c=vm.createContext({});
  vm.runInContext(section('class LocalCfg {','async function cfgLocalAsegurar'),c);
  let closes=0;
  c.port={open:async()=>{throw new Error('ocupado');},close:async()=>{closes++;}};
  await vm.runInContext('(async()=>{const s=new LocalCfg(port);try{await s.open();}catch(e){}finally{await s.close();}})()',c);
  assert.equal(closes,0);
});
