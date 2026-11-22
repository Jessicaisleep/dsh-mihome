import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

// Exercise the registered client and its handlers; only React hooks and HTTP are faked.
// Contracts: https://react.dev/reference/react-dom/components/input and
// https://react.dev/learn/synchronizing-with-effects#fetching-data
function clientHarness(route = () => undefined) {
  let current;
  const React = {
    createElement(type, props, ...children) { return { type, props: { ...props, children: children.flat(Infinity) } }; },
    useState(initial) {
      const owner = current, index = owner.index++;
      if (!(index in owner.slots)) owner.slots[index] = typeof initial === 'function' ? initial() : initial;
      return [owner.slots[index], (next) => {
        const value = typeof next === 'function' ? next(owner.slots[index]) : next;
        if (!Object.is(value, owner.slots[index])) { owner.slots[index] = value; owner.dirty = true; }
      }];
    },
    useRef(initial) { const owner = current, index = owner.index++; return owner.slots[index] ??= { current: initial }; },
    useEffect(fn, deps) {
      const owner = current, index = owner.index++, previous = owner.slots[index];
      if (!previous || !deps || deps.some((item, i) => !Object.is(item, previous.deps?.[i]))) {
        owner.slots[index] = { deps, cleanup: previous?.cleanup };
        owner.effects.push(() => { previous?.cleanup?.(); owner.slots[index].cleanup = fn(); });
      }
    },
    useCallback(fn, deps) {
      const owner = current, index = owner.index++, previous = owner.slots[index];
      if (!previous || deps.some((item, i) => !Object.is(item, previous.deps[i]))) owner.slots[index] = { deps, fn };
      return owner.slots[index].fn;
    },
  };
  function mount(element) {
    const owner = { slots: [], effects: [], dirty: true, tree: null, index: 0 };
    owner.render = () => {
      owner.index = 0; owner.dirty = false; current = owner;
      owner.tree = element.type(element.props); current = null;
      for (const effect of owner.effects.splice(0)) effect();
      return owner.tree;
    };
    owner.flush = async () => {
      for (let i = 0; i < 48; i++) { await Promise.resolve(); if (owner.dirty) owner.render(); }
      return owner.tree;
    };
    owner.unmount = () => owner.slots.forEach((slot) => slot?.cleanup?.());
    owner.render();
    return owner;
  }
  const slots = new Map(), calls = [], timers = new Map(); let timerId = 0;
  const devices = [{ id: 'a', name: 'Device A' }, { id: 'b', name: 'Device B' }];
  let plugin;
  vm.runInNewContext(readFileSync(new URL('../client.js', import.meta.url), 'utf8'), {
    window: { __ModuleLoader__: { load(spec) { plugin = spec.factory(() => React); } } },
    navigator: { language: 'zh-CN' }, URL, console,
    setTimeout(fn) { const id = ++timerId; timers.set(id, fn); return id; }, clearTimeout(id) { timers.delete(id); },
    fetch: async (url, init = {}) => {
      const path = url.replace('/dsh-mihome/api/', '');
      const body = init.body ? JSON.parse(init.body) : undefined;
      calls.push({ path, body });
      const custom = await route(path, body);
      const result = custom ?? (path.startsWith('smart-home/devices') ? { ok: true, devices }
        : path === 'smart-home/scenes' ? { ok: true, scenes: [{ id: 's', name: 'Home', actions: [{ device: 'a', mode: 'write', name: 'power', value: true }] }] }
        : path === 'smart-home/credentials' ? { ok: true, devices: [] }
        : { ok: true });
      return { ok: true, status: 200, json: async () => result };
    },
  });
  plugin.apply({ effect(fn) { fn(); }, slots: {
    inject(name, fn) { return fn(); }, register(meta, render) { slots.set(meta.name, { meta, render }); return () => {}; },
  } });
  const shell = mount(slots.get('main').render());
  button(shell.tree, '全屋').props.onClick(); shell.render();
  const panel = mount(nodes(shell.tree).find((node) => node.type?.name === 'SmartHomePanel'));
  return { mount, panel, slots, calls, shell, timers, async mihome() {
    button(shell.tree, '米家').props.onClick(); shell.render();
    const legacy = mount(nodes(shell.tree).find((node) => node.type?.name === 'Panel'));
    await legacy.flush(); return legacy;
  }, async open(id = 'a', label = `Device ${id.toUpperCase()}`) {
    await panel.flush();
    const pending = nodes(panel.tree).find((node) => node.type === 'button' && text(node).startsWith(label)).props.onClick();
    await pending; await panel.flush();
    return nodes(panel.tree).filter((node) => node.type?.name === 'CapabilityControl');
  } };
}
function nodes(tree) { return !tree || typeof tree !== 'object' ? [] : [tree, ...tree.props.children.flatMap(nodes)]; }
function text(tree) { return tree == null || tree === false ? '' : typeof tree !== 'object' ? String(tree) : (tree.props.text ?? tree.props.children.map(text).join('')); }
function button(tree, label) { return nodes(tree).find((node) => node.type === 'button' && text(node).trim() === label); }
function deviceButton(tree, id) { return nodes(tree).find((node) => node.type === 'button' && text(node).startsWith(`Device ${id.toUpperCase()}`)); }
function detail(controls, values = {}) { return { ok: true, detail: { id: 'a', name: 'Device A', controls, values } }; }
const boolCap = { key: 'power', name: 'raw_power', kind: 'bool', writable: true };
const defer = () => { let resolve; const promise = new Promise((done) => { resolve = done; }); return { promise, resolve }; };
const legacyData = (path) => ({ status: { logged_in: true }, devices: { devices: [{ did: 'mi-1', name: 'Legacy device' }] },
  homes: { homes: [] }, scenes: { scenes: [] }, consumables: { items: [], low_count: 0 } }[path]);

