"""Conservative multi-reading requirements; no effects, providers or DB reads."""
import pytest
from app.conversation_objectives import required_read_objective_tools

def test_real_weather_and_selected_public_model_request():
    prompt='Consulta el tiempo actual de Ciudad de ejemplo, España, y busca y lee la ficha pública del catálogo de documentos que está seleccionada en el alcance de esta prueba. Resume para qué sirve el modelo y qué licencia tiene.'
    assert required_read_objective_tools(prompt)==['weather.current','resources.read']

def test_three_explicit_read_objectives_and_ellipsis():
    prompt='Consulta mi calendario de mañana y el tiempo actual de Ciudad de ejemplo. Después busca un documento de laboratorio y lee su ficha.'
    assert required_read_objective_tools(prompt)==['weather.current','calendar.list','resources.read']

@pytest.mark.parametrize('prompt',[
    'Consulta calendario y tiempo',
    'Dime el tiempo y revisa mi agenda',
    '¿Puedes consultar el calendario y el tiempo de mañana?',
    'Quiero que consultes mi horario y muestres la previsión meteorológica',
])
def test_explicit_weather_calendar_pairs(prompt):
    assert required_read_objective_tools(prompt)==['weather.current','calendar.list']

@pytest.mark.parametrize('prompt',[
    'Resume documento sobre tiempo y calendarios',
    'Lee el documento sobre tiempo y calendarios',
    'El documento menciona consulta el tiempo y el calendario',
    'Explica el texto: Consulta el tiempo y el calendario',
    'Consulta el tiempo',
    'Revisa la agenda',
    'Busca y lee la ficha del documento',
    'No consultes el calendario ni el tiempo',
    'No consulta el calendario y no muestra el tiempo',
    'Consulta el calendario y no el tiempo',
    'No consultes el calendario. Busca y lee una ficha',
    'Resume esta cita: "Consulta calendario y tiempo"',
    'Resume: «Consulta calendario y tiempo»',
    'Resume: “Consulta calendario y tiempo”',
    "Resume: 'Consulta calendario y tiempo'",
    'Resume: `Consulta calendario y tiempo`',
    'Resume: ```\nConsulta calendario y tiempo\n```',
    '',None,
])
def test_mentions_negations_quotations_and_single_queries_do_not_require_plan(prompt):
    assert required_read_objective_tools(prompt)==[]

@pytest.mark.parametrize('prompt',[
    'No consultes el calendario. Consulta el tiempo y lee la ficha del modelo.',
    'Consulta el tiempo, no consultes el calendario, y lee la ficha seleccionada.',
    'No consultes calendario pero consulta el tiempo y lee la ficha.',
    'Consulta el tiempo y lee la ficha. No consultes el calendario.',
    'Consulta el tiempo y revisa el documento sobre calendarios y meteorología.',
    'Consulta el tiempo. Resume la cita «Consulta calendario y tiempo». Lee la ficha.',
])
def test_negated_calendar_and_document_topics_not_added_to_positive_pair(prompt):
    assert required_read_objective_tools(prompt)==['weather.current','resources.read']

def test_requirements_deduplicated_and_never_add_write_or_external_permissions():
    prompt='Consulta el tiempo y el calendario. Revisa mi calendario. Lee la ficha pública. Crea dos notas y envía un correo.'
    result=required_read_objective_tools(prompt)
    assert result==['weather.current','calendar.list','resources.read']
    assert required_read_objective_tools(prompt)==result
    assert not any(name.startswith('items.') or 'send' in name for name in result)
