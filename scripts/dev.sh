#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
export PYENV_VERSION=RecipeBook
export PYTHONPATH="$PWD/backend/src"
# Explicit local-only login and deterministic fake model for development.
export DEV_AUTH_ENABLED=true
export LLM_PROVIDER=fake
pyenv exec uvicorn recipebook.main:app --host 127.0.0.1 --port 8000 --reload &
backend_pid=$!
trap 'kill "$backend_pid" 2>/dev/null || true' EXIT INT TERM
npm --prefix frontend run dev -- --port 5173 --strictPort
