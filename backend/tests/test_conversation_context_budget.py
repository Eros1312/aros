"""Whole-message budgeting preserves authority, receipts and honest coverage."""
import copy
import json

import pytest

from app.conversation_context import bounded_context


def actual_size(context, messages, instructions):
    messages = [{'role': 'system', 'content': instructions}, *messages,
                {'role': 'user', 'content': json.dumps(context, ensure_ascii=False)}]
    return len(json.dumps(messages, ensure_ascii=False))


def base_context():
    return {
        'user_message': 'Consulta mis clases de mañana y crea el recordatorio que te he pedido.',
        'continued_human_request': 'Crea un recordatorio interno. Respuesta actual: mañana a las 18:00.',
        'authorize_internal_write': True,
        'authorization': {'user_id': 'owner', 'request_hash': 'original-human-request'},
        'scope': {'areas': ['learning', 'personal'], 'item_ids': []},
        'now': '2026-10-02T16:00:00+02:00',
        'tools': {'resources.read': {'properties': {'ids': {'type': 'array'}}},
                  'calendar.list': {'properties': {'start': {'type': 'string'}}}},
        'execution_protocol': {'available': True, 'available_backend_tools': ['resources.read', 'calendar.list']},
        'objectives': [{'id': 'agenda', 'text': 'Consultar las clases de mañana'},
                       {'id': 'reminder', 'text': 'Crear el recordatorio interno solicitado'}],
        'artifacts': [{'id': 'created-reminder', 'version': 2, 'verified': True,
                       'operation': 'create', 'title': 'Repasar gestión', 'content_hash': 'verified-current-hash'}],
        'available_history_resources': [{'id': 'created-reminder', 'version': 2, 'source': 'agent-conversation'}],
        'read_resources': [], 'read_coverage': [], 'search_results': [], 'observations': [],
    }


def test_huge_read_history_and_repeated_calendar_context_fit_complete_message_budget_without_mutation():
    context = base_context()
    context['read_resources'] = [{'id': 'long-document', 'offset': n * 10000, 'excerpt': chr(65 + n) * 10000,
                                  'excerpt_partial': True, 'total_chars': 150000} for n in range(6)]
    context['read_coverage'] = [{'id': 'long-document', 'offset': n * 10000, 'characters': 10000,
                                 'total_chars': 150000, 'excerpt_partial': True} for n in range(6)]
    context['search_results'] = [{'id': 'navigation-' + str(n), 'matched_excerpt': 'e' * 500,
                                  'read_offset': 24000, 'search_preview_only': True} for n in range(40)]
    context['observations'] = [{'tool': 'calendar.list', 'status': 'success', 'summary': 'Agenda consultada.',
                                'data': {'events': [{'id': str(n), 'title': 'Clase ' + 'x' * 800} for n in range(40)]}}
                               for _ in range(4)]
    error = {'tool': 'items.update', 'status': 'error', 'summary': 'Lee la versión actual.',
             'error_code': 'source_changed', 'recovery': {'safe_to_retry': False, 'action': 'reread_resource'},
             'next_actions': ['resources.read'], 'data': {'id': 'created-reminder'}}
    context['observations'].append(error)
    messages = [{'role': role, 'content': 'Mensaje ' + str(n) + ' ' + 'ñ' * 5500}
                for n in range(8) for role in ['user', 'assistant']]
    original_context, original_messages = copy.deepcopy(context), copy.deepcopy(messages)
    instructions = 'Decide el siguiente paso JSON. No amplíes los permisos. ' * 30
    packed, history, stats = bounded_context(context, messages, instructions)
    assert actual_size(packed, history, instructions) == stats['payload_chars'] <= 100000
    assert context == original_context and messages == original_messages
    for key in ['user_message', 'continued_human_request', 'authorization', 'authorize_internal_write',
                'scope', 'now', 'tools', 'execution_protocol', 'objectives', 'artifacts', 'available_history_resources']:
        assert packed[key] == original_context[key]
    assert packed['observations'][-1] == error
    assert packed['context_truncated'] and packed['context_truncation_note']
    assert stats['dropped_history_messages'] and stats['omitted_observation_details']
    assert stats['dropped_chars'] + stats['truncated_chars'] > 0
    assert len(packed['read_coverage']) == 6
    assert all(segment['total_chars'] == 150000 and segment['characters'] == 10000 for segment in packed['read_coverage'])
    assert all(o.get('context_detail_omitted') for o in packed['observations'][:-1] if 'data' not in o)


def test_oldest_whole_segments_are_removed_and_all_coverage_remains_honest():
    context = base_context()
    context['read_resources'] = [{'id': 'document', 'offset': n * 6000, 'excerpt': chr(65 + n) * 6000,
                                 'excerpt_partial': True} for n in range(3)]
    context['read_coverage'] = [{'id': 'document', 'offset': n * 6000, 'characters': 6000,
                                'total_chars': 18000, 'excerpt_partial': True} for n in range(3)]
    packed, history, stats = bounded_context(context, [], 'Lee solo el texto conservado.', max_chars=16000)
    assert stats['payload_chars'] <= 16000 and not history
    assert stats['dropped_read_segments'] == 1
    assert [r['offset'] for r in packed['read_resources']] == [6000, 12000]
    assert packed['read_context_chars'] == 12000 and packed['read_context_truncated']
    assert [r['retained_in_context'] for r in packed['read_coverage']] == [False, True, True]
    assert [r['offset'] for r in packed['read_coverage']] == [0, 6000, 12000]


