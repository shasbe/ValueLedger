#!/usr/bin/env bash
# Start ValueLedger locally. Creates the venv and seeds demo data on first run.
set -euo pipefail
cd "$(dirname "$0")"

PORT="${PORT:-8077}"

if [ ! -d .venv ]; then
  echo "→ creating venv"
  python3 -m venv .venv
  ./.venv/bin/pip install -q --upgrade pip
  ./.venv/bin/pip install -q -r service/requirements.txt
fi

if [ ! -f service/valueledger.db ]; then
  echo "→ seeding demo org (90 days, 40 users)"
  (cd service && PYTHONPATH=. ../.venv/bin/python -m app.seed)
fi

echo "→ http://127.0.0.1:${PORT}  (admin UI)"
echo "→ http://127.0.0.1:${PORT}/docs  (API)"
cd service && PYTHONPATH=. ../.venv/bin/uvicorn app.main:app --port "${PORT}" --reload
