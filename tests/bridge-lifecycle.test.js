import { test } from 'node:test';
import assert from 'node:assert/strict';
import { Bridge } from '../lib/bridge.js';
import { mkdtemp, writeFile, access, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { once } from 'node:events';
import { setTimeout as delay } from 'node:timers/promises';

const exists = (path) => access(path).then(() => true, () => false);
async function waitForFile(path) {
  const deadline = Date.now() + 5000;
  while (!(await exists(path))) {
    if (Date.now() >= deadline) throw new Error(`timed out waiting for ${path}`);
    await delay(10);
  }
}

test('cancelling one request marks other pending requests as interrupted with unknown outcome', async () => {
  const root = fileURLToPath(new URL('..', import.meta.url));
  const bridge = new Bridge({ pythonExe:join(root,'.venv','Scripts','python.exe'),script:join(root,'python','mihome_bridge.py'),dataDir:join(root,'data') });
  try {
    const controller = new AbortController();
    const a = bridge.request('status', {}, 5000, controller.signal);
    const b = bridge.request('status', {}, 5000);
    const outcomeA = assert.rejects(a, {name:'AbortError'});
    const outcomeB = assert.rejects(b, error => error.name !== 'AbortError' && error.code === 'bridge_interrupted' && /未知/.test(error.message));
    controller.abort();
    await Promise.all([outcomeA, outcomeB]);
  } finally { bridge.dispose(); }
});

test('old process exit and output cannot reject or corrupt a replacement request', async () => {
  const root = fileURLToPath(new URL('..', import.meta.url));
  const bridge = new Bridge({ pythonExe:join(root,'.venv','Scripts','python.exe'),script:join(root,'python','mihome_bridge.py'),dataDir:join(root,'data') });
  try {
    const controller = new AbortController();
    const aborted = bridge.request('status', {}, 2000, controller.signal);
    const old = bridge.child;
    controller.abort();
    await assert.rejects(aborted, { name:'AbortError' });
    const next = bridge.request('status', {}, 5000);
    old.stdout.emit('data', Buffer.from('broken partial'));
    old.emit('close', 1, null);
    const response = await next;
    assert.equal(typeof response.logged_in, 'boolean');
    assert.notEqual(bridge.child, old);
  } finally { bridge.dispose(); }
});

test('a final buffered response is preserved after process exit', async () => {
  const root = fileURLToPath(new URL('..', import.meta.url));
  const folder = await mkdtemp(join(tmpdir(), 'mihome-output-drain-'));
  const script = join(folder, 'fixture.py');
  await writeFile(script, `import json, sys
request = json.loads(sys.stdin.readline())
print(json.dumps({'id': request['id'], 'ok': True, 'result': {'confirmed': True}}), flush=True)
`);
  const bridge = new Bridge({ pythonExe: join(root, '.venv', 'Scripts', 'python.exe'), script, dataDir: folder, cwd: folder });
  let child, closed;
  try {
    const response = bridge.request('final', {}, 5000);
    child = bridge.child;
    closed = once(child, 'close');
    child.stdout.pause();
    const outcome = response.then(value => ({ value }), error => ({ error }));
    await once(child, 'exit');
    child.stdout.resume();
    const result = await outcome;
    assert.equal(result.error, undefined, 'exit must not discard a response buffered in stdout');
    assert.deepEqual(result.value, { confirmed: true });
  } finally {
    child?.stdout.resume();
    bridge.dispose();
    if (closed) await closed;
    await rm(folder, { recursive: true, force: true });
  }
});

test('a replacement request does not discard a final response from the exited process', async () => {
  const root = fileURLToPath(new URL('..', import.meta.url));
  const folder = await mkdtemp(join(tmpdir(), 'mihome-exit-drain-race-'));
  const script = join(folder, 'fixture.py');
  const started = join(folder, 'replacement-started');
  await writeFile(script, `import json, os, sys
from pathlib import Path
request = json.loads(sys.stdin.readline())
if request['op'] == 'final':
    print(json.dumps({'id': request['id'], 'ok': True, 'result': {'confirmed': True}}), flush=True)
    os._exit(0)
Path(${JSON.stringify(started)}).write_text('started')
print(json.dumps({'id': request['id'], 'ok': True, 'result': {'replacement': True}}), flush=True)
`);
  const bridge = new Bridge({ pythonExe:join(root,'.venv','Scripts','python.exe'), script, dataDir:folder, cwd:folder });
  let old, replacementClosed;
  try {
    const oldResponse = bridge.request('final', {}, 5000);
    old = bridge.child;
    old.stdout.pause();
    const oldOutcome = oldResponse.then(value => ({ value }), error => ({ error }));
    let replacement;
    const onExit = new Promise((resolve, reject) => {
      old.once('exit', () => {
        try {
          replacement = bridge.request('replacement', {}, 5000);
          replacementClosed = once(bridge.child, 'close');
          resolve();
        } catch (error) { reject(error); }
      });
    });
    await onExit;
    await waitForFile(started);
    old.stdout.resume();
    const [oldResult, newResult] = await Promise.all([oldOutcome, replacement]);
    assert.equal(oldResult.error, undefined, 'old successful response must drain after replacement spawn');
    assert.deepEqual(oldResult.value, { confirmed: true });
    assert.deepEqual(newResult, { replacement: true });
  } finally {
    const current = bridge.child;
    bridge.dispose();
    old?.stdout.resume();
    if (current && current !== old) await once(current, 'close');
    if (replacementClosed) await replacementClosed;
    if (old && old.exitCode === null) await once(old, 'close');
    await rm(folder, { recursive: true, force: true });
  }
});

test('an expired request timer cannot terminate a replacement process', async () => {
  const root = fileURLToPath(new URL('..', import.meta.url));
  const folder = await mkdtemp(join(tmpdir(), 'mihome-exit-race-'));
  const script = join(folder, 'fixture.py');
  const starts = join(folder, 'starts');
  const started = join(folder, 'replacement-started');
  const release = join(folder, 'release');
  const completed = join(folder, 'completed');
  await writeFile(script, `import json, os, sys, time
from pathlib import Path
starts = Path(${JSON.stringify(starts)})
number = int(starts.read_text()) + 1 if starts.exists() else 1
starts.write_text(str(number))
for line in sys.stdin:
    request = json.loads(line)
    if number == 1 and request['op'] == 'die':
        os._exit(7)
    if number > 1 and request['op'] == 'wait':
        Path(${JSON.stringify(started)}).write_text('started')
        while not Path(${JSON.stringify(release)}).exists():
            time.sleep(.005)
        Path(${JSON.stringify(completed)}).write_text('completed')
    print(json.dumps({'id': request['id'], 'ok': True, 'result': {'pid': os.getpid()}}), flush=True)
`);
  const bridge = new Bridge({ pythonExe:join(root,'.venv','Scripts','python.exe'), script, dataDir:folder, cwd:folder });
  let old, oldClosed, replacementClosed, replacementRequest;
  try {
    const requestTimeout = 1200;
    const requestStarted = Date.now();
    const firstRequest = bridge.request('die', {}, requestTimeout);
    old = bridge.child;
    old.stdout.pause();
    oldClosed = once(old, 'close');
    let replacementOutcome;
    old.once('exit', (code) => {
      if (code === 7) {
        replacementRequest = bridge.request('wait', {}, 5000);
        replacementOutcome = assert.doesNotReject(replacementRequest);
        replacementClosed = once(bridge.child, 'close');
      }
    });
    const firstFailure = assert.rejects(firstRequest, (error) => /已退出/.test(error.message));
    await waitForFile(started);
    await firstFailure;
    await delay(Math.max(0, requestStarted + requestTimeout + 100 - Date.now()));
    assert.equal(bridge.child.exitCode, null, 'replacement must survive the old request deadline');
    await writeFile(release, 'release');
    await replacementOutcome;
    const response = await replacementRequest;
    assert.notEqual(response.pid, undefined);
    assert.equal(await exists(completed), true);
    assert.notEqual(bridge.child, old);
  } finally {
    const current = bridge.child;
    const currentClosed = current && current !== old ? once(current, 'close') : null;
    bridge.dispose();
    if (currentClosed) await currentClosed;
    if (oldClosed) await oldClosed;
    if (replacementClosed && current !== old) await replacementClosed;
    await rm(folder, { recursive: true, force: true });
  }
});
