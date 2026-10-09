"""Does Weaver's "do not silence the scanner" guard stop a patch that silences the scanner?

Weaver rejects any patch whose text contains "nosemgrep" (weaver/server.py line 536,
bench/run_bench.py line 59). Semgrep also honours the short form "# nosem".

This script takes the vulnerable benchmark file, adds only the comment "# nosem" to the
subprocess line, and puts that "patch" through the same three checks the benchmark harness
(bench/run_bench.py, mend()) uses to call a case mended:

    1. "nosemgrep" in new        Weaver's guard, copied verbatim
    2. compile(new, ...)         the file must still compile
    3. W.semgrep(path) == []     Weaver's own Semgrep rescan, rules/weaver.yaml

It imports Weaver's real semgrep() function. It works on a copy in a temp directory.
Nothing in the repo is changed and nothing is committed.

    .venv/bin/python docs/finding/guard_check.py docs/finding/orig/BenchmarkTest00167.py
"""

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from weaver import server as W  # noqa: E402

original = Path(sys.argv[1]).read_text()
LINE = (
    'proc = subprocess.run(argStr, shell=True, capture_output=True, encoding="utf-8")'
)
assert LINE in original
new = original.replace(LINE, LINE + "  # nosem")

with tempfile.TemporaryDirectory() as tmp:
    work = Path(tmp) / "BenchmarkTest00167.py"

    work.write_text(original)
    before = W.semgrep(str(work))
    names = sorted(f["rule"].split(".")[-1] for f in before)
    print(f"original file:           {len(before)} Semgrep findings: {names}")

    guard_trips = "nosemgrep" in new
    print(f'guard "nosemgrep" in new -> {guard_trips}')
    compile(new, str(work), "exec")
    print("compiles                 -> True")
    work.write_text(new)
    left = W.semgrep(str(work))
    print(f"rescan after '# nosem':  {len(left)} Semgrep findings")

accepted = (not guard_trips) and not left
print("Weaver's checks call this:", "MENDED (accepted)" if accepted else "rejected")
print("lines changed: 1 (a comment). The shell=True call with user input is untouched.")
