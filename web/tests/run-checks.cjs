/* Compile into temporary storage, run the requested fixture checks, clean up. */
const fs=require('node:fs');
const os=require('node:os');
const path=require('node:path');
const {spawnSync}=require('node:child_process');
const root=path.resolve(__dirname,'..'),out=fs.mkdtempSync(path.join(os.tmpdir(),'aros-checks-'));
const mode=process.argv[2]||'unit';
function run(command,args,env={}){
 const result=spawnSync(command,args,{cwd:root,stdio:'inherit',env:{...process.env,...env}});
 if(result.error)throw result.error;
 if(result.status!==0)throw Error(command+' exited with '+result.status);
}
try{
 const inputs=mode==='dom'?['app/page.tsx','tests/styles.d.ts']:['lib/workflow.ts','app/voice-controller.ts'];
 run(path.join(root,'node_modules','.bin','tsc'),['--ignoreConfig','--target','ES2022','--lib','ESNext,DOM,DOM.Iterable','--module','nodenext','--moduleResolution','nodenext','--jsx','react-jsx','--esModuleInterop','--skipLibCheck','--outDir',out,...inputs]);
 if(mode==='dom'){
  for(const file of fs.readdirSync(path.join(root,'app')).filter(name=>name.endsWith('.css')))fs.copyFileSync(path.join(root,'app',file),path.join(out,'app',file));
  run(process.execPath,['tests/workflow.dom.cjs',out],{NODE_PATH:path.join(root,'node_modules')});
 }else{
  run(process.execPath,['lib/workflow.checks.cjs',path.join(out,'lib/workflow.js')]);
  run(process.execPath,['app/voice-controller.checks.cjs',path.join(out,'app/voice-controller.js')]);
 }
}catch(error){console.error(error.message);process.exitCode=1}
finally{fs.rmSync(out,{recursive:true,force:true})}
