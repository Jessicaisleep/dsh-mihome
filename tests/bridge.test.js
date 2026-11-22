/**
 * 桥接层测试：不起 DSH，直接驱动 Bridge，验证协议、错误分类与格式化。
 *
 * 用 node --test 跑：node --test tests/
 * 未登录是这些用例的常态（CI 上没有米家账号），所以断言都围绕
 * 「协议通不通、错误有没有被正确分类」而不是「设备列表有几台」。
 */

import { test } from "node:test";
import assert from "node:assert/strict";
import { join } from "node:path";
import { mkdtempSync, rmSync, writeFileSync, existsSync } from "node:fs";
import { tmpdir } from "node:os";
import { fileURLToPath } from "node:url";
import { dirname } from "node:path";
import { Bridge } from "../lib/bridge.js";
import { formatDeviceList, formatDeviceDetail, formatScenes } from "../lib/format.js";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const PYTHON = process.platform === "win32"
  ? join(ROOT, ".venv", "Scripts", "python.exe")
  : join(ROOT, ".venv", "bin", "python");

function makeBridge() {
  return new Bridge({
    pythonExe: PYTHON,
    script: join(ROOT, "python", "mihome_bridge.py"),
    dataDir: join(ROOT, "data"),
    cwd: join(ROOT, "python"),
  });
}

test("status 返回登录态与 api 版本", async () => {
  const bridge = makeBridge();
  try {
    const result = await bridge.request("status");
    assert.equal(typeof result.logged_in, "boolean");
    assert.match(String(result.api_version), /^\d+\.\d+/);
  } finally {
    bridge.dispose();
  }
});

test("多个请求按 id 配对，不会串台", async () => {
  const bridge = makeBridge();
  try {
    // 不 await 第一个，直接并发发三个：验证 pending map 的 id 配对
    const [a, b, c] = await Promise.all([
      bridge.request("status"),
      bridge.request("status"),
      bridge.request("status"),
    ]);
    assert.equal(a.api_version, b.api_version);
    assert.equal(b.api_version, c.api_version);
  } finally {
    bridge.dispose();
  }
});

test("未知 op 得到 unknown_op 而不是挂死", async () => {
  const bridge = makeBridge();
  try {
    await assert.rejects(
      () => bridge.request("nope_not_an_op"),
      (error) => error.code === "unknown_op",
    );
  } finally {
    bridge.dispose();
  }
});

test("未登录时设备操作分类为 need_login", async () => {
  const bridge = makeBridge();
  try {
    const status = await bridge.request("status");
    if (status.logged_in) return; // 本机已登录就跳过这条前提性断言
    await assert.rejects(
      () => bridge.request("devices"),
      (error) => error.code === "need_login",
    );
  } finally {
    bridge.dispose();
  }
});

test("缺参数报错带中文说明", async () => {
  const bridge = makeBridge();
  try {
    await assert.rejects(
      () => bridge.request("read_props", { did: "x" }),
      (error) => /names/.test(error.message),
    );
  } finally {
    bridge.dispose();
  }
});

test("aborting an in-flight request terminates its Python bridge", async () => {
  const dir = mkdtempSync(join(tmpdir(), "dsh-mihome-abort-"));
  const marker = join(dir, "late-write.txt");
  const script = join(dir, "fake_bridge.py");
  writeFileSync(script, [
    "import json, sys, time",
    "for line in sys.stdin:",
    " req = json.loads(line)",
    " if req['op'] == 'slow':",
    "  time.sleep(0.25)",
    "  open(r'" + marker.replaceAll("\\", "\\\\") + "', 'w').write('late')",
    " print(json.dumps({'id':req['id'],'ok':True,'result':{}}), flush=True)",
  ].join("\n"));
  const bridge = new Bridge({ pythonExe: PYTHON, script, dataDir: dir, cwd: dir });
  const controller = new AbortController();
  try {
    const request = bridge.request("slow", {}, 2000, controller.signal);
    setTimeout(() => controller.abort(), 30);
    await assert.rejects(request, (error) => error.name === "AbortError");
    await new Promise((resolve) => setTimeout(resolve, 350));
    assert.equal(existsSync(marker), false);
  } finally {
    bridge.dispose();
  }
});

test("stdout and stderr UTF-8 decoders do not share partial characters", async () => {
  const dir = mkdtempSync(join(tmpdir(), "dsh-mihome-utf8-"));
  const script = join(dir, "fake_bridge.py");
  writeFileSync(script, [
    "import json, os, sys, time",
    "req=json.loads(sys.stdin.readline())",
    "line=json.dumps({'id':req['id'],'ok':True,'result':{'text':'中'}}, ensure_ascii=False).encode()",
    "mark='中'.encode()",
    "at=line.index(mark)",
    "os.write(1, line[:at+1])",
    "time.sleep(0.04)",
    "os.write(2, b'log\\n')",
    "time.sleep(0.04)",
    "os.write(1, line[at+1:]+b'\\n')",
  ].join("\n"));
  const bridge = new Bridge({ pythonExe: PYTHON, script, dataDir: dir, cwd: dir });
  try {
    const result = await bridge.request("utf8", {}, 2000);
    assert.equal(result.text, "中");
  } finally {
    bridge.dispose();
  }
});

test("格式化为空数据时不崩且给出可读文案", () => {
  assert.match(formatDeviceList({ devices: [] }), /没有设备/);
  assert.match(formatDeviceList({ devices: [{ did: "d1", name: "灯", home_name: "家", room_name: "客厅", online: true }] }), /客厅/);
  assert.match(formatDeviceDetail({ did: "d1", name: "灯", props: [], actions: [] }), /没有返回可读属性/);
  assert.match(formatScenes({ scenes: [] }), /没有可用的米家场景/);
  assert.match(
    formatScenes({ scenes: [{ scene_id: "s1", name: "回家", home_id: "h1", home_name: "家" }] }),
    /scene_id=s1 home_id=h1/,
  );
});
