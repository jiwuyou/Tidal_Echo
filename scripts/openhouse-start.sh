#!/bin/sh
set -eu

ROOT="/root/Tidal_Echo"
STATE_DIR="${TIDAL_ECHO_STATE_DIR:-/root/.config/tidal-echo}"
ENV_FILE="${TIDAL_ECHO_ENV_FILE:-$STATE_DIR/relay.env}"
VENV="$ROOT/.venv-openhouse"

mkdir -p "$STATE_DIR"

if [ ! -f "$ENV_FILE" ]; then
  SECRET="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
  umask 077
  {
    printf 'RELAY_SECRET=%s\n' "$SECRET"
    printf 'RELAY_AI_NAME=Claude\n'
    printf 'RELAY_HUMAN_NAME=你\n'
    printf 'RELAY_PORT=3011\n'
    printf 'RELAY_DB=%s/relay.db\n' "$STATE_DIR"
    printf 'RELAY_UPLOAD_DIR=%s/uploads\n' "$STATE_DIR"
    printf 'RELAY_PUBLIC_PREFIX=/relay\n'
    printf 'RELAY_APP_PATH=/chat/\n'
    printf 'RELAY_ALLOW_ORIGINS=http://127.0.0.1:23087\n'
    printf 'RELAY_LOOP_INGEST_URL=http://127.0.0.1:3020/loop/ingest\n'
  } > "$ENV_FILE"
fi

set -a
. "$ENV_FILE"
set +a

if [ ! -x "$VENV/bin/python" ]; then
  python3 -m venv "$VENV"
fi

"$VENV/bin/python" -m pip install -q -r "$ROOT/backend/requirements.txt"

export TIDAL_ECHO_HOST="${TIDAL_ECHO_HOST:-127.0.0.1}"
export TIDAL_ECHO_PORT="${TIDAL_ECHO_PORT:-23087}"
export TIDAL_ECHO_BACKEND_PYTHON="$VENV/bin/python"

python3 "$ROOT/scripts/openhouse_local_gateway.py"
