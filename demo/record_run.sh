#!/bin/sh
# One real run, recorded, the way a user would start it:
#   cd my-app && weaver watch .
# my-app is a fresh copy of the demo repo at its vulnerable starting point. Weaver mends everything,
# then one new vulnerable commit lands and it mends that too.
# Output: demo/run.jsonl (the events) and demo/terminal.txt (what the command printed).
cd "$(dirname "$0")/.."
ROOT=$(pwd)
./demo/reset.sh
[ -d demo/my-app ] && rm -r demo/my-app
cp -R target demo/my-app
EV=demo/my-app/.weaver/events.jsonl
( cd demo/my-app && PATH="$ROOT/.venv/bin:$PATH" exec weaver watch . ) > demo/terminal.txt 2>&1 &
SERVER=$!
count() { c=$(grep -c "\"type\": \"$1\"" $EV 2>/dev/null); echo "${c:-0}"; }
wait_for() { i=0; while [ "$(count "$1")" -lt "$2" ] && [ $i -lt 1500 ]; do sleep 0.2; i=$((i+1)); done; }
wait_for scan_done 1
N=$(count tear)
wait_for mended "$N"
sleep "${GAP:-1.2}"
WEAVER_REPO="$ROOT/demo/my-app" ./demo/introduce.sh
wait_for mended $((N+1))
i=0; while [ $(( $(count rule_written) + $(count rule_discarded) )) -lt $((N+1)) ] && [ $i -lt 150 ]; do sleep 0.2; i=$((i+1)); done
sleep 4
cp $EV demo/run.jsonl
kill $SERVER
echo "recorded $(wc -l < demo/run.jsonl) events to demo/run.jsonl"
cat demo/terminal.txt
