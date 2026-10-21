"""Pure guidance policy: no database, network, model or exception text."""
import copy,json
import pytest
from app.conversation_guidance import tool_guidance,TOOL_DESCRIPTIONS

@pytest.mark.parametrize('code',['permission_denied','write_not_authorized','scope_denied','authorization_invalid','consent_revoked','source_revoked','original_protected'])
def test_permission_veto_wins_over_success_and_rate_limit(code):
    result=tool_guidance('items.create','success',error_code=code,http_status=429,adapter_next_actions=['items.create'])
    assert result['category']=='permission_denied' and result['retryable'] is False
    assert 'nueva autorización' in result['stop_condition']
    assert 'items.create' not in result['next_actions']

@pytest.mark.parametrize('code',['version_conflict','source_changed','operation_conflict','operation_changed'])
def test_conflicts_require_current_read_and_never_replacement_operation_key(code):
    result=tool_guidance('items.update','error',error_code=code)
    assert result['category']=='version_conflict' and result['retryable']
    assert result['next_actions'][0].startswith('resources.read:')
    assert 'sobrescribas' in result['stop_condition']

def test_repetition_requires_new_strategy_and_does_not_invite_duplicate_write():
    result=tool_guidance('items.create','error',error_code='repeated_tool')
    assert result['category']=='change_strategy' and not result['retryable']
    assert 'operation_key' in result['stop_condition']

@pytest.mark.parametrize('code',['source_missing','source_unavailable','no_results'])
def test_missing_sources_offer_scoped_search_without_blind_retry(code):
    result=tool_guidance('resources.read','error',error_code=code)
    assert result['category']=='source_missing' and not result['retryable']
    assert result['next_actions'][0].startswith('resources.search:')
    assert 'ausencia global' in result['stop_condition']

def test_source_limit_narrows_without_discarding_provenance():
    result=tool_guidance('resources.read','error',error_code='source_limit')
    assert result['category']=='narrow_scope'
    assert 'ocultes referencias' in result['stop_condition']

@pytest.mark.parametrize('code,required',[('calendar_range_invalid','end es exclusivo'),('calendar_timezone_invalid','Europe/Madrid')])
def test_calendar_failure_offers_concrete_safe_correction(code,required):
    result=tool_guidance('calendar.list','error',error_code=code)
    assert result['category']=='invalid_arguments' and result['retryable']
    assert required in result['next_actions'][0]

@pytest.mark.parametrize('http_status,category',[(401,'permission_denied'),(403,'permission_denied'),(404,'source_missing'),(409,'version_conflict'),(422,'invalid_arguments'),(429,'rate_limited'),(502,'service_unavailable'),(503,'service_unavailable'),(504,'service_unavailable')])
def test_safe_http_taxonomy(http_status,category):
    result=tool_guidance('resources.read','error',http_status=http_status)
    assert result['category']==category
    if http_status in [429,502,503,504]:assert result['retryable']

@pytest.mark.parametrize('name',['items.create','items.update','printing.favorite'])
@pytest.mark.parametrize('code,http_status',[(None,429),(None,502),(None,503),('timeout',None),('read_timeout',None)])
def test_service_failure_does_not_replay_uncertain_effect(name,code,http_status):
    result=tool_guidance(name,'error',error_code=code,http_status=http_status)
    assert not result['retryable']
    assert 'escritura' in result['stop_condition'] or 'efectos' in result['stop_condition']

@pytest.mark.parametrize('name,first',[('resources.search','resources.read:'),('resources.read','resources.read:'),('items.create','artifact.verify:'),('items.update','artifact.verify:'),('printing.favorite','artifact.verify:'),('artifact.verify','finish:')])
def test_success_offers_next_relevant_step_not_entire_registry(name,first):
    result=tool_guidance(name,'success')
    assert result['category']=='success' and not result['retryable']
    assert result['next_actions'][0].startswith(first)
    assert len(result['next_actions'])<=2

def test_success_preserves_bounded_adapter_hints_without_mutation_or_secrets():
    actions=['weather.current con refresh=true para solicitar condiciones actuales','finish: explica la lectura anterior','weather.current con refresh=true para solicitar condiciones actuales','https://private.invalid/?token=SECRET','items.create: Bearer SECRET',{'secret':'SECRET'},'arbitrary.tool']
    original=copy.deepcopy(actions)
    result=tool_guidance('weather.current','success',adapter_next_actions=actions)
    assert result['next_actions']==actions[:2] and actions==original
    assert 'SECRET' not in json.dumps(result)

def test_bounding_and_untrusted_parameter_content_never_echoed():
    result=tool_guidance('https://private.invalid?token=SECRET','error',error_code='Raw provider traceback password=SECRET',adapter_next_actions=['resources.read: '+('x'*500)])
    assert result['category']=='tool_unavailable'
    assert 'SECRET' not in json.dumps(result) and 'traceback' not in json.dumps(result)
    assert set(result)=={'category','retryable','next_actions','stop_condition'}

def test_descriptions_cover_registered_operational_tools_and_honest_limits():
    from app.conversation_engine import tool_schemas
    assert set(TOOL_DESCRIPTIONS)==set(tool_schemas())
    assert 'NO acredita' in TOOL_DESCRIPTIONS['printing.organizer.create']
    assert 'EXCLUSIVO' in TOOL_DESCRIPTIONS['calendar.list']
    assert 'original' in TOOL_DESCRIPTIONS['resources.read']
    assert 'operation_key' in TOOL_DESCRIPTIONS['items.create']
    assert 'No escribe' in TOOL_DESCRIPTIONS['calendar.list']
    for name in TOOL_DESCRIPTIONS:
        result=tool_guidance(name,'success')
        assert len(result['next_actions'])<=4
        assert all(len(action)<=240 for action in result['next_actions'])

@pytest.mark.parametrize('code',['budget_exhausted','artifact_limit','operation_archived','inference_uncertain','verification_failed'])
def test_terminal_guards_never_retried(code):
    assert tool_guidance('items.create','error',error_code=code)['retryable'] is False

def test_unknown_error_warns_without_fictitious_global_permission_denial():
    result=tool_guidance('calendar.list','error',error_code='unrecognized')
    assert result['category']=='tool_error' and not result['retryable']
    assert 'todas las herramientas' in result['stop_condition']

def test_unknown_tool_is_not_made_retryable_by_rate_limit():
    result=tool_guidance('external.send','error',http_status=429)
    assert result['category']=='tool_unavailable' and not result['retryable']

def test_weather_warning_keeps_cache_honesty_and_adapter_refresh_hint():
    result=tool_guidance('weather.current','warning',adapter_next_actions=['weather.current con refresh=true para solicitar condiciones actuales'])
    assert result['category']=='warning' and not result['retryable']
    assert 'refresh=true' in result['next_actions'][0] and 'pendiente' in result['stop_condition']