test('whole-home is the initial view and Mi Home remains directly available', () => {
  const app = clientHarness(); const initial = app.mount(app.slots.get('main').render());
  assert.ok(nodes(initial.tree).find((node) => node.type?.name === 'SmartHomePanel'));
  assert.ok(button(initial.tree, '米家'));
});

test('unknown boolean values offer explicit on/off without announcing an off switch', async () => {
  const app = clientHarness((path) => path.startsWith('smart-home/device?') ? detail([boolCap]) : undefined);
  const [cap] = await app.open();
  for (const value of [undefined, null, 'unknown', 'unavailable', '']) {
    const control = app.mount({ ...cap, props: { ...cap.props, value } }); await control.flush();
    assert.match(text(control.tree), /未知/);
    assert.equal(nodes(control.tree).some((node) => node.props.role === 'switch' || node.type?.name === 'Switch'), false);
    await button(control.tree, '开启').props.onClick(); await button(control.tree, '关闭').props.onClick();
  }
  assert.deepEqual(app.calls.filter((call) => call.path === 'smart-home/control').slice(0, 2).map((call) => call.body.value), [true, false]);
});

for (const resource of ['devices', 'homes', 'scenes', 'consumables']) {
  test(`Mi Home ${resource} read failure is visible and can be retried`, async () => {
    let fails = true;
    const app = clientHarness((path) => path === resource && fails ? Promise.reject(new Error(`${resource} read failed`)) : legacyData(path));
    const panel = await app.mihome();
    assert.match(text(panel.tree), new RegExp(`${resource} read failed`));
    assert.doesNotMatch(text(panel.tree), /一切正常|耗材状态良好/);
    fails = false; await button(panel.tree, '重试').props.onClick(); await panel.flush();
    assert.doesNotMatch(text(panel.tree), /read failed/);
    assert.ok(nodes(panel.tree).some((node) => node.type?.name === 'DeviceTile'));
  });
}

test('Mi Home missing power value is unknown and explicit power choice sends a boolean', async () => {
  const app = clientHarness((path) => path.startsWith('device?') ? { detail: { props: [{ name: 'on', writable: true }], actions: [] } }
    : path.startsWith('values?') ? { values: {} } : path === 'control' ? { ok: true, on: true } : legacyData(path));
  const panel = await app.mihome();
  const tile = nodes(panel.tree).find((node) => node.type?.name === 'DeviceTile'); tile.props.onOpen(tile.props.device); await panel.flush();
  const device = app.mount(nodes(panel.tree).find((node) => node.type?.name === 'DeviceDetail')); await device.flush();
  assert.match(text(device.tree), /未知/);
  assert.equal(nodes(device.tree).some((node) => node.props['aria-checked'] === false), false);
  await button(device.tree, '关闭').props.onClick();
  assert.equal(app.calls.find((call) => call.path === 'control').body.state, false);
});

test('device tile ignores keyboard events bubbling from its inner power button', async () => {
  const app = clientHarness((path) => legacyData(path)); const panel = await app.mihome();
  const element = nodes(panel.tree).find((node) => node.type?.name === 'DeviceTile'); let opened = 0;
  const tile = app.mount({ ...element, props: { ...element.props, power: true, onOpen: () => { opened += 1; } } });
  const inner = button(tile.tree, '关闭');
  tile.tree.props.onKeyDown({ key: 'Enter', target: inner, currentTarget: tile.tree, preventDefault() {} });
  assert.equal(opened, 0);
  tile.tree.props.onKeyDown({ key: 'Enter', target: tile.tree, currentTarget: tile.tree, preventDefault() {} });
  assert.equal(opened, 1);
});

