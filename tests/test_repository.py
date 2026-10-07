import json
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
import psutil

from research.execution import repository
from research.execution.repository import RepositoryError, normalize_repository, prepare_repository, task_environment


@pytest.mark.parametrize('url,transport,canonical', [
    ('https://github.com/example/research', 'auto', 'https://github.com/example/research.git'),
    ('git@github.com:example/research.git', 'auto', 'git@github.com:example/research.git'),
    ('ssh://git@github.com/example/research.git', 'https', 'https://github.com/example/research.git'),
    ('https://github.com/example/research', 'ssh', 'git@github.com:example/research.git'),
])
def test_url_transport_normalization(url, transport, canonical):
    assert normalize_repository({'url':url, 'transport':transport}) == {
        'url':canonical,'ref':'HEAD','directory':'source','transport':'ssh' if canonical.startswith('git@') else 'https'}


@pytest.mark.parametrize('url', [
    '/local/repository', 'file:///local/repository', 'https://gitlab.com/a/b',
    'https://github.com.evil.test/a/b', 'https://secret@github.com/a/b',
    'https://user:secret@github.com/a/b', 'ssh://root@github.com/a/b',
    'ssh://git@github.com:2222/a/b', 'https://github.com/a/b?token=secret',
    'https://github.com/a/b#secret', 'git@github.com:a/b --upload-pack=evil',
    'git@github.com:a/b/c', 'https://github.com/a/../b',
    'https://github.com/a/%2e%2e', 'https://github.com/a/b\nsecret',
])
def test_rejects_urls_without_echoing_credentials(url):
    with pytest.raises(RepositoryError) as raised:
        normalize_repository({'url':url})
    assert 'secret' not in str(raised.value)


@pytest.mark.parametrize('key,value', [
    ('ref','--upload-pack=evil'), ('ref','main:other'), ('ref','HEAD~1'),
    ('ref','/main'), ('ref','refs/heads/../main'), ('ref','branch name'),
    ('directory','../outside'), ('directory','/outside'), ('directory','.'),
    ('directory','a//b'), ('directory','C:\\outside'), ('directory','.git'),
    ('credential','/private/key'), ('credential', {'token':'secret'}),
    ('transport','ftp'), ('token','secret'), ('identity_file','/private/key'),
])
def test_rejects_unbounded_specification(key, value):
    with pytest.raises(RepositoryError):
        normalize_repository({'url':'https://github.com/example/research', key:value})


def write_private(path, value):
    path.write_text(value)
    path.chmod(0o600)
    return str(path)


def configure_profile(tmp_path, monkeypatch, name, profile):
    path = tmp_path/'profiles.json'
    write_private(path, json.dumps({'profiles':{name:profile}}))
    monkeypatch.setenv('FOREST_GIT_CREDENTIALS_FILE', str(path))


def test_ssh_is_explicit_strict_and_does_not_inherit_agent_or_config(tmp_path, monkeypatch):
    identity = write_private(tmp_path/'private key', 'private-key-placeholder')
    hosts = tmp_path/'known hosts'
    hosts.write_text('github.com ssh-ed25519 PLACEHOLDER\n')
    configure_profile(tmp_path, monkeypatch, 'github', {'type':'ssh','identity_file':identity,'known_hosts_file':str(hosts)})
    spec = normalize_repository({'url':'git@github.com:example/research', 'credential':'github'})
    monkeypatch.setenv('GIT_CONFIG_COUNT','1')
    monkeypatch.setenv('GIT_CONFIG_KEY_0','core.sshCommand')
    monkeypatch.setenv('SSH_AUTH_SOCK','/private/agent')
    profile = repository._profile(spec)
    env = repository._git_environment(tmp_path, profile)
    args = shlex.split(env['GIT_SSH_COMMAND'])
    assert args[args.index('-F')+1] == os.devnull
    for option in ('BatchMode=yes','IdentitiesOnly=yes','StrictHostKeyChecking=yes','IdentityAgent=none',
                   'PasswordAuthentication=no','KbdInteractiveAuthentication=no'):
        assert option in args
    assert 'UserKnownHostsFile="' + str(hosts) + '"' in args
    assert args[args.index('-i')+1] == identity
    assert 'SSH_AUTH_SOCK' not in env and 'GIT_CONFIG_COUNT' not in env
    assert env['GIT_TERMINAL_PROMPT']=='0' and env['GIT_CONFIG_NOSYSTEM']=='1'
    assert env['GIT_CONFIG_GLOBAL']==os.devnull
    with pytest.raises(RepositoryError, match='requires a named credential'):
        repository._profile(normalize_repository({'url':'git@github.com:example/research'}))


