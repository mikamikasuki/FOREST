#!/usr/bin/env python3
"""Foreground supervisor for local dev/demo services; children use durable data."""
import argparse
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def main():
    parser=argparse.ArgumentParser(); parser.add_argument('--dev',action='store_true'); parser.add_argument('--port',default=8000,type=int); args=parser.parse_args()
    os.chdir(ROOT)
    python=ROOT/'.venv/bin/python'
    if not python.exists(): raise SystemExit('Run ./scripts/setup.sh first')
    subprocess.run([str(python),'-m','services.api.db'],check=True)
    if not args.dev and not (ROOT/'apps/web/dist/index.html').exists(): subprocess.run(['npm','run','build'],cwd=ROOT/'apps/web',check=True)
    commands=[[str(python),'-m','uvicorn','services.api.main:app','--host','127.0.0.1','--port',str(args.port),'--timeout-graceful-shutdown','5'],[str(python),'-m','services.worker.main']]
    if args.dev: commands.append(['npm','run','dev','--','--host','127.0.0.1'])
    children=[]
    try:
        for i,command in enumerate(commands): children.append(subprocess.Popen(command,cwd=ROOT/'apps/web' if i==2 else ROOT))
        print('FOREST: '+('http://127.0.0.1:5173' if args.dev else f'http://127.0.0.1:{args.port}'),flush=True)
        def stop(*args): raise KeyboardInterrupt
        signal.signal(signal.SIGTERM,stop)
        while all(c.poll() is None for c in children): time.sleep(.5)
    except KeyboardInterrupt: pass
    finally:
        for child in children:
            if child.poll() is None: child.terminate()
        for child in children:
            try: child.wait(timeout=10)
            except subprocess.TimeoutExpired: child.kill()
if __name__=='__main__': main()