test('detail capability diagnostics are visible and do not claim the device has no controls', async () => {
  const app = clientHarness((path) => path.startsWith('smart-home/device?')
    ? { detail: { id: 'a', controls: [], values: {}, capability_error: 'capability listing failed', read_error: 'state read failed' } }
    : undefined);
  await app.open();
  const rendered = text(app.panel.tree);
  assert.match(rendered, /capability listing failed/);
  assert.match(rendered, /state read failed/);
  assert.doesNotMatch(rendered, /没有可远程控制的项/);
});

test('configured status is derived for the device opened after initial credential load', async () => {
  const app = clientHarness((path) => path === 'smart-home/credentials' ? { ok: true, devices: ['a'] }
    : path.startsWith('smart-home/device?') ? detail([]) : undefined);
  await app.open('a');
  assert.match(text(app.panel.tree), /已保存连接配置/);
  assert.doesNotMatch(text(app.panel.tree), /尚未保存连接配置/);
});

test('HA entity displays its inherited configured hub connection', async () => {
  const entityId = 'home_assistant:main/light.desk';
  const app = clientHarness((path) => path.startsWith('smart-home/devices')
    ? { ok: true, devices: [{ id: entityId, name: 'Desk light' }] }
    : path === 'smart-home/credentials'
      ? { ok: true, devices: ['home_assistant:main'] }
      : path.startsWith('smart-home/device?')
        ? { detail: { ...detail([]).detail, id: entityId, configured: true } }
        : undefined);
  await app.open(entityId, 'Desk light');
  assert.match(text(app.panel.tree), /已保存连接配置/);
  assert.doesNotMatch(text(app.panel.tree), /尚未保存连接配置/);
});

test('read-only JSON capability shows structured data instead of editable object coercion', async () => {
  const app = clientHarness((path) => path.startsWith('smart-home/device?') ? detail([{ key: 'status', kind: 'json', readable: true, writable: false }], { status: { ok: true, public_only: true, title: 'Gateway' } }) : undefined);
  const [element] = await app.open(); const control = app.mount(element); await control.flush();
  assert.match(text(control.tree), /"public_only":true/); assert.match(text(control.tree), /Gateway/);
  assert.equal(nodes(control.tree).some((node) => node.type === 'input'), false);
});

test('detail shows complete scene authoring identifiers and typed constraints', async () => {
  const cap = { key: 'canonical.target', name: 'raw', kind: 'range', readable: true, writable: false, range: [16, 30, 0.5], unit: '°C',
    value_list: [{ value: 1 }, { value: '1' }] };
  const app = clientHarness((path) => path.startsWith('smart-home/device?') ? detail([cap]) : undefined); await app.open();
  const output = text(app.panel.tree);
  for (const pattern of [/id=a/, /canonical.target/, /readable=true/, /writable=false/, /16.*30.*0.5/, /step=0.5/, /°C/, /enums=.*1.*"1"/]) assert.match(output, pattern);
});

for (const mode of ['save', 'delete']) {
  test(`connection ${mode} keeps confirmed result when detail refresh fails`, async () => {
    let reads = 0;
    const app = clientHarness((path, body) => path.startsWith('smart-home/device?') ? (++reads === 1 ? detail([]) : Promise.reject(new Error('refresh unavailable')))
      : path === 'smart-home/credentials' && body ? { ok: true, configured: mode === 'save' } : undefined);
    await app.open();
    if (mode === 'save') { nodes(app.panel.tree).find((node) => node.type === 'textarea').props.onChange({ target: { value: '{}' } }); await app.panel.flush(); }
    await button(app.panel.tree, mode === 'save' ? '保存连接' : '删除连接').props.onClick(); await app.panel.flush();
    assert.match(text(app.panel.tree), mode === 'save' ? /已保存连接配置/ : /尚未保存连接配置/);
    assert.match(text(app.panel.tree), /刷新.*失败|refresh unavailable/);
    assert.doesNotMatch(text(app.panel.tree), /保存连接失败|删除连接失败/);
  });
}

