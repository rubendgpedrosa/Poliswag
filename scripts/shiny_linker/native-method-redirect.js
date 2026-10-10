'use strict';

// Filled by raid-lobby-id.py. Routes only nonce-marked diagnostic GET_PLAYER
// requests to one of CONFIG.methods; ordinary scanner RPCs are unchanged.
const CONFIG = __OWNED_REDIRECT_CONFIG__;
let listener = null;

// Progress goes to a file so it can be read without the injector's stdout.
function log(line) {
  try {
    const file = new File(CONFIG.logPath, 'a');
    file.write(line + '\n');
    file.close();
  } catch (error) { /* logging must never disturb the game */ }
}

function decode(data) {
  const values = new Map();
  let offset = 0;
  function uint() {
    let value = 0;
    let shift = 0;
    while (offset < data.length && shift < 35) {
      const b = data[offset++];
      value += (b & 127) * Math.pow(2, shift);
      if (!(b & 128)) return value;
      shift += 7;
    }
    throw new Error('Invalid diagnostic protobuf');
  }
  while (offset < data.length) {
    const tag = uint();
    const field = Math.floor(tag / 8);
    const wire = tag % 8;
    if (!field || values.has(field)) throw new Error('Ambiguous diagnostic protobuf');
    if (wire === 0) values.set(field, uint());
    else if (wire === 2) {
      const length = uint();
      if (offset + length > data.length) throw new Error('Truncated diagnostic protobuf');
      values.set(field, data.slice(offset, offset + length));
      offset += length;
    } else throw new Error('Unexpected diagnostic protobuf wire type');
  }
  return values;
}

function equal(a, b) {
  return a && a.length === b.length && a.every((value, index) => value === b[index]);
}

function detach(reason) {
  if (!listener) return;
  listener.detach();
  listener = null;
  Interceptor.flush();
  log('OWNED_REDIRECT_DETACHED ' + reason);
}

function start() {
  if (Process.arch !== 'arm64' || Process.pointerSize !== 8) throw new Error('Inspected ARM64 client required');
  const plugin = Process.getModuleByName('libNianticLabsPlugin.so');
  const getApi = new NativeFunction(plugin.getExportByName('GetN2Api'), 'void', ['pointer']);
  // Replacement buffers come from the game's allocator and are never freed,
  // so they stay valid after this script unloads.
  const malloc = new NativeFunction(Process.getModuleByName('libc.so').getExportByName('malloc'), 'pointer', ['ulong']);
  const api = Memory.alloc(CONFIG.apiFieldCount * Process.pointerSize);
  getApi(api);
  const sender = api.add(CONFIG.sendIndex * Process.pointerSize).readPointer();
  const range = Process.findRangeByAddress(sender);
  if (!range || !range.protection.includes('x')) throw new Error('Native request sender is not executable');
  listener = Interceptor.attach(sender, {
    onEnter(args) {
      try {
        if (args[3].toInt32() !== 1 || args[4].readInt() !== 2) return;
        const length = args[6].readInt();
        if (length < CONFIG.nonce.length || length > 16384) return;
        const envelope = decode(new Uint8Array(args[5].readByteArray(length)));
        if (!equal(envelope.get(4095), CONFIG.nonce)) return;
        const method = envelope.get(4093);
        if (method === 0) {
          // Unpatch only after this request has left the sender.
          setTimeout(() => detach('requested'), CONFIG.detachDelayMs);
          return;
        }
        if (!CONFIG.methods.includes(method)) throw new Error('Method is outside diagnostic scope');
        const body = envelope.get(4094) || new Uint8Array(0);
        if (method === 5012) {
          const action = decode(body).get(1);
          if (!CONFIG.socialActions.includes(action)) throw new Error('Social action is outside diagnostic scope');
        }
        const methods = malloc(8);
        const lengths = malloc(8);
        const payload = malloc(body.length + 8);
        if (methods.isNull() || lengths.isNull() || payload.isNull()) throw new Error('Allocation failed');
        methods.writeInt(method);
        lengths.writeInt(body.length);
        if (body.length) payload.writeByteArray(body);
        args[4] = methods;
        args[5] = payload;
        args[6] = lengths;
        log('OWNED_REDIRECT ' + method);
      } catch (error) {
        log('OWNED_REDIRECT_REQUEST_ERROR ' + error.message);
      }
    }
  });
  setTimeout(() => detach('timeout'), CONFIG.lifetimeMs);
  log('OWNED_REDIRECT_READY');
}

try { start(); }
catch (error) { log('OWNED_REDIRECT_SETUP_ERROR ' + error.message); }
