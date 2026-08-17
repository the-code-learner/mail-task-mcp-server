#!/bin/sh
set -eu

ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
VENV_DIR="${VENV_DIR:-/opt/venv}"
REQ_FILE="$ROOT/requirements.txt"
REQ_HASH="$(sha256sum "$REQ_FILE" | awk '{print $1}')"
REQ_MARKER="$VENV_DIR/.nomadcompass-requirements.sha256"

if [ ! -x "$VENV_DIR/bin/python" ] || [ ! -f "$REQ_MARKER" ] || [ "$(cat "$REQ_MARKER" 2>/dev/null || true)" != "$REQ_HASH" ]; then
    echo "Preparing Python environment..."
    rm -rf "$VENV_DIR"
    python -m venv "$VENV_DIR"
    "$VENV_DIR/bin/pip" install --disable-pip-version-check --no-cache-dir -r "$REQ_FILE"
    printf '%s\n' "$REQ_HASH" > "$REQ_MARKER"
fi

if [ "${CONTEXT_SEMANTIC_ENABLED:-true}" = "true" ] && [ "${CONTEXT_MODEL_AUTO_PREPARE:-true}" = "true" ]; then
    if ! "$VENV_DIR/bin/python" "$ROOT/scripts/prepare_context_model.py"; then
        echo "WARNING: semantic model preparation failed; lexical FTS search remains available." >&2
    fi
fi

export PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
exec "$VENV_DIR/bin/python" -m nomadcompass.server
