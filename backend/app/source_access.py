"""Active source permissions, independent of sync liveness.

Pausing stops ingestion, while revoking removes remote-source records from active
queries. Originals explicitly imported from the Mac/browser remain local copies.
"""
import hashlib, json
from sqlalchemy import select, true, and_, func
from sqlalchemy.orm import defer
from .database import Connector, Item

SOURCE_CONNECTORS = {
    'gmail': 'gmail', 'gmail-order': 'gmail', 'calendar': 'calendar',
    'drive': 'drive', 'canva': 'canva', 'rss': 'rss',
    'github': 'github', 'notion': 'notion', 'vercel': 'vercel',
    'github-release': 'github-releases',
}
INACTIVE_STATES = ('archived', 'cancelled', 'canceled', 'withdrawn')
DERIVED_SOURCES = {'agent-execution','agent-selection','agent-autonomous','agent-conversation','automation-result','agent-review'}


def resource_fingerprint(item):
    payload = {'id': item.id, 'version': item.version, 'title': item.title,
               'description': item.description, 'kind': item.kind, 'area': item.area,
               'source': item.source, 'status': item.status,
               'sha256': (item.data or {}).get('sha256')}
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True,
                                   separators=(',', ':')).encode()).hexdigest()


def effect_fingerprint(item):
    """Versioned effect proof; existing provenance hashes retain their contract."""
    payload={'resource':resource_fingerprint(item),'due_at':item.due_at,
             'priority':item.priority,'data':item.data or {}}
    return hashlib.sha256(json.dumps(payload,ensure_ascii=False,sort_keys=True,
                                    separators=(',',':')).encode()).hexdigest()


def blocked_sources(db, user):
    revoked = set(db.scalars(select(Connector.id).where(
        Connector.user_id == user.id,
        Connector.status.in_(['disconnected', 'revoked']),
    )))
    return [source for source, connector in SOURCE_CONNECTORS.items() if connector in revoked]


def visible_sources_clause(db, user):
    denied = blocked_sources(db, user)
    return Item.source.notin_(denied) if denied else true()


def available_source_clause(db, user):
    """Availability applies to direct reads and to every ancestor of a copy."""
    return and_(visible_sources_clause(db, user),
                func.coalesce(Item.data['revoked'].as_boolean(), False) == False,
                func.coalesce(Item.data['access_revoked'].as_boolean(), False) == False,
                func.coalesce(Item.data['available'].as_boolean(), True) == True)


def visible_items_clause(db, user):
    clause = available_source_clause(db, user)
    # A copied excerpt must not bypass revocation, deletion or changed provenance.
    artifacts = list(db.execute(select(Item.id, Item.data, Item.source).where(
        Item.user_id == user.id,
    )))
    references = {}
    empty_allowed = set()
    malformed = set()
    for ident, data, source in artifacts:
        if source not in DERIVED_SOURCES and (not isinstance(data,dict) or 'source_references' not in data):continue
        refs = data.get('source_references', []) if isinstance(data, dict) else []
        if source not in DERIVED_SOURCES and refs==[]:continue
        if not isinstance(refs, list) or len(refs)>100 or any(not isinstance(r,dict) or not isinstance(r.get('id'),str) or not isinstance(r.get('version'),int) for r in refs):
            malformed.add(ident);refs=[]
        references[ident] = refs
        if source == 'automation-result' and isinstance(data, dict) and data.get('empty_selection') is True:
            empty_allowed.add(ident)
        if source in ['agent-conversation','manual'] and isinstance(data, dict) and data.get('origin_user_prompt') is True:
            empty_allowed.add(ident)
    ids = {r['id'] for refs in references.values() for r in refs}
    current = {}
    if ids:
        for item in db.scalars(select(Item).options(defer(Item.blob)).where(
            Item.user_id == user.id, Item.id.in_(ids), clause,
            Item.status.notin_(INACTIVE_STATES),
        )):
            current[item.id] = (item.version, resource_fingerprint(item))
    # Cycles cannot establish a trustworthy origin. Resolve from leaves; any
    # unresolvable component (including descendants of cycles) remains hidden.
    authorized = set()
    unresolved = set(references)-malformed
    for _ in range(len(references)+1):
        ready={ident for ident in unresolved if
               (references[ident] or ident in empty_allowed) and all(
                   ref['id'] in current and
                   (ref['id'] not in references or ref['id'] in authorized) and
                   ref.get('version') == current[ref['id']][0] and
                   (not ref.get('content_hash') or ref['content_hash'] == current[ref['id']][1])
                   for ref in references[ident])}
        if not ready:break
        authorized.update(ready);unresolved.difference_update(ready)
    unavailable = set(references)-authorized
    return and_(clause, Item.id.notin_(unavailable)) if unavailable else clause
