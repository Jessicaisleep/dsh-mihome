import { test } from 'node:test';
import assert from 'node:assert/strict';
import { registerHooks } from 'node:module';
const bridges = [];
const source = 'data:text/javascript,';
registerHooks({ resolve(specifier, context, next) {
  let code;
  if(specifier==='@deepseek-ai/schemastery') code='export default {object:x=>x,string:()=>({default(){return this}}),number:()=>({default(){return this}})}';
  if(specifier==='@deepseek-ai/dsh-tools') code='export const defineTool = x=>x;';
  if(specifier==='@deepseek-ai/dsh-credentials') code='export const credentialKey=(a,b)=>a+"/"+b;';
  if(specifier==='./bridge.js') code='export const Bridge=globalThis.__testBridge;';
  return code ? {url:source+encodeURIComponent(code),shortCircuit:true} : next(specifier,context);
}});
globalThis.__testBridge = class {
  constructor(){ this.calls=[]; bridges.push(this); }
  check(){return {}}
  dispose(){}
  async request(...args){this.calls.push(args); if(args[3]?.aborted) throw Object.assign(new Error('cancelled'),{name:'AbortError'}); return {devices:[],ok:true};}
};
const { apply } = await import('../lib/index.js');
function setup(){
  bridges.length=0;
  const tools=new Map();
  const ctx={credentials:{readRecord:async()=>({kind:'grant',payload:{devices:{'home_assistant:main':{token:'hidden',base_url:'http://192.0.2.1:8123'}}}})},
    effect(){},systemPrompt:{section(){}},tools:{register(t){tools.set(t.name,t)}},inject(){}};
  apply(ctx,{});
  return {tools,data:bridges[0],login:bridges[1]};
}
test('all smart-home calls carry private current connections and control cancellation', async()=>{
  const {tools,data}=setup();
  const signal=new AbortController().signal;
  await tools.get('smart_home_control').execute({device_id:'home_assistant:light.a',mode:'power',state:true},{signal});
  assert.equal(data.calls[0][1]._connections['home_assistant:main'].token,'hidden');
  assert.equal(data.calls[0][3],signal);
});
test('AbortError identity is preserved at tool boundary',async()=>{
  const {tools}=setup(); const controller=new AbortController();controller.abort();
  await assert.rejects(tools.get('smart_home_devices').execute({}, {signal:controller.signal}), {name:'AbortError'});
});
test('login uses separate channel and scan-backed operations have full budget',async()=>{
  const {tools,data,login}=setup();
  await tools.get('mihome_login').execute({action:'status'},{});
  assert.equal(data.calls.length,0);assert.equal(login.calls.length,1);
  for(const name of ['smart_home_devices','smart_home_scenes','smart_home_device']) {
    await tools.get(name).execute({id:'x',mode:'list'},{});
    assert.ok(data.calls.at(-1)[2]>180000,name);
  }
});
test('model guidance does not promise controls for unverified devices',()=>{
  const tools=new Map();let prompt='';
  const ctx={credentials:{readRecord:async()=>({})},effect(){},systemPrompt:{section(section){prompt=section.text}},tools:{register(tool){tools.set(tool.name,tool)}},inject(){}};
  apply(ctx,{});
  assert.match(prompt,/仅对.*确认.*能力.*控制/);
  assert.doesNotMatch(prompt,/并给出每台设备的协议与控制能力/);
  assert.doesNotMatch(tools.get('smart_home_scan').description,/并给出每台设备的协议与控制能力/);
});
