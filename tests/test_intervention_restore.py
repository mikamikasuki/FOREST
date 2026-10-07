"""Actual backup/restore and concurrent upgrades on SQLite and PostgreSQL."""
import getpass
import json
import os
from pathlib import Path
import subprocess
import sys

from tests.test_intervention_worker import intervention_harness

ROOT=Path(__file__).resolve().parents[1]


def test_legacy_schema_upgrade_serializes_concurrent_migrators(tmp_path,intervention_harness):
    h=intervention_harness
    env={**os.environ,'FOREST_DATABASE_URL':h.env['FOREST_DATABASE_URL'],'FOREST_DATA_DIR':str(tmp_path/'source'),
         'FOREST_MODEL':'','PYTHONPATH':str(ROOT)}
    # Build a version-4 database from the core schema in this checkout. The
    # previous test depended on an untracked sibling repository that CI does
    # not provision. Migration-owned tables are deliberately absent here.
    legacy="""from sqlalchemy import inspect
from services.api.db import Base, Migration, Project, Session, engine
Base.metadata.create_all(engine)
with Session.begin() as s:
    for version in (1, 2, 3, 4): s.add(Migration(version=version))
    s.add(Project(id='legacy-retained-project', name='Retained old schema', goal='Preserve actual stored data'))
tables=set(inspect(engine).get_table_names())
assert not {'interventions', 'intervention_effects', 'action_decisions'} & tables
"""
    seeded=subprocess.run([sys.executable,'-c',legacy],cwd=ROOT,env=env,capture_output=True,text=True,timeout=30)
    assert seeded.returncode==0,seeded.stderr
    env['PYTHONPATH']=str(ROOT)
    processes=[subprocess.Popen([sys.executable,'-m','services.api.db'],cwd=ROOT,env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True) for _ in range(4)]
    for process in processes:
        output,error=process.communicate(timeout=30)
        assert process.returncode==0,output+error
    check="""from services.api.db import Session, Project, Migration, engine
from sqlalchemy import inspect
with Session() as s:
    assert s.get(Project, 'legacy-retained-project').goal == 'Preserve actual stored data'
    assert s.get(Migration, 5)
schema = inspect(engine)
assert {'interventions', 'intervention_effects', 'action_decisions'} <= set(schema.get_table_names())
expected_indexes = {
    'interventions': {'ix_interventions_project_created'},
    'action_decisions': {'ix_decisions_project_status_created'},
    'intervention_effects': {'ix_effects_action_status_retry'},
}
for table, names in expected_indexes.items():
    actual = {index['name'] for index in schema.get_indexes(table)}
    assert names <= actual, f'{table} missing indexes: {names - actual}'
print('PASS concurrent legacy upgrade')
"""
    checked=subprocess.run([sys.executable,'-c',check],cwd=ROOT,env=env,capture_output=True,text=True,timeout=30)
    assert checked.returncode==0,checked.stdout+checked.stderr
    (tmp_path/'migration_result.txt').write_text(checked.stdout)


def test_restored_pending_controls_cannot_target_original_execution(tmp_path,intervention_harness):
    h=intervention_harness
    admin=os.environ.get('FOREST_TEST_POSTGRES','1')
    if admin=='1': admin=f'postgresql://{getpass.getuser()}@127.0.0.1:5432/postgres'
    env={**os.environ,'FOREST_DATABASE_URL':h.env['FOREST_DATABASE_URL'],'FOREST_DATA_DIR':str(tmp_path/'source'),
         'FOREST_MODEL':'','PYTHONPATH':str(ROOT),'RESTORE_TEST_ADMIN':admin}
    process=subprocess.run([sys.executable,str(Path(__file__).resolve()),'restore',str(tmp_path)],cwd=ROOT,env=env,capture_output=True,text=True,timeout=60)
    assert process.returncode==0,process.stdout+process.stderr
    (tmp_path/'restore_result.txt').write_text(process.stdout)


def restore_scenario(directory):
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine,text
    from services.api.db import migrate,Session,settings
    from services.api.main import app
    from services.interventions import application
    from services.interventions.controls import decision_gate
    from scripts.backup import create_backup,restore_backup,_drop_postgres,_new_postgres
    import uuid
    migrate();application.kick_effects=lambda **kwargs:None
    client=TestClient(app)
    project=client.post('/api/projects',json={'name':'Restore execution authority'}).json();pid=project['id']
    graph=client.post(f'/api/projects/{pid}/graph/commands',json={'request_id':str(uuid.uuid4()),'expected_revision':0,
        'operation':'add_node','params':{'title':'Pending review','type':'analysis','config':{'kind':'agent','tools':['write_file','finish'],'human_review_tools':['write_file']}}}).json()['graph']
    node=graph['nodes'][0]
    run=client.post('/api/nodes/'+node['id']+'/run',json={}).json()
    action={'id':str(uuid.uuid4()),'tool':'write_file','arguments':{'path':'not-approved.txt','content':'await owner'},'observed_graph_revision':1}
    _,decision=decision_gate(run['id'],action)
    instruction=client.post(f'/api/projects/{pid}/instructions',json={'request_id':str(uuid.uuid4()),'expected_revision':1,
        'scope':'project','text':'Retain future owner intent','boundary':'next_request'}).json()
    fork=client.post(f'/api/projects/{pid}/graph/commands',json={'request_id':str(uuid.uuid4()),'expected_revision':2,
        'operation':'fork_branch','targets':[node['id']],'params':{'name':'Pending copied workspace'}}).json()
    assert fork['intervention']['status']=='accepted',fork
    archive=directory/'actual-backup.tar.gz';create_backup(settings.data_dir,settings.database_url,archive)
    restored=restore_backup(archive,directory/'restored',os.environ['RESTORE_TEST_ADMIN'])
    engine=create_engine(restored['database_url'])
    try:
        with engine.connect() as session:
            assert session.execute(text('SELECT status FROM task_runs WHERE id=:id'),{'id':run['id']}).scalar_one()=='interrupted'
            assert session.execute(text('SELECT status FROM action_decisions WHERE id=:id'),{'id':decision['id']}).scalar_one()=='stale'
            for status,target,lease in session.execute(text('SELECT status,target,lease_until FROM intervention_effects')):
                assert status=='superseded' and (json.loads(target) if isinstance(target,str) else target)=={} and lease==0
            for status,extra in session.execute(text('SELECT status,extra FROM branches')):
                extra=json.loads(extra) if isinstance(extra,str) else extra
                assert 'workspace_intervention' not in extra
                if extra.get('historical_workspace_intervention'):assert status=='disabled'
        with Session() as session:
            from services.interventions.models import ActionDecision,Intervention
            assert session.get(ActionDecision,decision['id']).status=='pending'
            assert session.get(Intervention,instruction['id']).status=='accepted'
        print(json.dumps({'status':'passed','backend':engine.dialect.name,'actual_restore':True,'pending_effect_authority':False,'original_database_unchanged':True}))
    finally:
        engine.dispose()
        if restored.get('database_name'):
            from sqlalchemy.engine import make_url
            url=make_url(os.environ['RESTORE_TEST_ADMIN'])
            parameters={'host':url.host,'port':url.port or 5432,'user':url.username,'dbname':url.database or 'postgres'}
            if url.password:parameters['password']=url.password
            _drop_postgres(parameters,restored['database_name'])


if __name__=='__main__':restore_scenario(Path(sys.argv[2]))
