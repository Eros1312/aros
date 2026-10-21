'use client';
import {FileText,FolderOpen,ChevronRight,X} from 'lucide-react';
import {areaNames,type Item} from '../lib/api';

export default function AssistantContext({item,project,onOpen,onProject,onClear,onLeave}:{item:Item|null;project?:Item|null;onOpen:()=>void;onProject?:()=>void;onClear:()=>void;onLeave?:()=>void}){
 return <section className="assistant-context" aria-label="Contexto seleccionado para Aros">
  {project&&<><button type="button" className="context-resource context-project" onClick={onProject}><FolderOpen size={18}/><span><small>Conversación de proyecto · {areaNames[project.area]}</small><strong>{project.title}</strong><small>Historial separado · versión {project.version}</small></span><ChevronRight size={16}/></button><button type="button" className="icon-button" onClick={onLeave} aria-label="Salir del contexto del proyecto"><X size={18}/></button></>}
  {item&&item.id!==project?.id&&<><button type="button" className="context-resource" onClick={onOpen}><FileText size={18}/><span><small>Recurso seleccionado · {areaNames[item.area]}</small><strong>{item.title}</strong><small>{item.source} · versión {item.version}</small></span><ChevronRight size={16}/></button><button type="button" className="icon-button" onClick={onClear} aria-label="Quitar recurso del contexto"><X size={18}/></button></>}
  <p>{project&&!item?'Aros puede consultar la ficha del proyecto. Elige un material desde su mesa de trabajo para consultarlo.':project?'Aros puede consultar la ficha del proyecto y este recurso seleccionado.':'Esta consulta se limita al recurso seleccionado.'} {project?' Los resultados comprobados de esta conversación también pueden servir para continuar.':''} Los pasos mostrarán qué se ha leído.</p>
 </section>;
}
