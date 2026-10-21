/* Run against the compiled workflow helper; no browser or network access. */
const assert=require('node:assert/strict');
const {canonicalView,parseWorkspaceRoute,visibleRecords,contextPayload,linkedProjectIds,conversationPath,belongsToProject}=require(process.argv[2]);
const item={id:'resource-12345678',kind:'document',area:'university',title:'Termodinámica',source:'university-browser',version:7,status:'active',data:{}};
assert.equal(canonicalView('overview'),'today');
for(const route of ['today','chat','agents','printing','university','library'])assert.equal(canonicalView(route),route,'legacy routes stay available');
assert.deepEqual(parseWorkspaceRoute('overview'),{view:'today',projectId:null});
assert.deepEqual(parseWorkspaceRoute('projects/12345678-1234-1234-1234-123456789abc'),{view:'projects',projectId:'12345678-1234-1234-1234-123456789abc'});
assert.equal(parseWorkspaceRoute('projects/../../settings').projectId,null,'invalid IDs are never fetched');
assert.deepEqual(contextPayload(item),{item_ids:[item.id],area:'university'});
assert.deepEqual(contextPayload(null),{},'removing context removes scope IDs');
const project={...item,id:'project-fixture-123456',kind:'project',data:{}};
assert.deepEqual(contextPayload(null,project),{project_id:project.id,thread_id:project.id,area:project.area,item_ids:[project.id]},'project defaults to its visible ficha only');
assert.deepEqual(contextPayload(item,project).item_ids,[item.id],'selecting a resource never includes other documents');
assert.equal(conversationPath(project.id),'/chat?thread_id='+project.id+'&project_id='+project.id);
assert.equal(conversationPath(null),'/chat?thread_id=chat');
assert.equal(belongsToProject({...item,data:{project_id:project.id}},project),true);
assert.equal(belongsToProject({...item,area:'prisma',data:{project_id:project.id}},project),false);
assert.equal(belongsToProject({...item,data:{}},project),false);

assert.equal(JSON.stringify(contextPayload(item)).includes(item.title),false,'selected document content is not blindly injected');
assert.deepEqual(visibleRecords(['active','pending','done','archived','cancelled','canceled','withdrawn'].map(status=>({...item,status}))).map(row=>row.status),['active','pending','done']);
assert.deepEqual(linkedProjectIds({...item,data:{project_id:'project-1',project_ids:['project-1','project-2',null,33,'']}}),['project-1','project-2']);
assert.deepEqual(linkedProjectIds({...item,title:'PRISMA project-1',data:{}}),[],'names do not infer project links');
console.log('Workflow checks passed: route aliases, exact selected IDs, context removal, archive defaults and explicit project links.');
