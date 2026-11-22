// Real Node -> JSONL Python -> manager -> HA REST flow; only a localhost emulator.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { once } from 'node:events';
import { mkdtemp, writeFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { Bridge } from '../lib/bridge.js';
import { createConnections } from '../lib/connections.js';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');

test('private connection expands HA entities and preserves REST failures through the real Python bridge', { timeout: 20000 }, async () => {
  const owned = await mkdtemp(join(tmpdir(), 'mihome-e2e-'));
  const token = 'localhost-test-token-never-return';
  let state = 'on'; let rejectNext = false; let posts = 0;
  const entity = () => ({ entity_id: 'light.desk', state, attributes: { friendly_name: 'Desk lamp', supported_color_modes: ['onoff'] } });
  const server = createServer(async (req, res) => {
    const send = (status, body) => { res.writeHead(status, { 'Content-Type': 'application/json' }); res.end(JSON.stringify(body)); };
    if (req.headers.authorization !== `Bearer ${token}`) return send(401, { error: 'Unauthorized' });
    if (req.method === 'GET' && req.url === '/api/states') return send(200, [entity()]);
    if (req.method === 'GET' && req.url === '/api/states/light.desk') return send(200, entity());
    if (req.method === 'GET' && req.url === '/api/services') return send(200, [{ domain: 'light', services: { turn_on: {}, turn_off: {} } }]);
    if (req.method === 'POST' && ['/api/services/light/turn_on', '/api/services/light/turn_off'].includes(req.url)) {
      let body = ''; for await (const chunk of req) body += chunk;
      assert.equal(JSON.parse(body).entity_id, 'light.desk'); posts++;
      if (rejectNext) { rejectNext = false; return send(503, { error: 'offline' }); }
      state = req.url.endsWith('turn_on') ? 'on' : 'off'; return send(200, []);
    }
    send(404, { error: 'not found' });
  });
  let raw;
  try {
    server.listen(0, '127.0.0.1'); await once(server, 'listening');
    const wrapper = join(owned, 'bridge_fixture.py');
    await writeFile(wrapper, `import os, sys\nsys.path.insert(0, ${JSON.stringify(join(root, 'python'))})\nimport mihome_bridge as bridge\nfrom smart_home.manager import SmartHomeManager\nfrom smart_home import discovery\n# Only radio/LAN discovery and cloud accounts are replaced, never REST/manager/JSONL.\nasync def no_discovery(**kwargs): return []\ndiscovery.discover_all = no_discovery\nmanager = SmartHomeManager({'data_dir': os.environ['MIHOME_DATA_DIR']})\nbridge.smart_home_manager = lambda: manager\n# Block accidental non-loopback traffic in this subprocess.\ndef guard(event, args):\n    if event in ('socket.connect', 'socket.sendto'):\n        target = args[-1]\n        if not isinstance(target, tuple) or target[0] not in ('127.0.0.1', '::1'):\n            raise RuntimeError('Non-loopback network forbidden by E2E fixture')\nsys.addaudithook(guard)\nraise SystemExit(bridge.main())\n`);
    const connections = createConnections({ readRecord: async () => ({ kind: 'grant', payload: { devices: {
      'home_assistant:main': { base_url: `http://127.0.0.1:${server.address().port}`, token },
    } } }) }, 'mihome/devices');
    raw = new Bridge({ pythonExe: join(root, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python'), script: wrapper, dataDir: owned, env: { NO_PROXY: '127.0.0.1,localhost', no_proxy: '127.0.0.1,localhost' } });
    const bridge = connections.wrap(raw);
    const call = (op, args) => bridge.request(op, args, 10000);
    const scan = await call('smart_home_scan', { subnets: '192.0.2.0/32', ports: '80', scan_ping: false });
    const id = 'home_assistant:main/light.desk';
    assert.ok(scan.devices.some(device => device.id === id));
    assert.equal(posts, 0, 'discovery must never control devices');
    const detail = await call('smart_home_device', { id });
    assert.ok(detail.controls.some(cap => cap.key === 'power' && cap.writable));
    assert.equal(detail.values.power, true);
    assert.equal((await call('smart_home_control', { device_id: id, mode: 'power', state: false })).ok, true);
    assert.equal((await call('smart_home_device', { id })).values.power, false);
    const saved = await call('smart_home_scenes', { mode: 'save', scene: { name: 'Local emulator only', actions: [
      { device: id, mode: 'power', state: false }, { device: id, mode: 'power', state: true },
    ] } });
    rejectNext = true;
    const result = await call('smart_home_scenes', { mode: 'run', scene_id: saved.saved.id });
    assert.equal(result.ok, false); assert.equal(result.success, 1); assert.equal(result.total, 2);
    assert.equal(result.results[0].ok, false); assert.equal(result.results[1].ok, true);
    assert.equal(state, 'on'); assert.equal(posts, 3);
    assert.equal(JSON.stringify([scan, detail, saved, result]).includes(token), false);
    assert.equal(JSON.stringify(await connections.list()).includes(token), false);
  } finally {
    const child = raw?.child;
    const closed = child ? once(child, 'close') : Promise.resolve();
    raw?.dispose(); await closed;
    await new Promise(resolveClose => server.close(resolveClose));
    // owned is an absolute, uniquely created temporary directory, never project data.
    assert.equal(dirname(owned), resolve(tmpdir()));
    await rm(owned, { recursive: true, force: true });
  }
});