def test_https_token_is_only_read_by_temporary_askpass(tmp_path, monkeypatch):
    token = write_private(tmp_path/'token', 'not-a-real-provider-token\n')
    configure_profile(tmp_path, monkeypatch, 'github', {'type':'https','token_file':token})
    profile = repository._profile(normalize_repository({'url':'https://github.com/example/research','credential':'github'}))
    auth = tmp_path/'auth'; auth.mkdir()
    env = repository._git_environment(auth, profile)
    assert env['FOREST_GIT_TOKEN_FILE']==token
    assert 'not-a-real-provider-token' not in json.dumps(env)
    assert 'not-a-real-provider-token' not in Path(env['GIT_ASKPASS']).read_text()
    username = subprocess.check_output([env['GIT_ASKPASS'], 'Username for HTTPS:'], env=env, text=True)
    password = subprocess.check_output([env['GIT_ASKPASS'], 'Password for HTTPS:'], env=env, text=True)
    assert username.strip()=='x-access-token' and password.strip()=='not-a-real-provider-token'


def test_profile_secret_permissions_and_type_are_validated(tmp_path, monkeypatch):
    token = write_private(tmp_path/'token', 'placeholder-token')
    configure_profile(tmp_path, monkeypatch, 'github', {'type':'https','token_file':token})
    Path(token).chmod(0o644)
    with pytest.raises(RepositoryError, match='private permissions'):
        repository._profile(normalize_repository({'url':'https://github.com/a/b','credential':'github'}))
    Path(token).chmod(0o600)
    with pytest.raises(RepositoryError, match='must match'):
        repository._profile(normalize_repository({'url':'git@github.com:a/b','credential':'github'}))


def test_failed_git_output_never_leaks_into_errors(tmp_path, monkeypatch):
    calls = []
    def run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=128, communicate=lambda **kwargs: ('secret-output','secret-token echoed by helper'))
    monkeypatch.setattr(repository.subprocess, 'Popen', run)
    with pytest.raises(RepositoryError) as error:
        repository._git(['fetch','origin','HEAD'], tmp_path, {})
    assert 'secret' not in str(error.value)
    command, options = calls[0]
    for setting in ('credential.helper=','core.hooksPath='+os.devnull,'core.fsmonitor=false',
                    'http.followRedirects=false','protocol.allow=never','submodule.recurse=false'):
        assert setting in command
    assert options['stdin']==subprocess.DEVNULL and options['stderr']==subprocess.DEVNULL


def _assert_process_terminated(pid, *, timeout=2, process_factory=psutil.Process):
    deadline = time.monotonic() + timeout
    while True:
        try:
            status = process_factory(pid).status()
        except psutil.NoSuchProcess:
            return
        if status in (psutil.STATUS_ZOMBIE, psutil.STATUS_DEAD):
            return
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise AssertionError(f'process {pid} remained live with status {status!r}')
        time.sleep(min(0.01, remaining))


@pytest.mark.parametrize('status', [psutil.STATUS_ZOMBIE, psutil.STATUS_DEAD])
def test_process_termination_check_accepts_terminal_statuses(status):
    process = SimpleNamespace(status=lambda: status)
    _assert_process_terminated(123, timeout=0, process_factory=lambda _pid: process)


