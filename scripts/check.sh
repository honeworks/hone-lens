#!/usr/bin/env bash
# All quality gates. "Green" means this script exits 0.
set -euo pipefail
cd "$(dirname "$0")/.."
unset VIRTUAL_ENV   # never use another project's active venv
PKG_IMPORT="$(python3 - <<'PY'
import tomllib; d = tomllib.load(open("pyproject.toml", "rb"))
print(d["project"]["name"].replace("-", "_"))
PY
)"
step() { printf '\n\033[1m== %s\033[0m\n' "$*"; }

step "sync"
if [ -f uv.lock ]; then uv sync --all-extras --frozen; else uv sync --all-extras; fi
step "ruff check";   uv run ruff check .
step "ruff format";  uv run ruff format --check .
step "pyright";      uv run pyright
step "pytest (default suite + coverage)"
uv run pytest --cov="src/${PKG_IMPORT}" --cov-branch --cov-report=term-missing:skip-covered --cov-fail-under=90
step "build";        rm -rf dist && uv build
step "wheel smoke test (fresh venv, no extras)"
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
uv venv -q "$TMP/venv"
uv pip install -q --python "$TMP/venv/bin/python" dist/*.whl
"$TMP/venv/bin/python" - <<PY
import importlib, sys
m = importlib.import_module("${PKG_IMPORT}")
assert m.__version__, "missing __version__"
print("import ok:", m.__name__, m.__version__)
PY
if [ -f tests/smoke_quickstart.py ]; then
  "$TMP/venv/bin/python" tests/smoke_quickstart.py
fi
printf '\n\033[32mALL GATES GREEN\033[0m\n'
