"""Run Weaver's scan -> patch -> verify loop, unattended, on a sample of OWASP BenchmarkPython.

Sample rule (fixed before the run, no cherry-picking): for every category in the benchmark's
expected-results CSV, the first 3 real-vulnerability cases by test number.
The cases have no unit tests, so "verified" here means only: Semgrep rescan of the file is clean
and the file still compiles. That is weaker than the test-verified mends in the main demo.

    .venv/bin/python bench/run_bench.py
"""
import csv
import json
import statistics
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from weaver import server as W  # noqa: E402

REPO = ROOT / "bench" / "BenchmarkPython"
PER_CATEGORY = 3
MAX_ATTEMPTS = 3


def sample():
    rows = [r for r in csv.reader((REPO / "expectedresults-0.1.csv").open()) if r and not r[0].startswith("#")]
    picked, seen = [], {}
    for name, cat, real, cwe in sorted(rows):
        if real.strip() == "true" and seen.get(cat, 0) < PER_CATEGORY:
            seen[cat] = seen.get(cat, 0) + 1
            picked.append({"case": name, "category": cat, "cwe": cwe})
    return picked


def mend(case, findings):
    path = REPO / "testcode" / f"{case['case']}.py"
    original = path.read_text()
    t0, feedback, reason = time.time(), "", ""
    if any(f["fix"] for f in findings):  # same order as the engine: Semgrep's own fix first
        fix = W.semgrep_autofix(str(path), original)
        if fix:
            try:
                compile(fix["content"], str(path), "exec")
                path.write_text(fix["content"])
                if not W.semgrep(str(path)):
                    return {"status": "mended", "fixed_by": "semgrep autofix", "attempts": 1,
                            "seconds": round(time.time() - t0, 2),
                            "lines_changed": len(W.diff_lines(original, fix["content"]))}
            except SyntaxError:
                pass
            path.write_text(original)
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            patch = W.generate_patch(f"testcode/{path.name}", original, findings, feedback)
            new = patch["content"]
            if "nosemgrep" in new:
                raise ValueError("patch tried to silence the scanner")
            compile(new, str(path), "exec")  # the file must still compile
            path.write_text(new)
        except Exception as exc:
            path.write_text(original)
            reason = f"patch rejected: {str(exc)[:100]}"
            feedback = f"\nYour previous reply was rejected: {reason}\n"
            continue
        left = W.semgrep(str(path))
        if not left:
            return {"status": "mended", "fixed_by": patch["backend"], "attempts": attempt, "seconds": round(time.time() - t0, 2),
                    "lines_changed": len(W.diff_lines(original, new))}
        path.write_text(original)
        reason = f"rescan still finds {left[0]['rule']}"
        feedback = f"\nA previous attempt was rolled back: Semgrep still reported {left[0]['rule']} at line {left[0]['line']}. Try a different, still minimal fix.\n"
    return {"status": "held", "attempts": MAX_ATTEMPTS, "seconds": round(time.time() - t0, 2), "reason": reason}


def main():
    cases = sample()
    subprocess.run(["git", "checkout", "-q", "--", "testcode"], cwd=REPO, check=True)
    started = time.time()
    by_case = {}
    for c in cases:
        by_case[c["case"]] = W.semgrep(str(REPO / "testcode" / f"{c['case']}.py"))  # baseline, one file each
    flagged = [c for c in cases if by_case[c["case"]]]
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda c: mend(c, by_case[c["case"]]), flagged))
    out = []
    for c in cases:
        f = by_case[c["case"]]
        row = {**c, "semgrep_findings": len(f), "rules": sorted({x["rule"] for x in f})}
        row.update(results[flagged.index(c)] if c in flagged else {"status": "not_flagged"})
        out.append(row)
    mended = [r for r in out if r["status"] == "mended"]
    summary = {
        "benchmark": "OWASP Benchmark for Python (BenchmarkPython) v0.1",
        "benchmark_commit": subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True, text=True).stdout.strip(),
        "selection_rule": f"first {PER_CATEGORY} real-vulnerability cases per category by test number, from expectedresults-0.1.csv",
        "verified_means": "Semgrep rescan of the file is clean and the file still compiles; these cases have no unit tests",
        "semgrep": W.semgrep_version(), "rules": W.RULES.name, "patcher": f"claude code cli ({W.MODEL})",
        "sampled": len(cases), "flagged_by_semgrep": len(flagged), "not_flagged": len(cases) - len(flagged),
        "mended": len(mended), "held": len(flagged) - len(mended),
        "engine": "Semgrep autofix first, LLM patch only when there is no fix or it does not verify",
        "mended_by_semgrep_autofix": sum(r.get("fixed_by") == "semgrep autofix" for r in mended),
        "mended_by_llm": sum(r.get("fixed_by") != "semgrep autofix" for r in mended),
        "median_seconds_to_mend": round(statistics.median(r["seconds"] for r in mended), 1) if mended else None,
        "wall_seconds": round(time.time() - started, 1),
        "time_note": "seconds per case = patch written to rescan clean, 4 cases in parallel",
    }
    (ROOT / "bench" / "results.json").write_text(json.dumps({"summary": summary, "cases": out}, indent=1) + "\n")
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()
