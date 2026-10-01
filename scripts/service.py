#!/usr/bin/env python3
"""Render or explicitly install a durable per-user FOREST service."""
import argparse
import os
from pathlib import Path
import platform
import plistlib
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
LABEL = 'org.forest.research'


def _unit_quote(value):
    """Quote one systemd ExecStart argument; systemd does not use a shell."""
    return '"' + str(value).replace('\\', '\\\\').replace('"', '\\"').replace('%', '%%') + '"'


def render(port=8000, *, headless=False, data_dir=None):
    if os.name == 'nt':
        raise SystemExit('Run user services inside WSL2; native Windows services are unavailable.')
    logs = ROOT / 'var' / 'service'; logs.mkdir(parents=True, exist_ok=True)
    python = ROOT / '.venv' / 'bin' / 'python'
    arguments = [str(python), str(ROOT / 'scripts' / 'start.py'), '--port', str(port)]
    if headless:
        arguments.append('--headless')
    if data_dir is not None:
        arguments.extend(['--data-dir', str(Path(data_dir).expanduser().resolve())])
    if platform.system() == 'Darwin':
        target = logs / (LABEL + '.plist')
        content = {'Label': LABEL, 'ProgramArguments': arguments,
                   'WorkingDirectory': str(ROOT), 'RunAtLoad': True, 'KeepAlive': True, 'ThrottleInterval': 5,
                   'StandardOutPath': str(logs / 'stdout.log'), 'StandardErrorPath': str(logs / 'stderr.log'),
                   'EnvironmentVariables': {'PATH': os.environ.get('PATH', '/usr/bin:/bin'), 'PYTHONUNBUFFERED': '1'}}
        with target.open('wb') as stream: plistlib.dump(content, stream)
        return target
    target = logs / 'forest.service'
    target.write_text('[Unit]\nDescription=FOREST research service\nAfter=network.target\n\n[Service]\nType=simple\nWorkingDirectory='+_unit_quote(ROOT)+'\nExecStart='+' '.join(_unit_quote(value) for value in arguments)+'\nRestart=always\nRestartSec=5\n\n[Install]\nWantedBy=default.target\n')
    return target


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['render','install','status','remove'])
    parser.add_argument('--port', type=int, default=8000)
    parser.add_argument('--headless', action='store_true')
    parser.add_argument('--data-dir', type=Path)
    args = parser.parse_args()
    if os.name == 'nt':
        raise SystemExit('Run user services inside WSL2; native Windows services are unavailable.')
    mac = platform.system() == 'Darwin'
    installed = Path.home() / ('Library/LaunchAgents/'+LABEL+'.plist' if mac else '.config/systemd/user/forest.service')
    domain = 'gui/'+str(os.getuid()) if mac else ''
    if args.action == 'render': print(render(args.port, headless=args.headless, data_dir=args.data_dir)); return
    if args.action == 'status':
        subprocess.run(['launchctl','print',domain+'/'+LABEL] if mac else ['systemctl','--user','status','forest.service'], check=False); return
    if args.action == 'remove':
        subprocess.run(['launchctl','bootout',domain,str(installed)] if mac else ['systemctl','--user','disable','--now','forest.service'],check=False)
        installed.unlink(missing_ok=True); return
    generated = render(args.port, headless=args.headless, data_dir=args.data_dir)
    probe=['launchctl','print',domain+'/'+LABEL] if mac else ['systemctl','--user','is-active','--quiet','forest.service']
    loaded=subprocess.run(probe,capture_output=True).returncode==0
    changed=not installed.exists() or installed.read_bytes()!=generated.read_bytes()
    if changed:
        installed.parent.mkdir(parents=True, exist_ok=True)
        if installed.exists(): installed.with_suffix(installed.suffix+'.previous').write_bytes(installed.read_bytes())
        temporary=installed.with_suffix(installed.suffix+'.tmp'); temporary.write_bytes(generated.read_bytes()); temporary.replace(installed)
    if loaded:
        print('Service configuration updated; running work was preserved. Restart the service to apply it.' if changed else 'FOREST service is already installed and active.')
        return
    if mac: subprocess.run(['launchctl','bootstrap',domain,str(installed)],check=True)
    else:
        subprocess.run(['systemctl','--user','daemon-reload'],check=True)
        subprocess.run(['systemctl','--user','enable','--now','forest.service'],check=True)
    print('FOREST service installed: '+str(installed))


if __name__ == '__main__': main()