def test_process_termination_check_accepts_missing_process():
    def missing_process(pid):
        raise psutil.NoSuchProcess(pid)

    _assert_process_terminated(123, timeout=0, process_factory=missing_process)


def test_process_termination_check_rejects_live_process():
    child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
    try:
        with pytest.raises(AssertionError, match='remained live'):
            _assert_process_terminated(child.pid, timeout=0.05)
    finally:
        if child.poll() is None:
            child.kill()
        child.wait(timeout=2)


@pytest.mark.parametrize('_attempt', range(10))
def test_git_timeout_terminates_helpers_without_detaching_from_worker(_attempt, tmp_path, monkeypatch):
    executable=tmp_path/'git'
    pidfile=tmp_path/'child.pid'
    executable.write_text('#!'+sys.executable+'\nimport subprocess,sys,time\nfrom pathlib import Path\n'
                          'child=subprocess.Popen([sys.executable,"-c","import time;time.sleep(60)"])\n'
                          f'Path({str(pidfile)!r}).write_text(str(child.pid))\n'
                          'time.sleep(60)\n')
    executable.chmod(0o700)
    monkeypatch.setattr(repository,'_GIT_TIMEOUT_SECONDS',.5)
    started=time.monotonic()
    try:
        with pytest.raises(RepositoryError, match='timed out'):
            repository._git(['fetch','origin','HEAD'],tmp_path,{'PATH':str(tmp_path)})
        assert time.monotonic()-started<5
        childpid=int(pidfile.read_text())
        _assert_process_terminated(childpid)
    finally:
        if pidfile.exists():
            try: psutil.Process(int(pidfile.read_text())).kill()
            except psutil.NoSuchProcess: pass


def test_task_environment_strips_host_auth_and_rejects_explicit_auth(monkeypatch):
    for key in ('FOREST_GIT_CREDENTIALS_FILE','FOREST_GIT_TOKEN_FILE','SSH_AUTH_SOCK','SSH_AGENT_PID',
                'GIT_ASKPASS','SSH_ASKPASS','GIT_CONFIG_COUNT','GIT_SSH_COMMAND','GCM_INTERACTIVE'):
        monkeypatch.setenv(key, 'host-only')
        assert key not in task_environment({'NORMAL_TASK_SETTING':'okay'})
        with pytest.raises(RepositoryError):
            task_environment({key:'task-supplied'})
    assert task_environment({'NORMAL_TASK_SETTING':'okay'})['NORMAL_TASK_SETTING']=='okay'


@pytest.fixture
def local_git(tmp_path, monkeypatch):
    """Exercise real Git while replacing only its GitHub network transport."""
    origin = tmp_path/'origin'; origin.mkdir()
    def git(*args):
        return subprocess.check_output(['git', '-c','user.name=Test','-c','user.email=test@example.invalid', *args], cwd=origin, text=True).strip()
    git('init','-q')
    (origin/'calculation.py').write_text('print(6 * 7)\n')
    git('add','calculation.py'); git('commit','-qm','First source')
    first = git('rev-parse','HEAD'); git('tag','v1')
    (origin/'calculation.py').write_text('print(6 * 8)\n')
    git('add','calculation.py'); git('commit','-qm','Second source')
    latest = git('rev-parse','HEAD')
    real_git = repository._git
    calls = []
    def local_transport(arguments, cwd, env):
        calls.append(arguments)
        if arguments[0]=='clone':
            arguments = [origin.as_uri() if arg=='https://github.com/example/research.git' else arg for arg in arguments]
        # File transport is admitted solely by this test shim, never production.
        return real_git(['-c','protocol.file.allow=always',*arguments], cwd, env)
    monkeypatch.setattr(repository, '_git', local_transport)
    return origin, first, latest, calls


