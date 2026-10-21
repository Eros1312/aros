"""Specialist retrieval ranks course metadata and refreshes isolated historical references."""
from types import SimpleNamespace
from sqlalchemy import inspect
from app.ai import agent_conversation_context, context_items
from app.database import Item, User
from test_university import db, user


def document(db,user,title,course,**kwargs):
    item=Item(user_id=user.id,kind='document',area='university',title=title,description=kwargs.pop('description','Texto del documento'),
              data={'course_title':course},blob=b'file',**kwargs)
    db.add(item);db.flush()
    return item


def previous(user,source_ids=(),applied_ids=()):
    return [SimpleNamespace(user_id=user.id,agent_id='university',sources=[{'id':id} for id in source_ids],
                            actions=[{'applied_id':id} for id in applied_ids])]


def test_specialist_finds_old_biochemistry_document_after_twenty_new_documents(db,user):
    bio=document(db,user,'Tema 1.pdf','Bioquímica',updated_at='2026-01-01T00:00:00+00:00')
    bio_id=bio.id
    for n in range(20):document(db,user,f'Nuevo {n}.pdf','Química orgánica',updated_at='2026-09-01T00:00:00+00:00')
    db.commit();db.expire_all()
    result=agent_conversation_context(db,user,'Resume los documentos de Bioquímica',[],'university')
    assert result[0].id==bio_id and len(result)==16
    assert all('blob' in inspect(item).unloaded for item in result)
    assert context_items(db,user,'Bioquímica')[0].id==bio_id


def test_explicit_new_course_outranks_old_references_but_followup_preserves_ties(db,user):
    bio=document(db,user,'Documento.pdf','Bioquímica')
    organic=document(db,user,'Documento nuevo.pdf','Química orgánica')
    history=previous(user,[bio.id])
    assert agent_conversation_context(db,user,'Dame detalles de esa asignatura',history,'university')[0].id==bio.id
    changed=agent_conversation_context(db,user,'Ahora resume Química orgánica',history,'university')
    assert changed[0].id==organic.id


def test_course_metadata_outweighs_incidental_body_match(db,user):
    actual=document(db,user,'Tema 1.pdf','Bioquímica',description='Introducción a la materia')
    unrelated=document(db,user,'Bioquímica mencionada.pdf','Química orgánica',description='Incluye comparación con bioquímica')
    result=agent_conversation_context(db,user,'Bioquímica',[],'university')
    assert result[0].id==actual.id and result[1].id==unrelated.id


def test_retrieval_and_rehydration_exclude_other_owner_area_and_inactive_records(db,user):
    own=document(db,user,'Propio.pdf','Bioquímica')
    other=User(email='private-retrieval@fixture',password_hash='fixture');db.add(other);db.flush()
    secret=document(db,other,'Privado.pdf','Bioquímica')
    archived=document(db,user,'Archivado.pdf','Bioquímica',status='archived')
    cancelled=document(db,user,'Cancelado.pdf','Bioquímica',status='cancelled')
    wrong_area=Item(user_id=user.id,kind='document',area='personal',title='Bioquímica privada de otra área')
    db.add(wrong_area);db.flush()
    result=agent_conversation_context(db,user,'Bioquímica',previous(user,[secret.id,archived.id,cancelled.id,wrong_area.id]),'university')
    assert [item.id for item in result]==[own.id]
    foreign_history=previous(other,[secret.id])
    assert [item.id for item in agent_conversation_context(db,user,'Bioquímica',foreign_history,'university')]==[own.id]


def test_applied_reference_outside_candidate_window_is_refreshed(db,user):
    applied=document(db,user,'Anterior.pdf','Bioquímica',updated_at='2020-01-01T00:00:00+00:00')
    for n in range(505):document(db,user,f'Reciente {n}.pdf','Química orgánica',updated_at='2026-09-01T00:00:00+00:00')
    result=agent_conversation_context(db,user,'Dame detalles de esa tarea',previous(user,applied_ids=[applied.id]),'university')
    assert result[0].id==applied.id and len(result)==16
    applied.description='Texto actualizado';db.flush()
    assert agent_conversation_context(db,user,'Dame detalles',previous(user,applied_ids=[applied.id]),'university')[0].description=='Texto actualizado'
    db.delete(applied);db.flush()
    assert applied.id not in [item.id for item in agent_conversation_context(db,user,'Dame detalles',previous(user,applied_ids=[applied.id]),'university')]
