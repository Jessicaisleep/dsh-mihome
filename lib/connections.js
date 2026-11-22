// Device connection payloads remain in DSH's Host credential store.
const object = (value) => value !== null && typeof value === 'object' && !Array.isArray(value);

export function createConnections(credentials, key) {
  async function read() {
    const record = await credentials.readRecord(key);
    return record?.kind === 'grant' && object(record.payload?.devices) ? record.payload.devices : {};
  }
  function validate(deviceId, payload) {
    const separator = deviceId.indexOf(":");
    const protocol = deviceId.slice(0, separator);
    const identity = deviceId.slice(separator + 1);
    if (separator < 1 || !/^[a-z][a-z0-9_-]*$/.test(protocol) || !identity || identity.length > 240 || /[\u0000-\u001f\u007f]/.test(identity)) throw new Error('设备 ID 必须是有效的 protocol:identity');
    if (!object(payload)) throw new Error('连接配置必须是 JSON 对象');
    const text = JSON.stringify(payload);
    if (Buffer.byteLength(text) > 48 * 1024) throw new Error('连接配置过大');
    return JSON.parse(text);
  }
  return {
    list: async () => Object.keys(await read()).sort(),
    async save(deviceId, payload) {
      const clean = validate(deviceId, payload);
      await credentials.modifyRecord(key, async (record) => ({
        kind: 'grant', payload: { devices: { ...(record?.payload?.devices ?? {}), [deviceId]: clean } },
      }));
    },
    async delete(deviceId) {
      validate(deviceId, {});
      await credentials.modifyRecord(key, async (record) => {
        const devices = { ...(record?.payload?.devices ?? {}) };
        delete devices[deviceId];
        return { kind: 'grant', payload: { devices } };
      });
    },
    wrap(bridge) {
      return {
        check: () => bridge.check(),
        dispose: () => bridge.dispose(),
        request: async (op, args = {}, timeoutMs, signal) => {
          const payload = op.startsWith('smart_home_') ? { ...args, _connections: await read() } : args;
          return bridge.request(op, payload, timeoutMs, signal);
        },
      };
    },
  };
}
