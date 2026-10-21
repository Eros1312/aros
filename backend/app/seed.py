from datetime import datetime, timedelta, timezone
from sqlalchemy import select
from .database import User, Item, Inbox, Connector, Automation
from .security import hash_password
from .config import settings
AREAS = ['university','prisma','technology','printing','server','personal']
CONNECTORS = {
 'gmail':('Gmail',['https://www.googleapis.com/auth/gmail.readonly']),
 'calendar':('Google Calendar',['https://www.googleapis.com/auth/calendar.readonly']),
 'drive':('Google Drive',['https://www.googleapis.com/auth/drive.readonly']),
 'weather':('Tiempo · Open-Meteo',[]), 'university':('Aula Virtual',[]), 'rss':('Noticias RSS',[]), 'server':('Servidor',[]),
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
        db.add(item); db.flush(); return item
    cls=add('event','university','Química Orgánica II','Aula de ejemplo',hours=2,data={'end_at':(t+timedelta(hours=4)).isoformat(),'location':'Aula de ejemplo'})
    add('event','university','Seminario de Analítica II','Resolución de problemas · Aula 2.4',hours=26,data={'end_at':(t+timedelta(hours=28)).isoformat(),'location':'Campus de ejemplo'})
    organic=add('task','university','Entregar el informe de Orgánica','Revisar cálculos y conclusiones del guion de prácticas.',priority='high',hours=24)
    add('task','university','Preparar problemas de Analítica','Problemas 4–8 del tema 3.',hours=48)
    add('document','university','Guion de prácticas · Orgánica II','Objetivo: estudiar la síntesis y purificación de un compuesto orgánico. Antes de la práctica revisa el material, las cantidades y el procedimiento. Entrega: informe con resultados, rendimiento y conclusiones.',status='active',data={'filename':'Guion de prácticas.txt','mime':'text/plain'})
    add('email','university','Prácticas: documentación disponible','Ejemplo de comunicación universitaria. Revisa el guion antes de la siguiente sesión.',status='active',data={'sender':'Docencia · ejemplo'})
    add('event','prisma','Evento de demostración','Encuentro ficticio para probar la organización',hours=10,data={'location':'Sala de ejemplo'})
    guest=add('task','prisma','Confirmar la lista de RRPP','Última revisión antes del evento.',priority='urgent',hours=8)
    for title in ['Enviar diseño a imprenta','Revisar entradas de invitación','Preparar cierre de caja']: add('task','prisma',title,hours=10)
    add('project','prisma','Evento de demostración · producción','Diseños, materiales y promoción del próximo evento.',status='active')
    add('contact','prisma','Equipo RRPP','Coordinación de la lista de invitados.',status='active',data={'role':'RRPP'})
    add('expense','prisma','Impresión de carteles','Ejemplo de gasto del evento.',status='done',data={'amount':42.5,'currency':'EUR'})
    for title,desc in [('Modelos de IA: tu selección de hoy','Una noticia ilustrativa para explorar la sección.'),('Herramientas para estudiar','Ideas para organizar apuntes y revisar documentos.'),('Automatización personal','Un resumen de ejemplo sobre flujos de trabajo.')]:
        add('news','technology',title,desc,status='active')
    for title in ['Organizador de escritorio','Soporte para móvil','Maceta paramétrica','Caja para herramientas']:
        add('model','printing',title,'Modelo de demostración. Añade un enlace STL o 3MF a tu biblioteca.',status='active',data={'material':'PLA','favorite':title=='Soporte para móvil'})
    for title in ['PLA blanco','PETG transparente','Resina gris']:
        add('material','printing',title,'Inventario de materiales.',status='active',data={'quantity':'1 kg'})
    add('project','printing','Imprimir organizador','Revisar dimensiones antes de imprimir.',status='pending')
    add('order','personal','Soporte de herramientas','Pedido de demostración.',status='in_transit',hours=72,data={'store':'Tienda de ejemplo','amount':12.9})
    add('order','personal','Cuaderno de laboratorio','Pedido de demostración.',status='in_transit',hours=48,data={'store':'Papelería de ejemplo','amount':8.5})
    led=add('task','personal','Preparar devolución de tiras LED','Importe de ejemplo: 19,82 €.',priority='high',hours=48)
    add('reminder','personal','Revisar presupuesto del mes','Seguimiento de pago pendiente.',hours=96)
    add('note','personal','Ideas para esta semana','Reservar dos bloques de estudio. Revisar el material de PRISMA. Organizar la mesa de impresión.',status='active')
    for item,reason in [(organic,'Vence mañana.'),(guest,'El evento es esta noche.'),(led,'El plazo termina en dos días.')]:
        db.add(Inbox(user_id=user.id,item_id=item.id,title=item.title,reason=reason,area=item.area))
    for title,kind,minutes in [('Prepárame el día','daily_digest',1440),('Revisar entregas','deadline_review',360),('Sincronizar conexiones','connector_sync',360),('Comprobar servidor','server_health',15)]:
        db.add(Automation(user_id=user.id,title=title,kind=kind,interval_minutes=minutes,enabled=False))
