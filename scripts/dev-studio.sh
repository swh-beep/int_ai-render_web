#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STUDIO_DIR="${ROOT_DIR}/studio-app"
PYTHON_BIN="${PYTHON_BIN:-${ROOT_DIR}/venv/bin/python}"
API_HOST="${API_HOST:-127.0.0.1}"
API_PORT="${API_PORT:-8001}"
MISE_NODE_BIN="${HOME}/.local/share/mise/installs/node/24.14.0/bin"

if [[ -d "${MISE_NODE_BIN}" ]]; then
  export PATH="${MISE_NODE_BIN}:${PATH}"
fi

cleanup() {
  if [[ -n "${API_PID:-}" ]]; then
    kill "${API_PID}" 2>/dev/null || true
  fi
  if [[ -n "${VITE_PID:-}" ]]; then
    kill "${VITE_PID}" 2>/dev/null || true
  fi
}

trap cleanup EXIT INT TERM

cd "${ROOT_DIR}"
"${PYTHON_BIN}" -m uvicorn main:app --host "${API_HOST}" --port "${API_PORT}" &
API_PID=$!

cd "${STUDIO_DIR}"
if [[ ! -x "node_modules/.bin/vite" ]]; then
  echo "Installing studio-app dependencies with pnpm..."
  pnpm install --frozen-lockfile
fi

pnpm dev &
VITE_PID=$!

echo "FastAPI: http://${API_HOST}:${API_PORT}"
echo "Vite:    http://127.0.0.1:5173/marketing"

while kill -0 "${API_PID}" 2>/dev/null && kill -0 "${VITE_PID}" 2>/dev/null; do
  sleep 1
done

wait "${API_PID}" 2>/dev/null || true
wait "${VITE_PID}" 2>/dev/null || true
