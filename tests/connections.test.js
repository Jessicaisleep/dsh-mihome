import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createConnections } from '../lib/connections.js';

function store() {
  let record;
  return {
    readRecord: async () => structuredClone(record),
    modifyRecord: async (_key, update) => { record = structuredClone(await update(structuredClone(record))); },
  };
}
test('connections keep secrets in credential records and expose only configured IDs', async () => {
  const records = store();
  const connections = createConnections(records, 'mihome/devices');
  await connections.save('tuya:192.0.2.1', {local_key:'hidden', device_id:'tuya-device'});
  assert.deepEqual(await connections.list(), ['tuya:192.0.2.1']);
  assert.equal((await records.readRecord()).payload.devices['tuya:192.0.2.1'].local_key, 'hidden');
  assert.equal(JSON.stringify(await connections.list()).includes('hidden'), false);
  await connections.delete('tuya:192.0.2.1');
  assert.deepEqual(await connections.list(), []);
});
test('connections inject current secrets into smart-home transport only without echoing them', async () => {
  const connections = createConnections(store(), 'mihome/devices');
  const calls = [];
  const bridge = connections.wrap({request:async (...args)=>{calls.push(args); return {ok:true};}});
  await connections.save('home_assistant:main',{base_url:'http://192.0.2.2:8123',token:'old'});
  await bridge.request('smart_home_devices',{},195000);
  await connections.save('home_assistant:main',{token:'new'});
  const signal = new AbortController().signal;
  assert.deepEqual(await bridge.request('smart_home_control',{device_id:'x'},1000,signal),{ok:true});
  assert.equal(calls[0][1]._connections['home_assistant:main'].token,'old');
  assert.equal(calls[1][1]._connections['home_assistant:main'].token,'new');
  assert.equal(calls[1][3], signal);
  await bridge.request('devices',{},100);
  assert.equal(calls[2][1]._connections,undefined);
});
test('invalid connection payloads fail without returning secrets', async () => {
  const connections = createConnections(store(), 'mihome/devices');
  await assert.rejects(connections.save('invalid',{token:'hidden'}), /设备/);
  await assert.rejects(connections.save('tuya:x',[]), /对象/);
});
