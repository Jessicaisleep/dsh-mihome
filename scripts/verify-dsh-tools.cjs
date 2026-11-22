// Run with the installed DSH Electron runtime (ELECTRON_RUN_AS_NODE=1).
// Keep DSH schema validation real; replace only the Python transport and Host services.
const assert = require('node:assert/strict');
const { registerHooks } = require('node:module');
const { pathToFileURL } = require('node:url');
const { join } = require('node:path');
const calls = [];
globalThis.__mihomeSchemaBridge = class {
  check() { return {}; }
  dispose() {}
  async request(op, args, timeout, signal) {
    if (signal?.aborted) throw Object.assign(new Error('cancelled'), { name: 'AbortError' });
    calls.push({ op, args, timeout });
    return { ok: true, devices: [], scenes: [] };
  }
};
const anchor = process.env.DSH_INSTALL_DIR;
assert.ok(anchor, 'DSH_INSTALL_DIR must identify the running installation');
const dshPackages = Object.fromEntries(['@deepseek-ai/schemastery', '@deepseek-ai/dsh-tools', '@deepseek-ai/dsh-credentials'].map(name => [name, pathToFileURL(require.resolve(name, { paths: [anchor] })).href]));
registerHooks({ resolve(specifier, context, next) {
  if (dshPackages[specifier]) {
    return next(dshPackages[specifier], context);
  }
  if (specifier === './bridge.js' && context.parentURL?.endsWith('/lib/index.js')) {
    return { url: 'data:text/javascript,export const Bridge=globalThis.__mihomeSchemaBridge;', shortCircuit: true };
  }
  return next(specifier, context);
}});
(async () => {
  const { apply } = await import(pathToFileURL(join(__dirname, '..', 'lib', 'index.js')).href);
  const tools = new Map();
  const ctx = {
    credentials: { readRecord: async () => ({ kind: 'grant', payload: { devices: {} } }) },
    effect() {}, inject() {}, systemPrompt: { section() {} },
    tools: { register(tool) { assert.ok(!tools.has(tool.name)); tools.set(tool.name, tool); } },
  };
  apply(ctx, {});
  assert.equal(tools.size, 13);
  await tools.get('smart_home_scan').execute({ subnets: '192.0.2.0/24', ports: '80,443', prefix: 24, scan_ping: false }, {});
  assert.equal(calls.at(-1).args.ports, '80,443');
  await assert.rejects(tools.get('smart_home_scan').execute({ prefix: '24' }, {}), /must be a number/);
  for (const params of [[42, false], { brightness: 42 }]) {
    await tools.get('smart_home_control').execute({ device_id: 'contract:device', mode: 'action', name: 'set', params }, {});
    assert.deepEqual(calls.at(-1).args.params, params);
  }
  const controller = new AbortController(); controller.abort();
  await assert.rejects(tools.get('smart_home_devices').execute({}, { signal: controller.signal }), { name: 'AbortError' });
  console.log(JSON.stringify({ runtime: process.versions.electron ? 'DSH Electron' : 'Node', node: process.versions.node, tools: tools.size, scanSchema: 'passed', objectAndArrayActionParams: 'passed', cancellation: 'passed' }));
})().catch(error => { console.error(error); process.exitCode = 1; });
