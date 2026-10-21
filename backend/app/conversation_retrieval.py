"""Scoped lexical retrieval for integrated conversations, without model calls.

Search previews are navigation aids, never completed document reads. The engine
keeps its existing ownership/version fences and supplies its reference builder.
"""
import re
import unicodedata

from sqlalchemy import String, case, cast, func, or_, select
from sqlalchemy.orm import defer

from .agent_capabilities import CapabilityBlocked, source_allowed
from .ai import normalized
from .database import Item
from .source_access import visible_items_clause

CANDIDATE_LIMIT = 500
RESULT_LIMIT = 20
SOURCE_LIMIT = 40
SNIPPET_LIMIT = 500
TOKEN_LIMIT = 12
TECHNICAL_TERMS = frozenset({'ia', 'ai', 'pdf', 'gpt', 'url', 'api', 'css', 'html',
                            'llm', 'mac', 'pdf', 'cpu', 'ram', 'usb', 'mcp', 'cad'})
STOP_WORDS = frozenset({
    'ahora', 'antes', 'anterior', 'anteriores', 'continua', 'dame', 'detalles',
    'documento', 'documentos', 'quiero', 'necesito', 'puedes', 'podrias', 'sobre',
    'para', 'esta', 'este', 'estas', 'estos', 'esas', 'esos', 'tarea', 'tareas',
    'tema', 'temas', 'curso', 'cursos', 'asignatura', 'asignaturas', 'resume',
    'resumen', 'explica', 'explicame', 'prepara', 'preparame', 'hacer', 'hazme',
    'favor', 'material', 'materiales', 'aprendizaje', 'primera', 'segunda',
    'ultimo', 'ultima', 'tambien', 'tengo', 'tiene', 'tienen', 'como', 'todas',
    'todos', 'esto', 'todo', 'entre', 'desde', 'hasta', 'puedo', 'puede',
    'busca', 'buscar', 'encuentra', 'consultar', 'consulta', 'please', 'about',
    'this', 'that', 'with',
})


def search_keywords(query):
    """Retain useful short technical terms while bounding SQL expression size."""
    words = re.findall(r'\w+', normalized(query or ''))
    return [word for word in dict.fromkeys(words)
            if word not in STOP_WORDS and (len(word) > 3 or word in TECHNICAL_TERMS)][:TOKEN_LIMIT]


def _folded(column):
    value = func.lower(func.coalesce(column, ''))
    for accented, plain in [('á', 'a'), ('é', 'e'), ('í', 'i'), ('ó', 'o'),
                            ('ú', 'u'), ('ü', 'u'), ('ñ', 'n'), ('Á', 'a'),
                            ('É', 'e'), ('Í', 'i'), ('Ó', 'o'), ('Ú', 'u'),
                            ('Ü', 'u'), ('Ñ', 'n')]:
        value = func.replace(value, accented, plain)
    return value


def _short_pattern(word):
    if word not in TECHNICAL_TERMS or len(word) > 3:
        return None
    # Underscores/hyphens in filenames are separators; letters and digits in a
    # random identifier are not. GPT6 is a conventional versioned model name.
    term = re.escape(word)
    if word == 'gpt':
        term += r'(?:[0-9]+(?:\.[0-9]+)*)?'
    return r'(^|[^a-z0-9])(' + term + r')([^a-z0-9]|$)'


def _sql_match(field, word):
    pattern = _short_pattern(word)
    # SQLAlchemy implements regexp_match as SQLite's registered REGEXP
    # operator and PostgreSQL's ~ operator, without adding a database function.
    return field.regexp_match(pattern) if pattern else field.contains(word, autoescape=True)


def _has_word(folded_text, word):
    pattern = _short_pattern(word)
    return bool(re.search(pattern, folded_text)) if pattern else word in folded_text


