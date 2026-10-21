import type {Item} from './api';

export type TechMotif='intelligence'|'network'|'code';
export function technologyURL(item:Item):string|null {
 const value=item.data.source_url??item.data.url;
 if(typeof value!=='string')return null;
 try{const url=new URL(value);return ['https:','http:'].includes(url.protocol)&&!url.username&&!url.password?url.href:null}catch{return null}
}
export function technologySource(item:Item):{label:string;host:string|null} {
 const link=technologyURL(item);
 if(link){const host=new URL(link).hostname.replace(/^www\./,'');const names:Record<string,string>={'huggingface.co':'Hugging Face','github.com':'GitHub','openai.com':'OpenAI','anthropic.com':'Anthropic','blog.google':'Google'};return {label:names[host]||host,host}}
 return {label:item.source==='rss'?'Fuente RSS':item.source==='upload'?'Archivo importado':item.source==='agent-conversation'?'Asistente de Aros':'Tu espacio',host:null};
}
export function technologyMotif(item:Item):TechMotif {
 if(/(github|release|code|coding|kernel|training|gradio|pytorch)/i.test(item.title+' '+(technologyURL(item)||'')))return 'code';
 if(/(robot|vision|image|multimodal|embedding|earth)/i.test(item.title))return 'network';
 return 'intelligence';
}
export function technologyOrder(items:Item[]):Item[]{
 return items.slice().sort((a,b)=>{
  const technical=(value:Item)=>/^release-publish\//i.test(value.title)?1:0;
  return technical(a)-technical(b)||(Date.parse(b.created_at)||0)-(Date.parse(a.created_at)||0)||a.id.localeCompare(b.id);
 });
}
export function technologyFeatured(items:Item[]):Item|null {
 const news=technologyOrder(items).filter(i=>i.kind==='news'&&!['archived','cancelled'].includes(i.status));
 return news.find(i=>!/^release-publish\//i.test(i.title))||news[0]||null;
}
