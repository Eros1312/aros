import type {Item} from './api';

/** Keep old deep links working while Today has one authoritative destination. */
export function canonicalView(view:string){return view==='overview'?'today':view;}

export function parseWorkspaceRoute(raw:string){const match=/^projects\/([a-zA-Z0-9-]{8,80})$/.exec(raw);return match?{view:'projects',projectId:match[1]}:{view:canonicalView(raw),projectId:null};}

/** Archived resources remain inspectable through the explicit Archive filter. */
export function visibleRecords(items:Item[]){return items.filter(item=>!['archived','cancelled','canceled','withdrawn'].includes(item.status));}

/** IDs are scope, never document text or permission to change an external source. */
export function contextPayload(item:Item|null,project:Item|null=null){
 if(project)return {project_id:project.id,thread_id:project.id,area:project.area,item_ids:[(item||project).id]};
 return item?{item_ids:[item.id],area:item.area}:{};
}

export function conversationPath(projectId:string|null,threadId=projectId||'chat'){
 const params=new URLSearchParams({thread_id:threadId});if(projectId)params.set('project_id',projectId);
 return '/chat?'+params;
}

export function belongsToProject(item:Item,project:Item){return item.area===project.area&&(item.id===project.id||linkedProjectIds(item).includes(project.id));}

export function linkedProjectIds(item:Item){
 const values=[item.data.project_id,...(Array.isArray(item.data.project_ids)?item.data.project_ids:[])];
 return Array.from(new Set(values.filter((value):value is string=>typeof value==='string'&&!!value)));
}