def _match_positions(folded_text, word):
    pattern = _short_pattern(word)
    cursor = 0
    for _ in range(4):
        if pattern:
            match = re.search(pattern, folded_text[cursor:])
            if not match:
                break
            index, end = cursor + match.start(2), cursor + match.end(2)
        else:
            index = folded_text.find(word, cursor)
            if index < 0:
                break
            end = index + len(word)
        yield index
        cursor = end


def resource_metadata(row):
    """Only descriptive fields; never copy arbitrary provider metadata or URLs."""
    data = row.data if isinstance(row.data, dict) else {}
    result = {}
    for key, limit in [('course_title', 240), ('filename', 240), ('category', 80),
                       ('review_state', 80), ('mime', 120), ('extraction_note', 1600)]:
        value = data.get(key)
        if isinstance(value, str):
            result[key] = value[:limit]
    for key in ['extracted', 'indexed', 'partial']:
        if isinstance(data.get(key), bool):
            result[key] = data[key]
    for key in ['page_count', 'size']:
        value = data.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            result[key] = value
    return result


def _fold_with_offsets(text):
    # A decomposed accent can change string length; return actual source offsets.
    chars, offsets = [], []
    for position, char in enumerate(text):
        for folded in unicodedata.normalize('NFD', char.lower()):
            if unicodedata.category(folded) != 'Mn':
                chars.append(folded)
                offsets.append(position)
    return ''.join(chars), offsets


def _matched_window(text, words):
    if not text or not words:
        return '', 0, []
    folded, positions = _fold_with_offsets(text)
    matches = []
    for word in words:
        # Multiple bounded occurrences favor a cluster of query terms over a
        # repeated generic course header at the beginning of a long document.
        for index in _match_positions(folded, word):
            matches.append(positions[index])
    if not matches:
        return '', 0, []
    windows = []
    for position in matches:
        start = max(0, position - 120)
        window = text[start:start + SNIPPET_LIMIT]
        present = [word for word in words if _has_word(normalized(window), word)]
        windows.append((len(present), -start, start, window, present))
    _, _, start, window, present = max(windows)
    return window, start, present


def search_preview(row, words):
    text = row.description or ''
    snippet, offset, terms = _matched_window(text, words)
    data = row.data if isinstance(row.data, dict) else {}
    fields = {'title': row.title or '', 'course_title': data.get('course_title'),
              'filename': data.get('filename'), 'description': text}
    matched_fields = [key for key, value in fields.items()
                      if isinstance(value, str) and any(_has_word(normalized(value), word) for word in words)]
    if not snippet:
        # Title/course matches still provide a useful navigation cue, but cannot
        # invent a passage or pretend that the document body was read.
        snippet = next((value[:SNIPPET_LIMIT] for key, value in fields.items()
                        if key != 'description' and key in matched_fields), '')
        terms = [word for word in words if _has_word(normalized(snippet), word)]
    return {'matched_excerpt': snippet, 'matched_terms': terms,
            'matched_fields': matched_fields, 'matched_offset': offset,
            'read_offset': min(offset, 150000), 'search_preview_only': True,
            'read_offset_limited': offset > 150000}


