"""Public task objectives, never model reasoning or permission to act.

Completion follows observations and verified receipts, not a model's claim.
The plan is immutable within a turn and does not change the human scope.
"""
from pydantic import BaseModel, ConfigDict, Field
from .agent_capabilities import CapabilityBlocked
import re

WRITE_TOOLS = {'items.create', 'items.update'}

def required_read_objective_tools(prompt):
    """Recognize explicit multi-reading requests, never permission or NLP intent.

    Only command-like clauses are inspected. Topic words inside a requested
    document do not become weather/calendar objectives. Single-type requests
    return no requirement; the planner handles those through the normal path.
    """
    import unicodedata
    if not isinstance(prompt,str):return []
    text=''.join(c for c in unicodedata.normalize('NFKD',prompt.lower()) if not unicodedata.combining(c))
    text=re.sub(r'```[\s\S]*?```|`[^`]*`|"[^"]*"|«[^»]*»|“[^”]*”|\u2018[^\u2019]*\u2019|\x27[^\x27]*\x27','',text)
    verbs=r'consulta|consultar|consultes|dime|muestra|muestrame|muestres|revisa|revisar|revises|busca|buscar|busques|lee|leer|leas'
    boundary=r'(?:^|[,;.!?¿¡\n]\s*|\b(?:y|pero|despues|luego)\s+)'
    prefix=r'(?:(?:por favor|ahora|despues|luego|tambien)[,]?\s+)*'
    request=r'(?:(?:puedes|podrias|quiero|necesito)\s+(?:que\s+)?(?:me\s+)?)?'
    commands=list(re.finditer(boundary+prefix+request+r'(?P<negative>(?:no|sin)\s+)?(?P<verb>'+verbs+r')\b',text))
    objects={
        'weather.current':r'\b(?:tiempo|clima|temperatura|prevision(?:\s+meteorologica)?|meteorologia)\b',
        'calendar.list':r'\b(?:calendarios?|agenda|horario)\b',
        'resources.read':r'\b(?:fichas?|documentos?|archivos?|recursos?|modelo(?:s)?|catalogos?|notas?|apuntes?|material(?:es)?|guion(?:es)?|informes?|manual(?:es)?|pdf|carteles?|textos?)\b',
    }
    found=set()
    for index,command in enumerate(commands):
        if command.group('negative'):continue
        end=commands[index+1].start() if index+1<len(commands) else len(text)
        clause=re.split(r'[.!?;\n]',text[command.end():end],maxsplit=1)[0][:240]
        # Elliptical prohibitions share the current verb: "consulta calendario
        # y no tiempo". Their topic is not another positive objective.
        clause=re.split(r'\b(?:no|sin)\b',clause,maxsplit=1)[0]
        matches={name:re.search(pattern,clause) for name,pattern in objects.items()}
        resource=matches['resources.read']
        first=min((match.start() for match in matches.values() if match),default=None)
        if resource and resource.start()==first:
            found.add('resources.read')
        else:
            found.update(name for name,match in matches.items() if match and name!='resources.read')
    order=['weather.current','calendar.list','resources.read']
    return [name for name in order if name in found] if len(found)>=2 else []

def minimum_creation_objectives(prompt):
    # A narrow workflow check, never permission. Complex natural language is
    # still planned by the model; quoted titles/source text cannot add objectives.
    from .conversation_tools import normalized,human_intent
    if not human_intent(prompt,'items.create'):return 0
    text=re.sub(r'"[^"]*"|«[^»]*»|“[^”]*”|`[^`]*`','',normalized(prompt))
    explicit=re.search(r'\b(?:crea|crear|crees|guarda|guardar|anade|anadir|apunta|programa)\s+(?:me\s+)?(dos|tres|cuatro|[2-4])\s+(?:notas|tareas|recordatorios|eventos|proyectos)\b',text)
    if explicit:return {'dos':2,'tres':3,'cuatro':4}.get(explicit.group(1),int(explicit.group(1)) if explicit.group(1).isdigit() else 0)
    pattern=r'(?:\b(?:crea|crear|guarda|anade|apunta|programa)\b|\by\b)\s+(?:(?:tambien|despues|luego)\s+)?(?:(?:un|una|otro|otra|el|la)\s+)?(?:nota|tarea|recordatorio|evento|proyecto)\b'
    count=len(re.findall(pattern,text))
    return min(count,4) if count>1 else 0