test('failed credential status is unknown and has an explicit retry', async () => {
  let failed = true;
  const app = clientHarness((path) => path === 'smart-home/credentials' ? failed ? Promise.reject(new Error('status denied')) : { ok: true, devices: [] } : undefined);
  await app.panel.flush(); assert.match(text(app.panel.tree), /连接.*未知/);
  assert.doesNotMatch(text(app.panel.tree), /尚未保存连接配置/);
  failed = false; await button(app.panel.tree, '重试连接状态').props.onClick(); await app.panel.flush();
  assert.match(text(app.panel.tree), /尚未保存连接配置/);
});

for (const listFailure of [false, true]) {
  test(`new connection path stays visible before an ${listFailure ? 'errored' : 'empty'} device list`, async () => {
    const app = clientHarness((path) => path.startsWith('smart-home/devices') ? listFailure ? Promise.reject(new Error('devices denied')) : { ok: true, devices: [] } : undefined);
    await app.panel.flush(); const tree = nodes(app.panel.tree), editor = tree.find((node) => node.type === 'details');
    assert.ok(editor, 'connection configuration must be available'); assert.equal(editor.props.open, true);
    assert.ok(nodes(editor).some((node) => node.type === 'input' && node.props['aria-label'] === '连接目标 ID'));
    assert.ok(tree.indexOf(editor) < tree.findIndex((node) => node.props.role === 'alert' || text(node) === '没有发现设备') || !listFailure);
  });
}

test('scene CRUD, capability writes and JSON inputs all expose visible focus handling', async () => {
  const app = clientHarness((path) => path.startsWith('smart-home/device?') ? detail([boolCap]) : undefined);
  await app.panel.flush(); button(app.panel.tree, '场景· 1').props.onClick(); await app.panel.flush();
  button(app.panel.tree, '新建场景').props.onClick(); await app.panel.flush();
  const assertFocus = (tree) => nodes(tree).filter((node) => ['button', 'textarea'].includes(node.type)).forEach((node) => {
    assert.equal(typeof node.props.onFocus, 'function', `${node.type} ${text(node)}`);
    const event = { target: { style: {} } }; node.props.onFocus(event); assert.ok(event.target.style.outlineColor);
  });
  assertFocus(app.panel.tree);
  button(app.panel.tree, '设备· 2').props.onClick(); await app.panel.flush();
  const [element] = await app.open();
  for (const cap of [{ key: 'text', kind: 'text' }, { key: 'action', kind: 'action' }, { key: 'enum', kind: 'enum', values: [1, '1'] }]) {
    const control = app.mount({ type: element.type, props: { cap, t: (key) => key, value: '1', onWrite() {}, onAction() {} } });
    assertFocus(control.tree);
  }
});

test('a newer toast cancels the older timer and small enum buttons announce selection', async () => {
  const app = clientHarness((path) => path.startsWith('smart-home/device?') ? detail([boolCap]) : undefined);
  const [cap] = await app.open(); await cap.props.onWrite(true); await app.panel.flush(); const first = [...app.timers.keys()][0];
  await cap.props.onWrite(false); await app.panel.flush(); assert.equal(app.timers.has(first), false); assert.equal(app.timers.size, 1);
  const control = app.mount({ type: cap.type, props: { ...cap.props, cap: { key: 'mode', kind: 'enum', values: [1, '1'] }, value: '1' } });
  assert.deepEqual(nodes(control.tree).filter((node) => node.type === 'button').map((node) => node.props['aria-pressed']), [false, true]);
});

test('sidebar identifies unified whole-home controls', () => {
  assert.equal(clientHarness().slots.get('sidebar.panellist').meta.label(), '全屋智能');
});

for (const failure of [{ ok: false, error: 'device refused' }, { ok: true, result: { ok: false, error: 'adapter refused' } }]) {
  test(`control shows returned failure (${failure.error ?? failure.result.error}) and never Done`, async () => {
    const app = clientHarness((path) => path.startsWith('smart-home/device?') ? detail([boolCap], { power: false }) : path === 'smart-home/control' ? failure : undefined);
    const [cap] = await app.open();
    await cap.props.onWrite(true); await app.panel.flush();
    assert.match(text(app.panel.tree), new RegExp(failure.error ?? failure.result.error));
    assert.doesNotMatch(text(app.panel.tree), /已完成/);
  });
}

test('current values use capability key first and original name only as fallback', async () => {
  const app = clientHarness((path) => path.startsWith('smart-home/device?') ? detail([boolCap, { ...boolCap, key: 'other' }], { power: false, raw_power: true }) : undefined);
  const caps = await app.open();
  assert.equal(caps[0].props.value, false);
  assert.equal(caps[1].props.value, true);
});

