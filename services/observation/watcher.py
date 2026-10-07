"""Native-platform write hints plus the independently recoverable reconciler."""
import logging
import threading
from pathlib import Path
from services.api.config import settings
from services.api.db import Session, Project
from .reporting import write_lock, ensure_state
from .files import allowed, sync_scopes, managed_change

class FileWatcher:
    def __init__(self, stop_event):
        self.stop_event=stop_event
        self.available=False
        self.thread=threading.Thread(target=self.run,daemon=True,name='forest-file-hints')
    def start(self): self.thread.start()
    def run(self):
        try:
            from watchfiles import watch, DefaultFilter
            root=settings.data_dir/'projects'
            root.mkdir(parents=True,exist_ok=True)
            self.available=True
            for changes in watch(root,stop_event=self.stop_event,debounce=200,step=50,
                                 watch_filter=DefaultFilter(ignore_dirs=('.git','node_modules','.venv','__pycache__'))):
                grouped={}
                for _,absolute in changes:
                    try: relative=Path(absolute).relative_to(root); project_id=relative.parts[0]; path=Path(*relative.parts[1:]).as_posix()
                    except (ValueError,IndexError): continue
                    if allowed(path): grouped.setdefault(project_id,set()).add(path)
                for project_id,paths in list(grouped.items())[:10]:
                    if self.stop_event.is_set(): return
                    with Session.begin() as s:
                        write_lock(s)
                        if not s.get(Project,project_id): continue
                        ensure_state(s,project_id); sync_scopes(s,project_id,recent_only=True)
                        for path in sorted(paths)[:200]: managed_change(s,project_id,path,'external_observation')
                # Overflow/excess is repaired by periodic bounded reconciliation.
        except Exception:
            self.available=False
            logging.getLogger(__name__).warning('Native file hints unavailable; bounded reconciliation remains active')
