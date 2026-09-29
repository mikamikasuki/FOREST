#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
exec .venv/bin/python scripts/start.py