test('boolean off strings render off and writes remain booleans', async () => {
  const app = clientHarness((path) => path.startsWith('smart-home/device?') ? detail([boolCap], { raw_power: 'off' }) : undefined);
  const [cap] = await app.open();
  for (const value of ['off', 'false', '0', false, 0]) {
    const control = app.mount({ ...cap, props: { ...cap.props, value } }); await control.flush();
    assert.equal(control.tree.props.checked, false, String(value));
  }
  const control = app.mount(cap); await control.flush();
  await control.tree.props.onChange(true);
  assert.deepEqual(app.calls.find((call) => call.path === 'smart-home/control').body, { device_id: 'a', mode: 'write', name: 'power', value: true });
});

test('value_list enum keeps number, string and boolean options distinct', async () => {
  const values = [{ value: 1, zh: 'number' }, { value: '1', zh: 'string' }, { value: false, zh: 'false' }, { value: 2 }, { value: 3 }, { value: 4 }];
  const cap = { key: 'mode', name: 'mode', kind: 'enum', writable: true, value_list: values };
  const app = clientHarness((path) => path.startsWith('smart-home/device?') ? detail([cap], { mode: '1' }) : undefined);
  const [element] = await app.open(); const control = app.mount(element); await control.flush();
  const select = nodes(control.tree).find((node) => node.type === 'select');
  assert.ok(select, 'value_list must create enum selector');
  const options = nodes(select).filter((node) => node.type === 'option');
  assert.notEqual(options[0].props.value, options[1].props.value);
  assert.equal(select.props.value, options[1].props.value);
  await select.props.onChange({ target: { value: options[2].props.value } });
  assert.equal(app.calls.find((call) => call.path === 'smart-home/control').body.value, false);
});

test('range tuple drives limits and keyboard release commits a numeric value', async () => {
  const cap = { key: 'temperature', name: 'target', kind: 'range', writable: true, range: [16, 30, 0.5], unit: '°C' };
  const app = clientHarness((path) => path.startsWith('smart-home/device?') ? detail([cap], { target: 22 }) : undefined);
  const [element] = await app.open(); const control = app.mount(element); await control.flush();
  const slider = nodes(control.tree).find((node) => node.props.type === 'range');
  assert.deepEqual([slider.props.min, slider.props.max, slider.props.step], [16, 30, 0.5]);
  slider.props.onChange({ target: { value: '22.5' } }); await control.flush();
  const updated = nodes(control.tree).find((node) => node.props.type === 'range');
  assert.equal(typeof updated.props.onKeyUp, 'function');
  await updated.props.onKeyUp({ key: 'ArrowRight', target: { value: '22.5' } });
  assert.equal(app.calls.find((call) => call.path === 'smart-home/control').body.value, 22.5);
});

test('action requires explicit JSON params and dispatches action with canonical key', async () => {
  const cap = { key: 'start_clean', name: 'raw_start', kind: 'action', writable: true, inputs: [{ name: 'zone' }] };
  const app = clientHarness((path) => path.startsWith('smart-home/device?') ? detail([cap]) : undefined);
  const [element] = await app.open(); const control = app.mount(element); await control.flush();
  const run = button(control.tree, '执行'); assert.ok(run, 'action needs an execute control');
  await run.props.onClick(); await control.flush();
  assert.equal(app.calls.filter((call) => call.path === 'smart-home/control').length, 0);
  const input = nodes(control.tree).find((node) => node.type === 'textarea'); assert.ok(input);
  input.props.onChange({ target: { value: '[2,{"zone":"kitchen"}]' } }); await control.flush();
  await button(control.tree, '执行').props.onClick();
  assert.deepEqual(app.calls.find((call) => call.path === 'smart-home/control').body, { device_id: 'a', mode: 'action', name: 'start_clean', params: [2, { zone: 'kitchen' }] });
});

test('an older list refresh cannot overwrite devices returned by a newer scan', async () => {
  const refresh = defer(), scan = defer(); let deviceReads = 0;
  const app = clientHarness((path) => {
    if (path.startsWith('smart-home/devices')) { deviceReads += 1; return deviceReads === 1 ? { ok: true, devices: [] } : refresh.promise; }
    if (path.startsWith('smart-home/scan')) return scan.promise;
    return undefined;
  });
  await app.panel.flush();
  const olderRefresh = button(app.panel.tree, '刷新').props.onClick();
  const newerScan = button(app.panel.tree, '扫描全屋').props.onClick();
  scan.resolve({ ok: true, devices: [{ id: 'scan-result', name: 'Scanned device' }] }); await newerScan; await app.panel.flush();
  refresh.resolve({ ok: true, devices: [{ id: 'stale-result', name: 'Stale device' }] }); await olderRefresh; await app.panel.flush();
  assert.ok(nodes(app.panel.tree).some((node) => node.type === 'button' && text(node).startsWith('Scanned device')));
  assert.ok(!nodes(app.panel.tree).some((node) => node.type === 'button' && text(node).startsWith('Stale device')));
});

