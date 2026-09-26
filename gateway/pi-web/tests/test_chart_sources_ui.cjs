// Verifica trazos por vía sin unir lecturas ausentes ni duplicar medidas.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(require('node:path').join(__dirname,'../static/app.js'),'utf8');
const c=vm.createContext({colorDeCanal:()=> 'blue', etiquetaCanal:id=>'channel '+id,
  graficoEsCompacto:()=>false, seriesOcultas:new Set(), COLOR:{}, EJE_X:{}, EJE_Y:{axisLabel:{}},
  FUENTE_GRAFICO:'system-ui',unidad:x=>x,tooltipGrafico:()=>''});
vm.runInContext(source.slice(source.indexOf('function lineasDeCanal('),source.indexOf('let solicitudGrafico')),c);
const series={channel_id:1,unit:'C',bucket_s:10,points:[],source_points:{
  lora:[[10,20,1,0],[20,21,1,0],[30,null,0,0],[40,null,0,0],[100,25,1,0]],
  nbiot:[[10,null,0,0],[20,null,0,0],[30,22,0,1],[40,23,0,1],[100,null,0,0]]}};
test('las dos vías conservan color y control de visibilidad de una medida',()=>{
  const options=c.opcionesGrafico([series]);
  assert.equal(options.series.length,2);
  const [lo,nb]=options.series;
  assert.equal(lo.name,nb.name);
  assert.equal(lo.lineStyle.color,nb.lineStyle.color);
  assert.equal(lo.lineStyle.type,'solid');
  assert.equal(nb.lineStyle.type,'dotted');
  assert.equal(Object.keys(options.legend.selected).length,1);
  c.seriesOcultas.add('1');
  assert.equal(c.opcionesGrafico([series]).legend.selected['channel 1'],false);
  c.seriesOcultas.clear();
});
test('los huecos y cambios de vía no se conectan y el punto aislado es visible',()=>{
  const lo=c.lineasDeCanal(series)[0];
  assert.equal(lo.connectNulls,false);
  assert.equal(lo.data[2][1],null);
  assert.equal(lo.data.at(-2)[1],null);
  assert.equal(lo.symbolSize(null,{dataIndex:lo.data.length-1}),5);
  assert.equal(lo.symbolSize(null,{dataIndex:0}),0);
});
test('una vía sin valores no genera una serie vacía',()=>{
  const lines=c.lineasDeCanal({...series,source_points:{lora:[[10,20,1,0]],nbiot:[[10,null,0,0]]}});
  assert.equal(lines.length,1);
  assert.equal(lines[0].symbolSize(null,{dataIndex:0}),5);
});
test('tres unidades mantienen ejes y zoom enlazados para ambas vías',()=>{
  const data=['C','%RH','g'].map((unit,i)=>({...series,channel_id:i+1,unit}));
  const options=c.opcionesGrafico(data);
  assert.equal(options.series.length,6);
  assert.equal(options.yAxis.length,3);
  assert.equal(options.series[4].yAxisIndex,2);
  assert.equal(options.series[5].xAxisIndex,2);
});

test('el intervalo SQL menor que el muestreo no convierte la línea en puntos aislados',()=>{
  const [line]=c.lineasDeCanal({...series,bucket_s:1,source_points:{lora:[[10,20,1,0],[20,21,1,0],[30,22,1,0]]}});
  assert.equal(line.data.length,3);
  assert.ok(line.data.every(p=>p[1]!=null));
});

vm.runInContext(source.slice(source.indexOf('function opcionesModal('),source.indexOf('async function cargarModalGrafica(')),c);
Object.assign(c,{fmtDia:()=> '23 sept',fmtHora:()=> '18:00',fmtValor:String,fmtEje:String,
  htmlSeguro:String,viaMuestras:value=>value[2]?'LoRa':'NB-IoT'});
test('el modal comparte trazos y cortes del histórico con el color de su medida',()=>{
  const options=c.opcionesModal(series,'C','purple');
  assert.equal(options.series.length,2);
  assert.equal(options.series[0].lineStyle.type,'solid');
  assert.equal(options.series[1].lineStyle.type,'dotted');
  assert.ok(options.series.every(s=>s.lineStyle.color==='purple'&&!s.connectNulls));
});
test('el tooltip del modal identifica ambas vías y omite valores ausentes',()=>{
  const tooltip=c.opcionesModal(series,'C').tooltip.formatter;
  const result=tooltip([{value:[1000,20,2,0]},{value:[1000,23,0,1]},{value:[1000,null,0,0]}]);
  assert.match(result,/20 C/);assert.match(result,/23 C/);
  assert.match(result,/LoRa/);assert.match(result,/NB-IoT/);
  assert.equal(tooltip([{value:[1000,null,0,0]}]),'');
});
