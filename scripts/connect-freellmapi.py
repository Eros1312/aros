#!/usr/bin/env python3
"""Run on the VPS after FreeLLMAPI starts. Credentials stay private."""
from pathlib import Path
import subprocess,json,re,os
root=Path(__file__).resolve().parents[1];env=root/'deploy/.env'
text=env.read_text();values=dict(line.split('=',1) for line in text.splitlines() if '=' in line and not line.startswith('#'))
password=values['FREELLM_ADMIN_PASSWORD']
script="""
const {initDb,getUnifiedApiKey} = await import('./server/dist/db/index.js');
initDb();
const key=getUnifiedApiKey();
const status=await fetch('http://127.0.0.1:3001/api/auth/status').then(r=>r.json());
if(status.needsSetup){
 const result=await fetch('http://127.0.0.1:3001/api/auth/setup',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({email:'aros@localhost.local',password:PASSWORD})});
 if(!result.ok)throw new Error('Private dashboard setup failed: '+result.status);
}
const data=await fetch('http://127.0.0.1:3001/v1/models',{headers:{Authorization:'Bearer '+key}}).then(r=>r.json());
console.log('AROS_CONFIG:'+JSON.stringify({token:key,models:data.data}));
""".replace('PASSWORD',json.dumps(password))
result=subprocess.run(['docker','compose','--profile','providers','exec','-T','freellmapi','node','--input-type=module','-'],cwd=root/'deploy',input="try {\n"+script+"\n} catch(error) { console.error(\"BOOTSTRAP_ERROR:\"+error.message);process.exit(1) }",text=True,capture_output=True)
if result.returncode:
    reason=next((line.split("BOOTSTRAP_ERROR:",1)[1] for line in result.stderr.splitlines() if line.startswith("BOOTSTRAP_ERROR:")),"FreeLLMAPI bootstrap failed; check service health")
    raise RuntimeError(reason)
line=next(line for line in result.stdout.splitlines() if line.startswith('AROS_CONFIG:'));data=json.loads(line[len('AROS_CONFIG:'):])
models=[model for model in data['models'] if isinstance(model.get('id'),str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_./:@-]{0,190}',model['id']) and not model['id'].startswith(('auto','fusion','claude-'))]
updates={'FREELLM_TOKEN':data['token'],'FREELLM_MODELS':','.join(model['id'] for model in models)}
lines=[line for line in text.splitlines() if not any(line.startswith(name+'=') for name in updates)]
env.write_text('\n'.join(lines)+'\n'+''.join(f'{name}={value}\n' for name,value in updates.items()));os.chmod(env,0o600)
# Merge, preserving the account and agent configuration in the actual gateway volume.
result=subprocess.run(['docker','compose','--profile','providers','exec','-T','openclaw','cat','/home/node/.openclaw/openclaw.json'],cwd=root/'deploy',capture_output=True,text=True,check=True)
config=json.loads(result.stdout)
config.setdefault('models',{}).setdefault('providers',{})['freellmapi']={'baseUrl':'http://freellmapi:3001/v1','apiKey':'${FREELLM_API_KEY}','api':'openai-completions','models':[{'id':model['id'],'name':model.get('name') or model['id'],'reasoning':False,'input':['text']} for model in models]}
allowed=[values['OPENCLAW_MODEL']]+['freellmapi/'+model['id'] for model in models]
config['agents']['defaults']['modelPolicy']={'allow':allowed}
for ref in allowed: config['agents']['defaults']['models'].setdefault(ref,{'agentRuntime':{'id':'openclaw'}})
for agent in config['agents']['entries'].values():agent['modelPolicy']={'allow':allowed}
write='umask 077; cat > /home/node/.openclaw/openclaw.json'
subprocess.run(['docker','compose','--profile','providers','exec','-T','openclaw','sh','-c',write],cwd=root/'deploy',input=json.dumps(config,indent=2),text=True,check=True)
# Never copy gateway state or credentials back into tracked source files.
print(f'FreeLLMAPI connected privately; {len(models)} model IDs registered. No provider keys or prompts were sent.')
