from datetime import datetime, timedelta, timezone
from sqlalchemy import select
from .database import User, Item, Inbox, Connector, Automation
from .security import hash_password
from .config import settings
AREAS = ['learning','events','technology','resources','server','personal']
CONNECTORS = {
 'gmail':('Gmail',['https://www.googleapis.com/auth/gmail.readonly']),
 'calendar':('Google Calendar',['https://www.googleapis.com/auth/calendar.readonly']),
 'drive':('Google Drive',['https://www.googleapis.com/auth/drive.readonly']),
 'weather':('Tiempo · Open-Meteo',[]), 'rss':('Noticias RSS',[]), 'server':('Servidor',[]),
 'github':('GitHub',[]), 'notion':('Notion',[]), 'canva':('Canva',[]), 'vercel':('Vercel',[]),
}
def setup_user(db):
    user=db.scalar(select(User).where(User.email==settings.admin_email))
    if user is None:
        import secrets
        password=settings.admin_password or secrets.token_urlsafe(32)
        user=User(email=settings.admin_email,password_hash=hash_password(password))
        db.add(user); db.flush()
        for key,(name,scopes) in CONNECTORS.items(): db.add(Connector(id=key,user_id=user.id,name=name,scopes=scopes))
        if settings.demo_mode: seed(db,user)
        db.commit()
    for key,(name,scopes) in CONNECTORS.items():
        if not db.get(Connector,(key,user.id)):db.add(Connector(id=key,user_id=user.id,name=name,scopes=scopes))
    db.commit()
    return user

def seed(db,user):
    t=datetime.now(timezone.utc)
    def add(kind,area,title,description='',status='pending',priority='normal',hours=None,data=None):
        item=Item(user_id=user.id,kind=kind,area=area,title=title,description=description,status=status,priority=priority,due_at=(t+timedelta(hours=hours)).isoformat() if hours is not None else None,data=data or {},source='demo',is_demo=True)
        db.add(item);db.flush();return item
    add('event','learning','Sesión de aprendizaje','Reserva de tiempo de ejemplo.',hours=2,data={'end_at':(t+timedelta(hours=3)).isoformat(),'location':'En línea'})
    study=add('task','learning','Revisar las notas del curso','Resume tres ideas y anota una pregunta para la próxima sesión.',priority='high',hours=24)
    add('document','learning','Guía de aprendizaje','Ejemplo original de Aros: define un objetivo, elige una lectura, practica y revisa lo aprendido.',status='active',data={'filename':'Guía de aprendizaje.txt','mime':'text/plain'})
    add('project','learning','Aprender algo nuevo','Organiza tus lecturas, tareas y notas alrededor de un objetivo.',status='active')
    add('event','events','Encuentro de la comunidad','Evento ficticio para explorar la planificación.',hours=72,data={'location':'Espacio de ejemplo'})
    event=add('task','events','Confirmar la agenda del encuentro','Revisar duración, lugar y participantes.',priority='high',hours=48)
    add('project','events','Preparar un encuentro','Agenda, materiales y tareas del evento de ejemplo.',status='active')
    add('note','events','Lista de preparación','Confirmar lugar. Revisar accesibilidad. Preparar los materiales.',status='active')
    add('document','resources','Guía para organizar un proyecto','Ejemplo original: describe el resultado esperado, divide el trabajo en pasos y conserva las decisiones.',status='active',data={'filename':'Guía del proyecto.txt','mime':'text/plain'})
    add('note','resources','Ideas para reutilizar','Guarda aquí referencias y apuntes útiles para varios proyectos.',status='active')
    add('link','resources','Ejemplo de enlace','Sustituye este enlace de demostración por una referencia propia.',status='active',data={'url':'https://example.com'})
    add('project','resources','Biblioteca compartida de ideas','Agrupa documentos, notas y enlaces mediante proyectos.',status='active')
    add('note','technology','Herramientas por evaluar','Apunta qué problema resuelve cada herramienta y cómo comprobarlo.',status='active')
    add('news','technology','Ejemplo de noticia guardada','Contenido sintético para explorar la lectura de noticias.',status='active')
    personal=add('task','personal','Planificar la semana','Elige tres prioridades y reserva tiempo para completarlas.',priority='high',hours=24)
    add('reminder','personal','Revisar el presupuesto','Recordatorio ficticio, sin información financiera personal.',hours=96)
    add('note','personal','Ideas para esta semana','Aprender algo, terminar una tarea y reservar tiempo libre.',status='active')
    for item in [study,event,personal]:
        db.add(Inbox(user_id=user.id,item_id=item.id,title=item.title,reason='Una tarea de ejemplo tiene una fecha próxima.',area=item.area))
    for title,kind,minutes in [('Preparar el día','daily_digest',1440),('Revisar plazos','deadline_review',360),('Sincronizar conexiones','connector_sync',360),('Comprobar servidor','server_health',15)]:
        db.add(Automation(user_id=user.id,title=title,kind=kind,interval_minutes=minutes,enabled=False))
