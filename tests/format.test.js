import { test } from 'node:test';
import assert from 'node:assert/strict';
import { formatSmartHomeList, formatSmartHomeDetail, formatSmartHomeScenes } from '../lib/format.js';

test('device and detail formatting preserves configured and BLE sources', () => {
  const devices = [{ id: 'a', source: 'configured' }, { id: 'b', source: 'ble' }];
  const list = formatSmartHomeList({ devices });
  assert.match(list, /configured|已配置/); assert.match(list, /ble|蓝牙/i);
  assert.doesNotMatch(formatSmartHomeDetail({ source: 'configured' }), /来源=局域网/);
  assert.match(formatSmartHomeDetail({ source: 'ble' }), /ble|蓝牙/i);
});

test('whole-home detail exposes canonical keys, permissions and typed capability constraints', () => {
  const output = formatSmartHomeDetail({ id: 'a', name: 'AC', controls: [
    { key: 'temperature', name: 'target', zh: '温度', kind: 'range', readable: true, writable: true, range: [16, 30, 0.5], unit: '°C' },
    { key: 'mode', name: 'raw_mode', kind: 'enum', readable: true, writable: false, value_list: [{ value: 1, zh: '自动' }, { value: '1', zh: '强力' }] },
    { key: 'start', name: 'start_raw', kind: 'action', readable: false, writable: true },
  ] });
  assert.match(output, /key=temperature/); assert.match(output, /readable=true/); assert.match(output, /writable=true/);
  assert.match(output, /16.*30.*0\.5/); assert.match(output, /°C/);
  assert.match(output, /key=mode/); assert.match(output, /writable=false/);
  assert.match(output, /1=自动/); assert.match(output, /"1"=强力/);
  assert.match(output, /key=start/); assert.match(output, /readable=false/); assert.match(output, /action/);
});

test('detail formatting preserves capability and state diagnostics without claiming unsupported controls', () => {
  const output = formatSmartHomeDetail({ controls: [], values: {}, capability_error: 'capability listing failed', read_error: 'state read failed' });
  assert.match(output, /capability listing failed/);
  assert.match(output, /state read failed/);
  assert.doesNotMatch(output, /没有返回可控制项|当前没有读到任何数值/);
});

test('action parameter schema is included with useful object and required-field guidance', () => {
  const output = formatSmartHomeDetail({ controls: [{ key: 'turn_on', name: 'turn_on', kind: 'action', writable: true,
    params_schema: { type: 'object', properties: { brightness: { type: 'number', minimum: 1, maximum: 100 } }, required: ['brightness'], additionalProperties: false } }] });
  assert.match(output, /params_schema=/); assert.match(output, /brightness/); assert.match(output, /required/);
});

test('scene formatter reports save and delete outcomes instead of an empty list', () => {
  assert.match(formatSmartHomeScenes({ mode: 'save', ok: true, saved: { id: 's', name: 'Home' } }), /保存.*Home.*s/s);
  const deleted = formatSmartHomeScenes({ ok: true, deleted: true }, 'delete');
  assert.match(deleted, /删除/); assert.doesNotMatch(deleted, /没有保存/);
  assert.match(formatSmartHomeScenes({ mode: 'delete', ok: false, deleted: false, error: 'missing' }), /missing/);
});

test('scene run reports real success, failure and each step including nested errors', () => {
  const output = formatSmartHomeScenes({ mode: 'run', ok: false, scene: 'Home', success: 1, total: 2,
    steps: [{ device: 'a', ok: true, mode: 'write' }, { device: 'b', ok: false, mode: 'action', result: { ok: false, error: 'device offline' } }] });
  assert.match(output, /失败/); assert.match(output, /成功.*1/); assert.match(output, /失败.*1/);
  assert.match(output, /a/); assert.match(output, /b/); assert.match(output, /device offline/);
  assert.doesNotMatch(output, /没有保存/);
});

test('scene list preserves action values and explicit params for reuse', () => {
  const output = formatSmartHomeScenes({ mode: 'list', scenes: [{ id: 's', name: 'Home', actions: [
    { device: 'a', mode: 'write', name: 'mode', value: false }, { device: 'b', mode: 'action', name: 'start', params: [2] },
  ] }] });
  assert.match(output, /value=false/); assert.match(output, /params=\[2\]/);
});
