"""Pure planner guidance, without granting permission or scheduling retries.

`retryable` permits considering a corrected next step within the existing budget.
It never authorizes replay of an uncertain write, inference or external effect.
No exception text, provider payload, credentials or resource content is accepted.
"""
import re

TOOL_DESCRIPTIONS = {
    'resources.search': 'Busca recursos visibles del propietario y ámbito elegido. Devuelve candidatos, no su texto completo; una búsqueda acotada no prueba ausencia global. Lee IDs reales antes de sintetizar.',
    'resources.read': 'Lee IDs autorizados y sus versiones actuales. Usa offset y max_chars para continuar texto indexado; respeta next_offset, extracción parcial y falta de visión. No deduzcas que has leído todo el original.',
    'artifact.verify': 'Comprueba un resultado de este turno por propietario, versión, contenido y dependencias. Necesario tras cada escritura; la verificación acredita el resultado registrado, no una acción externa.',
    'weather.current': 'Consulta el tiempo de la ubicación ya configurada. Para condiciones actuales usa refresh=true; si falla, la caché es una lectura anterior. No cambia ciudad, permisos ni proveedor.',
    'calendar.list': 'Consulta eventos guardados que intersectan start incluido y end EXCLUSIVO, YYYY-MM-DD, entre 1 y 62 días, con zona IANA. Para un día usa end=día siguiente. No escribe ni demuestra sincronización externa.',
    'platform.status': 'Lee el estado técnico disponible del servicio. Una lectura en caché no acredita disponibilidad actual; no ejecuta administración, comandos ni cambios del servidor.',
    'connections.status': 'Lee estados registrados y autorización existente del propietario. Una conexión desconectada no se activa aquí; no inicia OAuth ni revela secretos. No confundas configurada con autorizada.',
    'items.create': 'Crea únicamente un elemento interno solicitado por el humano. project_id permite vincularlo a un proyecto interno del mismo área previamente leído, cuando sea el destino solicitado. Conserva fuentes y originales; operation_key estable evita duplicados del turno. Revisa el recibo y llama artifact.verify; no equivale a envío ni publicación.',
    'items.update': 'Actualiza un elemento interno permitido y leído en este turno usando su versión actual. Solo cambios humanos solicitados; no altera originales UV, PRISMA ni fuentes externas. Verifica el recibo después.',
    'printing.favorite': 'Cambia solo el favorito de un modelo 3D permitido, previamente leído, usando su versión actual. Conserva licencia, procedencia e imágenes; no descarga, imprime ni compra. Verifica el recibo.',
    'printing.organizer.create': 'Encola un organizador rectangular abierto con medidas en mm, pared, base y filas/columnas. El humano debe solicitarlo y dar medidas o autorizar valores por defecto. CPU local, sin imagen/GPU/proveedor. El recibo acredita aceptación; NO acredita STL generado ni impresión. Termina con outcome:partial y enlaza el encargo que comprobará el worker.',
}
_WRITES = {'items.create', 'items.update', 'printing.favorite','printing.organizer.create'}
_PERMISSION = {'permission_denied', 'write_not_authorized', 'scope_denied', 'scope_invalid', 'authorization_invalid', 'authorization_revoked', 'consent_revoked', 'source_revoked', 'original_protected', 'artifact_scope'}
_CONFLICT = {'version_conflict', 'source_changed', 'operation_changed', 'operation_conflict'}
_MISSING = {'source_unavailable', 'resource_unavailable', 'project_unavailable', 'model_unavailable', 'source_missing', 'resource_missing', 'no_results', 'not_found'}
_VALIDATION = {'validation_error', 'invalid_arguments', 'invalid_args', 'invalid_event', 'missing_date','missing_dimensions', 'empty_change', 'invalid_change', 'invalid_sources', 'invalid_decision'}


def _result(category, retryable, actions, stop):
    return {'category': category, 'retryable': retryable, 'next_actions': actions[:4], 'stop_condition': stop}


def _adapter_actions(actions):
    """Preserve small operational hints, excluding URLs and secret-like payloads."""
    if not isinstance(actions, (list, tuple)):
        return []
    result = []
    for value in actions[:12]:
        if not isinstance(value, str) or len(value) > 240:
            continue
        value = ' '.join(value.split())
        if not value or re.search(r'https?://|bearer\b|password\b|secret\b|token\s*[:=]|api[_ -]?key\b|authorization\s*:', value, re.I):
            continue
        # Hints describe registered operations, not arbitrary commands/tools.
        if not any(value == name or value.startswith(name + ' ') or value.startswith(name + ':') for name in (*TOOL_DESCRIPTIONS, 'ask', 'finish')):
            continue
        if value not in result:
            result.append(value)
        if len(result) == 4:
            break
    return result


