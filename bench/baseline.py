"""Baseline: Semgrep autofix alone on the benchmark cases where Semgrep found the benchmark's vulnerability.

For each case: `semgrep scan --config rules/weaver.yaml --autofix <file>` (same rules, no LLM),
then the same check Weaver's mends get here: Semgrep rescan of the file is clean and it compiles.
The file is restored afterwards.

    .venv/bin/python bench/baseline.py
"""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from weaver import server as W  # noqa: E402

REPO = ROOT / "bench" / "BenchmarkPython"
CMD = [W.SEMGREP, "scan", "--config", str(W.RULES), "--autofix", "--quiet", "--metrics=off", "--disable-version-check"]


def main():
    res = json.loads((ROOT / "bench" / "results.json").read_text())
    cases = [c for c in res["cases"] if c.get("rule_matches_category")]
    subprocess.run(["git", "checkout", "-q", "--", "testcode"], cwd=REPO, check=True)
    out = []
    for c in cases:
        path = REPO / "testcode" / f"{c['case']}.py"
        original = path.read_text()
        before = W.semgrep_raw(str(path))
        with_fix = sum(1 for r in before if r["extra"].get("fix"))
        subprocess.run(CMD + [str(path)], cwd=REPO, capture_output=True, text=True, timeout=300)
        new = path.read_text()
        changed = new != original
        try:
            compile(new, str(path), "exec")
            compiles = True
        except SyntaxError:
            compiles = False
        left = W.semgrep(str(path))
        path.write_text(original)
        out.append({"case": c["case"], "category": c["category"], "findings": len(before),
                    "findings_with_semgrep_fix": with_fix, "file_changed": changed, "compiles": compiles,
                    "findings_after": len(left), "fixed": bool(changed and compiles and not left)})
    fixed = sum(r["fixed"] for r in out)
    summary = {
        "what": "Semgrep autofix alone, no LLM, on the cases where Semgrep found the benchmark's vulnerability",
        "command": "semgrep scan --config rules/weaver.yaml --autofix --quiet --metrics=off --disable-version-check testcode/<case>.py",
        "fixed_means": "after autofix the Semgrep rescan of the file is clean and the file compiles (same check as Weaver's mends in this sample)",
        "semgrep": W.semgrep_version(), "rules": W.RULES.name,
        "cases": len(out), "fixed": fixed, "percent": round(100 * fixed / len(out)) if out else None,
        "cases_with_any_semgrep_fix": sum(1 for r in out if r["findings_with_semgrep_fix"]),
    }
    (ROOT / "bench" / "baseline.json").write_text(json.dumps({"summary": summary, "cases": out}, indent=1) + "\n")
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()
