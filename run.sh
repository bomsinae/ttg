#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

if [[ ! -x .venv/bin/python ]]; then
  python3 -m venv .venv
fi

if [[ ! -f .venv/.ttg_deps_stamp ]] || [[ requirements.txt -nt .venv/.ttg_deps_stamp ]]; then
  ./.venv/bin/python -m pip install -r requirements.txt
  touch .venv/.ttg_deps_stamp
fi

exec ./.venv/bin/python tg_client.py "$@"
