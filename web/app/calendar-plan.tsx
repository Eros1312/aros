'use client';
import {useEffect,useMemo,useState,type CSSProperties,type KeyboardEvent} from 'react';
import {AlertCircle,ArrowUpRight,CalendarDays,CalendarCheck,ChevronDown,ChevronLeft,ChevronRight,Clock,GraduationCap,MapPin,RefreshCw,Search,Sun,Check,BookOpen} from 'lucide-react';
import {api,type Area} from '../lib/api';
import {calendarDayInZone,calendarDayLabel,calendarEventTime,calendarMinuteInZone,calendarMonthDays,calendarWeekDays,eventTouchesDay,layoutCalendarEvents,shiftCalendarDay,shiftCalendarMonth,type CalendarEvent} from '../lib/calendar-display';
import './calendar-plan.css';

type Plan={date:string;timezone:string;events:CalendarEvent[];conflicts:{ids:string[];start:string;end:string}[];free_slots:{start:string;end:string;minutes:number}[];note:string};
type Range={start:string;end:string;timezone:string;events:CalendarEvent[]};
type View='month'|'week'|'day';
const views:[View,string][]=[['month','Mes'],['week','Semana'],['day','Día']];
const areaList:Area[]=['university','prisma','personal','technology','printing','server'];
const shortAreas:Record<Area,string>={university:'Universidad',prisma:'PRISMA',personal:'Personal',technology:'IA',printing:'3D',server:'Servidor'};
const colors:Record<string,string>={university:'#4977d8',prisma:'#cc4b70',personal:'#a9762b',technology:'#8660c5',printing:'#218a78',server:'#65758f'};
function exam(event:CalendarEvent){return ['exam','examen'].includes((event.category||'').toLowerCase());}
function eventColor(event:CalendarEvent){return exam(event)?'#c45c4a':colors[event.area]||colors.personal;}
function duration(minutes:number){return minutes>=60?`${Math.floor(minutes/60)} h${minutes%60?' '+minutes%60+' min':''}`:`${minutes} min`;}
function eventType(event:CalendarEvent){return exam(event)?'Examen':event.category==='class'?'Clase':shortAreas[event.area as Area]||'Evento';}

