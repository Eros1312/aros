'use client';
import {useState,useEffect} from 'react';
import {api,json} from '../lib/api';
type Preferences={model:string;models:{id:string;name:string;provider:string;detail:string}[];freellm_detail:string;scope:string};
export default function ModelSelector(){
 const[prefs,setPrefs]=useState<Preferences|null>(null),[model,setModel]=useState(''),[error,setError]=useState(''),[busy,setBusy]=useState(false);
 useEffect(()=>{let active=true;api<Preferences>('/ai/preferences').then(p=>{if(active){setPrefs(p);setModel(p.model)}}).catch(e=>{if(active)setError(e.message)});return()=>{active=false}},[]);
 async function save(){setBusy(true);setError('');try{const p=await api<Preferences>('/ai/preferences',json('PATCH',{model}));setPrefs(p);setModel(p.model)}catch(e){setError((e as Error).message)}finally{setBusy(false)}}
 return <section className="setting-panel"><h2>Modelo de todo Aros</h2><p>Tu elección se aplica al asistente y a las nuevas consultas de los agentes. Las opciones dependen de los proveedores configurados por el operador de tu servidor. El resumen local funciona sin proveedor de IA.</p>{prefs&&<><label className="field">Modelo<select value={model} onChange={e=>setModel(e.target.value)}>{!prefs.models.some(m=>m.id===prefs.model)&&<option value={prefs.model}>{prefs.model} · No disponible ahora</option>}{prefs.models.map(m=><option value={m.id} key={m.id}>{m.name}</option>)}</select></label><p className="muted">{prefs.models.find(m=>m.id===model)?.detail}</p><button className="primary" onClick={()=>void save()} disabled={busy||model===prefs.model}>{busy?'Guardando…':'Usar en todo Aros'}</button><p className="muted">{prefs.freellm_detail}</p><p className="muted">{prefs.scope} Si falla el modelo elegido, verás el error; no se elige otro automáticamente.</p></>}{error&&<p role="alert">{error}</p>}</section>
}
