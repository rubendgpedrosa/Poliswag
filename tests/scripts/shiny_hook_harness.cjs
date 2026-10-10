'use strict';
// Runs scripts/shiny_linker/native-method-redirect.js against a mocked
// native sender. Usage: node shiny_hook_harness.cjs <path-to-hook.js>
const fs = require('fs');

const config = {
  nonce: [1, 2, 3], apiFieldCount: 36, sendIndex: 28, logPath: '/log',
  methods: [5012, 159, 160, 815, 1504], socialActions: [10000],
  lifetimeMs: 60000, detachDelayMs: 10,
};
const source = fs.readFileSync(process.argv[2], 'utf8')
  .replace('__OWNED_REDIRECT_CONFIG__', JSON.stringify(config));

const log = [];
let hook;
global.File = function () { this.write = (line) => log.push(line.trim()); this.close = () => {}; };
global.Process = {
  arch: 'arm64', pointerSize: 8,
  getModuleByName: () => ({ getExportByName: (name) => name }),
  findRangeByAddress: () => ({ protection: 'r-x' }),
};
global.NativeFunction = function (name) {
  if (name !== 'malloc') return () => {};
  return () => ({ isNull: () => false, writeInt(v) { this.int = v; }, writeByteArray(b) { this.bytes = Array.from(b); } });
};
global.Memory = { alloc: () => ({ add: () => ({ readPointer: () => 'sender' }) }) };
global.Interceptor = { attach: (_, handlers) => { hook = handlers; return { detach() { log.push('UNPATCHED'); } }; }, flush() {} };
eval(source);

const varint = (n) => { const out = []; while (n >= 128) { out.push((n & 127) | 128); n = Math.floor(n / 128); } out.push(n); return out; };
const blob = (field, bytes) => [...varint(field * 8 + 2), ...varint(bytes.length), ...bytes];
const integer = (field, value) => [...varint(field * 8), ...varint(value)];
const marked = (method, body, nonce = [1, 2, 3]) => [...blob(4095, nonce), ...blob(4094, body), ...integer(4093, method)];

function call(outerMethod, envelope) {
  const data = Uint8Array.from(envelope);
  const args = [0, 0, 0, { toInt32: () => 1 }, { readInt: () => outerMethod }, { readByteArray: () => data.buffer }, { readInt: () => data.length }];
  hook.onEnter(args);
  // An untouched request still has the caller's own arguments: report null.
  return { method: args[4].int ?? null, length: args[6].int ?? null, bytes: args[5].bytes ?? null };
}

const social = (action) => [...integer(1, action), ...blob(2, []), ...blob(3, [1])];
const result = {
  lobbyJoin: call(2, marked(159, [9, 9])),
  invite: call(2, marked(1504, [7])).method,
  search: call(2, marked(5012, social(10000))).method,
  battleStart: call(2, marked(165, [1])).method,
  friendRemoval: call(2, marked(5012, social(10009))).method,
  wrongNonce: call(2, marked(159, [9], [1, 2, 4])).method,
  notGetPlayer: call(106, marked(159, [9])).method,
  plainGetPlayer: call(2, []).method,
};
call(2, marked(0, []));
// The hook's lifetime timer would keep Node alive, so exit explicitly.
setTimeout(() => { console.log(JSON.stringify({ ...result, log })); process.exit(0); }, 60);
