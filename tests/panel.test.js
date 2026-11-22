import { test } from "node:test";
import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import { mountPanelRoutes } from "../lib/panel.js";

function makePanel(bridgeResult = { ok: true }, bridgeError = null, credentials = null) {
  const routes = new Map();
  let bridgeCalls = 0;
  const requests = [];
  const bridge = {
    request: async (...args) => {
      bridgeCalls += 1;
      requests.push(args);
      if (bridgeError) throw bridgeError;
      return bridgeResult;
    },
    check: () => ({}),
  };
  const ctx = {
    credentials: credentials ?? {
      readRecord: async () => undefined,
      modifyRecord: async () => {},
      deleteRecord: async () => {},
    },
    effect(fn) { fn(); },
    webServer: { register(route) { routes.set(route.path, route.handler); return () => {}; } },
  };
  mountPanelRoutes(ctx, {
    bridge,
    loginBridge: bridge,
    qrDir: "C:\\temp",
    dataDir: "C:\\temp",
    requestTimeoutMs: 100,
    loginTimeoutMs: 100,
  });
  return { routes, requests, calls: () => bridgeCalls };
}

async function request(route, { method = "POST", path = "/dsh-mihome/api/smart-home/control", origin, contentType = "application/json", body = "{}" } = {}) {
  const req = new EventEmitter();
  req.method = method;
  req.url = path;
  req.headers = { host: "127.0.0.1:8765", "content-type": contentType };
  if (origin !== undefined) req.headers.origin = origin;
  req.socket = { localAddress: "127.0.0.1", encrypted: false };
  const result = { status: 0, headers: {}, body: "" };
  const res = {
    writeHead(status, headers = {}) { result.status = status; result.headers = headers; },
    end(text = "") { result.body = String(text); },
  };
  const pending = route(req, res);
  req.emit("data", Buffer.from(body));
  req.emit("end");
  await pending;
  result.json = JSON.parse(result.body || "{}");
  return result;
}

test("panel rejects Origin with a different port or scheme", async () => {
  const panel = makePanel();
  const route = panel.routes.get("/dsh-mihome/api/smart-home/control");
  for (const origin of ["http://127.0.0.1:8766", "https://127.0.0.1:8765"]) {
    const response = await request(route, { origin, body: JSON.stringify({ device_id: "fake" }) });
    assert.equal(response.status, 403, `${origin}: ${response.body}`);
  }
  assert.equal(panel.calls(), 0);
});

test("whole-home power rejects explicit null and non-boolean states without dispatch", async () => {
  const panel = makePanel({ on: true });
  const route = panel.routes.get('/dsh-mihome/api/smart-home/control');
  for (const state of ['off', null, 0, {}, []]) {
    const response = await request(route, {
      origin: 'http://127.0.0.1:8765',
      body: JSON.stringify({ device_id: 'fake', mode: 'power', state }),
    });
    assert.equal(response.status, 400, JSON.stringify({ state, response: response.json }));
  }
  assert.equal(panel.calls(), 0);
});

test("panel preserves a device-control failure instead of returning success", async () => {
  const panel = makePanel({ ok: false, error: "device rejected command" });
  const response = await request(panel.routes.get("/dsh-mihome/api/smart-home/control"), {
    origin: "http://127.0.0.1:8765",
    body: JSON.stringify({ device_id: "fake", mode: "power", state: true }),
  });
  assert.equal(response.json.ok, false);
});

test("panel rejects non-JSON control bodies", async () => {
  const panel = makePanel();
  const response = await request(panel.routes.get("/dsh-mihome/api/smart-home/control"), {
    origin: "http://127.0.0.1:8765",
    contentType: "text/plain",
    body: "{}",
  });
  assert.equal(response.status, 415);
  assert.equal(panel.calls(), 0);
});

test("whole-home panel requests outlive the Python operation budget", async () => {
  const panel = makePanel();
  for (const suffix of ['scan', 'devices', 'device?id=contract%3Aone', 'scenes']) {
    const path = '/dsh-mihome/api/smart-home/' + suffix;
    await request(panel.routes.get(path.split('?')[0]), { method:'GET', path });
  }
  await request(panel.routes.get('/dsh-mihome/api/smart-home/scene'), {
    origin:'http://127.0.0.1:8765', body:JSON.stringify({mode:'run',scene_id:'sample'}),
  });
  for (const [op, , budget] of panel.requests) {
    assert.ok(budget > 180_000, `${op} bridge timeout ${budget} is too short`);
  }
});

test("legacy power rejects explicit non-boolean state without toggling a device", async () => {
  const panel = makePanel({ on: true });
  const route = panel.routes.get('/dsh-mihome/api/control');
  for (const state of ['off', null, 0, {}, []]) {
    const response = await request(route, {
      path:'/dsh-mihome/api/control', origin:'http://127.0.0.1:8765',
      body:JSON.stringify({did:'fake',mode:'power',state}),
    });
    assert.equal(response.status, 400, JSON.stringify({state,response:response.json}));
  }
  assert.equal(panel.calls(), 0);
  const response = await request(route, {
    path:'/dsh-mihome/api/control', origin:'http://127.0.0.1:8765',
    body:JSON.stringify({did:'fake',mode:'power',state:false}),
  });
  assert.equal(response.status, 200);
  assert.deepEqual(panel.requests[0][1], {did:'fake',state:false});
});

test("credential status never returns record payloads", async () => {
  const panel = makePanel();
  const response = await request(panel.routes.get("/dsh-mihome/api/smart-home/credentials"), {
    method: "GET", path: "/dsh-mihome/api/smart-home/credentials",
  });
  assert.deepEqual(response.json, { ok:true, devices:[] });
});

test("credential writes store private payload and return only configured status", async () => {
  let record;
  const credentials = {
    async readRecord() { return record; },
    async modifyRecord(_key, fn) { record = await fn(record); },
    async deleteRecord() { record = undefined; },
  };
  const panel = makePanel({ ok:true }, null, credentials);
  const response = await request(panel.routes.get("/dsh-mihome/api/smart-home/credentials"), {
    path:"/dsh-mihome/api/smart-home/credentials",
    origin:"http://127.0.0.1:8765",
    body:JSON.stringify({ device_id:"tuya:192.0.2.1", mode:"save", payload:{local_key:"not-in-response"} }),
  });
  assert.equal(response.json.ok, true);
  assert.equal(JSON.stringify(response.json).includes("not-in-response"), false);
  assert.equal(record.payload.devices["tuya:192.0.2.1"].local_key, "not-in-response");
});
