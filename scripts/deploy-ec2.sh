#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STUDIO_DIR="${ROOT_DIR}/studio-app"
PM2_NAME="${PM2_NAME:-int-ai-render-api}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
VENV_DIR="${VENV_DIR:-${ROOT_DIR}/venv}"
API_HOST="${API_HOST:-127.0.0.1}"
API_PORT="${API_PORT:-8001}"
PNPM_VERSION="${PNPM_VERSION:-10.27.0}"
SKIP_PULL="${SKIP_PULL:-0}"

log() {
  printf '\n[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"
}

need_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    printf 'Missing required command: %s\n' "$1" >&2
    exit 1
  fi
}

log "Checking required commands"
need_cmd git
need_cmd "${PYTHON_BIN}"
need_cmd node
need_cmd corepack
need_cmd pm2
need_cmd curl

cd "${ROOT_DIR}"

if [[ "${SKIP_PULL}" != "1" ]]; then
  log "Pulling latest code"
  git pull --ff-only
else
  log "Skipping git pull because SKIP_PULL=1"
fi

log "Preparing Python virtualenv"
if [[ ! -x "${VENV_DIR}/bin/python" ]]; then
  "${PYTHON_BIN}" -m venv "${VENV_DIR}"
fi
"${VENV_DIR}/bin/python" -m pip install -U pip
"${VENV_DIR}/bin/python" -m pip install -r "${ROOT_DIR}/requirements.txt"

log "Preparing pnpm ${PNPM_VERSION}"
corepack prepare "pnpm@${PNPM_VERSION}" --activate
corepack pnpm --version

log "Building studio-app"
cd "${STUDIO_DIR}"
corepack pnpm install --frozen-lockfile
corepack pnpm build

if [[ ! -f "${STUDIO_DIR}/dist/index.html" ]]; then
  printf 'Vite build did not create %s\n' "${STUDIO_DIR}/dist/index.html" >&2
  exit 1
fi

cd "${ROOT_DIR}"

log "Starting or restarting pm2 app ${PM2_NAME}"
if pm2 describe "${PM2_NAME}" >/dev/null 2>&1; then
  pm2 restart "${PM2_NAME}" --update-env
else
  pm2 start "${VENV_DIR}/bin/python" \
    --name "${PM2_NAME}" \
    -- -m uvicorn main:app --host "${API_HOST}" --port "${API_PORT}"
fi

pm2 save

log "Running local smoke checks"
curl -fsS "http://127.0.0.1:${API_PORT}/version.json" >/dev/null
curl -fsS "http://127.0.0.1:${API_PORT}/marketing" >/dev/null

log "Deploy complete"
printf 'FastAPI: http://127.0.0.1:%s\n' "${API_PORT}"
printf 'Marketing: http://127.0.0.1:%s/marketing\n' "${API_PORT}"
