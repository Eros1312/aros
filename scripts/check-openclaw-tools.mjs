import assert from 'node:assert/strict';
import plugin from '../deploy/openclaw/aros-tools/index.js';
const factories=[];
plugin.register({registerTool(factory){factories.push(factory)}});
assert.equal(factories.length,2);
for(const factory of factories){
 const tool=factory({sessionKey:'agent:aros:openai:isolated-user'});
 await assert.rejects(()=>tool.execute('call',{}),/solo están disponibles/);
}
for(const factory of factories)await assert.rejects(()=>factory({sessionKey:'agent:aros:main'}).execute('call',{}),/solo están disponibles/);
for(const sessionKey of ['agent:aros:whatsapp:direct:owner','agent:personal:whatsapp:direct:+34600000000','agent:aros:whatsapp:group:+34600000000','agent:aros:whatsapp:direct:+34600000000:extra'])for(const factory of factories)await assert.rejects(()=>factory({sessionKey}).execute('call',{}),/solo están disponibles/);
const tools=factories.map(f=>f({sessionKey:'agent:aros:whatsapp:direct:+34600000000'}));
assert.deepEqual(tools.map(t=>t.name),['aros_resources','aros_prepare_action']);
let captured;
process.env.OPENCLAW_GATEWAY_TOKEN='private-test-token';
globalThis.fetch=async(url,options)=>{captured={url,options};return {ok:true,json:async()=>({status:'awaiting_review'})}};
await tools[1].execute('call',{type:'item.create',payload:{kind:'task',area:'personal',title:'Test'}});
assert.equal(captured.url,'http://api:8000/api/v1/internal/openclaw/prepare');
assert.equal(captured.options.headers.Authorization,'Bearer private-test-token');
assert.equal(captured.options.headers['X-Aros-WhatsApp-Sender'],'+34600000000');
assert.equal(JSON.parse(captured.options.body).type,'item.create');
console.log('PASS: WhatsApp-only tools, private backend transport and reviewable proposals');
