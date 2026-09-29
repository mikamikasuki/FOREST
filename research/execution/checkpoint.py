"""Editable, versioned task checkpoints with atomic replacement.

The task defines its state, including optimizer/random/data-position state when
needed. This module guarantees durable replacement, not algorithmic equivalence.
"""
from __future__ import annotations
import json
import os
from pathlib import Path
import uuid


class CheckpointStore:
    def __init__(self, path=None, *, schema_version=1):
        configured = path or os.environ.get('FOREST_RESUME_PATH') or os.environ.get('FOREST_CHECKPOINT_PATH')
        if not configured:
            raise ValueError('Provide a checkpoint path or FOREST_CHECKPOINT_PATH')
        self.path = Path(configured)
        self.schema_version = schema_version

    def load(self, default=None):
        if not self.path.exists():
            return default
        record = json.loads(self.path.read_text())
        if record.get('schema_version') != self.schema_version:
            raise ValueError('Checkpoint schema changed; migrate the saved state or restart explicitly')
        if 'state' not in record:
            raise ValueError('Checkpoint does not contain state')
        return record['state']

    def save(self, state, *, progress=None):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        previous = None
        if self.path.exists():
            previous = json.loads(self.path.read_text())
        record = {'id': str(uuid.uuid4()), 'revision': (previous or {}).get('revision', 0) + 1,
                  'schema_version': self.schema_version, 'attempt_id': os.environ.get('FOREST_ATTEMPT_ID'),
                  'state': state, 'progress': progress}
        encoded = json.dumps(record, ensure_ascii=False, allow_nan=False)
        temporary = self.path.with_name(self.path.name + '.' + str(uuid.uuid4()) + '.tmp')
        try:
            with temporary.open('w') as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            # Keep one readable previous version for explicit recovery/migration.
            if previous is not None:
                backup = self.path.with_name(self.path.name + '.previous')
                backup_tmp = backup.with_name(backup.name + '.tmp')
                with backup_tmp.open('w') as handle:
                    json.dump(previous, handle, ensure_ascii=False)
                    handle.flush()
                    os.fsync(handle.fileno())
                backup_tmp.replace(backup)
            temporary.replace(self.path)
            try:
                directory = os.open(self.path.parent, os.O_RDONLY)
                try: os.fsync(directory)
                finally: os.close(directory)
            except OSError:
                pass  # Some filesystem platforms do not support directory fsync.
        finally:
            temporary.unlink(missing_ok=True)
        return record
