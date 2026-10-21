"""Pure, measured context packing for the configured conversation protocol.

Limits the complete serialized message array, including the nested JSON user
context. Authority and current requests are never shortened to fit the budget.
"""
import copy
import json

HISTORY_MARKER = '\n[… Mensaje histórico abreviado por el presupuesto de contexto …]\n'
TRUNCATION_NOTE = ('Parte del historial o de los detalles de herramientas se ha omitido por el presupuesto de contexto. '
                   'Los fragmentos conservados y la cobertura indican qué texto está disponible aquí; '
                   'no des por leído el contenido omitido ni inventes sus detalles.')


def _json(value):
    return json.dumps(value, ensure_ascii=False)


def _payload_chars(context, messages, instructions):
    payload = [{'role': 'system', 'content': instructions}, *messages,
               {'role': 'user', 'content': _json(context)}]
    return len(_json(payload))


def _list(context, key):
    value = context.get(key)
    return value if isinstance(value, list) else []


def _abbreviate(value, limit):
    if not isinstance(value, str) or len(value) <= limit:
        return value
    available = max(0, limit - len(HISTORY_MARKER))
    head = (available * 2) // 3
    tail = available - head
    return value[:head] + HISTORY_MARKER + (value[-tail:] if tail else '')


def bounded_context(context, messages, instructions, max_chars=100000):
    """Return copied context/history/stats or ValueError for unfit fixed input.

    Only explicit dynamic context fields are reduced. Tools, execution protocol,
    current human request and authorization, scope, dates, objectives, artifact
    receipts and resource identities remain intact. No provider or DB access.
    """
    if (not isinstance(context, dict) or not isinstance(messages, list)
            or not isinstance(instructions, str) or not isinstance(max_chars, int)
            or isinstance(max_chars, bool) or max_chars < 1):
        raise ValueError('El contexto o su presupuesto no tienen un formato válido.')
    packed, history = copy.deepcopy(context), copy.deepcopy(messages)
    try:
        original_chars = _payload_chars(packed, history, instructions)
    except (TypeError, ValueError) as exc:
        raise ValueError('El contexto no se puede serializar.') from exc
    stats = {
        'max_chars': max_chars, 'original_payload_chars': original_chars,
        'dropped_history_messages': 0, 'truncated_history_messages': 0,
        'omitted_search_previews': 0, 'omitted_observation_details': 0,
        'dropped_read_segments': 0, 'dropped_chars': 0, 'truncated_chars': 0,
        'context_truncated': bool(packed.get('context_truncated') or packed.get('read_context_truncated')),
    }

    def refresh():
        reads = _list(packed, 'read_resources')
        if 'read_resources' in packed:
            packed['read_context_chars'] = sum(len(r.get('excerpt', '')) for r in reads
                                               if isinstance(r, dict) and isinstance(r.get('excerpt'), str))
        retained = {(r.get('id'), r.get('offset', 0)) for r in reads if isinstance(r, dict)}
        for segment in _list(packed, 'read_coverage'):
            if isinstance(segment, dict):
                segment['retained_in_context'] = (segment.get('id'), segment.get('offset', 0)) in retained
        packed['context_truncated'] = stats['context_truncated']
        packed['context_budget'] = dict(stats)
        if stats['context_truncated']:
            packed['context_truncation_note'] = TRUNCATION_NOTE
        return _payload_chars(packed, history, instructions)

    def over_budget():
        return refresh() > max_chars

    def mark_truncated():
        stats['context_truncated'] = True

    # Preserve the most recent historical exchange before sacrificing current
    # document passages. Earlier complete user/assistant pairs are removed whole.
    while over_budget() and len(history) > 2:
        count = 2 if (isinstance(history[0], dict) and isinstance(history[1], dict)
                      and history[0].get('role') == 'user' and history[1].get('role') == 'assistant') else 1
        count = min(count, len(history) - 2)
        removed = history[:count]
        del history[:count]
        stats['dropped_history_messages'] += count
        stats['dropped_chars'] += len(_json(removed))
        mark_truncated()

    # A recent pair retains both its opening and its ending, including a final
    # clarification or a result identifier, with an explicit abbreviation marker.
    for message in history:
        if not over_budget():
            break
        if isinstance(message, dict) and isinstance(message.get('content'), str):
            original = message['content']
            abbreviated = _abbreviate(original, 2000)
            if abbreviated != original:
                message['content'] = abbreviated
                stats['truncated_history_messages'] += 1
                stats['truncated_chars'] += len(original) - len(abbreviated)
                mark_truncated()

    # Search excerpts are navigation aids, not document reads. IDs, versions,
    # matched fields/terms, metadata and read offsets remain available.
    for result in _list(packed, 'search_results'):
        if not over_budget():
            break
        if isinstance(result, dict) and isinstance(result.get('matched_excerpt'), str) and result['matched_excerpt']:
            stats['truncated_chars'] += len(result['matched_excerpt'])
            result['matched_excerpt'] = ''
            result['matched_excerpt_omitted'] = True
            result['context_detail_omitted'] = True
            stats['omitted_search_previews'] += 1
            mark_truncated()

    observations = _list(packed, 'observations')
    latest_error = next((index for index in range(len(observations) - 1, -1, -1)
                         if isinstance(observations[index], dict) and observations[index].get('status') == 'error'), None)
    latest_by_tool = {}
    for index, observation in enumerate(observations):
        if isinstance(observation, dict):
            latest_by_tool[observation.get('tool')] = index
    # First omit repeated older tool details, then other older detail payloads.
    # The latest error is protected in full, including its corrective recovery.
    ordering = sorted(range(len(observations)), key=lambda index: (
        latest_by_tool.get(observations[index].get('tool')) == index
        if isinstance(observations[index], dict) else True, index))
    for index in ordering:
        if not over_budget():
            break
        observation = observations[index]
        if index == latest_error or not isinstance(observation, dict):
            continue
        removed = {key: observation.pop(key) for key in ['data', 'resources'] if key in observation}
        if removed:
            stats['dropped_chars'] += len(_json(removed))
            observation['context_detail_omitted'] = True
            stats['omitted_observation_details'] += 1
            mark_truncated()

    # Whole segments retain their exact offsets and extraction status. Coverage
    # is never dropped and says which indexed segments are no longer in context.
    reads = _list(packed, 'read_resources')
    while over_budget() and reads:
        removed = reads.pop(0)
        stats['dropped_chars'] += len(_json(removed))
        stats['dropped_read_segments'] += 1
        packed['read_context_truncated'] = True
        mark_truncated()

    # Even the last historical exchange is dynamic; current user_message and
    # continued_human_request in packed are immutable throughout this function.
    while over_budget() and history:
        removed = history.pop(0)
        stats['dropped_chars'] += len(_json(removed))
        stats['dropped_history_messages'] += 1
        mark_truncated()

    final_chars = refresh()
    if final_chars > max_chars:
        raise ValueError('El contexto fijo excede el presupuesto; la petición y sus permisos no se han recortado.')
    stats['payload_chars'] = final_chars
    # Avoid a self-referential measurement: payload_chars is returned separately;
    # context_budget retains fixed counters and the configured maximum.
    return packed, history, copy.deepcopy(stats)