def test_actual_checkout_records_commit_and_preserves_edits_on_resume(tmp_path, local_git):
    origin, first, latest, calls = local_git
    output = tmp_path/'output'; workspace = output/'workspace'
    spec = {'url':'https://github.com/example/research','ref':'v1','directory':'inputs/source'}
    manifest = prepare_repository(spec, workspace, output)
    assert manifest=={'url':'https://github.com/example/research.git','requested_ref':'v1','commit':first,
                      'directory':'inputs/source','transport':'https'}
    assert json.loads((output/'repository.json').read_text())==manifest
    source = workspace/'inputs/source'
    assert subprocess.check_output(['python3',str(source/'calculation.py')],text=True).strip()=='42'
    assert subprocess.check_output(['git','rev-parse','HEAD'],cwd=source,text=True).strip()==first
    (source/'calculation.py').write_text('print(7 * 7)\n')
    before = len(calls)
    assert prepare_repository(spec, workspace, output)==manifest
    assert (source/'calculation.py').read_text()=='print(7 * 7)\n'
    assert len(calls)==before+1 and calls[-1][0]=='rev-parse'
    config = (source/'.git/config').read_text()
    assert 'github.com/example/research.git' in config
    assert 'askpass' not in config and 'identity_file' not in config and 'credential' not in config
    assert not list(workspace.rglob('.forest-source-*'))
    # Fresh runs resolve the then-current source; they do not replace old runs.
    fresh = prepare_repository({'url':spec['url']},tmp_path/'fresh/workspace',tmp_path/'fresh')
    assert fresh['commit']==latest


def test_existing_unrelated_directory_and_changed_head_are_preserved(tmp_path, local_git):
    output = tmp_path/'output'; workspace = output/'workspace'; source = workspace/'source'
    source.mkdir(parents=True); (source/'keep').write_text('keep')
    spec = {'url':'https://github.com/example/research'}
    with pytest.raises(RepositoryError, match='occupied'):
        prepare_repository(spec, workspace, output)
    assert (source/'keep').read_text()=='keep'
    second = tmp_path/'second'; manifest=prepare_repository(spec, second/'workspace', second)
    checkout=second/'workspace/source'
    subprocess.check_call(['git','-c','user.name=Test','-c','user.email=test@example.invalid','commit','--allow-empty','-qm','Changed by task'],cwd=checkout)
    with pytest.raises(RepositoryError, match='HEAD differs'):
        prepare_repository(spec, second/'workspace', second)
    assert json.loads((second/'repository.json').read_text())==manifest


def test_symlink_directory_escape_and_missing_checkout_are_rejected(tmp_path, local_git):
    workspace = tmp_path/'workspace'; workspace.mkdir()
    outside = tmp_path/'outside'; outside.mkdir()
    (workspace/'source').symlink_to(outside, target_is_directory=True)
    with pytest.raises(RepositoryError, match='symlinks'):
        prepare_repository({'url':'https://github.com/example/research'},workspace,tmp_path/'output')
    output=tmp_path/'recorded'; output.mkdir()
    (output/'repository.json').write_text('{}')
    with pytest.raises(RepositoryError, match='checkout is missing'):
        prepare_repository({'url':'https://github.com/example/research'},output/'workspace',output)


def test_clone_failure_leaves_no_source_or_manifest(tmp_path, monkeypatch):
    def failed(*args):
        raise RepositoryError('safe Git error')
    monkeypatch.setattr(repository,'_git',failed)
    workspace=tmp_path/'workspace'; output=tmp_path/'output'
    with pytest.raises(RepositoryError):
        prepare_repository({'url':'https://github.com/example/research'},workspace,output)
    assert not (workspace/'source').exists() and not (output/'repository.json').exists()
    assert not list(workspace.iterdir())


def test_source_receipt_cannot_be_a_symlink(tmp_path):
    output=tmp_path/'output'; output.mkdir()
    external=tmp_path/'external.json'; external.write_text('{}')
    (output/'repository.json').symlink_to(external)
    with pytest.raises(RepositoryError, match='not symlinks'):
        prepare_repository({'url':'https://github.com/example/research'},output/'workspace',output)
    assert external.read_text()=='{}'
