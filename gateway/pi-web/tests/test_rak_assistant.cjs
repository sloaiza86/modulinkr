const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../static/app.js'), 'utf8');
const policy = JSON.parse(fs.readFileSync(path.join(__dirname, '../../pi-service/rak_policy.json')));
const rak = require('../static/rak-onboarding.js');
const section = (start, end) => source.slice(source.indexOf(start), source.indexOf(end, source.indexOf(start)));
function context(latest='0.0.85+abcdef123456') {
  const status = {}, destinations = [];
  const c = vm.createContext({document:{getElementById:()=>status}, RakOnboarding:rak,
    formDestino:value=>destinations.push(value), formFuenteLocal:()=>true,
    fetchApi:async()=>({json:async()=>({version:latest})}),
    rakPeticion:async()=>policy});
  vm.runInContext(section('function fwNumero(', 'function fwMensaje('), c);
  vm.runInContext(section('function cmpVersionFw(', 'async function formBuscar('), c);
  return {c,status,destinations};
}
const node = (version, radio, feature=1) => ({fw:'ModuLinkr/nodo',version,schemas:'3.3',
  rak_maintenance:feature,rak:{version:radio,approved:radio==='RUI_4.3.0_RAK3172-E',recoverable:false}});

test('el asistente bloquea un RAK posterior desconocido sin ofrecer downgrade', async()=>{
  const {c,status,destinations}=context();
  await c.formCheckFw(node('0.0.85', 'RUI_4.4.0_RAK3172-E'));
  assert.equal(destinations.at(-1),false);
  assert.match(status.textContent,/posterior.*no validada/);
  assert.match(status.textContent,/no se realizará un downgrade/);
});

test('el alta local instala el anfitrión con mantenimiento antes de preparar el módulo', async()=>{
  const {c,status,destinations}=context();
  await c.formCheckFw(node('0.0.84','',0));
  assert.equal(c.formMode,'flash');
  assert.equal(destinations.at(-1),true);
  assert.match(status.textContent,/se instalará ModuLinkr.*se comprobará la radio RAK/);
});

test('un anfitrión posterior se conserva si admite mantenimiento y el RAK está aprobado', async()=>{
  const {c,status,destinations}=context();
  await c.formCheckFw(node('0.0.86','RUI_4.3.0_RAK3172-E'));
  assert.equal(c.formMode,'config');
  assert.equal(destinations.at(-1),true);
  assert.match(status.textContent,/posterior al disponible/);
});

test('sin imagen con mantenimiento no se habilita un alta inicial', async()=>{
  for(const latest of [null,'0.0.84']) {
    const {c,status,destinations}=context(latest);
    await c.formCheckFw({});
    assert.equal(destinations.at(-1),false);
    assert.match(status.textContent,/Actualiza la distribución del gateway/);
  }
});

test('el asistente distingue actualización admitida, recuperación y versión desconocida', async()=>{
  for(const [radio,recoverable,allowed] of [['RUI_4.0.6_RAK3172-E',false,true],['',true,true],['',false,false],['RUI_4.4.0_RAK3172-E',true,false]]) {
    const {c,destinations}=context();
    const identified=node('0.0.85',radio);identified.rak.recoverable=recoverable;
    await c.formCheckFw(identified);
    assert.equal(destinations.at(-1),allowed);
  }
});

test('cambiar el nodo después de identificarlo bloquea la instalación local antes de abrir el cargador', async()=>{
  const c=vm.createContext({formUsbIdentity:{version:'0.0.84',device_uid:'f8b3b71bdf58'},formFirmwareAvailable:'0.0.85',
    cfgLocalSes:{port:{},hello:async()=>({device_uid:'ffffffffffff'}),close:async()=>{throw new Error('No debe cerrarse');}},
    window:{ESPLoader:class{},Transport:class{}},fwFlashParts:()=>[],Uint8Array,
    fetchApi:async()=>({ok:true,headers:{get:()=> '0.0.85'},arrayBuffer:async()=>new ArrayBuffer(8)})});
  vm.runInContext(section('function fwNumero(', 'function fwMensaje('),c);
  vm.runInContext(section('async function formFlashLocal()', 'async function formEnviar()'),c);
  await assert.rejects(c.formFlashLocal(),/ha cambiado/);
});
