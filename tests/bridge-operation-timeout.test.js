import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, writeFile, access, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { once } from 'node:events';
import { setTimeout as delay } from 'node:timers/promises';
import { Bridge } from '../lib/bridge.js';

const root = fileURLToPath(new URL('..', import.meta.url));
const python = join(root, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
const exists = async (file) => access(file).then(() => true, () => false);
async function waitForFile(file) {
  const deadline = Date.now() + 3000;
  while (!(await exists(file))) {
    if (Date.now() >= deadline) throw new Error('blocking operation did not start');
    await delay(5);
  }
}

// Only the external write is replaced: real Python dispatcher, _run_async and Node Bridge run.
// A file gate keeps the fake write blocked without contacting any device or network.
for (const mode of ['sync', 'thread']) {
  test(`Python operation timeout stops a ${mode} write and invalidates queued requests`, async () => {
    const folder = await mkdtemp(join(tmpdir(), 'mihome-operation-timeout-'));
    const started = join(folder, 'started');
    const release = join(folder, 'release');
    const completed = join(folder, 'completed');
    const queued = join(folder, 'queued');
    const script = join(folder, 'fixture.py');
    await writeFile(script, `import asyncio, sys, time
from pathlib import Path
sys.path.insert(0, ${JSON.stringify(join(root, 'python'))})
import mihome_bridge as bridge

def blocked_write():
    Path(${JSON.stringify(started)}).write_text('started')
    while not Path(${JSON.stringify(release)}).exists():
        time.sleep(.005)
    Path(${JSON.stringify(completed)}).write_text('completed')
    return {'ok': True}

async def operation():
    if ${JSON.stringify(mode)} == 'thread':
        return await asyncio.to_thread(blocked_write)
    return blocked_write()

def timed_write(args):
    return bridge._run_async(operation(), timeout=.25)

def queued_write(args):
    Path(${JSON.stringify(queued)}).write_text('queued')
    return {'ok': True}

bridge.HANDLERS.update(contract_status=lambda args: {'ready': True},
                       contract_write=timed_write, contract_queued=queued_write)
bridge.main()
`);
    const bridge = new Bridge({ pythonExe: python, script, dataDir: folder, cwd: folder });
    let child, closed;
    try {
      assert.equal((await bridge.request('contract_status', {}, 3000)).ready, true);
      child = bridge.child;
      closed = once(child, 'close');
      const request = bridge.request('contract_write', {}, 4000);
      const primary = assert.rejects(request, (error) => error.code === 'operation_timeout'
        && error.outcomeUnknown === true && /未知/.test(error.message));
      await waitForFile(started);
      const pending = assert.rejects(bridge.request('contract_queued', {}, 4000),
        (error) => error.code === 'bridge_interrupted' && /未知/.test(error.message));
      await Promise.all([primary, pending]);
      await closed;
      await writeFile(release, 'release');
      assert.equal(await exists(completed), false, 'blocked local write must not continue after timeout');
      assert.equal(await exists(queued), false, 'queued request must never run after timeout');
      assert.equal((await bridge.request('contract_status', {}, 3000)).ready, true);
      assert.notEqual(bridge.child, child);
    } finally {
      await writeFile(release, 'release');
      const current = bridge.child;
      const currentClosed = current ? once(current, 'close') : null;
      bridge.dispose();
      if (currentClosed) await currentClosed;
      if (closed) await closed;
      await rm(folder, { recursive: true, force: true });
    }
  });
}

for (const mode of ['sync', 'thread']) {
  test(`A device ${mode} TimeoutError does not terminate the bridge`, async () => {
    const folder = await mkdtemp(join(tmpdir(), 'mihome-device-timeout-'));
    const script = join(folder, 'fixture.py');
    await writeFile(script, `import asyncio, sys
sys.path.insert(0, ${JSON.stringify(join(root, 'python'))})
import mihome_bridge as bridge

def device_call():
    raise TimeoutError('device socket read timed out')

async def operation():
    if ${JSON.stringify(mode)} == 'thread':
        return await asyncio.to_thread(device_call)
    return device_call()

bridge.HANDLERS.update(contract_status=lambda args: {'ready': True},
                       contract_device_timeout=lambda args: bridge._run_async(operation(), timeout=2))
bridge.main()
`);
    const bridge = new Bridge({ pythonExe: python, script, dataDir: folder, cwd: folder });
    let child, closed;
    try {
      assert.equal((await bridge.request('contract_status', {}, 3000)).ready, true);
      child = bridge.child;
      closed = once(child, 'close');
      await assert.rejects(bridge.request('contract_device_timeout', {}, 3000),
        (error) => error.code === 'io_error' && /device socket read timed out/.test(error.message)
          && error.outcomeUnknown !== true);
      assert.equal((await bridge.request('contract_status', {}, 3000)).ready, true);
      assert.equal(bridge.child, child, 'completed device failure must not discard the worker');
      assert.equal(child.exitCode, null);
    } finally {
      const current = bridge.child;
      const currentClosed = current && current !== child ? once(current, 'close') : null;
      bridge.dispose();
      if (currentClosed) await currentClosed;
      if (closed) await closed;
      await rm(folder, { recursive: true, force: true });
    }
  });
}
