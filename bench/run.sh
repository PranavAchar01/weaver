#!/bin/sh
# Reproduce the OWASP BenchmarkPython sample.
#   ./bench/run.sh            clone at the pinned commit, run Weaver on the 42-case sample, then the baseline
#   ./bench/run.sh --dry-run  check everything is in place and show the sample, change nothing
# Needs: semgrep on PATH, the claude CLI signed in, and `uv sync` done once.
# The patches come from a language model, so a new run can differ by a case from the saved one.
# Benchmark code is untrusted: it is scanned and patched, never installed or run.
set -e
cd "$(dirname "$0")/.."
PIN=f1291485808b66e20ddb6b01b10dc71b3df8c8ba
URL=https://github.com/OWASP-Benchmark/BenchmarkPython
REPO=bench/BenchmarkPython
PY=.venv/bin/python
DRY=""
[ "$1" = "--dry-run" ] && DRY=1

command -v semgrep >/dev/null || { echo "bench: semgrep is not on PATH"; exit 2; }
[ -x "$PY" ] || { echo "bench: run 'uv sync' first"; exit 2; }
echo "bench: semgrep $(semgrep --version), rules rules/weaver.yaml"

if [ ! -d "$REPO/.git" ]; then
  if [ -n "$DRY" ]; then echo "bench: would clone $URL at $PIN"; exit 0; fi
  git clone -q "$URL" "$REPO"
fi
HEAD=$(git -C "$REPO" rev-parse HEAD)
if [ "$HEAD" != "$PIN" ]; then
  if [ -n "$DRY" ]; then echo "bench: clone is at $HEAD, would check out $PIN"; else git -C "$REPO" checkout -q "$PIN"; fi
fi
echo "bench: $URL @ $(git -C "$REPO" rev-parse --short HEAD)"

$PY - <<'PYEOF'
import sys
sys.path.insert(0, "bench")
import run_bench
cases = run_bench.sample()
cats = sorted({c["category"] for c in cases})
print(f"bench: sample rule = first {run_bench.PER_CATEGORY} real-vulnerability cases per category, by test number")
print(f"bench: {len(cases)} cases in {len(cats)} categories")
PYEOF

if [ -n "$DRY" ]; then
  echo "bench: dry run, nothing changed. Without --dry-run this runs:"
  echo "  $PY bench/run_bench.py   (Weaver: Semgrep autofix first, then the LLM; writes bench/results.json)"
  echo "  $PY bench/classify.py    (marks which findings are in the benchmark's own category)"
  echo "  $PY bench/baseline.py    (Semgrep autofix alone on those cases; writes bench/baseline.json)"
  echo "  weaver bench --summary"
  exit 0
fi

git -C "$REPO" checkout -q -- testcode
$PY bench/run_bench.py
$PY bench/classify.py
$PY bench/baseline.py
.venv/bin/weaver bench --summary
