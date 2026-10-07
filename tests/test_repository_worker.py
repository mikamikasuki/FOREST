"""Real source checkout and executor subprocesses in an isolated database."""
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def test_repository_preparation_integrates_with_execution_and_receipts(tmp_path):
    env = {**os.environ, 'FOREST_DATA_DIR':str(tmp_path/'data'),
           'FOREST_DATABASE_URL':'sqlite:///'+str(tmp_path/'worker.sqlite'),
           'FOREST_MODEL':'', 'FOREST_OWNER_TOKEN':'isolated-repository-owner',
           'PYTHONPATH':str(ROOT)}
    result = subprocess.run([sys.executable, __file__, str(tmp_path)], cwd=ROOT,
                            env=env, capture_output=True, text=True, timeout=45)
    assert result.returncode==0, result.stdout+result.stderr
    assert 'Repository executor integration passed' in result.stdout


if __name__=='__main__':
    import json
    import shlex
    from unittest.mock import patch

    from fastapi.testclient import TestClient
    from research.execution import repository
    from services.api.main import app
    from services.api.common import project_dir
    from services.api.db import Session, TaskRun
    from services.worker.scheduler import enqueue
    from services.worker.execute import execute

    directory=Path(sys.argv[1])
    origin=directory/'origin'; origin.mkdir()
    def git(*arguments):
        return subprocess.check_output(['git','-c','user.name=Test','-c','user.email=test@example.invalid',*arguments],cwd=origin,text=True).strip()
    git('init','-q')
    (origin/'calculation.py').write_text('import json,os\nfrom pathlib import Path\n'
        'result={"observed":6*7,"host_auth_inherited":any(name in os.environ for name in '
        '["FOREST_GIT_CREDENTIALS_FILE","SSH_AUTH_SOCK","GIT_ASKPASS"])}\n'
        'Path("metrics.json").write_text(json.dumps(result))\nprint(result,flush=True)\n')
    (origin/'entrypoint.py').write_text('import os\ndef run(parameters,output):\n'
        '    return {"host_auth_inherited": "FOREST_GIT_CREDENTIALS_FILE" in os.environ}\n')
    git('add','.'); git('commit','-qm','Actual test calculation')
    commit=git('rev-parse','HEAD')
    real_git=repository._git
    def local_transport(arguments,cwd,env):
        if arguments[0]=='clone':
            arguments=[origin.as_uri() if argument=='https://github.com/example/research.git' else argument for argument in arguments]
        return real_git(['-c','protocol.file.allow=always',*arguments],cwd,env)
    spec={'url':'https://github.com/example/research.git'}

    with TestClient(app) as client, patch.object(repository,'_git',local_transport):
        # This integration test invokes execute() directly rather than the
        # worker lifecycle, so project-time reservation/finalization is covered
        # by the scheduler tests instead of this fixture.
        project=client.post('/api/projects',json={'name':'Actual repository computation','mode':'manual',
            'config':{'publication_profile':{'id':'operational'}},'budget':{'max_runs':20,'allow_paid':False}}).json()
        pid=project['id']
        def queue(kind,config,request_id):
            with Session.begin() as session:
                run=enqueue(session,pid,kind,config,request_id)
                return run.id,project_dir(pid)/run.output_path

        # Clone-only runs acquire a source receipt and never invoke model tools.
        clone_id,clone_output=queue('repository_clone',{'repository':spec},'clone-only')
        manifest=execute(clone_id)
        assert manifest['commit']==commit
        assert json.loads((clone_output/'repository.json').read_text())==manifest
        assert not (clone_output/'execution.json').exists()
        assert not (clone_output/'metrics.json').exists()

        command=['/bin/sh','-c','cd source && '+shlex.quote(sys.executable)+' calculation.py']
        identifier,output=queue('experiment',{'repository':spec,'command':command,'metrics_file':'source/metrics.json'},'actual-computation')
        os.environ['FOREST_GIT_CREDENTIALS_FILE']='/operator-only/profile-path'
        os.environ['SSH_AUTH_SOCK']='/operator-only/agent'
        os.environ['GIT_ASKPASS']='/operator-only/helper'
        measured=execute(identifier)
        assert measured['observed']==42 and measured['host_auth_inherited'] is False
        assert measured['repository_source']['commit']==commit
        assert json.loads((output/'metrics.json').read_text())=={'observed':42,'host_auth_inherited':False}
        receipt=json.loads((output/'execution.json').read_text())
        assert receipt['backend']=='local' and receipt['command']==command
        assert receipt['status']=='completed' and receipt['exit_code']==0
        assert receipt['started_at']<=receipt['finished_at']
        assert '_repository_source' not in json.loads((output/'task_config.json').read_text())
        # Same-run checkpoint attempts retain code edits and the pinned source.
        (output/'workspace/source/calculation.py').write_text('import json\nfrom pathlib import Path\nPath("metrics.json").write_text(json.dumps({"observed":49}))\n')
        with Session.begin() as session:
            run=session.get(TaskRun,identifier)
            run.config={**run.config,'execution_attempt':{'number':2,'mode':'checkpoint'}}
        resumed=execute(identifier)
        assert resumed['observed']==49 and resumed['repository_source']==measured['repository_source']

        # Container and agent dispatch see a prepared tree before their runner
        # starts. These assertions test integration contracts, not Docker/model
        # execution; the local calculation above is the measured computation.
        container_id,container_output=queue('command',{'repository':spec,'command':['true'],'execution_backend':'container'},'container-dispatch')
        def container(config,workspace,output,**kwargs):
            assert (workspace/'source/calculation.py').is_file()
            assert config['_repository_source']['commit']==commit
            assert (output/'repository.json').is_file()
            return {'dispatch_contract':True}
        with patch('runners.container.execute_container',container):
            assert execute(container_id)['repository_source']['commit']==commit
        agent_id,agent_output=queue('agent',{'repository':spec},'agent-dispatch')
        def agent(run_id,workspace,config):
            assert (workspace/'source/calculation.py').is_file()
            assert config['_repository_source']['commit']==commit
            return {'dispatch_contract':True}
        with patch('research.agents.runtime.run_agent',agent):
            assert execute(agent_id)['repository_source']['commit']==commit

        # Same-process entry points are scrubbed after host-side acquisition.
        entry_id,entry_output=queue('experiment',{'repository':spec,'entrypoint':'source.entrypoint:run'},'entrypoint-dispatch')
        os.environ['FOREST_GIT_CREDENTIALS_FILE']='/operator-only/profile-path'
        assert execute(entry_id)['host_auth_inherited'] is False

        failing_id,failing_output=queue('command',{'command':[sys.executable,'-c','raise SystemExit(7)']},'failed-subprocess')
        try:
            execute(failing_id)
        except RuntimeError as error:
            assert 'code 7' in str(error)
        else:
            raise AssertionError('A failed subprocess must fail its executor')
        failed=json.loads((failing_output/'execution.json').read_text())
        assert failed['status']=='failed' and failed['exit_code']==7

        # Resolved input bindings cannot be overwritten by a repository clone.
        project_root=project_dir(pid); (project_root/'keep.txt').write_text('preserved input')
        occupied_id,occupied_output=queue('command',{'repository':spec,'command':['true'],
            'resolved_inputs':[{'source_path':'keep.txt','destination':'source/keep.txt'}]},'occupied-source')
        try:
            execute(occupied_id)
        except repository.RepositoryError as error:
            assert error.code=='REPOSITORY_DIRECTORY_OCCUPIED'
        else:
            raise AssertionError('Occupied input directory must be preserved')
        assert (occupied_output/'workspace/source/keep.txt').read_text()=='preserved input'
        assert not (occupied_output/'repository.json').exists()
    print('Repository executor integration passed')
