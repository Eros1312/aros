'use client';

import {useCallback,useEffect,useId,useRef,useState,type ChangeEvent,type FormEvent} from 'react';
import {Box,Check,Download,ExternalLink,ImagePlus,LoaderCircle,LockKeyhole,RefreshCw,X} from 'lucide-react';
import {api,formatDate,json} from '../lib/api';
import './printing-generator.css';

type Parameters={width_mm:number;depth_mm:number;height_mm:number;wall_mm:number;base_mm:number;columns:number;rows:number};
type Evidence={sha256:string;bytes:number;triangles:number;volume_mm3:number;dimensions_mm:number[];watertight:boolean;physical_print_verified:boolean};
type GenerationJob={id:string;title:string;state:string;parameters:Parameters;created_at:string;finished_at:string|null;error:string|null;evidence:Evidence|null;download_url:string|null;manifest_url:string|null};
type GenerationCapabilities={engine:string;available:boolean;detail:string;parametric?:{available:boolean;worker_state:string;limits:{max_pending:number;max_daily:number;max_saved:number}}};
const defaults:Parameters={width_mm:120,depth_mm:80,height_mm:35,wall_mm:2,base_mm:2,columns:2,rows:1};
const fields:{key:keyof Parameters;label:string;min:number;max:number;step:number}[]=[
 {key:'width_mm',label:'Ancho (mm)',min:20,max:180,step:0.1}, {key:'depth_mm',label:'Fondo (mm)',min:20,max:180,step:0.1},
 {key:'height_mm',label:'Alto (mm)',min:10,max:100,step:0.1}, {key:'wall_mm',label:'Paredes (mm)',min:1.2,max:6,step:0.1},
 {key:'base_mm',label:'Base (mm)',min:1.2,max:8,step:0.1}, {key:'columns',label:'Columnas',min:1,max:6,step:1}, {key:'rows',label:'Filas',min:1,max:6,step:1},
];
const states:Record<string,string>={queued:'En cola',generating:'Generando',verified:'STL verificado',failed:'No completado',cancelled:'Cancelado'};
const active=(job:GenerationJob)=>job.state==='queued'||job.state==='generating';
const safeFileUrl=(job:GenerationJob,kind:'file'|'manifest')=>'/api/v1/printing/generation/jobs/'+encodeURIComponent(job.id)+'/'+kind;

function OrganizerPreview({parameters:p}:{parameters:Parameters}){
 const w=Math.max(20,Math.min(180,p.width_mm||20)),d=Math.max(20,Math.min(180,p.depth_mm||20));
 const columns=Math.max(1,Math.min(6,Math.round(p.columns||1))),rows=Math.max(1,Math.min(6,Math.round(p.rows||1)));
 const wall=Math.max(1.2,Math.min(6,p.wall_mm||2)),gapW=Math.max(1,(w-(columns+1)*wall)/columns),gapD=Math.max(1,(d-(rows+1)*wall)/rows);
 return <figure className="organizer-preview"><svg viewBox={'-14 -10 '+(w+28)+' '+(d+25)} role="img" aria-label={'Vista superior orientativa de '+columns+' columnas y '+rows+' filas'}><rect x="0" y="0" width={w} height={d} fill="#587965"/>{Array.from({length:columns*rows},(_,index)=><rect key={index} x={wall+(index%columns)*(gapW+wall)} y={wall+Math.floor(index/columns)*(gapD+wall)} width={gapW} height={gapD} fill="#e7f0e9"/>)}<path d={'M0 '+(d+5)+' v3 H'+w+' v-3'} fill="none" stroke="#52715c" strokeWidth="0.5"/></svg><figcaption>Vista superior orientativa · {w} × {d} mm<br/><span>Organizador abierto, con base y divisiones.</span></figcaption></figure>;
}

