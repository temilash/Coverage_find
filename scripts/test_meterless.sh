#!/usr/bin/env bash
# The Meterless Engine tests: the two vendored reference engines' own suites (H-MEM, World Model),
# our HTTP service over them (meterless_service/test/service.test.ts) and the Python client
# (tests/test_memory.py, including the in-memory fallback). Run from the repo root:
#
#   scripts/test_meterless.sh
#
# Installs each engine's test dependencies (vitest) on first run if node_modules is missing.
set -euo pipefail
cd "$(dirname "$0")/.."
PY=${PYTHON:-.venv/bin/python}
fail=0

suite() {  # name, directory, command...
  local name=$1 dir=$2; shift 2
  echo "=== $name ($dir)"
  if (cd "$dir" && { [ -d node_modules ] || [ ! -f package-lock.json ] || npm ci --no-audit --no-fund --silent; } && "$@"); then
    echo "=== $name: PASSED"
  else
    echo "=== $name: FAILED"; fail=1
  fi
}

suite "H-MEM engine (vendored)" vendor/meterless/hmem npm test --silent
suite "World Model engine (vendored)" vendor/meterless/world-model npm test --silent
suite "Meterless service over both engines" meterless_service npm test --silent
suite "Python Meterless client" . "$PY" -m pytest -q tests/test_memory.py

exit $fail
