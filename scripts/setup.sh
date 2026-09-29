#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
FOREST_PYTHON="${FOREST_PYTHON:-python3}"
"$FOREST_PYTHON" -c 'import sys; assert sys.version_info >= (3,11), "Python 3.11+ is required; set FOREST_PYTHON=python3.11"'
"$FOREST_PYTHON" -m venv .venv
.venv/bin/pip install -r requirements.lock.txt
(cd apps/web && npm ci && npm run build)
.venv/bin/python -m services.api.db
printf 'FOREST ready. Start with: .venv/bin/python scripts/start.py\n'
