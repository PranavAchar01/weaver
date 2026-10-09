#!/bin/sh
# Weaver: one command. Watches ./target (or $WEAVER_TARGET) and serves the UI.
cd "$(dirname "$0")"
PORT="${PORT:-8765}"
# AWS credentials, if the operator has put them there. Never stored in the repo.
[ -f "$HOME/.config/weaver/aws.env" ] && { set -a; . "$HOME/.config/weaver/aws.env"; set +a; }
[ -d target ] || ./demo/reset.sh
echo "Weaver on http://localhost:$PORT"
exec uv run uvicorn weaver.server:app --host 127.0.0.1 --port "$PORT" --log-level warning
