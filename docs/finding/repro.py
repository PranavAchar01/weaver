"""Reproduce Weaver's patch loop on OWASP BenchmarkPython BenchmarkTest00167 and keep every AI patch.

Same code path as bench/run_bench.py mend(): Weaver's own generate_patch() (same prompt, same
Claude Code CLI flags, same model), then Weaver's own semgrep() rescan with rules/weaver.yaml.
The only difference from the benchmark harness: every patch, diff and rescan is written to disk.

    WEAVER_TARGET=<dir with testcode/> WEAVER_NO_AWS=1 python repro.py <round-name> [attempts]
"""

import difflib
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]  # repo root, where the weaver package lives
sys.path.insert(0, str(ROOT))
from weaver import server as W  # noqa: E402

CASE = "BenchmarkTest00167"
ROUND = sys.argv[1]
ATTEMPTS = int(sys.argv[2]) if len(sys.argv) > 2 else 3
OUT = HERE / "attempts" / ROUND
OUT.mkdir(parents=True, exist_ok=True)

path = W.TARGET / "testcode" / f"{CASE}.py"
original = (HERE / "orig" / f"{CASE}.py").read_text()
path.write_text(original)

base_raw = W.semgrep_raw(str(path))
(HERE / "orig" / "semgrep-original.json").write_text(
    json.dumps(base_raw, indent=1) + "\n"
)
findings = W.semgrep(str(path))
log = {
    "case": CASE,
    "semgrep": W.semgrep_version(),
    "rules": W.RULES.name,
    "model_flag": W.MODEL,
    "original_findings": [{"rule": f["rule"], "line": f["line"]} for f in findings],
    "attempts": [],
}

feedback = ""
for n in range(1, ATTEMPTS + 1):
    rec = {"attempt": n, "feedback_given": feedback.strip()}
    try:
        patch = W.generate_patch(f"testcode/{path.name}", original, findings, feedback)
        new = patch["content"]
        rec.update(
            backend=patch["backend"], model=patch["model"], seconds=patch["seconds"]
        )
        (OUT / f"attempt{n}.py").write_text(new)
        (OUT / f"attempt{n}.diff").write_text(
            "".join(
                difflib.unified_diff(
                    original.splitlines(True),
                    new.splitlines(True),
                    f"a/testcode/{CASE}.py",
                    f"b/testcode/{CASE}.py",
                )
            )
        )
        if "nosemgrep" in new:
            raise ValueError("patch tried to silence the scanner")
        compile(new, str(path), "exec")
        path.write_text(new)
    except Exception as exc:
        path.write_text(original)
        rec["rejected"] = str(exc)[:200]
        feedback = (
            f"\nYour previous reply was rejected: patch rejected: {str(exc)[:100]}\n"
        )
        log["attempts"].append(rec)
        continue
    raw = W.semgrep_raw(str(path))
    (OUT / f"attempt{n}.semgrep.json").write_text(json.dumps(raw, indent=1) + "\n")
    left = W.semgrep(str(path))
    path.write_text(original)  # always roll back; nothing is kept
    rec["rescan"] = [{"rule": f["rule"], "line": f["line"]} for f in left]
    rec["verdict"] = "clean" if not left else "rolled back"
    log["attempts"].append(rec)
    if left:
        feedback = (
            f"\nA previous attempt was rolled back: Semgrep still reported {left[0]['rule']} "
            f"at line {left[0]['line']}. Try a different, still minimal fix.\n"
        )
    else:
        break  # the benchmark harness stops at the first clean rescan

log["finished"] = time.strftime("%Y-%m-%d %H:%M:%S %Z")
(OUT / "log.json").write_text(json.dumps(log, indent=1) + "\n")
print(json.dumps(log, indent=1))
