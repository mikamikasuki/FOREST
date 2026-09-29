"""Incremental editable source writes with exact-offset crash replay."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import uuid

# This bounds one model tool payload, never the number of chunks or file size.
CODE_CHUNK_CHARS = 4000


def write_file_chunk(path, content, offset, *, replay=False):
    path = Path(path)
    if not isinstance(content, str) or not 0 < len(content) <= CODE_CHUNK_CHARS:
        raise ValueError(f'Each chunk must contain 1–{CODE_CHUNK_CHARS} characters; continue with another chunk')
    if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
        raise ValueError('offset must be a nonnegative byte offset')
    data = content.encode('utf-8')
    before = path.stat() if path.exists() else None
    size = before.st_size if before else 0
    if replay and size >= offset + len(data):
        with path.open('rb') as existing:
            existing.seek(offset)
            if existing.read(len(data)) == data:
                return {'bytes_written': len(data), 'next_offset': offset + len(data),
                        'size_bytes': size, 'replayed': True, 'exit_code': 0}
    if size != offset:
        raise ValueError(f'Chunk offset {offset} does not match current file size {size}; read the file before choosing the next offset')
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + str(uuid.uuid4()) + '.tmp')
    try:
        with temporary.open('xb') as target:
            if before:
                with path.open('rb') as existing:
                    shutil.copyfileobj(existing, target)
                os.chmod(temporary, before.st_mode)
            target.write(data)
            target.flush()
            os.fsync(target.fileno())
        current = path.stat() if path.exists() else None
        def identity(stat):
            return (stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_mode) if stat else None
        if identity(before) != identity(current):
            raise ValueError('File changed while preparing this chunk; read it before retrying')
        temporary.replace(path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)
    return {'bytes_written': len(data), 'next_offset': offset + len(data),
            'size_bytes': offset + len(data), 'replayed': False, 'exit_code': 0}
