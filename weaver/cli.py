"""weaver watch <path>: point Weaver at any git repo.

Picks Semgrep rule packs from the files it sees, finds the repo's tests if there are any,
prints three lines and starts watching. Nothing else to configure.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
AWS_ENV = Path.home() / ".config" / "weaver" / "aws.env"

# extension -> (language, rule pack). Python ships in this repo; the others are fetched
# once from the public Semgrep registry (no login) and cached.
PACKS = {
    ".py": ("python", ROOT / "rules" / "weaver.yaml"),
    ".js": ("javascript", "p/javascript"),
    ".jsx": ("javascript", "p/javascript"),
    ".ts": ("typescript", "p/typescript"),
    ".tsx": ("typescript", "p/typescript"),
}


def sh(cmd, cwd=None) -> str:
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True).stdout.strip()


def registry_pack(name: str) -> Path:
    cache = Path.home() / ".cache" / "weaver" / "packs"
    cache.mkdir(parents=True, exist_ok=True)
    dest = cache / (name.replace("/", "-") + ".yaml")
    if not dest.exists():
        req = urllib.request.Request(f"https://semgrep.dev/c/{name}", headers={"User-Agent": "Semgrep/weaver"})
        dest.write_bytes(urllib.request.urlopen(req, timeout=60).read())
    return dest


def count_rules(path: Path) -> int:
    import yaml

    return len((yaml.safe_load(path.read_text()) or {}).get("rules", []))


def detect_tests(repo: Path, files: list[str]) -> str:
    names = [os.path.basename(f) for f in files]
    if any((n.startswith("test_") and n.endswith(".py")) or n.endswith("_test.py") for n in names) or "pytest.ini" in names:
        return "pytest"
    pkg = repo / "package.json"
    if pkg.exists():
        try:
            script = json.loads(pkg.read_text()).get("scripts", {}).get("test", "")
        except ValueError:
            script = ""
        if script and "no test specified" not in script:
            return "npm"
    return "none"


def load_aws_env():
    """Credentials stay in the operator's own file; they are read into the process and never printed."""
    if not AWS_ENV.exists():
        return
    for line in AWS_ENV.read_text().splitlines():
        line = line.strip().removeprefix("export ").strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip("'\""))


def watch(args) -> int:
    repo = Path(args.path).resolve()
    top = sh(["git", "-C", str(repo), "rev-parse", "--show-toplevel"]) if repo.is_dir() else ""
    if not top:
        print(f"weaver: {args.path} is not a git repository", file=sys.stderr)
        return 2
    repo = Path(top)
    semgrep = os.environ.get("WEAVER_SEMGREP") or shutil.which("semgrep") or str(Path.home() / ".local/bin/semgrep")
    version = sh([semgrep, "--version"])
    if not version:
        print("weaver: semgrep is not installed (https://semgrep.dev/docs/getting-started)", file=sys.stderr)
        return 2

    # keep Weaver's own files out of the repo's history and out of the watcher
    exclude = repo / ".git" / "info" / "exclude"
    exclude.parent.mkdir(exist_ok=True)
    if ".weaver/" not in (exclude.read_text() if exclude.exists() else ""):
        with exclude.open("a") as fh:
            fh.write("\n.weaver/\n")
    (repo / ".weaver").mkdir(exist_ok=True)

    files = sh(["git", "ls-files", "-co", "--exclude-standard"], cwd=repo).splitlines()
    langs, packs = [], []
    for f in files:
        hit = PACKS.get(os.path.splitext(f)[1])
        if hit and hit[0] not in langs:
            langs.append(hit[0])
            packs.append(hit[1] if isinstance(hit[1], Path) else registry_pack(hit[1]))
    if not packs:
        print("weaver: no Python, JavaScript or TypeScript files to watch here", file=sys.stderr)
        return 2
    tests = detect_tests(repo, files)
    verify = {"pytest": "rescan + pytest", "npm": "rescan + npm test",
              "none": "rescan + compile only (no tests found)"}[tests]

    url = f"http://127.0.0.1:{args.port}"
    print(f"weaver: semgrep {version}, {sum(count_rules(p) for p in packs)} rules ({', '.join(langs)})")
    print(f"weaver: watching {repo.name} ({len(files)} files), verify: {verify}")
    print(f"weaver: {url}", flush=True)

    os.environ.update(
        WEAVER_TARGET=str(repo),
        WEAVER_RULES=os.pathsep.join(str(p) for p in packs),
        WEAVER_TESTS=tests,
        WEAVER_EVENTS=str(repo / ".weaver" / "events.jsonl"),
        WEAVER_SEMGREP=semgrep,
    )
    load_aws_env()
    import uvicorn

    uvicorn.run("weaver.server:app", host="127.0.0.1", port=args.port, log_level="warning")
    return 0


def bench(args) -> int:
    """Print the saved benchmark numbers. Reads bench/results.json and bench/baseline.json; runs nothing."""
    res = json.loads((ROOT / "bench" / "results.json").read_text())
    base = json.loads((ROOT / "bench" / "baseline.json").read_text())
    s, cases = res["summary"], res["cases"]
    auto = {}
    for c in base["cases"]:
        auto[c["category"]] = auto.get(c["category"], 0) + (1 if c["fixed"] else 0)
    print(f"OWASP BenchmarkPython @ {s['benchmark_commit'][:7]}, {s['sampled']}-case sample (first 3 real cases per category)")
    print()
    head = f"{'category':<17}{'sampled':>8}{'semgrep found':>15}{'autofix alone':>15}{'weaver mended':>15}{'held':>6}"
    print(head)
    cats = []
    for c in cases:
        if c["category"] not in cats:
            cats.append(c["category"])
    for cat in sorted(cats):
        rows = [c for c in cases if c["category"] == cat]
        found = [c for c in rows if c.get("rule_matches_category")]
        print(f"{cat:<17}{len(rows):>8}{len(found):>15}{auto.get(cat, 0):>15}"
              f"{sum(c['status'] == 'mended' for c in found):>15}{sum(c['status'] == 'held' for c in found):>6}")
    n, b = s["flagged_in_benchmark_category"], base["summary"]
    m, h = s["mended_in_benchmark_category"], s["held_in_benchmark_category"]
    print(f"{'total':<17}{s['sampled']:>8}{n:>15}{b['fixed']:>15}{m:>15}{h:>6}")
    print()
    held = [c["case"] for c in cases if c.get("rule_matches_category") and c["status"] == "held"]
    by_auto = sum(1 for c in cases if c.get("rule_matches_category") and c["status"] == "mended" and c.get("fixed_by") == "semgrep autofix")
    print(f"Semgrep autofix alone  {b['fixed']:>2} / {b['cases']}  {round(100 * b['fixed'] / b['cases']):>3}%")
    print(f"Weaver                 {m:>2} / {n}  {round(100 * m / n):>3}%   ({by_auto} by Semgrep autofix, {m - by_auto} by LLM patch)")
    print(f"held for a human       {h:>2}   {' '.join(held)}")
    print(f"not found by Semgrep   {s['flagged_other_issue_only'] + s['not_flagged']:>2}   stay open")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(prog="weaver", description="Semgrep finds it. Weaver mends it.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    w = sub.add_parser("watch", help="watch a git repo and mend what Semgrep finds")
    w.add_argument("path", nargs="?", default=".")
    w.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8765")))
    w.set_defaults(fn=watch)
    b = sub.add_parser("bench", help="print the saved OWASP Benchmark sample numbers")
    b.add_argument("--summary", action="store_true", help="per-category table and totals (the default)")
    b.set_defaults(fn=bench)
    args = ap.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
