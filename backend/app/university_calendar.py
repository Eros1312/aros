"""Official UV fourth-year EC timetable: read-only PDF -> owner calendar."""
import hashlib,re
from datetime import datetime,date,timedelta,timezone
from typing import Annotated
from zoneinfo import ZoneInfo
import httpx
from fastapi import APIRouter,Depends,HTTPException,Response
from sqlalchemy import select,update
from sqlalchemy.orm import defer
from .database import Item,Connector,Automation,get_db,now
from .security import current_user,audit

router=APIRouter(prefix='/api/v1/calendar/university',tags=['University calendar'])
SOURCE='https://www.uv.es/quimdocs/DG_Quimica_Ingenieria_Q/horaris26-27/Horaris_26-27_Quart-EC.pdf'
EXAMS='https://www.uv.es/quimdocs/DG_Quimica_Ingenieria_Q/horaris26-27/Calendari-Examens_DGQEQ_26-27.pdf'
ACADEMIC_YEAR='2026–2027'
MONTHS={'enero':1,'febrero':2,'marzo':3,'abril':4,'mayo':5,'junio':6,'julio':7,'agosto':8,'septiembre':9,'octubre':10,'noviembre':11,'diciembre':12}
NAMES={'BQ':'Bioquímica','QA3':'Química Analítica III','QO3':'Química Orgánica III','TDME':'Teoría y Diseño de Máquinas y Equipos','IRQ II':'Ingeniería de la Reacción Química II','LQO2':'Laboratorio de Química Orgánica II','LQA2':'Laboratorio de Química Analítica II','LQF2':'Laboratorio de Química Física II','OB III':'Operaciones Básicas III','OGP':'Organización y Gestión de la Producción','Lab OGP':'Laboratorio de Organización y Gestión de la Producción','IPP I':'Ingeniería de Procesos y Productos I','EIQ II':'Experimentación en Ingeniería Química II'}

