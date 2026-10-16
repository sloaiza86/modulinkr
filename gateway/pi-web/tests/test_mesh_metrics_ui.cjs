const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');const path=require('node:path');const vm=require('node:vm');
const source=fs.readFileSync(path.join(__dirname,'../static/app.js'),'utf8');
const html=fs.readFileSync(path.join(__dirname,'../static/index.html'),'utf8');
function section(a,b){const start=source.indexOf(a),end=source.indexOf(b,start);assert(start>=0&&end>start);return source.slice(start,end);}
test('USB tiene una sola fuente y la herramienta de archivo vive en el formulario',()=>{
 assert(!html.includes('id="cfg-usb"'));assert(!html.includes('value="gateway"'));
 assert(html.includes('<option value="local">USB</option>'));
 assert(html.includes('<option value="lora">Red Mesh</option>'));
 assert(html.includes('id="bc-duty-override"'));
 assert(!/id="bc-duty-override"[^>]*checked/.test(html));
});
test('las métricas del gateway conservan nombres y valores ausentes',()=>{
 const c=vm.createContext({});vm.runInContext(section('function gatewayMetricChannels(', 'function tarjetaGateway('),c);
 assert.equal(c.gatewayMetricChannels({}).length,4);
 assert.equal(c.gatewayMetricChannels({})[0].value,undefined);
 assert.equal(c.gatewayMetricChannels({gateway_metrics:{duty_pct:12.5,online_total:2}})[0].value,12.5);
});
test('el gráfico de estados permanece continuo con cambios separados por horas',()=>{
 const c=vm.createContext({colorDeCanal:()=> 'blue',etiquetaCanal:()=> 'Gateway'});
 vm.runInContext(section('function conexionGateway(', 'function actualizarLeyendasHistorico('),c);
 vm.runInContext(section('function lineasDeCanal(', 'function opcionesGrafico('),c);
 const line=c.lineasDeCanal({channel_id:-1,interpolation:'step',bucket_s:0,source_points:{gateway:[[1,2,0,0],[36000,3,0,0]]}})[0];
 assert.equal(line.step,'end');assert.equal(line.data.length,2);assert.equal(line.lineStyle.type,'solid');
});
test('el diagnóstico celular distingue señal válida y estado vencido',()=>{
 const c=vm.createContext({filaDet:(k,v)=>`${k}:${v};`,htmlSeguro:String,edadEnVivo:()=> '4 s',redAhora:1000});
 vm.runInContext(section('function chipsCelular(', 'function textoRuta('),c);
 vm.runInContext(section('function bloqueNbiot(', 'function bloqueSalud('),c);
 assert.match(c.bloqueNbiot({nbiot_flags:3,nbiot_csq:20,nbiot_ago_s:4,mqtt_ago_s:2}),/-73 dBm \(CSQ 20\)/);
 assert.match(c.bloqueNbiot({nbiot_flags:3,nbiot_csq:255,nbiot_ago_s:200}),/Registro informado por el módem:Sin informe reciente/);
 assert.match(c.bloqueNbiot({nbiot_flags:3,nbiot_csq:255,nbiot_ago_s:200}),/Última señal conocida:Sin dato/);
});

test('los iconos y el diagnóstico comparten evidencia celular con el informe vencido',()=>{
 const c=vm.createContext({filaDet:(k,v)=>`${k}:${v};`,htmlSeguro:String,edadEnVivo:()=> '3 min',redAhora:1000});
 vm.runInContext(section('function chipsCelular(', 'function textoRuta('),c);
 vm.runInContext(section('function bloqueNbiot(', 'function bloqueSalud('),c);
 const node={role:'supernode',nbiot_flags:3,nbiot_ago_s:193,mqtt_ago_s:3,mqtt_recent:true};
 for(const chip of c.chipsCelular(node)) assert(c.bloqueNbiot(node).includes(chip.txt.split(': ')[1]));
 assert.match(c.bloqueNbiot(node),/Registro informado por el módem:Sin informe reciente/);
 node.mqtt_recent=false;node.nbiot_state='unknown';node.mqtt_state='unknown';
 assert(!c.bloqueNbiot(node).includes('entrega confirmada'));
});
test('la tarjeta del gateway muestra duty cycle y total conservando el índice del histórico',()=>{
 const c=vm.createContext({estadoGateway:()=>({chips:[],caido:false}),nodoDisponible:()=>true,
  chipConexion:()=>'',iconoMdi:()=>'',htmlSeguro:String,fmtValor:String});
 vm.runInContext(section('function gatewayMetricChannels(', '// Estado general del nodo'),c);
 const result=c.tarjetaGateway({nodes:[],gateway_metrics:{duty_pct:2,online_lora:1,online_nbiot:2,online_total:2}});
 assert.equal((result.match(/<modulinkr-measurement/g)||[]).length,2);
 assert.match(result,/data-canal="3"/);assert.match(result,/Nodos en línea</);
 assert(!result.includes('Nodos en línea por'));
});
test('el estado de la configuración depende de respuestas reales y no del tiempo transcurrido',()=>{
 const c=vm.createContext({});vm.runInContext(section('function formLoraActividad(', '// Sigue el envío'),c);
 assert.match(c.formLoraActividad({transport:'nbiot',elapsed_s:418,last_response_ago_s:null}),/primera respuesta/);
 assert.match(c.formLoraActividad({transport:'nbiot',last_response_ago_s:3}),/El nodo responde/);
 assert.match(c.formLoraActividad({transport:'nbiot',last_response_ago_s:90}),/Sin respuesta reciente/);
});
test('las leyendas de recepción se reservan a sensores y las de conexión a los recuentos',()=>{
 const elements=Object.fromEntries(['modal-vias','modal-conexiones'].map(id=>[id,{hidden:true,innerHTML:''}]));
 const c=vm.createContext({document:{getElementById:id=>elements[id]}});
 vm.runInContext(section('function conexionGateway(', 'function lineasDeCanal('),c);
 c.actualizarLeyendasHistorico('modal',[{channel_id:-1}]);
 assert(elements['modal-vias'].hidden && elements['modal-conexiones'].hidden);
 c.actualizarLeyendasHistorico('modal',[-4,-2,-3].map(channel_id=>({channel_id})));
 assert(elements['modal-vias'].hidden);assert(!elements['modal-conexiones'].hidden);
 assert.match(elements['modal-conexiones'].innerHTML,/Método de conexión/);
 for(const name of ['Total','LoRa','NB-IoT'])assert(elements['modal-conexiones'].innerHTML.includes(name));
 c.actualizarLeyendasHistorico('modal',[{channel_id:1}]);
 assert(!elements['modal-vias'].hidden && elements['modal-conexiones'].hidden);
});
