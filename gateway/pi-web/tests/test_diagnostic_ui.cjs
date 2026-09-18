const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../static/app.js'), 'utf8');

function setup() {
  const elements = {};
  function element() {
    return {value:'', dataset:{}, children:[], options:[], scrollTop:0, clientHeight:100, scrollHeight:100,
      textContent:'', add(option) {this.options.push(option);},
      appendChild(child) {this.children.push(child);},
      replaceChildren(...children) {
        this.children = children.flatMap(child => child.fragment ? child.children : [child]);
        this.options = this.children;
      }};
  }
  const document = {getElementById(id) {return elements[id] ||= element();},
    createElement: element, createDocumentFragment() {return {...element(), fragment:true};}};
  const c = vm.createContext({document, Option:function(text,value) {this.text=text; this.value=value;}});
  const start = source.indexOf('function debugRecord(');
  const end = source.indexOf('document.getElementById("dbg-nivel").addEventListener', start);
  vm.runInContext('let dbgLines = [];\n' + source.slice(start,end), c);
  return {c, document};
}

test('USB y journal conservan tiempo, componente y atributos originales', () => {
  const {c} = setup();
  for (const time of ['up=0000000123.456s', '2026-09-07T17:00:00Z']) {
    const line = `${time} WARNING  node.modbus              event=modbus.failed slave=0x01 reason=timeout`;
    const record = c.debugRecord(line);
    assert.equal(record.line,line);
    assert.equal(record.level,'WARNING');
    assert.equal(record.component,'node.modbus');
  }
  assert.equal(c.debugRecord('2026-09-07T17:00:00Z INFO     modulinkr.gateway        event=beacon.sent seq=2').component,'modulinkr.gateway');
});

test('una línea antigua no recibe una severidad inventada', () => {
  const {c} = setup();
  const record = c.debugRecord('[rx] #2 len=5 rssi=-24.0 snr=12.0 hex=0123456789');
  assert.equal(record.level,null);
  assert.equal(c.debugVisible(record,'','',''),true);
  assert.equal(c.debugVisible(record,'ERROR','',''),false);
});

test('nivel, componente y búsqueda se combinan sin borrar registros', () => {
  const {c,document:d} = setup();
  c.debugAppend('up=0000000123.456s DEBUG    node.mesh                event=mesh.beacon source=255');
  c.debugAppend('up=0000000123.456s ERROR    node.modbus              event=modbus.failed slave=0x01');
  d.getElementById('dbg-nivel').value='WARNING';
  d.getElementById('dbg-componente').value='node.modbus';
  d.getElementById('dbg-filtro').value='SLAVE=0x01';
  c.debugRender();
  assert.equal(d.getElementById('dbg-consola').children.length,1);
  assert.match(d.getElementById('dbg-contador').textContent,/1 de 2/);
  d.getElementById('dbg-nivel').value='';
  d.getElementById('dbg-componente').value='';
  d.getElementById('dbg-filtro').value='';
  c.debugRender();
  assert.equal(d.getElementById('dbg-consola').children.length,2);
});

test('el contenido externo se muestra como texto y no como HTML', () => {
  const {c,document:d} = setup();
  c.debugAppend('up=0000000123.456s ERROR node.config event=config.invalid name=<script>alert(1)</script>');
  const row = d.getElementById('dbg-consola').children[0];
  assert.match(row.textContent,/<script>alert\(1\)<\/script>/);
  assert.equal(row.innerHTML,undefined);
  assert.equal(row.dataset.level,'ERROR');
});

test('el límite retiene los 800 últimos registros y limpiar elimina sus componentes', () => {
  const {c,document:d} = setup();
  for (let i=0;i<805;i++) c.debugAppend(`up=0000000123.456s INFO node.sensor event=sensor.sample seq=${i}`);
  const rows=d.getElementById('dbg-consola').children;
  assert.equal(rows.length,800);
  assert.match(rows[0].textContent,/seq=5\n$/);
  assert.match(rows[799].textContent,/seq=804\n$/);
  c.debugClear();
  assert.equal(d.getElementById('dbg-consola').textContent,'');
  assert.equal(d.getElementById('dbg-componente').options.length,1);
});