test('scene load failure is visible and retry can recover the list', async () => {
  let sceneReads = 0;
  const app = clientHarness((path) => {
    if (path === 'smart-home/scenes') {
      sceneReads += 1;
      return sceneReads === 1 ? Promise.reject(new Error('scene storage unavailable')) : { ok: true, scenes: [{ id: 'retry-scene', name: 'Recovered', actions: [] }] };
    }
    return undefined;
  });
  await app.panel.flush(); nodes(app.panel.tree).find((node) => node.type === 'button' && node.props.role === 'tab' && node.props['aria-selected'] === false && text(node).startsWith('场景')).props.onClick(); await app.panel.flush();
  assert.match(text(app.panel.tree), /scene storage unavailable/);
  await button(app.panel.tree, '重试场景').props.onClick(); await app.panel.flush();
  assert.match(text(app.panel.tree), /Recovered/);
  assert.equal(sceneReads, 2);
});

test('scene execution exposes aggregate and per-step results from an HTTP error body', async () => {
  const app = clientHarness((path) => {
    if (path === 'smart-home/scene') {
      const error = new Error('partial scene failure');
      error.body = { ok: false, error: 'partial scene failure', result: { ok: false, success: 1, total: 2,
        steps: [{ device: 'lamp', mode: 'write', ok: true }, { device: 'blind', mode: 'action', ok: false, error: 'offline' }] } };
      throw error;
    }
    return undefined;
  });
  await app.panel.flush(); nodes(app.panel.tree).find((node) => node.type === 'button' && node.props.role === 'tab' && node.props['aria-selected'] === false && text(node).startsWith('场景')).props.onClick(); await app.panel.flush();
  await button(app.panel.tree, '执行').props.onClick(); await app.panel.flush();
  assert.match(text(app.panel.tree), /成功 1.*2|1.*2.*成功/s);
  assert.match(text(app.panel.tree), /lamp/); assert.match(text(app.panel.tree), /blind/); assert.match(text(app.panel.tree), /offline/);
});

test('action schema is shown and validates object shape, required fields and types', async () => {
  const cap = { key: 'turn_on', name: 'raw_action', kind: 'action', writable: true,
    params_schema: { type: 'object', properties: { brightness: { type: 'number', minimum: 1, maximum: 100 } }, required: ['brightness'] } };
  const app = clientHarness((path) => path.startsWith('smart-home/device?') ? detail([cap]) : undefined);
  const [element] = await app.open(); const control = app.mount(element); await control.flush();
  assert.match(text(control.tree), /brightness/);
  assert.match(text(control.tree), /\{\}/);
  const params = () => nodes(control.tree).find((node) => node.type === 'textarea');
  params().props.onChange({ target: { value: '[]' } }); await control.flush();
  await button(control.tree, '执行').props.onClick(); await control.flush();
  assert.match(text(control.tree), /对象/);
  params().props.onChange({ target: { value: '{"brightness":"high"}' } }); await control.flush();
  await button(control.tree, '执行').props.onClick(); await control.flush();
  assert.match(text(control.tree), /number|数值|数字/i);
  assert.equal(app.calls.filter((call) => call.path === 'smart-home/control').length, 0);
  params().props.onChange({ target: { value: '{"brightness":42}' } }); await control.flush();
  await button(control.tree, '执行').props.onClick(); await control.flush();
  assert.deepEqual(app.calls.find((call) => call.path === 'smart-home/control').body.params, { brightness: 42 });
});

test('capability inputs, Toast and whole-home buttons expose accessible labels and focus handlers', async () => {
  const app = clientHarness((path) => path.startsWith('smart-home/device?') ? detail([boolCap]) : path === 'smart-home/control' ? { ok: true } : undefined);
  const [cap] = await app.open();
  assert.equal(cap.props['aria-label'], 'raw_power');
  assert.equal(typeof button(app.panel.tree, '‹ 返回').props.onFocus, 'function');
  await cap.props.onWrite(true); await app.panel.flush();
  const toast = nodes(app.panel.tree).find((node) => node.type?.name === 'Toast');
  const toastDOM = toast.type(toast.props);
  assert.equal(toastDOM.props.role, 'status'); assert.equal(toastDOM.props['aria-live'], 'polite');
});

