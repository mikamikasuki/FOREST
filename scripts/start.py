#!/usr/bin/env python3
"""Foreground supervisor for local services; children use durable data."""
import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
# A script launched by absolute path otherwise only has scripts/ on sys.path.
sys.path.insert(0, str(ROOT))
from forest_cli.server import serve


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dev', action='store_true')
    parser.add_argument('--headless', action='store_true', help='Run only API and worker; no Node.js or Web build required')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', default=8000, type=int)
    parser.add_argument('--data-dir', type=Path, help='Data directory (defaults SQLite to forest.db inside it)')
    parser.add_argument('--database-url', help='Explicit SQLAlchemy database URL')
    args = parser.parse_args()
    python = ROOT / '.venv/bin/python'
    if not python.exists():
        raise SystemExit('Run python3 scripts/install.py first (add --headless for API/worker only)')
    if Path(sys.executable).absolute() != python.absolute():
        # Preserve the legacy .venv interpreter even when invoked with system
        # Python. exec avoids an additional supervisor process and signal relay.
        import os
        os.execv(str(python), [str(python), str(ROOT / 'scripts/start.py'), *sys.argv[1:]])
    return serve(host=args.host, port=args.port, data_dir=args.data_dir, database_url=args.database_url, headless=args.headless, dev=args.dev)


if __name__ == '__main__':
    raise SystemExit(main())