def test_search_previews_can_be_omitted_without_becoming_document_reads_or_losing_navigation():
    context = base_context()
    context['search_results'] = [{'id': 'result-' + str(n), 'version': 3, 'matched_excerpt': 'p' * 500,
                                  'read_offset': n * 6000, 'matched_fields': ['description'],
                                  'search_preview_only': True} for n in range(20)]
    packed, _, stats = bounded_context(context, [], 'Decide.', max_chars=7500)
    assert stats['payload_chars'] <= 7500 and stats['omitted_search_previews'] > 0
    assert len(packed['search_results']) == 20 and packed['read_resources'] == []
    for n, result in enumerate(packed['search_results']):
        assert result['read_offset'] == n * 6000 and result['version'] == 3
        assert result['search_preview_only'] and 'excerpt' not in result
        if result.get('matched_excerpt_omitted'):
            assert result['matched_excerpt'] == '' and result['context_detail_omitted']


def test_recent_history_keeps_both_ends_with_explicit_abbreviation_and_current_request_unchanged():
    context = base_context()
    messages = [
        {'role': 'user', 'content': 'OBJETIVO ORIGINAL ' + 'u' * 9000 + ' ACLARACIÓN FINAL: a las 18:00'},
        {'role': 'assistant', 'content': 'RESULTADO GUARDADO ' + 'a' * 9000 + ' ID FINAL: created-reminder'},
    ]
    packed, history, stats = bounded_context(context, messages, 'El mensaje actual tiene prioridad.', max_chars=8000)
    assert len(history) == 2 and stats['truncated_history_messages'] == 2
    assert history[0]['content'].startswith('OBJETIVO ORIGINAL') and history[0]['content'].endswith('a las 18:00')
    assert history[1]['content'].startswith('RESULTADO GUARDADO') and history[1]['content'].endswith('created-reminder')
    assert 'Mensaje histórico abreviado' in history[0]['content']
    assert packed['user_message'] == context['user_message'] and packed['continued_human_request'] == context['continued_human_request']
    assert stats['payload_chars'] <= 8000


def test_complete_budget_counts_nested_json_escapes_and_preserves_unicode():
    context = base_context()
    context['read_resources'] = [{'id': 'quoted-document', 'offset': 0,
                                  'excerpt': ('España 🌈 "texto" \\ salto\n' * 700), 'excerpt_partial': False}]
    context['read_coverage'] = [{'id': 'quoted-document', 'offset': 0, 'characters': 16800}]
    instructions = 'Responde en español y conserva los caracteres Unicode.'
    packed, history, stats = bounded_context(context, [], instructions, max_chars=50000)
    assert actual_size(packed, history, instructions) == stats['payload_chars'] <= 50000
    assert 'España 🌈' in packed['read_resources'][0]['excerpt']
    # Returned stats do not alias the counters in the copied user context.
    stats['dropped_chars'] = -1
    assert packed['context_budget']['dropped_chars'] == 0


def test_preexisting_read_omission_is_marked_in_full_coverage_even_without_new_trimming():
    context = base_context()
    context['read_resources'] = [{'id': 'document', 'offset': 6000, 'excerpt': 'Texto actual.'}]
    context['read_coverage'] = [{'id': 'document', 'offset': 0, 'characters': 6000},
                                {'id': 'document', 'offset': 6000, 'characters': 12}]
    context['read_context_truncated'] = True
    packed, _, stats = bounded_context(context, [], 'No inventes texto omitido.')
    assert packed['context_truncated'] and stats['context_truncated']
    assert [r['retained_in_context'] for r in packed['read_coverage']] == [False, True]
    assert stats['dropped_read_segments'] == 0


@pytest.mark.parametrize('field', ['tools', 'user_message', 'authorization'])
def test_unfit_fixed_context_raises_controlled_error_without_cutting_current_authority(field):
    context = base_context()
    context[field] = 'PRIVATE_FIXED_VALUE_' * 1000
    original = copy.deepcopy(context)
    with pytest.raises(ValueError, match='contexto fijo excede') as caught:
        bounded_context(context, [], 'Protocol.', max_chars=3000)
    assert 'PRIVATE_FIXED_VALUE' not in str(caught.value) and context == original


def test_latest_error_is_not_trimmed_to_force_an_impossible_budget():
    context = base_context()
    context['observations'] = [{'tool': 'calendar.list', 'status': 'error', 'summary': 'Consulta rechazada.',
                                'recovery': {'required_details': 'e' * 15000}, 'next_actions': ['calendar.list']}]
    original = copy.deepcopy(context)
    with pytest.raises(ValueError, match='contexto fijo excede'):
        bounded_context(context, [], 'Protocol.', max_chars=4000)
    assert context == original


@pytest.mark.parametrize('invalid', [0, -1, True, '1000'])
def test_invalid_budget_is_a_controlled_value_error(invalid):
    with pytest.raises(ValueError, match='formato válido'):
        bounded_context(base_context(), [], 'Protocol.', max_chars=invalid)
