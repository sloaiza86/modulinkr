const {test} = require('node:test');
const assert = require('node:assert/strict');
const {webcrypto, createHash} = require('node:crypto');
const {readFileSync} = require('node:fs');
const {join} = require('node:path');
globalThis.crypto = webcrypto;
const rak = require('../static/rak-onboarding.js');
const policy = JSON.parse(readFileSync(join(__dirname, '../../pi-service/rak_policy.json')));
const raw = number => `RUI_${number}_RAK3172-E`;

test('las versiones posteriores necesitan aprobación explícita y se conservan si la tienen', () => {
  assert.equal(rak.classify(raw('4.3.0'), policy), 'approved');
  assert.equal(rak.classify(raw('4.4.0'), policy), 'newer_unvalidated');
  assert.equal(rak.classify(raw('4.0.6'), policy), 'update_required');
  assert.equal(rak.classify(raw('3.4.11'), policy), 'unsupported');
  assert.equal(rak.classify('RUI_4.3.0_RAK3172-T', policy), 'wrong_model');
  assert.equal(rak.classify(raw('65536.0.0'), policy), 'unknown');
  assert.equal(rak.classify(raw('4.4.0'), {...policy, approved_versions: ['4.3.0', '4.4.0']}), 'approved');
  assert.equal(rak.canPrepare({version: raw('4.4.0'), recoverable: true}, policy), false);
  assert.equal(rak.canPrepare({version: '', recoverable: true}, policy), true);
});

function session(version, options = {}) {
  const data = new Uint8Array(2050).map((_, n) => n % 256);
  const p = structuredClone(policy);
  p.image.size = data.length;
  p.image.sha256 = createHash('sha256').update(data).digest('hex');
  const s = {lines: [], commands: [], bytes: [], version, transferred: 0, downloads: 0,
    async hello() { return {fw: 'ModuLinkr/nodo', device_uid: 'f8b3b71bdf58', rak_maintenance: 1,
      rak: {version: this.version, approved: rak.classify(this.version, p) === 'approved'}}; },
    status(phase, extra = {}) {this.lines.push('CFG:RAK ' + JSON.stringify({phase, version: this.version,
      approved: rak.classify(this.version, p) === 'approved', done: this.transferred, total: data.length, ...extra}));},
    async send(command) {
      this.commands.push(command);
      if (command === 'CFG.RAK.INFO') this.status('checked');
      if (command.startsWith('CFG.RAK.BEGIN ')) {this.status('checked'); this.status('receiving');}
      if (command.startsWith('CFG.RAK.DATA ')) this.lines.push('CFG:READY');
      if (command === 'CFG.RAK.INSTALL') {
        this.status('installing');
        this.version = options.wrongFinal ? raw('4.2.4') : raw('4.3.0'); this.status('complete');
      }
    },
    async waitLine(predicate) { while (this.lines.length) {const line = this.lines.shift(); if (predicate(line)) return line;} throw new Error('timeout');},
  };
  s.writer = {write: async block => {s.bytes.push(...block); s.transferred += block.length;
    s.status('receiving', options.wrongOffset ? {done: 1} : {});}};
  const load = async () => {s.downloads++; return options.badImage ? data.subarray(1) : data;};
  return {s, p, data, load};
}

test('un RAK aprobado no descarga ni escribe imagen', async () => {
  const {s, p, load} = session(raw('4.3.0'));
  await rak.prepareLocal(s, p, load);
  assert.equal(s.downloads, 0);
  assert.deepEqual(s.commands, ['CFG.RAK.INFO']);
});

test('un RAK posterior desconocido o de otro modelo no inicia transferencia', async () => {
  for (const version of [raw('4.4.0'), 'RUI_4.0.6_RAK3172-T', 'unknown']) {
    const {s, p, load} = session(version);
    await assert.rejects(rak.prepareLocal(s, p, load));
    assert.equal(s.downloads, 0);
    assert.deepEqual(s.commands, ['CFG.RAK.INFO']);
  }
});

test('solo una imagen completa y comprobada alcanza BEGIN', async () => {
  const {s, p, load} = session(raw('4.0.6'), {badImage: true});
  await assert.rejects(rak.prepareLocal(s, p, load), /SHA256/);
  assert.deepEqual(s.commands, ['CFG.RAK.INFO']);
});

test('la transferencia mantiene bytes, offsets y confirmación de versión', async () => {
  const {s, p, load, data} = session(raw('4.0.6'));
  const progress = [];
  const node = await rak.prepareLocal(s, p, load, value => progress.push(value));
  assert.equal(node.rak.approved, true);
  assert.deepEqual(s.bytes, [...data]);
  assert.deepEqual(s.commands.filter(c => c.startsWith('CFG.RAK.DATA ')),
    ['CFG.RAK.DATA 0 1024', 'CFG.RAK.DATA 1024 1024', 'CFG.RAK.DATA 2048 2']);
  assert.ok(progress.some(v => v.phase === 'installing'));
});

test('una confirmación de offset incorrecta impide INSTALL', async () => {
  const {s, p, load} = session(raw('4.0.6'), {wrongOffset: true});
  await assert.rejects(rak.prepareLocal(s, p, load), /posición incorrecta/);
  assert.ok(!s.commands.includes('CFG.RAK.INSTALL'));
});

test('la escritura no se declara completada sin la versión real esperada', async () => {
  const {s, p, load} = session(raw('4.0.6'), {wrongFinal: true});
  await assert.rejects(rak.prepareLocal(s, p, load), /no confirmó/);
});

test('el progreso identifica cada fase y la discrepancia de identidad bloquea', () => {
  assert.match(rak.progressText({phase:'receiving',done:50,total:100}), /Copiando.*50 %/);
  assert.match(rak.progressText({phase:'installing',done:50,total:100}), /Actualizando.*50 %/);
  assert.throws(() => rak.identity({device_uid:'ffffffffffff'}, 'f8b3b71bdf58'), /ha cambiado/);
});