export default function PrintingGenerator(){
 const titleId=useId(),inputId=useId(),helpId=useId();
 const [capabilities,setCapabilities]=useState<GenerationCapabilities|null>(null),[jobs,setJobs]=useState<GenerationJob[]>([]);
 const [parameters,setParameters]=useState<Parameters>(defaults),[worker,setWorker]=useState('unknown');
 const [loading,setLoading]=useState(true),[busy,setBusy]=useState(false),[error,setError]=useState(''),[notice,setNotice]=useState('');
 const [file,setFile]=useState<File|null>(null),[preview,setPreview]=useState(''),[imageError,setImageError]=useState('');
 const request=useRef<{fingerprint:string;key:string}|null>(null),mounted=useRef(false),refreshing=useRef<AbortController|null>(null);
 const load=useCallback(async(signal?:AbortSignal)=>{
  refreshing.current?.abort();const controller=new AbortController();refreshing.current=controller;
  const abort=()=>controller.abort();signal?.addEventListener('abort',abort,{once:true});
  if(signal?.aborted)controller.abort();
  try{
   const [caps,result]=await Promise.all([api<GenerationCapabilities>('/printing/generation/capabilities',{signal:controller.signal}),api<{items:GenerationJob[];worker_state:string}>('/printing/generation/jobs',{signal:controller.signal})]);
   if(mounted.current&&!controller.signal.aborted){setCapabilities(caps);setJobs(result.items);setWorker(result.worker_state);setError('')}
  }catch(e){if(mounted.current&&!controller.signal.aborted)setError((e as Error).message)}
  finally{signal?.removeEventListener('abort',abort);if(refreshing.current===controller)refreshing.current=null;if(mounted.current&&!controller.signal.aborted)setLoading(false)}
 },[]);
 useEffect(()=>{mounted.current=true;const controller=new AbortController();void load(controller.signal);return()=>{mounted.current=false;controller.abort();refreshing.current?.abort()}},[load]);
 const hasActive=jobs.some(active);
 useEffect(()=>{
  if(!hasActive)return;
  const timer=window.setInterval(()=>{if(document.visibilityState==='visible'&&!refreshing.current)void load()},4000);
  const focus=()=>void load();window.addEventListener('focus',focus);
  return()=>{window.clearInterval(timer);window.removeEventListener('focus',focus)};
 },[hasActive,load]);
 useEffect(()=>{if(!file){setPreview('');return}const url=URL.createObjectURL(file);setPreview(url);return()=>URL.revokeObjectURL(url)},[file]);
 async function create(event:FormEvent<HTMLFormElement>){
  event.preventDefault();if(busy)return;setError('');setNotice('');
  if((parameters.width_mm-(parameters.columns+1)*parameters.wall_mm)/parameters.columns<5||(parameters.depth_mm-(parameters.rows+1)*parameters.wall_mm)/parameters.rows<5||parameters.height_mm-parameters.base_mm<5){setError('Cada hueco debe medir al menos 5 mm de ancho, fondo y profundidad. Ajusta divisiones o paredes.');return}
  const fingerprint=JSON.stringify(parameters);
  if(!request.current||request.current.fingerprint!==fingerprint)request.current={fingerprint,key:crypto.randomUUID()};
  setBusy(true);
  try{
   const job=await api<GenerationJob>('/printing/generation/jobs',json('POST',{parameters,idempotency_key:request.current.key}));
   if(mounted.current){setJobs(old=>[job,...old.filter(row=>row.id!==job.id)]);setNotice('Encargo guardado. Puedes cerrar esta pantalla y volver para descargarlo.');request.current=null;void load()}
  }catch(e){if(mounted.current)setError((e as Error).message)}finally{if(mounted.current)setBusy(false)}
 }
 async function cancel(job:GenerationJob){
  if(busy)return;setBusy(true);setError('');
  try{const result=await api<GenerationJob>('/printing/generation/jobs/'+encodeURIComponent(job.id)+'/cancel',json('POST',{}));if(mounted.current){setJobs(old=>old.map(row=>row.id===result.id?result:row));setNotice('Encargo cancelado.')}}catch(e){if(mounted.current)setError((e as Error).message)}finally{if(mounted.current)setBusy(false)}
 }
 function chooseImage(event:ChangeEvent<HTMLInputElement>){
  const selected=event.target.files?.[0];event.target.value='';if(!selected)return;
  if(!['image/png','image/jpeg','image/webp'].includes(selected.type)){setImageError('Elige una imagen PNG, JPEG o WebP.');return}
  if(selected.size===0||selected.size>10*1024*1024){setImageError('La imagen debe ocupar entre 1 byte y 10 MB.');return}
  setImageError('');setFile(selected);
 }
 return <section className="print-generator" aria-labelledby={titleId}>
  <div className="print-generator-heading"><div><span className="print-generator-kicker"><Box size={16} aria-hidden="true"/>Crea con tus medidas</span><h2 id={titleId}>Un organizador a tu medida.</h2><p>Elige el tamaño y las divisiones. Aros prepara un STL en el servidor y comprueba su geometría antes de ofrecerte la descarga.</p></div><span className="print-generator-badge">Generación en CPU</span></div>
  <div className="organizer-body"><OrganizerPreview parameters={parameters}/><form onSubmit={event=>void create(event)}>
   <div className="organizer-fields">{fields.map(field=><label key={field.key}>{field.label}<input required type="number" min={field.min} max={field.max} step={field.step} value={parameters[field.key]||''} onChange={event=>setParameters(old=>({...old,[field.key]:Number(event.target.value)}))}/></label>)}</div>
   <p className="print-generator-note">Hasta 180 × 180 × 100 mm y 36 compartimentos. Huecos de al menos 5 mm. El STL se importa en milímetros; revisa medidas y laminado antes de imprimir.</p>
   <button type="submit" className="print-generator-generate" disabled={busy||loading||!capabilities?.parametric?.available}>{busy?<LoaderCircle size={18} className="print-spin" aria-hidden="true"/>:<Box size={18} aria-hidden="true"/>}{busy?'Guardando…':'Crear organizador STL'}</button>
   <p className="print-generator-local">{capabilities?.parametric?.limits?.max_daily||20} encargos cada 24 horas · {capabilities?.parametric?.limits?.max_pending||3} en cola · {capabilities?.parametric?.limits?.max_saved||200} guardados. No necesita IA externa.</p>
  </form></div>
  {error&&<p className="print-generator-error" role="alert">{error}</p>}<p className="organizer-notice" role="status">{notice}</p>
  <div className="organizer-jobs-heading"><h3>Tus organizadores</h3><button type="button" className="print-button" disabled={loading} onClick={()=>void load()} aria-label="Actualizar encargos de organizadores"><RefreshCw size={16} aria-hidden="true"/>Actualizar</button></div>
  {worker!=='online'&&<p className="print-generator-note" role="status">{worker==='unknown'?'Aún no hay una señal reciente del servicio de generación.':'El servicio de generación no está respondiendo con normalidad.'} Los encargos aceptados quedan guardados y esperan a que el servicio esté disponible.</p>}
  {loading?<p role="status">Consultando tus encargos…</p>:jobs.length===0?<p className="print-generator-note">Aquí aparecerán tus encargos y sus descargas.</p>:<ul className="organizer-jobs">{jobs.map(job=><li key={job.id}><div className="organizer-job-title"><strong>{job.title}</strong><span className={'organizer-job-state state-'+job.state}>{job.state==='verified'?<Check size={14} aria-hidden="true"/>:active(job)?<LoaderCircle size={14} className="print-spin" aria-hidden="true"/>:null}{states[job.state]||'Estado no disponible'}</span></div><p>{formatDate(job.created_at)}{job.evidence?' · '+job.evidence.triangles+' triángulos · '+(job.evidence.bytes/1024).toLocaleString('es-ES',{maximumFractionDigits:1})+' KB':''}</p>{job.error&&<p className="print-generator-error">{job.error}</p>}{job.state==='verified'&&job.download_url&&job.evidence&&<><div className="organizer-job-actions"><a className="print-button print-button-primary" href={safeFileUrl(job,'file')} download><Download size={16} aria-hidden="true"/>Descargar STL</a><a className="print-button" href={safeFileUrl(job,'manifest')} download>Informe de geometría</a></div><p className="print-generator-note">Malla cerrada y conectada, con dimensiones comprobadas. Impresión física pendiente.</p><details className="organizer-evidence"><summary>Huella del STL</summary><code>{job.evidence.sha256}</code></details></>}{active(job)&&<div className="organizer-job-actions"><span>Continúa en el servidor aunque cierres la pantalla.</span><button type="button" className="print-button" disabled={busy} onClick={()=>void cancel(job)}>Cancelar</button></div>}</li>)}</ul>}
  <details className="organizer-image-mode"><summary><ImagePlus size={17} aria-hidden="true"/>Imagen → 3D <span><LockKeyhole size={13} aria-hidden="true"/>Motor pendiente</span></summary><p>{capabilities?.detail||'La conversión de imágenes necesita conectar un motor Linux con GPU NVIDIA. Este VPS no dispone de esa GPU.'}</p><div className="print-generator-body"><div className="print-generator-reference"><div className="print-generator-preview">{preview?<img src={preview} alt={'Referencia local: '+file?.name} onError={()=>{setImageError('No se puede previsualizar esta imagen.');setFile(null)}}/>:<div><ImagePlus size={32} aria-hidden="true"/><strong>Previsualiza una referencia</strong><span>La imagen permanece en este dispositivo.</span></div>}</div><div className="print-generator-image-actions"><label className="print-generator-choose" htmlFor={inputId}><ImagePlus size={17} aria-hidden="true"/>{file?'Cambiar imagen':'Elegir imagen'}<input id={inputId} type="file" accept="image/png,image/jpeg,image/webp" onChange={chooseImage} aria-describedby={helpId}/></label>{file&&<button type="button" className="print-generator-remove" aria-label="Quitar imagen de referencia" onClick={()=>{setFile(null);setImageError('')}}><X size={18} aria-hidden="true"/></button>}</div><p id={helpId} className="print-generator-local">PNG, JPEG o WebP · hasta 10 MB. Elegir una imagen no la sube ni crea un encargo.</p>{imageError&&<p className="print-generator-error" role="alert">{imageError}</p>}</div><div className="print-generator-info"><h3>{capabilities?.engine||'TRELLIS.2'}</h3><p>Requiere Linux y GPU NVIDIA con al menos 24 GB de VRAM. No hay un motor conectado ni una salida generada desde tu imagen.</p><button type="button" className="print-generator-generate" disabled>Conversión de imagen no disponible</button><a className="print-generator-docs" href="https://github.com/microsoft/TRELLIS.2" target="_blank" rel="noopener noreferrer">Proyecto oficial<ExternalLink size={14} aria-hidden="true"/></a></div></div></details>
 </section>;
}