test('late device request cannot replace the newly opened detail', async () => {
  const a = defer(), b = defer();
  const app = clientHarness((path) => path.includes('device?id=a') ? a.promise : path.includes('device?id=b') ? b.promise : undefined);
  await app.panel.flush(); const first = deviceButton(app.panel.tree, 'a').props.onClick(); await app.panel.flush();
  button(app.panel.tree, '‹ 返回').props.onClick(); await app.panel.flush();
  const second = deviceButton(app.panel.tree, 'b').props.onClick();
  b.resolve(detail([{ ...boolCap, key: 'new', zh: 'New control' }])); await second; await app.panel.flush();
  a.resolve(detail([{ ...boolCap, key: 'old', zh: 'Old control' }])); await first; await app.panel.flush();
  assert.match(text(app.panel.tree), /New control/); assert.doesNotMatch(text(app.panel.tree), /Old control/);
});

test('late control completion cannot toast success after navigating to another device', async () => {
  const write = defer();
  const app = clientHarness((path) => path.startsWith('smart-home/device?id=a') ? detail([boolCap])
    : path.startsWith('smart-home/device?id=b') ? detail([{ ...boolCap, key: 'new', zh: 'New control' }])
      : path === 'smart-home/control' ? write.promise : undefined);
  const [cap] = await app.open(); const pending = cap.props.onWrite(true);
  button(app.panel.tree, '‹ 返回').props.onClick(); await app.panel.flush();
  const opening = deviceButton(app.panel.tree, 'b').props.onClick(); await opening; await app.panel.flush();
  write.resolve({ ok: true }); await pending; await app.panel.flush();
  assert.match(text(app.panel.tree), /New control/); assert.doesNotMatch(text(app.panel.tree), /已完成/);
});

test('scene JSON editor validates, saves, edits and deletes without running a scene', async () => {
  const app = clientHarness((path, body) => path === 'smart-home/scene' ? { ok: true, result: body.mode === 'save' ? { saved: { ...body.scene, id: 's' } } : { deleted: true } } : undefined);
  await app.panel.flush(); button(app.panel.tree, '场景· 1').props.onClick(); await app.panel.flush();
  const create = button(app.panel.tree, '新建场景'); assert.ok(create); create.props.onClick(); await app.panel.flush();
  const input = () => nodes(app.panel.tree).find((node) => node.type === 'textarea' && node.props['aria-label'] === '场景 JSON');
  input().props.onChange({ target: { value: '{bad' } }); await app.panel.flush();
  await button(app.panel.tree, '保存场景').props.onClick(); await app.panel.flush();
  assert.match(text(app.panel.tree), /JSON/); assert.equal(app.calls.filter((call) => call.path === 'smart-home/scene').length, 0);
  input().props.onChange({ target: { value: '{"name":"Morning","actions":[{"device":"a","mode":"write","name":"power","value":false}]}' } }); await app.panel.flush();
  await button(app.panel.tree, '保存场景').props.onClick(); await app.panel.flush();
  assert.equal(app.calls.find((call) => call.body?.mode === 'save').body.scene.actions[0].value, false);
  button(app.panel.tree, '编辑').props.onClick(); await app.panel.flush(); assert.match(input().props.value, /"id": "s"/);
  await button(app.panel.tree, '删除').props.onClick(); await app.panel.flush();
  assert.deepEqual(app.calls.find((call) => call.body?.mode === 'delete').body, { mode: 'delete', scene_id: 's' });
  assert.equal(app.calls.filter((call) => call.body?.mode === 'run').length, 0);
});

test('control requires explicit success confirmation', async () => {
  const app = clientHarness((path) => path.startsWith('smart-home/device?') ? detail([boolCap]) : path === 'smart-home/control' ? {} : undefined);
  const [cap] = await app.open(); await cap.props.onWrite(true); await app.panel.flush();
  assert.match(text(app.panel.tree), /未返回成功确认/); assert.doesNotMatch(text(app.panel.tree), /已完成/);
});