def parse_timetable(raw,exams=False):
    import pymupdf
    document=pymupdf.open(stream=raw,filetype='pdf')
    if not 1<=len(document)<=100:raise ValueError('Número de páginas no admitido')
    if not exams:
        cover=document[0].get_text().upper()
        if '2026-2027' not in cover or not re.search(r'QUART\s+CURS',cover) or not re.search(r'GRUP\s+EC\b',cover):raise ValueError('El PDF no corresponde al curso y grupo esperados')
    entries=[];tz=ZoneInfo('Europe/Madrid')
    for index,page in enumerate(document):
        matrix=page.rotation_matrix
        def coords(values):return pymupdf.Rect(values[:4])*matrix
        heading=' '.join(b[4].replace('\n',' ') for b in page.get_text('blocks') if coords(b).x0<100 and coords(b).y0<120)
        m=re.search(r'(\d{1,2})\s+('+'|'.join(MONTHS)+r')\s*-',heading.lower())
        if not m:continue
        month=MONTHS[m[2]];monday=date(2026 if month>=9 else 2027,month,int(m[1]))
        if exams:
            # The last week of a month shows the next two miniature calendars.
            labelled=re.findall(r'('+'|'.join(MONTHS)+r')\s+(20\d{2})\b',page.get_text().lower())
            month_index=monday.year*12+monday.month-1
            expected={(value//12,value%12+1) for value in range(month_index,month_index+3)}
            if not labelled or any((int(year),MONTHS[name]) not in expected for name,year in labelled):raise ValueError('El calendario de exámenes no corresponde al curso esperado')
        if monday.weekday()!=0:raise ValueError('La cuadrícula semanal ha cambiado')
        axis={}
        words=[(*coords(w),w[4]) for w in page.get_text('words')]
        for x0,y0,x1,y1,text,*_ in words:
            if x1<65 and y0>130 and text.isdigit() and 7<=int(text)<=20:axis[int(text)]=(y0+y1)/2
        if len(axis)<5:raise ValueError('No se ha podido verificar el eje horario')
        low=min(axis);high=max(axis);scale=(axis[high]-axis[low])/(high-low)
        if not 25<scale<65:raise ValueError('Escala horaria desconocida')
        base=axis[low]-(low+.5)*scale
        if any(abs(y-(base+(hour+.5)*scale))>3 for hour,y in axis.items()):raise ValueError('El eje horario no mantiene una escala uniforme')
        columns=[w for w in words if 120<w[1]<145 and w[4] in ['lu.','ma.','mi.','ju.','vi.','sá.','do.']]
        if len(columns)!=(5 if exams else 7):raise ValueError('No se han verificado los días')
        columns.sort(key=lambda w:w[0]);centres=[(w[0]+w[2])/2 for w in columns]
        for drawing in page.get_drawings():
            original_rect=drawing['rect'];rect=original_rect*matrix;fill=drawing['fill']
            if not fill or (not exams and max(fill)-min(fill)<.07) or rect.y0<140 or not 20<rect.width<(160 if exams else 90):continue
            lines=page.get_textbox(original_rect+(-.5,-.5,.5,.5)).strip().splitlines()
            if not lines:continue
            label=lines[0].strip();code=label.split('>')[0]
            prefix=next((k for k in sorted(NAMES,key=len,reverse=True) if code.startswith(k)),None)
            if exams:
                # Official exam PDF includes every year: import only fourth-year subjects.
                aliases={'Bioquímica':'BQ','Química Analítica III':'QA3','Química Orgànica III':'QO3','IRQ II':'IRQ II','TDME':'TDME','OB III':'OB III','OGP':'OGP','IPP I':'IPP I','Lab. Q. Orgànica II':'LQO2','Lab. Q. Analítica II':'LQA2','Lab. Q. Física II':'LQF2'}
                compact=' '.join(lines).strip();prefix=next((v for k,v in aliases.items() if re.search(r'(?<!\w)'+re.escape(k)+r'(?!\w)',compact)),None)
                if not prefix or ('EV' in compact and 'EC' not in compact) or re.search(r'Bioquímica\s+B\b',compact):continue
            if not prefix:raise ValueError('Actividad sin identificar: '+label[:80])
            centre=(rect.x0+rect.x1)/2
            spacing=(centres[-1]-centres[0])/(len(centres)-1)
            if not centres[0]-spacing/2<=centre<=centres[-1]+spacing/2:raise ValueError('Actividad fuera de la cuadrícula de días')
            day_index=min(range(len(columns)),key=lambda j:abs(centre-centres[j]))
            start_minutes=round(((rect.y0-base)/scale)*4)*15
            end_minutes=round(((rect.y1-base)/scale)*4)*15
            if not 7*60<=start_minutes<end_minutes<=21*60:raise ValueError('Intervalo horario fuera de la cuadrícula')
            local=datetime.combine(monday+timedelta(days=day_index),datetime.min.time(),tz)
            begin=local+timedelta(minutes=start_minutes);end=local+timedelta(minutes=end_minutes)
            title=NAMES[prefix]+(' · examen' if exams else ' · parcial' if 'parcial' in label.lower() else '')
            location='' if exams else ' '.join(lines[1:]).strip().strip('()')
            entries.append({'title':title,'start':begin.astimezone(timezone.utc).isoformat(),'end':end.astimezone(timezone.utc).isoformat(),'location':location,'label':' '.join(lines) if exams else label,'page':index+1,'date':begin.date().isoformat(),'exam':exams or 'parcial' in label.lower(),'source_url':EXAMS if exams else SOURCE})
    document.close()
    if not entries:raise ValueError('El PDF no contiene actividades verificables')
    return entries

def read_pdf(url):
    # Fixed official host, never accepts a model/user-supplied URL or follows redirects.
    if url not in [SOURCE,EXAMS]:raise ValueError('Solo se admiten los PDF oficiales configurados')
    with httpx.Client(timeout=45,follow_redirects=False,trust_env=False) as client:
        with client.stream('GET',url) as response:
            response.raise_for_status();chunks=[];size=0
            for chunk in response.iter_bytes():
                size+=len(chunk)
                if size>8_000_000:raise ValueError('El PDF oficial supera 8 MB')
                chunks.append(chunk)
    raw=b''.join(chunks)
    if not raw.startswith(b'%PDF'):raise ValueError('La fuente oficial no devolvió un PDF')
    return raw

def sync_uv_calendar(db,user):
    row=db.get(Connector,('university-calendar',user.id))
    if row and row.status=='paused':raise HTTPException(409,'El calendario UV está pausado')
    if row and row.status=='syncing':raise HTTPException(409,'El calendario UV ya se está sincronizando')
    if not row:
        row=Connector(id='university-calendar',user_id=user.id,name='Horario UV · 4º EC',scopes=['public:read']);db.add(row);db.flush()
    previous_revision=row.revision
    claimed=db.execute(update(Connector).where(Connector.id=='university-calendar',Connector.user_id==user.id,Connector.revision==previous_revision,Connector.status==row.status).values(status='syncing',revision=Connector.revision+1,sync_started_at=now()))
    db.commit()
    if claimed.rowcount!=1:raise HTTPException(409,'El calendario UV cambió. Vuelve a intentarlo')
    db.refresh(row);revision=row.revision
    try:
        raw=read_pdf(SOURCE);exam_raw=read_pdf(EXAMS)
        timetable=parse_timetable(raw);exams=parse_timetable(exam_raw,True);entries=timetable+exams
        fingerprint=hashlib.sha256(raw+exam_raw).hexdigest()
        source_hashes={SOURCE:hashlib.sha256(raw).hexdigest(),EXAMS:hashlib.sha256(exam_raw).hexdigest()}
        created=updated=0;seen=set()
        for event in entries:
            external=hashlib.sha256((event['source_url']+event['label']+event['start']).encode()).hexdigest();seen.add(external)
            item=db.scalar(select(Item).options(defer(Item.blob)).where(Item.user_id==user.id,Item.source=='uv-calendar',Item.external_id==external))
            data={'end_at':event['end'],'location':event['location'],'url':event['source_url']+'#page='+str(event['page']),'source_url':event['source_url'],'source_page':event['page'],'source_label':event['label'],'source_sha256':source_hashes[event['source_url']],'group':'EC','academic_year':ACADEMIC_YEAR,'language':'Teoría castellano/valenciano; laboratorios castellano','timezone':'Europe/Madrid','verified_source':True,'exam':event['exam']}
            if not item:
                item=Item(user_id=user.id,kind='event',area='university',source='uv-calendar',external_id=external,title=event['title'],description='Horario oficial UV de cuarto curso, grupo EC. '+event['label'],due_at=event['start'],status='active',data=data);db.add(item);created+=1
            elif item.data!=data or item.title!=event['title'] or item.status!='active':
                item.data=data;item.title=event['title'];item.status='active';item.due_at=event['start'];item.version+=1;item.updated_at=now();updated+=1
        for old in db.scalars(select(Item).options(defer(Item.blob)).where(Item.user_id==user.id,Item.source=='uv-calendar',Item.kind=='event',Item.status=='active')):
            if old.external_id not in seen:old.status='cancelled';old.version+=1;old.updated_at=now();updated+=1
        for external_id,content,filename,url,title in [('official-pdf',raw,'UV-4-EC-2026-27.pdf',SOURCE,'Horario oficial UV · 4º EC · 2026–2027'),('official-exams-pdf',exam_raw,'UV-examenes-2026-27.pdf',EXAMS,'Exámenes oficiales UV · 2026–2027')]:
            original=db.scalar(select(Item).where(Item.user_id==user.id,Item.source=='uv-calendar',Item.external_id==external_id))
            if not original:original=Item(user_id=user.id,source='uv-calendar',external_id=external_id,kind='document',area='university',title=title,status='active');db.add(original)
            elif original.blob!=content:original.version+=1;original.updated_at=now()
            original.blob=content;original.data={'filename':filename,'mime':'application/pdf','size':len(content),'url':url,'sha256':source_hashes[url]}
        details={'count':len(entries),'classes':len(timetable),'exams':len(exams),'sha256':fingerprint,'group':'EC','academic_year':ACADEMIC_YEAR,'source_url':SOURCE,'exam_source_url':EXAMS,'created':created,'updated':updated}
        synced_at=now()
        finished=db.execute(update(Connector).where(Connector.id=='university-calendar',Connector.user_id==user.id,Connector.revision==revision,Connector.status=='syncing').values(status='connected',last_sync=synced_at,error=None,sync_details=details,sync_started_at=None))
        if finished.rowcount!=1:raise HTTPException(409,'El calendario UV cambió durante la sincronización. Se descartó la importación')
        if not db.scalar(select(Automation).where(Automation.user_id==user.id,Automation.kind=='uv_calendar_sync')):db.add(Automation(user_id=user.id,title='Actualizar horario oficial UV',kind='uv_calendar_sync',enabled=True,interval_minutes=1440,next_run=(datetime.now(timezone.utc)+timedelta(days=1)).isoformat()))
        audit(db,user,'calendar.uv_synced',str(len(entries)));db.commit();return {**details,'last_sync':synced_at}
    except Exception as exc:
        db.rollback()
        db.execute(update(Connector).where(Connector.id=='university-calendar',Connector.user_id==user.id,Connector.revision==revision,Connector.status=='syncing').values(status='error',error='No se pudo verificar el horario oficial. Se conserva el calendario anterior.',sync_started_at=None));db.commit()
        if isinstance(exc,HTTPException):raise
        raise HTTPException(502,'No se pudo verificar el horario oficial. Se conserva el calendario anterior.') from None

@router.get('/status')
def status(user:Annotated[object,Depends(current_user)],db:Annotated[object,Depends(get_db)]):
    row=db.get(Connector,('university-calendar',user.id))
    return {'configured':bool(row),'count':(row.sync_details or {}).get('count',0) if row else 0,'last_sync':row.last_sync if row else None,'error':row.error if row else None,'status':row.status if row else 'disconnected','source_url':SOURCE,'group':'EC','academic_year':ACADEMIC_YEAR,'read_only':True,'detail':'Horario oficial: teoría castellano/valenciano, laboratorios castellano. Revisión automática diaria; no modifica la UV ni tu calendario de Apple.'}

@router.post('/sync')
def sync(user:Annotated[object,Depends(current_user)],db:Annotated[object,Depends(get_db)]):return sync_uv_calendar(db,user)

@router.get('/export')
def export(user:Annotated[object,Depends(current_user)],db:Annotated[object,Depends(get_db)]):
    def escape(text):return str(text).replace('\\','\\\\').replace('\n','\\n').replace(';','\\;').replace(',','\\,')
    def instant(value):return datetime.fromisoformat(value).astimezone(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    lines=['BEGIN:VCALENDAR','VERSION:2.0','PRODID:-//Aros//UV 4 EC//ES','CALSCALE:GREGORIAN','X-WR-CALNAME:UV 4º EC · 2026–2027']
    for item in db.scalars(select(Item).options(defer(Item.blob)).where(Item.user_id==user.id,Item.source=='uv-calendar',Item.kind=='event',Item.status=='active').order_by(Item.due_at)):
        lines+=['BEGIN:VEVENT','UID:'+item.external_id+'@aros.local','DTSTAMP:'+instant(now()),'DTSTART:'+instant(item.due_at),'DTEND:'+instant(item.data['end_at']),'SUMMARY:'+escape(item.title),'LOCATION:'+escape(item.data.get('location','')),'DESCRIPTION:'+escape(item.description),'URL:'+item.data['url'],'END:VEVENT']
    lines+=['END:VCALENDAR']
    # RFC5545 folds at 75 UTF-8 octets rather than characters.
    folded=[]
    for line in lines:
        buf='';length=0
        for char in line:
            n=len(char.encode())
            if length+n>75:folded.append(buf);buf=' '+char;length=1+n
            else:buf+=char;length+=n
        folded.append(buf)
    return Response('\r\n'.join(folded)+'\r\n',media_type='text/calendar',headers={'Content-Disposition':'attachment; filename="UV-4-EC-2026-27.ics"'})
