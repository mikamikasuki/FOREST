"""Structured scientific dependencies and precise stale locations.

Canonical legacy bindings remain supported. Prose mentioning an identifier is
not a dependency, and an unrelated edit does not invalidate every manuscript.
"""
from sqlalchemy import select
from services.api.db import TaskRun, Node, Figure, PaperDocument, ResearchClaim, SourcePaper, Analysis, now

REFERENCE_KEYS = {
    'run_id': 'run', 'run_ids': 'run', 'source_run_ids': 'run', 'evidence_run_ids': 'run',
    'analysis_run_id': 'run', 'analysis_run_ids': 'run', 'compile_run_id': 'run',
    'node_id': 'node', 'source_node_id': 'node', 'producer_node_id': 'node',
    'figure_id': 'figure', 'figure_ids': 'figure', 'claim_id': 'claim', 'claim_ids': 'claim',
    'source_id': 'source', 'source_ids': 'source', 'verification_node_id': 'node',
}


def record_bindings(data):
    bindings=[]
    def visit(value, pointer=''):
        if isinstance(value, list):
            for index,item in enumerate(value): visit(item,pointer+'/'+str(index))
        elif isinstance(value, dict):
            # An explicit binding can locate one paragraph/claim/figure rather
            # than invalidating the whole document without an explanation.
            explicit=value.get('source_kind') in ('run','node','figure','claim','source','analysis','file') and isinstance(value.get('source_id'),str)
            if explicit:
                bindings.append({'source_kind':value['source_kind'],'source_id':value['source_id'],
                    'target_path':value.get('target_path',pointer), 'artifact_path':value.get('artifact_path'),
                    'source_revision':value.get('source_revision'), **{key:value[key] for key in
                    ('metric_pointer','metric_id','source_run_id','passage_id','target_artifact_path') if key in value}})
            if isinstance(value.get('run_id'),str) and isinstance(value.get('path'),str):
                bindings.append({'source_kind':'file','source_id':value['path'],
                    'target_path':value.get('target_path',pointer), 'artifact_path':value['path']})
            for key,item in value.items():
                path=pointer+'/'+str(key).replace('~','~0').replace('/','~1')
                if key in REFERENCE_KEYS and not (explicit and key=='source_id'):
                    ids=item if isinstance(item,list) else [item]
                    for ident in ids:
                        if isinstance(ident,str): bindings.append({'source_kind':REFERENCE_KEYS[key],
                            'source_id':ident,'target_path':value.get('target_path',path),
                            'artifact_path':value.get('artifact_path',value.get('path'))})
                elif key not in ('stale_dependencies','dependency_impact'):
                    visit(item,path)
    visit(data)
    return bindings


def invalidate_dependents(session, project_id, origin):
    # Callers serialize scientific record edits with the project writer lock.
    aliases={origin}
    aliases.update(session.scalars(select(TaskRun.id).where(TaskRun.project_id==project_id,TaskRun.node_id==origin)))
    records=[obj for cls in (Figure,PaperDocument,ResearchClaim,Analysis)
             for obj in session.scalars(select(cls).where(cls.project_id==project_id))]
    impacts=[]; changed=set()
    while True:
        discovered=False
        for record in records:
            bindings=[binding for binding in record_bindings(record.data) if binding['source_id'] in aliases]
            if not bindings or record.id in changed: continue
            changed.add(record.id);aliases.add(record.id);discovered=True
            recorded=[{**binding,'changed_origin':origin,'observed_at':now()} for binding in bindings]
            previous=record.data.get('stale_dependencies',[])
            by_key={(item.get('source_id'),item.get('target_path')):item for item in previous if isinstance(item,dict)}
            by_key.update({(item['source_id'],item['target_path']):item for item in recorded})
            record.status='needs_update'
            record.data={**record.data,'stale_reason':'Bound upstream material changed: '+origin,
                         'stale_dependencies':list(by_key.values())}
            impacts.append({'record_id':record.id,'locations':recorded})
        if not discovered: break
    return impacts


def validate_bindings(session,project_id,data):
    from services.api.common import error,project_dir,safe_path
    models={'run':TaskRun,'node':Node,'figure':Figure,'claim':ResearchClaim,'source':SourcePaper,'analysis':Analysis}
    def visit(value):
        if isinstance(value,list):
            for item in value: visit(item)
        elif isinstance(value,dict):
            if 'dependency_bindings' in value:
                entries=value['dependency_bindings']
                if not isinstance(entries,list) or any(not isinstance(item,dict) or not {'source_kind','source_id','target_path'}<=item.keys() for item in entries):
                    error('INVALID_DEPENDENCY','dependency_bindings must contain structured sources and target paths',422)
            if 'source_kind' in value:
                kind=value['source_kind'];ident=value.get('source_id');pointer=value.get('target_path')
                if kind not in (*models,'file') or not isinstance(ident,str) or not ident or (pointer is not None and (not isinstance(pointer,str) or pointer and not pointer.startswith('/'))):
                    error('INVALID_DEPENDENCY','Dependency source and JSON target path are invalid',422)
                if kind=='file': safe_path(project_dir(project_id),ident)
                else:
                    record=session.get(models[kind],ident)
                    if not record or record.project_id!=project_id:
                        error('INVALID_DEPENDENCY','Bound source must exist in this project',422)
                if value.get('target_artifact_path'): safe_path(project_dir(project_id),value['target_artifact_path'])
            for key,item in value.items():
                if key not in ('stale_dependencies','dependency_impact'): visit(item)
    visit(data)