def execute_resources(db, user, job, execution, name, args, *, ref_builder):
    """Same resource authority as the engine; improve only retrieval and context."""
    if name not in ['resources.search', 'resources.read']:
        raise CapabilityBlocked('tool_unknown', 'Herramienta de lectura desconocida.')
    scope = execution['scope']
    query = select(Item).options(defer(Item.blob)).where(
        Item.user_id == user.id, Item.area.in_(scope['areas']),
        Item.status.notin_(['archived', 'cancelled', 'canceled', 'withdrawn']),
        visible_items_clause(db, user),
    )
    if scope['item_ids']:
        query = query.where(Item.id.in_(scope['item_ids']))
    words, ids = [], []
    if name == 'resources.read':
        ids = list(dict.fromkeys(args['ids']))
        if scope['item_ids'] and not set(ids) <= set(scope['item_ids']):
            raise CapabilityBlocked('scope_denied', 'La lectura está fuera de la selección de este mensaje.')
        query = query.where(Item.id.in_(ids))
        limit = RESULT_LIMIT
        ordering = [Item.updated_at.desc(), Item.id]
    else:
        if args['area'] != 'all':
            if args['area'] not in scope['areas']:
                raise CapabilityBlocked('scope_denied', 'El área no está autorizada en este mensaje.')
            query = query.where(Item.area == args['area'])
        if args.get('kind'):
            query = query.where(Item.kind == args['kind'])
        limit = CANDIDATE_LIMIT
        words = search_keywords(args['query'])
        ordering = []
        if words:
            title, body, metadata = _folded(Item.title), _folded(Item.description), _folded(cast(Item.data, String))
            course = _folded(Item.data['course_title'].as_string())
            fields = [title, body, metadata]
            query = query.where(or_(*(_sql_match(field, word)
                                      for word in words for field in fields)))
            # Rank BEFORE the candidate limit too: 500 recent one-word matches
            # must not hide an older document matching the complete request.
            relevance = sum(case((_sql_match(field, word), weight), else_=0)
                            for word in words for field, weight in
                            [(course, 25), (title, 10), (body, 1), (metadata, 1)])
            ordering.append(relevance.desc())
        ordering.extend([case((Item.area == job.agent_id, 1), else_=0).desc(),
                         Item.updated_at.desc(), Item.id])
    fetched = list(db.scalars(query.order_by(*ordering).limit(limit).with_for_update()
                             .execution_options(populate_existing=True)))
    states = {}
    rows = [row for row in fetched if source_allowed(db, row, user.id, row.area,
            policy_checked=True, connector_states=states)]
    if ids and set(ids) != {row.id for row in rows}:
        raise CapabilityBlocked('source_unavailable', 'Una fuente elegida no está disponible o pertenece a otro usuario.')
    rows = rows[:RESULT_LIMIT]
    refs = {ref['id']: ref for ref in execution['selected_sources']}
    if len(set(refs) | {row.id for row in rows}) > SOURCE_LIMIT:
        raise CapabilityBlocked('source_limit', 'Máximo cuarenta fuentes por turno; concreta la consulta.')
    refs.update({row.id: ref_builder(row) for row in rows})
    execution['selected_sources'] = list(refs.values())
    resources = []
    for row in rows:
        value = {**ref_builder(row), **resource_metadata(row)}
        if name == 'resources.search':
            value.update(search_preview(row, words))
        else:
            text = row.description or ''
            offset = args['offset']
            end = offset + args['max_chars']
            data = row.data if isinstance(row.data, dict) else {}
            original_partial = bool(data.get('partial'))
            warning = data.get('extraction_note')
            warning = warning[:1600] if isinstance(warning, str) else ''
            value.update(excerpt=text[offset:end], excerpt_partial=original_partial or offset > 0 or end < len(text),
                         original_extraction_partial=original_partial,
                         original_text_extracted=data.get('extracted') if isinstance(data.get('extracted'), bool) else None,
                         offset=offset, total_chars=len(text), total_indexed_chars=len(text),
                         next_offset=end if end < len(text) else None, due_at=row.due_at, status=row.status,
                         indexed_text=bool(text),
                         extraction_note=warning or 'Texto indexado por Aros; un documento sin extracción necesita recuperar y procesar su original. Las imágenes no se han visto.')
        resources.append(value)
    response = {'status': 'success', 'summary': f'Consultados {len(resources)} recursos actuales de Aros.',
                'resources': resources, 'search_exhaustive': False, 'artifacts': []}
    if name == 'resources.search':
        response.update(query_terms=words, candidate_limit=CANDIDATE_LIMIT,
                        candidate_limit_reached=len(fetched) == CANDIDATE_LIMIT,
                        candidate_count=len(fetched), result_limit=RESULT_LIMIT,
                        results_truncated=len(fetched) > RESULT_LIMIT,
                        needs_resource_read=True)
    return response
