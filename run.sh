#!/bin/sh
# Weaver: one command. Watches ./target (or $WEAVER_TARGET) and serves the UI.
cd "$(dirname "$0")"
PORT="${PORT:-8765}"
echo "Weaver on http://localhost:$PORT"
exec uv run uvicorn weaver.server:app --host 127.0.0.1 --port "$PORT" --log-level warning