export default function CalendarPlan({revision,onOpen}:{revision:string;onOpen:(id:string)=>void}){
 const [zone]=useState(()=>Intl.DateTimeFormat().resolvedOptions().timeZone||'Europe/Madrid');
 const [day,setDay]=useState(()=>calendarDayInZone(new Date(),zone)),[view,setView]=useState<View>('month');
 const [loadedRange,setRange]=useState<Range|null>(null),[loadedPlan,setPlan]=useState<Plan|null>(null);
 const [rangeError,setRangeError]=useState(''),[planError,setPlanError]=useState(''),[loading,setLoading]=useState(true),[planLoading,setPlanLoading]=useState(true),[refresh,setRefresh]=useState(0);
 const [area,setArea]=useState('all'),[query,setQuery]=useState(''),[availability,setAvailability]=useState(false),[now,setNow]=useState(()=>new Date());
 const monthDays=useMemo(()=>calendarMonthDays(day),[day]),weekDays=useMemo(()=>calendarWeekDays(day),[day]);
 const rangeStart=monthDays[0],rangeEnd=shiftCalendarDay(monthDays[41],1),today=calendarDayInZone(now,zone);
 useEffect(()=>{const timer=setInterval(()=>setNow(new Date()),60000);return()=>clearInterval(timer)},[]);
 useEffect(()=>{
  let active=true;const controller=new AbortController();setLoading(true);setRangeError('');setRange(null);
  api<Range>(`/calendar/range?start=${rangeStart}&end=${rangeEnd}&zone=${encodeURIComponent(zone)}`,{signal:controller.signal}).then(result=>{if(active&&result.start===rangeStart&&result.end===rangeEnd&&result.timezone===zone)setRange(result)}).catch(error=>{if(active)setRangeError(error.message)}).finally(()=>{if(active)setLoading(false)});
  return()=>{active=false;controller.abort()};
 },[rangeStart,rangeEnd,zone,revision,refresh]);
 useEffect(()=>{
  let active=true;const controller=new AbortController();setPlanLoading(true);setPlanError('');setPlan(null);
  api<Plan>(`/calendar/day?day=${day}&zone=${encodeURIComponent(zone)}`,{signal:controller.signal}).then(result=>{if(active&&result.date===day&&result.timezone===zone)setPlan(result)}).catch(error=>{if(active)setPlanError(error.message)}).finally(()=>{if(active)setPlanLoading(false)});
  return()=>{active=false;controller.abort()};
 },[day,zone,revision,refresh]);
 const range=loadedRange?.start===rangeStart&&loadedRange.end===rangeEnd&&loadedRange.timezone===zone?loadedRange:null;
 const plan=loadedPlan?.date===day&&loadedPlan.timezone===zone?loadedPlan:null;
 const events=range?.events||[];
 const normalize=(value:string)=>value.normalize('NFD').replace(/[\u0300-\u036f]/g,'').toLowerCase();
 const filtered=events.filter(event=>(area==='all'||event.area===area)&&normalize(`${event.title} ${event.location||''} ${eventType(event)}`).includes(normalize(query.trim())));
 const selected=filtered.filter(event=>eventTouchesDay(event,day,zone));
 const allSelected=plan?.events||events.filter(event=>eventTouchesDay(event,day,zone));
 const monthStart=day.slice(0,8)+'01',monthEnd=shiftCalendarDay(shiftCalendarMonth(monthStart,1),-1);
 const currentMonth=events.filter(event=>calendarDayInZone(new Date(event.start),zone)<=monthEnd&&calendarDayInZone(new Date(new Date(event.end).getTime()-1),zone)>=monthStart);
 const largestFree=plan?.free_slots.reduce((largest,slot)=>Math.max(largest,slot.minutes),0)||0;
 const time=(value:string)=>new Date(value).toLocaleTimeString('es-ES',{hour:'2-digit',minute:'2-digit',hourCycle:'h23',timeZone:zone});
 function move(direction:number){setDay(view==='month'?shiftCalendarMonth(day,direction):shiftCalendarDay(day,direction*(view==='week'?7:1)));}
 function selectMonthDay(value:string){setDay(value);if(window.matchMedia('(max-width:700px)').matches)requestAnimationFrame(()=>{const agenda=document.getElementById('cal-agenda-panel');agenda?.focus({preventScroll:true});agenda?.scrollIntoView({block:'start',behavior:window.matchMedia('(prefers-reduced-motion: reduce)').matches?'instant':'smooth'});});}
 function dayKeys(event:KeyboardEvent<HTMLButtonElement>,value:string){
  const offsets:Record<string,number>={ArrowLeft:-1,ArrowRight:1,ArrowUp:-7,ArrowDown:7};let next:string|undefined;
  if(event.key in offsets)next=shiftCalendarDay(value,offsets[event.key]);if(event.key==='Home')next=calendarWeekDays(value)[0];if(event.key==='End')next=calendarWeekDays(value)[6];if(event.key==='PageUp')next=shiftCalendarMonth(value,-1);if(event.key==='PageDown')next=shiftCalendarMonth(value,1);
  if(next){event.preventDefault();setDay(next);requestAnimationFrame(()=>document.querySelector<HTMLButtonElement>(`[data-calendar-day="${next}"]`)?.focus());}
 }
 function eventCard(event:CalendarEvent){return <button className={'cal-agenda-event '+(exam(event)?'is-exam':'')} style={{'--event-color':eventColor(event)} as CSSProperties} key={event.id} onClick={()=>onOpen(event.id)}>
  <span className="cal-event-kicker">{exam(event)?<CalendarCheck size={13}/>:event.category==='class'?<GraduationCap size={13}/>:<CalendarDays size={13}/>} {eventType(event)} <span>· {calendarEventTime(event,zone)}</span></span><strong>{event.title}</strong>
  {event.location&&<span className="cal-event-location"><MapPin size={13}/>{event.location}</span>}{event.estimated_duration&&<span className="cal-event-estimate">Hora de fin estimada</span>}{event.is_demo&&<span className="cal-event-estimate">Datos de ejemplo</span>}<ArrowUpRight className="cal-event-open" size={16}/>
 </button>;}
 function timeline(days:string[]){
  const placements=days.map(value=>layoutCalendarEvents(filtered,value,zone)),flat=placements.flat();
  const firstHour=Math.max(0,Math.min(8,...flat.map(event=>Math.floor(event.startMinute/60)))),lastHour=Math.min(24,Math.max(20,...flat.map(event=>Math.ceil(event.endMinute/60)))),height=(lastHour-firstHour)*64;
  return <div className="cal-time-scroll" role="region" aria-label={days.length===1?'Horario del día':'Horario de la semana'} tabIndex={0}><div className={'cal-time-table '+(days.length>1?'is-week':'')} style={{'--cal-days':days.length} as CSSProperties}>
   <div className="cal-time-head"><span className="cal-time-zone">{zone.split('/').at(-1)?.replaceAll('_',' ')}</span>{days.map(value=><button key={value} className={'cal-time-day '+(value===day?'is-selected':'')+(value===today?' is-today':'')} onClick={()=>setDay(value)} aria-label={`Ver ${calendarDayLabel(value)}`}><span>{calendarDayLabel(value,{weekday:'short'})}</span><strong>{Number(value.slice(-2))}</strong></button>)}</div>
   <div className="cal-all-day"><span>Todo el día</span>{days.map(value=><div key={value}>{filtered.filter(event=>event.all_day&&eventTouchesDay(event,value,zone)).map(event=><button key={event.id} className="cal-all-day-event" style={{'--event-color':eventColor(event)} as CSSProperties} onClick={()=>onOpen(event.id)}>{event.title}</button>)}</div>)}</div>
   <div className="cal-time-body" style={{height}}><div className="cal-time-ruler">{Array.from({length:lastHour-firstHour},(_,index)=><span key={index} style={{top:index*64}}>{String(firstHour+index).padStart(2,'0')}:00</span>)}</div>
    {days.map((value,index)=><div key={value} className={'cal-time-column '+(value===today?' is-today':'')}>
     {Array.from({length:lastHour-firstHour},(_,hour)=><div className="cal-hour-line" key={hour} style={{top:hour*64}}/>)}
     {placements[index].map(placement=>{const top=(placement.startMinute-firstHour*60)/60*64,eventHeight=Math.max(28,(placement.endMinute-placement.startMinute)/60*64-3);return <button key={placement.event.id} className={'cal-time-event '+(eventHeight<65?'is-short':'')} style={{top,height:eventHeight,left:`calc(${placement.column/placement.columns*100}% + 3px)`,width:`calc(${100/placement.columns}% - 6px)`,'--event-color':eventColor(placement.event)} as CSSProperties} onClick={()=>onOpen(placement.event.id)} aria-label={`${placement.event.title}, ${calendarEventTime(placement.event,zone)}${placement.event.location?', '+placement.event.location:''}`} title={`${placement.event.title} · ${calendarEventTime(placement.event,zone)}${placement.event.location?' · '+placement.event.location:''}`}><span>{exam(placement.event)?'Examen · ':''}{calendarEventTime(placement.event,zone)}</span><strong>{placement.event.title}</strong>{eventHeight>82&&placement.event.location&&<small><MapPin size={11}/>{placement.event.location}</small>}</button>;})}
     {value===today&&calendarMinuteInZone(now,zone)>=firstHour*60&&calendarMinuteInZone(now,zone)<lastHour*60&&<div className="cal-now-line" style={{top:(calendarMinuteInZone(now,zone)-firstHour*60)/60*64}} aria-label={`Hora actual: ${time(now.toISOString())}`}/>}
    </div>)}
   </div>
  </div></div>;
 }
 return <section className="cal-workspace" aria-label="Calendario personal">
  <div className="cal-toolbar"><div className="cal-month-title"><span>Tu agenda</span><h2>{calendarDayLabel(day,{month:'long'})}<span>{day.slice(0,4)}</span></h2></div><div className="cal-navigation"><button className="cal-control" aria-label={view==='month'?'Mes anterior':view==='week'?'Semana anterior':'Día anterior'} onClick={()=>move(-1)}><ChevronLeft size={18}/></button><button className="cal-today" onClick={()=>setDay(calendarDayInZone(new Date(),zone))}>Hoy</button><button className="cal-control" aria-label={view==='month'?'Mes siguiente':view==='week'?'Semana siguiente':'Día siguiente'} onClick={()=>move(1)}><ChevronRight size={18}/></button><label className="cal-date-control"><CalendarDays size={17}/><input type="date" aria-label="Ir a una fecha" value={day} onChange={event=>{if(event.target.value)setDay(event.target.value)}}/></label></div>
   <div className="cal-view-switch" role="tablist" aria-label="Vista del calendario">{views.map(([key,title],index)=><button id={'cal-tab-'+key} key={key} role="tab" aria-selected={view===key} aria-controls="cal-view-panel" tabIndex={view===key?0:-1} onClick={()=>setView(key)} onKeyDown={event=>{let next:number|undefined;if(event.key==='ArrowRight')next=(index+1)%views.length;if(event.key==='ArrowLeft')next=(index+views.length-1)%views.length;if(event.key==='Home')next=0;if(event.key==='End')next=views.length-1;if(next!==undefined){event.preventDefault();setView(views[next][0]);document.getElementById('cal-tab-'+views[next][0])?.focus()}}}>{title}</button>)}</div>
  </div>
  <div className="cal-filters"><div className="cal-area-filters" aria-label="Filtrar por área"><button className={area==='all'?'active':''} aria-pressed={area==='all'} onClick={()=>setArea('all')}>Todo</button>{areaList.map(value=><button key={value} className={area===value?'active':''} aria-pressed={area===value} onClick={()=>setArea(value)}><span style={{background:colors[value]}}/>{shortAreas[value]}</button>)}</div><label className="cal-search"><Search size={16}/><input type="search" placeholder="Buscar en este mes…" aria-label="Buscar en el calendario visible" value={query} onChange={event=>setQuery(event.target.value)}/></label></div>
  {rangeError&&<div className="cal-error" role="alert"><AlertCircle size={18}/><span>No se pudo cargar el calendario. {rangeError}</span><button onClick={()=>setRefresh(value=>value+1)}>Reintentar</button></div>}
  <div className="cal-layout"><div className="cal-main-panel" id="cal-view-panel" role="tabpanel" aria-labelledby={'cal-tab-'+view} aria-busy={loading}>
   <div className="cal-panel-caption"><span>{view==='month'?'El mes, de un vistazo':view==='week'?`${calendarDayLabel(weekDays[0],{day:'numeric',month:'short'})} – ${calendarDayLabel(weekDays[6],{day:'numeric',month:'short'})}`:calendarDayLabel(day)}</span><button className="cal-control" onClick={()=>setRefresh(value=>value+1)} aria-label="Actualizar calendario" disabled={loading}><RefreshCw size={15} className={loading?'cal-spinning':''}/></button></div>
   {loading&&<div className="cal-loading" role="status">Actualizando tu agenda…</div>}
   {view==='month'?<div className="cal-month-grid" role="grid" aria-label={calendarDayLabel(day,{month:'long',year:'numeric'})}><div className="cal-week-labels" role="row">{['Lun','Mar','Mié','Jue','Vie','Sáb','Dom'].map(label=><span key={label} role="columnheader">{label}</span>)}</div>
    {Array.from({length:6},(_,week)=><div className="cal-month-row" role="row" key={week}>{monthDays.slice(week*7,week*7+7).map(value=>{const dayEvents=filtered.filter(event=>eventTouchesDay(event,value,zone));return <div role="gridcell" aria-selected={value===day} key={value}><button data-calendar-day={value} tabIndex={value===day?0:-1} className={'cal-month-cell '+(value.slice(0,7)!==day.slice(0,7)?'outside-month ':'')+(value===day?'selected ':'')+(value===today?'today ':'')} onClick={()=>selectMonthDay(value)} onKeyDown={event=>dayKeys(event,value)} aria-label={`${calendarDayLabel(value,{weekday:'long',day:'numeric',month:'long',year:'numeric'})}${value===today?', hoy':''}, ${dayEvents.length} ${dayEvents.length===1?'evento':'eventos'}`}><span className="cal-date-number">{Number(value.slice(-2))}</span><span className="cal-cell-events">{dayEvents.slice(0,2).map(event=><span key={event.id} className="cal-cell-event" style={{'--event-color':eventColor(event)} as CSSProperties}><i/>{exam(event)?'Examen · ':''}{event.all_day?'':time(event.start)+' '}{event.title}</span>)}{dayEvents.length>2&&<span className="cal-cell-more">+{dayEvents.length-2} más</span>}</span><span className="cal-cell-dots">{dayEvents.slice(0,3).map(event=><i key={event.id} style={{background:eventColor(event)}}/>)}{dayEvents.length>3&&<small>+</small>}</span></button></div>;})}</div>)}
   </div>:timeline(view==='week'?weekDays:[day])}
   <div className="cal-legend"><span><i style={{background:colors.university}}/>Universidad</span><span><i style={{background:'#c45c4a'}}/>Exámenes</span><span><i style={{background:colors.prisma}}/>PRISMA</span><span><i style={{background:colors.personal}}/>Personal</span><small>{range?`${currentMonth.length} eventos en el mes`:'Sincronizando'}</small></div>{view==='week'&&<p className="cal-scroll-hint">Desliza el horario para recorrer la semana. Selecciona un día para ver su agenda.</p>}
  </div>
  <aside className="cal-agenda-panel" id="cal-agenda-panel" tabIndex={-1} aria-label="Agenda del día seleccionado"><div className="cal-agenda-heading"><div className={'cal-agenda-date '+(day===today?'is-today':'')}><span>{calendarDayLabel(day,{month:'short'})}</span><strong>{Number(day.slice(-2))}</strong></div><div><span className="cal-eyebrow">{day===today?'Tu día de hoy':'Día seleccionado'}</span><h3>{calendarDayLabel(day,{weekday:'long'})}</h3><p>{area!=='all'||query.trim()?`${selected.length} visibles de ${allSelected.length} eventos`:`${allSelected.length} ${allSelected.length===1?'evento':'eventos'} en tu agenda`}</p></div></div>
   <div className="cal-mobile-week" aria-label="Elegir día de esta semana">{weekDays.map(value=><button key={value} aria-pressed={value===day} className={value===day?'active':''} onClick={()=>setDay(value)}><span>{calendarDayLabel(value,{weekday:'short'}).replace('.','')}</span><strong>{Number(value.slice(-2))}</strong></button>)}</div>
   {loading&&<div className="cal-agenda-loading" role="status" aria-label="Cargando agenda"><span/><span/><span/></div>}{!loading&&selected.map(eventCard)}
   {!loading&&!rangeError&&selected.length===0&&<div className="cal-empty"><Sun size={28}/><h4>{allSelected.length?'Sin coincidencias':'Un día con espacio.'}</h4><p>{allSelected.length?'Prueba otra área o borra la búsqueda para ver tus eventos.':'No tienes eventos guardados para esta fecha.'}</p>{allSelected.length>0&&<button onClick={()=>{setArea('all');setQuery('')}}>Mostrar todo</button>}</div>}
   <button className="cal-availability-toggle" aria-expanded={availability} aria-controls="cal-availability" onClick={()=>setAvailability(value=>!value)}><span className="cal-availability-icon"><Clock size={17}/></span><span><strong>Espacio para ti</strong><small>{planLoading?'Calculando disponibilidad…':planError?'Disponibilidad no disponible':largestFree?`Tu mayor hueco: ${duration(largestFree)}`:'Consulta los huecos de tu día'}</small></span><ChevronDown size={17} style={{transform:availability?'rotate(180deg)':undefined}}/></button>
   {availability&&<div className="cal-availability" id="cal-availability">{planError?<div className="cal-error" role="alert"><span>{planError}</span><button onClick={()=>setRefresh(value=>value+1)}>Reintentar</button></div>:plan&&<><p className="cal-availability-caption">08:00 – 22:00 · Todos tus eventos, sin filtros</p>
    {plan.conflicts.length>0?<div className="cal-conflicts"><h4><AlertCircle size={15}/>{plan.conflicts.length} {plan.conflicts.length===1?'solapamiento':'solapamientos'}</h4>{plan.conflicts.map((conflict,index)=><div key={index}><strong>{time(conflict.start)} – {time(conflict.end)}</strong>{conflict.ids.map(id=><button key={id} onClick={()=>onOpen(id)}>{plan.events.find(event=>event.id===id)?.title||'Abrir evento'}<ChevronRight size={12}/></button>)}</div>)}</div>:<p className="cal-no-conflicts"><Check size={14}/>Tus eventos no se solapan</p>}
    <div className="cal-free-slots">{plan.free_slots.map(slot=><div key={slot.start}><span>{time(slot.start)} – {time(slot.end)}</span><strong>{duration(slot.minutes)}</strong></div>)}</div>{!plan.free_slots.length&&<p>No hay huecos de al menos 30 minutos en este tramo.</p>}<p className="cal-availability-note">{plan.note}</p>
   </>}</div>}
   <div className="cal-source-note"><BookOpen size={14}/><span>Horario UV de consulta. Tus documentos y clases permanecen en modo de lectura.</span></div>
  </aside></div>
 </section>;
}
