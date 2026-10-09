"""Reconnectable owner-only PTYs, separate from experiment worker jobs."""
import asyncio
import codecs
import json
import os
import pty
import signal
import subprocess
import time
from pathlib import Path
from fastapi import WebSocket,WebSocketDisconnect

class TerminalSession:
    def __init__(self,cwd):
        self.master,slave=pty.openpty(); self.proc=subprocess.Popen(['/bin/zsh' if Path('/bin/zsh').exists() else '/bin/bash','-i'],cwd=cwd,stdin=slave,stdout=slave,stderr=slave,start_new_session=True,env={**os.environ,'TERM':'xterm-256color'})
        os.close(slave); os.set_blocking(self.master,False); self.decoder=codecs.getincrementaldecoder('utf-8')(errors='replace'); self.buffer=''; self.clients=set(); self.last_seen=time.monotonic(); self.reader=asyncio.create_task(self.pump())
    async def publish(self,value):
        if not value: return
        self.buffer=(self.buffer+value)[-131072:]
        for ws in tuple(self.clients):
            try: await ws.send_text(value)
            except Exception: self.clients.discard(ws)
    async def pump(self):
        try:
            while self.proc.poll() is None:
                if not self.clients and time.monotonic()-self.last_seen>1800: self.close(); return
                try: await self.publish(self.decoder.decode(os.read(self.master,65536)))
                except BlockingIOError: pass
                except OSError: break
                await asyncio.sleep(.03)
        finally:
            await self.publish(self.decoder.decode(b'',final=True))
    def close(self):
        try: os.killpg(self.proc.pid,signal.SIGHUP)
        except ProcessLookupError: pass
        try: os.close(self.master)
        except OSError: pass
        self.reader.cancel()
SESSIONS={}
async def attach(ws,key,cwd):
    session=SESSIONS.get(key)
    if session is None or session.proc.poll() is not None:
        if session: session.close()
        if len(SESSIONS)>=32:
            old_key=next((k for k,v in SESSIONS.items() if not v.clients),None)
            if old_key is None: await ws.close(code=1013); return
            SESSIONS.pop(old_key).close()
        session=SESSIONS[key]=TerminalSession(cwd)
    await ws.accept(); session.clients.add(ws)
    if session.buffer: await ws.send_text(session.buffer)
    try:
        while True:
            text=await ws.receive_text(); session.last_seen=time.monotonic()
            if text.startswith('{"resize":'):
                import fcntl,termios,struct
                sz=json.loads(text)['resize']; fcntl.ioctl(session.master,termios.TIOCSWINSZ,struct.pack('HHHH',min(1000,max(1,int(sz['rows']))),min(1000,max(1,int(sz['cols']))),0,0))
            elif text=='{"action":"close"}': session.close(); SESSIONS.pop(key,None); break
            else: os.write(session.master,text.encode())
    except (WebSocketDisconnect,OSError): pass
    finally: session.clients.discard(ws); session.last_seen=time.monotonic()
def close_all():
    for session in SESSIONS.values(): session.close()
    SESSIONS.clear()
