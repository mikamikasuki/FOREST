#!/usr/bin/env python3
"""Exit unsuccessfully when this container has no recent worker heartbeat."""
import datetime as dt
import socket
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from sqlalchemy import select
from services.api.db import Session, Worker

with Session() as session:
    workers = list(session.scalars(select(Worker).where(Worker.name == socket.gethostname())))
    healthy = any((dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(worker.heartbeat)).total_seconds() < 30 for worker in workers)
sys.exit(0 if healthy else 1)