class Objective(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, str_strip_whitespace=True)
    id: str = Field(min_length=1, max_length=40, pattern=r'^[A-Za-z0-9_-]+$')
    text: str = Field(min_length=1, max_length=240)
    tool: str = Field(min_length=1, max_length=40)

def accept_objectives(execution, decision, tools):
    proposed = decision.get('objectives') or []
    existing = execution.get('objectives') or []
    minimum=minimum_creation_objectives(execution['command'].get('authorization_prompt',execution['command']['text']))
    candidate=proposed or existing
    required=required_read_objective_tools(execution['command'].get('authorization_prompt',execution['command']['text']))
    if required and not set(required)<=set(goal['tool'] for goal in candidate):
        raise ValueError('plan_each_requested_read')
    if minimum and len([goal for goal in candidate if goal['tool']=='items.create'])<minimum:
        raise ValueError('plan_each_requested_creation')
    if proposed:
        if len({goal['id'] for goal in proposed}) != len(proposed):
            raise ValueError('duplicate_objectives')
        if any(goal['tool'] not in tools for goal in proposed):
            raise ValueError('unknown_objective_tool')
        normalized = [{key:goal[key] for key in ('id','text','tool')} for goal in proposed]
        if existing:
            if normalized != [{key:goal[key] for key in ('id','text','tool')} for goal in existing]:
                raise ValueError('immutable_objectives')
        else:
            if execution['budget']['tool_calls']:
                raise ValueError('late_objectives')
            execution['objectives'] = [{**goal, 'status':'pending', 'artifact_ids':[]} for goal in normalized]
    ident = decision.get('objective_id')
    if decision['kind']=='tool' and any(goal['tool']==decision['tool'] for goal in execution.get('objectives',[])) and not ident:
        raise ValueError('objective_id_required')
    if ident:
        goal = next((goal for goal in execution.get('objectives',[]) if goal['id']==ident),None)
        if goal is None or (decision['tool'] != goal['tool'] and decision['tool'] != 'artifact.verify'):
            raise ValueError('objective_tool_mismatch')

def observe_objective(execution, decision, result):
    ident = decision.get('objective_id')
    if not ident or result.get('status') not in ['success','warning']:
        return
    goal = next(goal for goal in execution.get('objectives',[]) if goal['id']==ident)
    if decision['tool'] in WRITE_TOOLS:
        if decision['tool']=='items.create':
            receipt_ids={receipt['id'] for receipt in result.get('artifacts',[]) if isinstance(receipt,dict) and isinstance(receipt.get('id'),str)}
            other_ids={artifact for other in execution.get('objectives',[]) if other['id']!=ident and other['tool']=='items.create' for artifact in other['artifact_ids']}
            if receipt_ids & other_ids:
                raise CapabilityBlocked('duplicate_objective_effect','Ese resultado ya acredita otro objetivo de creación. Completa el segundo elemento solicitado con sus propios parámetros, sin reutilizar el mismo recibo.')
        goal['artifact_ids'] = list(dict.fromkeys([*goal['artifact_ids'], *[
            receipt['id'] for receipt in result.get('artifacts',[])
            if isinstance(receipt,dict) and isinstance(receipt.get('id'),str)]]))
    elif decision['tool'] == goal['tool']:
        # A warning can be a useful stale reading, but cannot prove completion.
        if result.get('status') == 'success':goal['status']='completed'
    refresh_objectives(execution)

def refresh_objectives(execution):
    verified = {receipt['id'] for receipt in execution.get('artifacts',[]) if receipt.get('verified')}
    for goal in execution.get('objectives',[]):
        if goal['tool'] in WRITE_TOOLS:
            goal['status'] = 'completed' if goal['artifact_ids'] and set(goal['artifact_ids'])<=verified else 'pending'
    execution['pending_objectives'] = [goal['id'] for goal in execution.get('objectives',[]) if goal['status']!='completed']

def terminal_summary(execution, decision):
    refresh_objectives(execution)
    pending = [goal for goal in execution.get('objectives',[]) if goal['status']!='completed']
    if pending and decision.get('outcome','completed') != 'partial':
        raise CapabilityBlocked('objectives_pending','Quedan partes de la solicitud sin comprobar. Continúa con ellas o termina con outcome:partial y explica lo pendiente.')
    partial = bool(pending or decision.get('outcome')=='partial')
    execution['outcome'] = 'partial' if partial else 'completed'
    text = decision['summary']
    if pending:
        text += '\n\nPendiente de completar o comprobar:\n' + '\n'.join('- '+goal['text'] for goal in pending)
    return text
