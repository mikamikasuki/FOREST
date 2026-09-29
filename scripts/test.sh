#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
.venv/bin/python -m pytest -q
(cd apps/web && npm run build)
(cd apps/web && npm test)