def tool_guidance(name, status, error_code=None, http_status=None, adapter_next_actions=None):
    """Return bounded, deterministic recovery instructions; never raw errors."""
    # Typed policy violations win even if an adapter supplied contradictory status.
    if error_code in _PERMISSION or http_status in (401, 403):
        return _result('permission_denied', False, ['ask: explica el permiso o alcance que falta; no intentes eludirlo.'], 'Detén esta operación hasta una nueva autorización humana válida; no amplíes el alcance.')
    if error_code == 'tool_unknown' or name not in TOOL_DESCRIPTIONS:
        return _result('tool_unavailable', False, ['ask: explica que esta operación no está disponible en este ejecutor.'], 'No inventes herramientas, permisos ni capacidades externas.')
    if error_code in {'budget_exhausted', 'artifact_limit', 'operation_archived', 'inference_uncertain', 'recovery_exhausted', 'verification_failed'}:
        return _result('stop_required', False, ['finish: explica el bloqueo y conserva los resultados confirmados.'], 'No repitas una operación incierta, un resultado archivado ni un paso fuera del presupuesto.')
    if error_code == 'repeated_tool':
        return _result('change_strategy', False, ['resources.search: cambia los términos dentro del alcance, o explica el bloqueo.'], 'No repitas el mismo paso ni cambies operation_key para duplicar un efecto.')
    if error_code in _CONFLICT or http_status == 409:
        return _result('version_conflict', True, ['resources.read: vuelve a leer el ID real y su versión actual.', 'artifact.verify: comprueba cualquier resultado ya registrado antes de otra escritura.'], 'No reutilices una versión anterior ni sobrescribas un resultado idempotente diferente; si el conflicto persiste, pregunta.')
    if error_code == 'source_limit':
        return _result('narrow_scope', True, ['resources.search: concreta la consulta sin ampliar áreas ni permisos.', 'ask: pide una selección menor si el historial supera el límite.'], 'No añadas más fuentes al contexto agotado ni ocultes referencias para guardar un derivado.')
    if error_code in _MISSING or http_status == 404:
        return _result('source_missing', False, ['resources.search: busca otro recurso visible dentro del alcance.', 'ask: pide el ID o dato que falta si no hay candidatos.'], 'No inventes recursos ni interpretes un resultado vacío como ausencia global; no consultes una fuente revocada.')
    if error_code in {'sources_not_read', 'target_not_read'}:
        return _result('read_required', True, ['resources.read: lee el ID autorizado y su versión actual antes de usarlo.'], 'No cites ni modifiques un recurso que aún no has leído.')
    if error_code == 'calendar_range_invalid':
        return _result('invalid_arguments', True, ['calendar.list: corrige start/end YYYY-MM-DD; end es exclusivo, entre 1 y 62 días después. Para un día, end es el día siguiente.'], 'No repitas el rango inválido ni amplíes el límite de 62 días.')
    if error_code == 'calendar_timezone_invalid':
        return _result('invalid_arguments', True, ['calendar.list: utiliza una zona IANA válida, por ejemplo Europe/Madrid.'], 'No repitas la zona inválida ni supongas otra fecha local.')
    if error_code in _VALIDATION or http_status == 422:
        action = ('calendar.list: corrige fechas YYYY-MM-DD, end exclusivo entre 1 y 62 días y zona IANA válida.' if name == 'calendar.list' else 'Corrige los argumentos según el esquema de la herramienta; usa IDs y versiones observados.')
        return _result('invalid_arguments', True, [action], 'No repitas los mismos argumentos inválidos; pregunta si falta un dato humano.')
    if http_status == 429 or error_code in {'rate_limit', 'rate_limited'}:
        return _result('rate_limited', name not in _WRITES, ['Espera el backoff del ejecutor dentro del presupuesto; conserva el mismo modelo y proveedor.'], 'Respeta el límite de reintentos; no cambies proveedor ni reenvíes una escritura sin conocer su resultado.')
    if http_status in (502, 503, 504) or error_code in {'timeout', 'read_timeout', 'connect_timeout', 'service_unavailable', 'tool_timeout'}:
        return _result('service_unavailable', name in TOOL_DESCRIPTIONS and name not in _WRITES, ['connections.status: consulta el estado registrado si corresponde.', 'Conserva la caché identificándola como anterior; comunica el fallo real.'], 'Solo considera un reintento acotado de lectura; no repitas efectos ni afirmes datos actuales sin observación.')
    if status == 'success':
        fallback = {
            'resources.search': ['resources.read: lee IDs reales encontrados antes de sintetizar o editar.'],
            'resources.read': ['resources.read: continúa con next_offset si el objetivo requiere más texto.', 'finish: responde con las fuentes leídas y declara los límites de extracción.'],
            'artifact.verify': ['finish: describe el resultado verificado y su alcance interno.'],
            'weather.current': ['finish: distingue condiciones actuales de una lectura anterior según stale/last_sync.'],
            'calendar.list': ['finish: responde con los eventos observados y la cobertura del intervalo guardado.'],
            'platform.status': ['finish: explica el estado observado y su fecha, sin deducir capacidades de administración.'],
            'connections.status': ['finish: distingue estado registrado, autorización y sincronización.'],
        }
        actions = _adapter_actions(adapter_next_actions) or fallback.get(name, ['artifact.verify: verifica el ID real del recibo antes de afirmar éxito.'])
        return _result('success', False, actions, 'Detente cuando el objetivo esté cubierto por observaciones; toda escritura exige verificación previa a finish.')
    if status == 'warning':
        actions = _adapter_actions(adapter_next_actions) or (['weather.current con refresh=true para solicitar condiciones actuales si la conexión lo permite.'] if name == 'weather.current' else ['finish: comunica la advertencia concreta y qué quedó sin comprobar.'])
        return _result('warning', False, actions, 'No conviertas una caché, limitación o solicitud pendiente en un resultado actual o completado.')
    return _result('tool_error', False, ['Revisa los argumentos y el estado disponible; pregunta si falta información.'], 'No repitas ciegamente el paso ni deduzcas que todas las herramientas carecen de permiso.')
