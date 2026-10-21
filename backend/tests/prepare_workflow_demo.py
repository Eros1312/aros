"""Isolated UI fixtures. Executes only local typed tools; never a provider."""
import json
from uuid import uuid4
from sqlalchemy import select
from app.config import settings
from app.database import Base,engine,SessionLocal,Item,AgentJob,now
from sqlalchemy.engine import make_url
from pathlib import Path
from app.seed import setup_user
from app import conversation_engine as conversation
# This helper is deliberately outside pytest collection. It creates a new
# disposable SQLite fixture, never a production or pre-existing database.
url=make_url(settings.database_url)
if settings.app_env!='development' or not settings.demo_mode or url.get_backend_name()!='sqlite':
    raise SystemExit('Use APP_ENV=development, DEMO_MODE=true and a fresh SQLite fixture database.')
path=Path(url.database or '')
if not path.name.startswith('aros-ui-test-') or path.exists():
    raise SystemExit('The SQLite filename must start with aros-ui-test- and must not already exist.')
if settings.openclaw_url or settings.openclaw_token or settings.openai_api_key or settings.freellm_url or settings.freellm_token:
    raise SystemExit('Clear external model/provider configuration before creating offline fixtures.')
path.parent.mkdir(parents=True,exist_ok=True)
Base.metadata.create_all(engine)
with SessionLocal() as db:
    user=setup_user(db)
    user.ai_consent=True
    project=Item(user_id=user.id,source='manual',kind='project',area='prisma',title='Ejemplo local · PRISMA de otoño',description='Preparar cartel, comprobar imprenta y revisar la lista. Proyecto de demostración sin datos de producción.',status='active',is_demo=True)
    db.add(project);db.flush()
    source=Item(user_id=user.id,source='upload',kind='document',area='prisma',title='Ejemplo local · Brief del cartel.txt',description='Cartel de demostración: revisar hora, lugar y nombre del evento antes de aprobar. Ningún diseño está aprobado todavía.',status='active',is_demo=True,blob=b'Offline demo original',data={'project_id':project.id,'filename':'Brief de ejemplo.txt','mime':'text/plain','review_state':'unconfirmed'})
    task=Item(user_id=user.id,source='manual',kind='task',area='prisma',title='Ejemplo local · Revisar la información del cartel',status='pending',priority='high',is_demo=True,data={'project_id':project.id})
    db.add_all([source,task]);db.commit();project_id=project.id;source_id=source.id
    original_model=settings.openclaw_model
    settings.openclaw_model='test/model';conversation.MODEL='test/model';user.ai_model='test/model';db.commit()
    settings.openclaw_url='http://fixture.invalid';settings.openclaw_token='fixture-not-a-credential'
    queued=conversation.enqueue(db,user,'Lee el proyecto y el brief, y crea una nota interna en este proyecto con los puntos que debo revisar.',item_ids=[project.id,source.id],project_id=project.id,thread_id=project.id)
    ident=queued['id']
values=[{'kind':'tool','tool':'resources.read','arguments':{'ids':[project_id,source_id]}},
        {'kind':'tool','tool':'items.create','arguments':{'kind':'note','area':'prisma','title':'Ejemplo local · Lista de revisión del cartel','description':'Comprobar la hora, el lugar y el nombre del evento. El diseño sigue sin aprobación. Resultado generado por una prueba determinista local, sin llamada al modelo.','status':'active','project_id':project_id,'operation_key':'offline-demo-note'}},
        lambda e:{'kind':'tool','tool':'artifact.verify','arguments':{'artifact_id':e['artifacts'][0]['id']}},
        {'kind':'finish','summary':'He guardado y comprobado una lista de revisión en el proyecto de ejemplo. Es una prueba local con decisiones prefijadas; no ha habido una conexión real con IA.','source_ids':[project_id,source_id]}]
index=0
original=conversation.infer
def fake(job,e,messages):
    global index
    value=values[index];index+=1
    return json.dumps(value(e) if callable(value) else value)
conversation.infer=fake
for _ in range(12):
    with SessionLocal() as db:
        job=db.get(AgentJob,ident)
        if job.status!='pending':break
        job.status='running';job.lease_id=str(uuid4());job.started_at=now();job.heartbeat_at=now();db.commit()
        conversation.process_step(db,job,job.lease_id)
conversation.infer=original
with SessionLocal() as db:
    job=db.get(AgentJob,ident)
    assert job.execution['state']=='completed',job.execution
    db.get(type(user),user.id).ai_model=original_model or 'local'
    for ref in job.execution['artifacts']:
        db.get(Item,ref['id']).is_demo=True
    db.commit()
settings.openclaw_url='';settings.openclaw_token='';settings.openclaw_model=original_model
print(json.dumps({'project_id':project_id,'source_id':source_id,'job_id':ident,'state':'completed','provider_calls':0}))