test('connection can be configured with no discovered devices and never displays returned secret errors', async () => {
  const app = clientHarness((path, body) => path.startsWith('smart-home/devices') ? { ok: true, devices: [] }
    : path === 'smart-home/credentials' && body ? { ok: false, error: 'secret-test was rejected' } : undefined);
  await app.panel.flush();
  const target = nodes(app.panel.tree).find((node) => node.type === 'input' && node.props['aria-label'] === '连接目标 ID');
  assert.ok(target); assert.equal(target.props.value, 'home_assistant:main');
  const input = nodes(app.panel.tree).find((node) => node.type === 'textarea' && node.props['aria-label'] === '连接配置 JSON');
  assert.ok(input); assert.equal(input.props.value, '');
  input.props.onChange({ target: { value: '{"base_url":"http://ha.local:8123","token":"secret-test"}' } }); await app.panel.flush();
  await button(app.panel.tree, '保存连接').props.onClick(); await app.panel.flush();
  assert.equal(app.calls.find((call) => call.path === 'smart-home/credentials' && call.body).body.device_id, 'home_assistant:main');
  assert.doesNotMatch(text(app.panel.tree), /secret-test/);
  assert.equal(nodes(app.panel.tree).find((node) => node.type === 'textarea').props.value, '');
});

test('an undiscovered Home Assistant gateway can be saved then scanned into entity tiles', async () => {
  let saved = false;
  const entity = { id: 'home_assistant:kitchen', name: 'Kitchen light', protocol: 'home_assistant' };
  const app = clientHarness((path, body) => {
    if (path.startsWith('smart-home/devices')) return { ok: true, devices: [] };
    if (path === 'smart-home/credentials' && body) {
      assert.equal(body.device_id, 'home_assistant:main');
      assert.deepEqual(body.payload, { base_url: 'http://ha.local:8123', token: 'private-token' });
      saved = true; return { ok: true, configured: true };
    }
    if (path.startsWith('smart-home/scan')) {
      assert.equal(saved, true); return { ok: true, devices: [entity] };
    }
    if (path.startsWith('smart-home/device?id=')) return detail([]);
    return undefined;
  });
  await app.panel.flush();
  assert.ok(nodes(app.panel.tree).find((node) => node.type === 'summary' && text(node) === '配置连接'));
  const input = () => nodes(app.panel.tree).find((node) => node.type === 'textarea' && node.props['aria-label'] === '连接配置 JSON');
  assert.equal(input().props.value, '');
  input().props.onChange({ target: { value: '{"base_url":"http://ha.local:8123","token":"private-token"}' } }); await app.panel.flush();
  await button(app.panel.tree, '保存连接').props.onClick(); await app.panel.flush();
  assert.equal(input().props.value, ''); assert.doesNotMatch(text(app.panel.tree), /private-token/);
  await button(app.panel.tree, '扫描全屋').props.onClick(); await app.panel.flush();
  const tile = nodes(app.panel.tree).find((node) => node.type === 'button' && text(node).startsWith('Kitchen light'));
  assert.ok(tile, 'scanned entities must be directly openable');
  await tile.props.onClick(); await app.panel.flush();
  assert.ok(app.calls.find((call) => call.path === 'smart-home/device?id=home_assistant%3Akitchen'));
});

test('connection JSON stays empty initially, validates locally, saves privately and clears', async () => {
  const app = clientHarness((path, body) => path.startsWith('smart-home/device?') ? detail([])
    : path === 'smart-home/credentials' && body ? { ok: true, configured: body.mode === 'save' } : undefined);
  await app.open();
  const input = () => nodes(app.panel.tree).find((node) => node.type === 'textarea' && node.props['aria-label'] === '连接配置 JSON');
  assert.ok(input(), 'connection editor must exist'); assert.equal(input().props.value, '');
  input().props.onChange({ target: { value: '{bad' } }); await app.panel.flush();
  await button(app.panel.tree, '保存连接').props.onClick(); await app.panel.flush();
  assert.match(text(app.panel.tree), /JSON/);
  assert.equal(app.calls.filter((call) => call.path === 'smart-home/credentials' && call.body).length, 0);
  input().props.onChange({ target: { value: '{"local_key":"secret-test","device_id":"tuya-id","dps":{"power":"1"}}' } }); await app.panel.flush();
  await button(app.panel.tree, '保存连接').props.onClick(); await app.panel.flush();
  assert.equal(app.calls.find((call) => call.path === 'smart-home/credentials' && call.body).body.payload.local_key, 'secret-test');
  assert.equal(input().props.value, ''); assert.doesNotMatch(text(app.panel.tree), /secret-test/);
  await button(app.panel.tree, '删除连接').props.onClick(); await app.panel.flush();
  assert.deepEqual(app.calls.filter((call) => call.path === 'smart-home/credentials' && call.body).at(-1).body, { device_id: 'a', mode: 'delete' });
  assert.match(text(app.panel.tree), /尚未保存连接配置/); assert.doesNotMatch(text(app.panel.tree), /删除连接失败/);
});
