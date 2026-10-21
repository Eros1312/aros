import time, logging, json
from datetime import datetime, timezone, timedelta
from sqlalchemy import select, update, or_
from .database import SessionLocal, Automation, Run, User, Item, Inbox, Connector, now
from .services import attention_for_item
from .security import audit
from .connectors import sync_connector
from .server import server_health
from .config import settings
logger=logging.getLogger('aros.worker')
def tick():
    # Small bounded CPU jobs run before slower external connector calls.
    with SessionLocal() as db:
        from .channels import reconcile_whatsapp
        reconcile_whatsapp(db)
        from .connectors import recover_connector_leases
        recover_connector_leases(db)
        from .events import process_canva_jobs
        process_canva_jobs()
        owner=db.scalar(select(User).where(User.email==settings.admin_email))
        if owner and not settings.demo_mode:
            from .github_releases import ensure_github_releases
            ensure_github_releases(db,owner,enabled=False);db.commit()
        due=list(db.scalars(select(Automation).where(Automation.enabled==True,Automation.next_run<=now())))
        for a in due:
            db.scalar(select(User).where(User.id==a.user_id).with_for_update())
            previous=a.next_run
            advanced=db.execute(update(Automation).where(Automation.id==a.id,Automation.enabled==True,Automation.next_run==previous).values(next_run=(datetime.now(timezone.utc)+timedelta(minutes=a.interval_minutes)).isoformat()))
            if advanced.rowcount==1:
                running=db.scalar(select(Run).where(Run.automation_id==a.id,Run.status.in_(['pending','running'])))
                if not running: db.add(Run(user_id=a.user_id,automation_id=a.id))
            db.commit()
        cutoff=(datetime.now(timezone.utc)-timedelta(minutes=15)).isoformat()
        db.execute(update(Run).where(Run.status=='running',or_(Run.started_at<cutoff,Run.started_at.is_(None))).values(status='failed',result='La ejecución se interrumpió. Puedes volver a ejecutarla.',finished_at=now()))
        db.commit()
        pending=list(db.scalars(select(Run).where(Run.status=='pending').order_by(Run.created_at).limit(10)))
        for job in pending:
            claimed=db.execute(update(Run).where(Run.id==job.id,Run.status=='pending').values(status='running',started_at=now())); db.commit()
            if claimed.rowcount!=1: continue
            a=db.get(Automation,job.automation_id); user=db.get(User,job.user_id)
            if not a or not user or (not a.enabled and job.origin!='manual'):
                job.status='cancelled';job.result='Programación pausada antes de ejecutar';job.finished_at=now();db.commit();continue
            try:
                if a.kind=='deadline_review':
                    from .source_access import visible_items_clause
                    items=list(db.scalars(select(Item).where(Item.user_id==user.id,Item.kind.in_(['task','reminder']),Item.status.notin_(['done','archived','cancelled']),Item.due_at<=(datetime.now(timezone.utc)+timedelta(days=2)).isoformat(),visible_items_clause(db,user))))
                    for item in items: attention_for_item(db,user,item)
                    result=f'{len(items)} entregas o recordatorios próximos revisados.'
                elif a.kind=='server_health':
                    h=server_health(); result=f"Servidor disponible · CPU {h['cpu_percent']}% · {h['scope']}"
                elif a.kind=='github_releases':
                    from .github_releases import refresh_github_releases
                    discovered=refresh_github_releases(db,user,manual=job.origin=='manual')
                    result=json.dumps(discovered,ensure_ascii=False)
                elif a.kind=='canva_sync':
                    from .events import sync_canva
                    details=sync_canva(db,user)
                    result=f"{details['count']} diseños Eventos de Canva sincronizados."
                elif a.kind=='connector_sync':
                    keys=[c.id for c in db.scalars(select(Connector).where(Connector.user_id==user.id,Connector.id.notin_(['_whatsapp-control','_canva-application','canva','github-releases']),Connector.status.in_(['connected','error'])))]
                    failures=[]
                    for key in keys:
                        try: sync_connector(db,user,key)
                        except Exception: failures.append(key)
                    if failures: raise RuntimeError('Revisa estas conexiones: '+', '.join(failures))
                    result=f'{len(keys)} conexiones sincronizadas.'
                elif a.kind=='daily_digest':
                    from .source_access import visible_items_clause, resource_fingerprint
                    db.scalar(select(User).where(User.id==user.id).with_for_update().execution_options(populate_existing=True))
                    tasks=list(db.scalars(select(Item).where(Item.user_id==user.id,Item.kind.in_(['task','reminder']),Item.status.notin_(['done','archived','cancelled']),visible_items_clause(db,user)).order_by(Item.due_at).limit(6)))
                    text='\n'.join('• '+t.title for t in tasks) or 'No hay tareas guardadas pendientes en Aros. Las fuentes sin sincronizar pueden tener otros asuntos.'
                    from zoneinfo import ZoneInfo
                    note=Item(user_id=user.id,kind='note',area='personal',title='Resumen del día · '+datetime.now(ZoneInfo('Europe/Madrid')).date().isoformat(),description=text,status='active',source='automation-result',external_id=job.id,data={'automation_id':a.id,'run_id':job.id,'empty_selection':not bool(tasks),'source_references':[{'id':t.id,'version':t.version,'content_hash':resource_fingerprint(t)} for t in tasks]})
                    db.add(note); db.flush(); db.add(Inbox(user_id=user.id,item_id=note.id,title=note.title,area='personal',reason='Tu resumen de tareas está preparado.'));result='Resumen guardado en Notas y en tu Inbox.'
                else:raise RuntimeError('Esta automatización ya no está disponible.')
                job=db.get(Run,job.id); db.refresh(job)
                if job.status!='running':db.rollback();continue
                job.status='failed' if a.kind in ['github_releases'] and discovered.get('outcome')=='error' else 'completed'
                job.result=result; job.finished_at=now(); audit(db,user,'automation.'+job.status,a.id); db.commit()
            except Exception as exc:
                db.rollback(); job=db.get(Run,job.id); db.refresh(job)
                if job.status!='running':continue
                job.status='failed'; job.result=str(exc)[:400] if isinstance(exc,RuntimeError) else 'No se pudo completar. Comprueba el conector o el servidor.'; job.finished_at=now()
                db.add(Inbox(user_id=user.id,item_id=None,title='Falló: '+a.title,area='server',reason=job.result)); db.commit()
if __name__=='__main__':
    from .agent_runtime import WorkerPulse
    logging.basicConfig(level=logging.INFO)
    settings.validate_production()
    with WorkerPulse('worker') as pulse:
        while True:
            try:tick();pulse.pulse(cycle=True,error='')
            except Exception:
                logger.exception('Worker cycle failed');pulse.pulse(cycle=True,error='El ciclo de automatizaciones falló; revisa el servicio.')
            time.sleep(10)
