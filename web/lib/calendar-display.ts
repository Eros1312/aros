export type CalendarEvent = {id:string;title:string;area:string;start:string;end:string;all_day:boolean;estimated_duration:boolean;location?:string;category?:string;source?:string;is_demo?:boolean};
export type CalendarPlacement = {event:CalendarEvent;startMinute:number;endMinute:number;column:number;columns:number};
// Date keys are civil dates, independent from the machine's UTC offset.
function civilDate(day:string){const [y,m,d]=day.split('-').map(Number);return new Date(Date.UTC(y,m-1,d,12));}
function civilKey(date:Date){return `${date.getUTCFullYear()}-${String(date.getUTCMonth()+1).padStart(2,'0')}-${String(date.getUTCDate()).padStart(2,'0')}`;}
export function shiftCalendarDay(day:string,offset:number){const date=civilDate(day);date.setUTCDate(date.getUTCDate()+offset);return civilKey(date);}
export function shiftCalendarMonth(day:string,offset:number){const date=civilDate(day),original=date.getUTCDate();date.setUTCDate(1);date.setUTCMonth(date.getUTCMonth()+offset);const last=new Date(Date.UTC(date.getUTCFullYear(),date.getUTCMonth()+1,0)).getUTCDate();date.setUTCDate(Math.min(original,last));return civilKey(date);}
export function calendarWeekDays(day:string){const date=civilDate(day),monday=shiftCalendarDay(day,-((date.getUTCDay()+6)%7));return Array.from({length:7},(_,i)=>shiftCalendarDay(monday,i));}
export function calendarMonthDays(day:string){const monday=calendarWeekDays(day.slice(0,8)+'01')[0];return Array.from({length:42},(_,i)=>shiftCalendarDay(monday,i));}
const dayFormats=new Map<string,Intl.DateTimeFormat>();
export function calendarDayInZone(date:Date,zone:string){let format=dayFormats.get(zone);if(!format){format=new Intl.DateTimeFormat('en-US',{year:'numeric',month:'2-digit',day:'2-digit',timeZone:zone});dayFormats.set(zone,format);}const parts=format.formatToParts(date);const part=(type:string)=>parts.find(p=>p.type===type)?.value;return `${part('year')}-${part('month')}-${part('day')}`;}
export function calendarDayLabel(day:string,options:Intl.DateTimeFormatOptions={weekday:'long',day:'numeric',month:'long'}){return civilDate(day).toLocaleDateString('es-ES',{...options,timeZone:'UTC'});}
export function eventTouchesDay(event:CalendarEvent,day:string,zone:string){const start=new Date(event.start),end=new Date(event.end);if(!Number.isFinite(start.getTime())||!Number.isFinite(end.getTime())||end<=start)return false;return calendarDayInZone(start,zone)<=day&&calendarDayInZone(new Date(end.getTime()-1),zone)>=day;}
export function calendarEventTime(event:CalendarEvent,zone:string){
 if(event.all_day)return 'Todo el día';const start=new Date(event.start),end=new Date(event.end);if(!Number.isFinite(start.getTime())||!Number.isFinite(end.getTime()))return 'Hora no disponible';
 const date=new Intl.DateTimeFormat('es-ES',{day:'numeric',month:'short',timeZone:zone}),offset=new Intl.DateTimeFormat('en-US',{timeZone:zone,timeZoneName:'shortOffset'});const zonePart=(value:Date)=>offset.formatToParts(value).find(part=>part.type==='timeZoneName')?.value;
 const clock=new Intl.DateTimeFormat('es-ES',{hour:'2-digit',minute:'2-digit',hourCycle:'h23',timeZone:zone,...(zonePart(start)!==zonePart(end)?{timeZoneName:'short' as const}:{})});
 return calendarDayInZone(start,zone)===calendarDayInZone(end,zone)?`${clock.format(start)} – ${clock.format(end)}`:`${date.format(start)} ${clock.format(start)} – ${date.format(end)} ${clock.format(end)}`;
}
export function calendarMinuteInZone(date:Date,zone:string){const parts=new Intl.DateTimeFormat('en-US',{hour:'2-digit',minute:'2-digit',hourCycle:'h23',timeZone:zone}).formatToParts(date);return Number(parts.find(p=>p.type==='hour')?.value)*60+Number(parts.find(p=>p.type==='minute')?.value);}
export function layoutCalendarEvents(events:CalendarEvent[],day:string,zone:string):CalendarPlacement[]{
 const segments=events.filter(event=>!event.all_day&&eventTouchesDay(event,day,zone)).map(event=>{const start=new Date(event.start),end=new Date(event.end);const startMinute=calendarDayInZone(start,zone)<day?0:calendarMinuteInZone(start,zone);let endMinute=calendarDayInZone(end,zone)>day?1440:calendarMinuteInZone(end,zone);
  // The autumn repeated hour can move the civil clock backwards. Keep a visible
  // segment; the time label exposes the two different UTC offsets.
  if(endMinute<=startMinute)endMinute=Math.min(1440,startMinute+Math.max(15,Math.min(60,(end.getTime()-start.getTime())/60000)));
  return {event,startMinute,endMinute,column:0,columns:1};}).sort((a,b)=>a.startMinute-b.startMinute||b.endMinute-a.endMinute||a.event.id.localeCompare(b.event.id));
 let cluster:CalendarPlacement[]=[],clusterEnd=-1;
 function place(){const columns:number[]=[];for(const segment of cluster){let column=columns.findIndex(end=>end<=segment.startMinute);if(column<0)column=columns.length;columns[column]=segment.endMinute;segment.column=column;}for(const segment of cluster)segment.columns=columns.length;}
 for(const segment of segments){if(segment.startMinute>=clusterEnd&&cluster.length){place();cluster=[];clusterEnd=-1;}cluster.push(segment);clusterEnd=Math.max(clusterEnd,segment.endMinute);}place();return segments;
}
