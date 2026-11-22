#!/bin/sh
set -eu
ROOT="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
cd "$ROOT"

if [ -x "$ROOT/.venv/bin/python" ]; then
  PY="$ROOT/.venv/bin/python"
elif [ -x "$ROOT/../PeakCut/.venv/bin/python" ]; then
  PY="$ROOT/../PeakCut/.venv/bin/python"
else
  printf '%s\n' "No Python environment found. Run ./setup.sh first." >&2
  exit 1
fi

from_env() {
  [ -f "$ROOT/.env" ] || return 0
  sed -n "s/^[[:space:]]*$1[[:space:]]*=[[:space:]]*//p" "$ROOT/.env" | tail -n 1 | sed 's/[[:space:]]*$//'
}

DATA="${PEAKCUT_DATA:-$(from_env PEAKCUT_DATA)}"
DATA="${DATA:-$ROOT/data}"
PORT="${PEAKCUT_PORT:-$(from_env PEAKCUT_PORT)}"
PORT="${PORT:-8766}"
PUBLIC="${PEAKCUT_PUBLIC_URL:-$(from_env PEAKCUT_PUBLIC_URL)}"
PUBLIC="${PUBLIC:-http://127.0.0.1:$PORT}"
CHECK="${PEAKCUT_UPDATE_CHECK:-$(from_env PEAKCUT_UPDATE_CHECK)}"
mkdir -p "$DATA"

if [ -z "${PEAKCUT_AISHA_TOKEN:-}" ] && [ ! -s "$DATA/aisha_token" ]; then
  "$PY" -c 'import secrets; print(secrets.token_urlsafe(32))' > "$DATA/aisha_token"
  chmod 600 "$DATA/aisha_token"
fi

export PEAKCUT_DATA="$DATA"
export PEAKCUT_PORT="$PORT"
export PEAKCUT_PUBLIC_URL="$PUBLIC"
export PEAKCUT_UPDATE_CHECK="${CHECK:-0}"
if [ -z "${PEAKCUT_MODELS:-}" ] && [ -d "$ROOT/../PeakCut/data/models" ]; then
  export PEAKCUT_MODELS="$ROOT/../PeakCut/data/models"
fi

printf '%s\n' "Aisha Video Studio: $PEAKCUT_PUBLIC_URL"
printf '%s\n' "Automation token: $DATA/aisha_token"
exec "$PY" -m studio.server
