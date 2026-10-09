#!/bin/sh
# Assemble the static Vercel site (no backend): the web UI plus the recorded runs it replays.
set -e
cd "$(dirname "$0")/.."
rm -rf deploy/site && mkdir -p deploy/site/replay deploy/site/bench
cp -R public/. deploy/site/
cp demo/real-run.jsonl demo/bench-run.jsonl demo/terminal.txt demo/real-times.json demo/real.cast.json demo/bench.cast.json deploy/site/replay/
cp bench/results.json bench/baseline.json deploy/site/bench/
cp deploy/vercel.json deploy/site/vercel.json
