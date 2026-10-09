#!/bin/sh
# One real run, recorded: reset the target, start Weaver, let it mend everything,
# land one new vulnerable commit, let it mend that too. Output: demo/run.jsonl
cd "$(dirname "$0")/.."
PORT="${PORT:-8765}"
./demo/reset.sh
rm -f events.jsonl
./run.sh > /tmp/weaver-record.log 2>&1 &
SERVER=$!
count() { c=$(grep -c "\"type\": \"$1\"" events.jsonl 2>/dev/null); echo "${c:-0}"; }
wait_for() { i=0; while [ "$(count "$1")" -lt "$2" ] && [ $i -lt 1500 ]; do sleep 0.2; i=$((i+1)); done; }
wait_for scan_done 1
N=$(count tear)
wait_for mended "$N"
sleep "${GAP:-1.2}"
./demo/introduce.sh
wait_for mended $((N+1))
sleep 6
cp events.jsonl demo/run.jsonl
kill $SERVER
echo "recorded $(wc -l < demo/run.jsonl) events to demo/run.jsonl"
