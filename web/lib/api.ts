export type Area = 'learning'|'events'|'technology'|'resources'|'server'|'personal';
export type AgentExecution={capability:string;state:string;scope?:{project_id?:string;areas?:string[];item_ids?:string[]};command?:{project_id?:string;thread_id?:string};state_version?:number;steps:{id:string;tool:string;status:string;summary:string;artifacts?:string[];at:string}[];artifacts?:{id:string;version:number;title:string;area:Area;kind:string;verified:boolean;effect?:string;operation?:string}[];budget?:{model_calls:number;tool_calls:number;iteration_limit:number;deadline_at:string};resources_unavailable?:boolean;objectives?:{id:string;text:string;tool:string;status:'pending'|'completed';artifact_ids:string[]}[];outcome?:'completed'|'partial';usage?:{calls?:{decision:string|number;prompt_tokens?:number;completion_tokens?:number;total_tokens?:number;model?:string;reported?:boolean}[]};cost_status?:string;pending_objectives?:string[]};
export type Item = {id:string;kind:string;area:Area;title:string;description:string;status:string;priority:string;due_at:string|null;data:Record<string,unknown>;source:string;is_demo:boolean;version:number;created_at:string;updated_at:string};
export type InboxItem = {id:string;item_id:string|null;title:string;reason:string;area:Area;state:string;snoozed_until:string|null;created_at:string};
export type Connector = {id:string;name:string;status:string;scopes:string[];last_sync:string|null;error:string|null;implemented:boolean;details:Record<string,unknown>};
export type Automation = {id:string;title:string;kind:string;enabled:boolean;interval_minutes:number;next_run:string};
export type Message = {id:string;role:string;text:string;sources:{id:string;title:string;area:Area;kind:string}[];mode:string;created_at:string};
export type Dashboard = {demo:boolean;attention:{id:string;title:string;area:Area;reason:string}[];counts:{tasks:number;documents:number};events:Item[];summaries:{area:Area;headline:string|null;detail:string|null;footnote:string|null;rows:{id:string;title:string;subtitle:string}[]}[]};
export type User = {id:string;email:string;ai_consent:boolean;demo:boolean};
export const areaNames:Record<Area,string>={learning:'Aprendizaje',events:'Eventos',technology:'IA y Tecnología',resources:'Recursos',server:'Servidor',personal:'Personal'};
export const kindNames:Record<string,string>={task:'Tarea',event:'Evento',document:'Documento',email:'Correo',order:'Pedido',link:'Enlace',news:'Noticia',note:'Nota',project:'Proyecto',service:'Servicio',reminder:'Recordatorio',memory:'Memoria',contact:'Contacto',expense:'Gasto'};
export const statusNames:Record<string,string>={pending:'Pendiente',active:'Activo',done:'Completado',archived:'Archivado',in_transit:'En camino',delivered:'Entregado',cancelled:'Cancelado'};
export async function api<T>(path:string, options:RequestInit={}):Promise<T>{
 const form=options.body instanceof FormData;
 const r=await fetch('/api/v1'+path,{...options,credentials:'same-origin',headers:{...(!form?{'Content-Type':'application/json'}:{}),'X-Aros-Client':'web',...options.headers},cache:'no-store'});
 if(!r.ok){ let data;try{data=await r.json()}catch{if(!options.signal?.aborted)console.warn('Aros HTTP non-JSON',path.split('?')[0],r.status);data={detail:'No se ha podido contactar con Aros'}}; if(r.status===401&&path!='/auth/login'&&!options.signal?.aborted) window.dispatchEvent(new Event('aros:logout')); throw Object.assign(new Error(typeof data.detail==='string'?data.detail:r.status===422?'Revisa los campos del formulario':'No se ha podido completar la acción'),{status:r.status}); }
 return r.json();
}
export const json=(method:string,body:unknown):RequestInit=>({method,body:JSON.stringify(body)});
export async function confirmToken(action:string,payload:unknown){return (await api<{token:string}>('/confirmations',json('POST',{action,payload}))).token;}
export function formatDate(date:string|null,options:Intl.DateTimeFormatOptions={day:'numeric',month:'short',hour:'2-digit',minute:'2-digit'}) {if(!date)return 'Sin fecha';const d=new Date(date); return isNaN(d.getTime())?'Sin fecha':d.toLocaleString('es-ES',{timeZone:'Europe/Madrid',...options});}
