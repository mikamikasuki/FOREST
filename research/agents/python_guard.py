"""Best-effort scope guard for ordinary Python agent scripts; not a security sandbox."""
import os
import runpy
import sys
from pathlib import Path
workspace=Path(sys.argv[1]).resolve(); script=Path(sys.argv[2]).resolve()
if not script.is_relative_to(workspace): raise PermissionError('Script escapes workspace')
read_roots=[workspace,Path(sys.prefix).resolve(),Path(sys.base_prefix).resolve(),Path('/System/Library'),Path('/usr/lib'),Path('/private/var/db/timezone')]
def check_path(value,write=False):
    if isinstance(value,int) or value is None: return
    p=Path(value).resolve()
    roots=[workspace] if write else read_roots
    if p==Path('/dev/null'): return
    if not any(p.is_relative_to(root) for root in roots): raise PermissionError('Agent file access outside allowed roots: '+str(p))
def guard(event,args):
    if event=='open':
        mode=args[1]; flags=args[2] if len(args)>2 else 0
        write=(isinstance(mode,str) and any(x in mode for x in ('w','a','+','x'))) or (isinstance(flags,int) and flags & (os.O_WRONLY|os.O_RDWR|os.O_CREAT|os.O_TRUNC))
        check_path(args[0],bool(write))
    elif event in ('os.remove','os.rmdir','os.mkdir','os.chmod','os.chown','os.truncate'): check_path(args[0],True)
    elif event in ('os.rename','os.link','os.symlink'): check_path(args[0],True); check_path(args[1],True)
    elif event.startswith(('subprocess.','socket.')) or event in ('os.system','os.exec','os.posix_spawn','ctypes.dlopen'): raise PermissionError('Nested process/network/native-library operations are not enabled in the Python agent tool')
os.chdir(workspace); sys.argv=[str(script),*sys.argv[3:]]; sys.dont_write_bytecode=True
sys.addaudithook(guard); runpy.run_path(str(script),run_name='__main__')
