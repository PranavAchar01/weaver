"""Weaver: continuous remediation.

Watch a git repo, scan it with Semgrep, have an LLM write a minimal patch for every
finding, verify the patch (rescan, tests, exploit check) and only then commit it.
Every step is an event: appended to events.jsonl and streamed to the UI over SSE.
"""

from __future__ import annotations

import ast
import asyncio
import difflib
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import aws

ROOT = Path(__file__).resolve().parent.parent
TARGET = Path(os.environ.get("WEAVER_TARGET", ROOT / "target")).resolve()
RULES_LIST = [
    Path(p) if os.path.exists(p) else p
    for p in os.environ.get("WEAVER_RULES", str(ROOT / "rules" / "weaver.yaml")).split(os.pathsep)
]
RULES = Path(str(RULES_LIST[0]))
EVENTS_FILE = Path(os.environ.get("WEAVER_EVENTS", ROOT / "events.jsonl"))
SEMGREP = (
    os.environ.get("WEAVER_SEMGREP")
    or shutil.which("semgrep")
    or str(Path.home() / ".local/bin/semgrep")
)
CLAUDE = os.environ.get("WEAVER_CLAUDE") or (
    "/usr/local/bin/claude"
    if Path("/usr/local/bin/claude").exists()
    else shutil.which("claude")
)
MODEL = os.environ.get("WEAVER_MODEL", "sonnet")
TESTS = os.environ.get("WEAVER_TESTS", "pytest")  # pytest | npm | none
_py = TARGET / ".venv" / "bin" / "python"
TEST_CMD = (
    ["npm", "test", "--silent"]
    if TESTS == "npm"
    else [str(_py) if _py.exists() else sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider"]
)
MAX_ATTEMPTS = 3
MEMORY = os.environ.get("WEAVER_MEMORY", "1") != "0"  # write a Semgrep rule after each verified mend
SCAN_EXCLUDES = ["tests", "exploits", ".weaver", "node_modules"] + [
    x for x in os.environ.get("WEAVER_EXCLUDE", "").split(os.pathsep) if x
]

events: list[dict] = []
events_lock = threading.Lock()


def emit(kind: str, **data) -> dict:
    with events_lock:
        ev = {"seq": len(events), "t": round(time.time(), 3), "type": kind, **data}
        events.append(ev)
        with EVENTS_FILE.open("a") as fh:
            fh.write(json.dumps(ev) + "\n")
    return ev


def run(cmd, cwd=TARGET, timeout=180, input=None, env=None):
    return subprocess.run(
        cmd,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=timeout,
        input=input,
        env=env,
    )


def git(*args) -> str:
    return run(["git", *args]).stdout.strip()


# ---------------------------------------------------------------- repo state


def repo_files() -> list[str]:
    out = git("ls-files", "-co", "--exclude-standard")
    return sorted(p for p in out.splitlines() if (TARGET / p).is_file())


def tree() -> list[dict]:
    dirs: dict[str, list[str]] = {}
    for p in repo_files():
        d = os.path.dirname(p) or "."
        dirs.setdefault(d, []).append(os.path.basename(p))
    return [{"dir": d, "files": sorted(f)} for d, f in sorted(dirs.items())]


def fingerprint() -> tuple[str, str]:
    h = hashlib.sha1()
    for p in repo_files():
        st = (TARGET / p).stat()
        h.update(f"{p}:{st.st_mtime_ns}:{st.st_size};".encode())
    return git("rev-parse", "HEAD"), h.hexdigest()


def sha(text: str) -> str:
    return hashlib.sha1(text.encode()).hexdigest()


# ---------------------------------------------------------------- semgrep


RULE_PREFIX = str(RULES.resolve().parent).strip("/").replace("/", ".") + "."


def short_rule(check_id: str) -> str:
    """Semgrep prefixes ids with the config path; strip it to get the registry rule id."""
    if "weaver-mem-" in check_id:  # a rule Weaver wrote into the repo's .weaver/rules
        return check_id[check_id.index("weaver-mem-"):]
    if check_id.startswith(RULE_PREFIX):
        check_id = check_id[len(RULE_PREFIX):]
    parts = check_id.split(".")
    if len(parts) > 1 and parts[-1] == parts[-2]:
        parts = parts[:-1]
    return ".".join(parts)


def semgrep_raw(path: str | None = None) -> list[dict]:
    """Run Semgrep on the repo (or one file) and return its JSON results untouched."""
    cmd = [SEMGREP, "scan"]
    for cfg in scan_configs():
        cmd += ["--config", cfg]
    cmd += ["--json", "--quiet", "--metrics=off", "--disable-version-check"]
    for ex in SCAN_EXCLUDES:
        cmd += ["--exclude", ex]
    cmd.append(path or ".")
    res = run(cmd, timeout=300)
    return json.loads(res.stdout).get("results", [])


def semgrep(path: str | None = None) -> list[dict]:
    """Findings come straight from Semgrep's JSON output."""
    out = []
    for r in semgrep_raw(path):
        out.append(
            {
                "rule": short_rule(r["check_id"]),
                "path": r["path"],
                "line": r["start"]["line"],
                "severity": r["extra"].get("severity", ""),
                "message": " ".join(r["extra"].get("message", "").split()),
                "fix": bool(r["extra"].get("fix")),
            }
        )
    out.sort(key=lambda f: (f["path"], f["line"], f["rule"]))
    return out


def scan_configs() -> list[str]:
    """The rule packs, plus any rules Weaver has written into the watched repo."""
    cfgs = [str(r) for r in RULES_LIST]
    mem = TARGET / ".weaver" / "rules"
    if mem.is_dir() and any(mem.glob("*.yaml")):
        cfgs.append(str(mem))
    return cfgs


def rule_count() -> int:
    import yaml

    n = 0
    for cfg in RULES_LIST:
        try:
            n += len(yaml.safe_load(Path(str(cfg)).read_text()).get("rules", []))
        except Exception:
            pass
    return n


def semgrep_version() -> str:
    return run([SEMGREP, "--version"]).stdout.strip()


# ---------------------------------------------------------------- patcher (Claude Code CLI, headless)

SYSTEM = (
    "You are the patch function of a security remediation tool. You receive one source file and the "
    "Semgrep findings in it. You return the complete corrected file and nothing else. You never use tools."
)

PROMPT = """Semgrep reported these findings in `{path}`:
{findings}
{feedback}
Fix every finding with the smallest change that really removes the vulnerability.
Rules:
- Keep behaviour for legitimate input. Keep function names, signatures, routes and response shapes.
- Only the standard library and modules this file already imports or the repo clearly depends on.
- Never silence the scanner: no `nosemgrep`, no comments about the fix, no renaming to dodge a rule.
- Secrets come from environment variables named after the constant (for example SECRET_KEY).
- Touch as few lines as possible. Do not reformat or reorder anything else.

Return the full corrected file between these two marker lines, with nothing before or after:
<<<FILE
...
FILE>>>

Current content of `{path}`:
<<<FILE
{content}
FILE>>>
"""


def ask_claude(prompt: str, system: str = "") -> tuple[str, dict]:
    cmd = [
        CLAUDE,
        "-p",
        "--output-format",
        "json",
        "--model",
        MODEL,
        "--tools",
        "",
        "--strict-mcp-config",
        "--mcp-config",
        '{"mcpServers":{}}',
        "--setting-sources",
        "",
        "--disable-slash-commands",
        "--system-prompt",
        system or SYSTEM,
    ]
    res = run(cmd, cwd=ROOT / "weaver", timeout=180, input=prompt)
    data = json.loads(res.stdout)
    if data.get("is_error"):
        raise RuntimeError(str(data.get("result"))[:200])
    model = next(iter(data.get("modelUsage", {})), MODEL)
    return data["result"], {"model": model}


def extract_file(reply: str) -> str:
    m = re.search(r"<<<FILE\n(.*?)\n?FILE>>>", reply, re.S)
    if not m:
        raise ValueError("patcher reply had no file block")
    return m.group(1).rstrip("\n") + "\n"


def generate_patch(
    path: str, content: str, findings: list[dict], feedback: str = ""
) -> dict:
    t0 = time.time()
    listing = "\n".join(
        f"- line {f['line']}: {f['rule']}: {f['message'][:300]}" for f in findings
    )
    prompt = PROMPT.format(
        path=path, findings=listing, feedback=feedback, content=content.rstrip("\n")
    )
    try:  # Amazon Bedrock first; any refusal or error falls back to the local CLI
        reply = aws.bedrock_converse(SYSTEM, prompt)
        meta = {"model": aws.BEDROCK_MODEL, "backend": "bedrock"}
        extract_file(reply)
    except Exception:
        reply, meta = ask_claude(prompt)
        meta["backend"] = "claude cli"
    return {
        "based_on": sha(content),
        "content": extract_file(reply),
        "seconds": round(time.time() - t0, 2),
        **meta,
    }


def semgrep_autofix(path: str, original: str) -> dict | None:
    """Apply the fixes Semgrep's own rules ship. Returns the fixed file, or None if nothing changed."""
    t0 = time.time()
    cmd = [SEMGREP, "scan"]
    for cfg in scan_configs():
        cmd += ["--config", cfg]
    cmd += ["--autofix", "--quiet", "--metrics=off", "--disable-version-check", path]
    run(cmd, timeout=300)
    file = Path(path) if os.path.isabs(path) else TARGET / path
    new = file.read_text()
    file.write_text(original)
    if new == original:
        return None
    return {
        "based_on": sha(original),
        "content": new,
        "seconds": round(time.time() - t0, 2),
        "model": "",
        "backend": "semgrep autofix",
    }


def diff_lines(old: str, new: str) -> list[str]:
    out = []
    for line in difflib.unified_diff(
        old.splitlines(), new.splitlines(), n=0, lineterm=""
    ):
        if line.startswith(("---", "+++", "@@")):
            continue
        out.append(line)
    return out


# ---------------------------------------------------------------- memory: Weaver writes Semgrep rules

RULE_SYSTEM = (
    "You write Semgrep rules. You receive a vulnerable file and its fixed version. "
    "You return one Semgrep rule in YAML and nothing else. You never use tools."
)

RULE_PROMPT = """Semgrep rule `{rule}` fired at line {line} of `{path}`. The file was then fixed.
Write ONE Semgrep rule for the vulnerable code shape that was just fixed, so the same mistake is caught
if it comes back anywhere in this repo.
- Use `pattern`, `patterns`, `pattern-either`, `pattern-not`, `pattern-inside` and metavariables. No taint mode.
- Generalise variable names with metavariables; keep the dangerous call or constant concrete.
- It MUST match the BEFORE file and MUST NOT match the AFTER file.
- Fields: id, languages, severity, message, and the pattern keys. Nothing else.

Return the YAML (top-level key `rules:`) between these two marker lines, with nothing before or after:
<<<FILE
...
FILE>>>

BEFORE `{path}`:
<<<FILE
{before}
FILE>>>

AFTER `{path}`:
<<<FILE
{after}
FILE>>>
"""


def write_rule(tear: dict, path: str, before: str, after: str, commit: str, busy: set[str]) -> dict:
    """Ask the LLM for a Semgrep rule, then let Semgrep decide whether it is kept.

    Kept only if: `semgrep --validate` accepts it, it matches the pre-fix file, it does not match
    the post-fix file, and it flags nothing else in the repo that the registry rules pass.
    """
    import tempfile

    import yaml

    t0 = time.time()
    prompt = RULE_PROMPT.format(
        rule=tear["rule"], line=tear["line"], path=path, before=before.rstrip("\n"), after=after.rstrip("\n")
    )
    try:
        reply = aws.bedrock_converse(RULE_SYSTEM, prompt)
        backend = "bedrock"
        extract_file(reply)
    except Exception:
        reply, _ = ask_claude(prompt, RULE_SYSTEM)
        backend = "claude cli"
    doc = yaml.safe_load(extract_file(reply))
    rule = doc["rules"][0]
    slug = re.sub(r"[^a-z0-9]+", "-", f"{tear['rule'].split('.')[-1]}-{Path(path).stem}".lower()).strip("-")
    rule["id"] = f"weaver-mem-{slug}"
    rule["metadata"] = {"written_by": "weaver", "learned_from": f"{path}@{commit}", "registry_rule": tear["rule"]}
    text = yaml.safe_dump({"rules": [rule]}, sort_keys=False, width=100)
    ext = Path(path).suffix
    base = [SEMGREP, "scan", "--quiet", "--metrics=off", "--disable-version-check"]
    with tempfile.TemporaryDirectory() as tmp:
        cand = Path(tmp) / "rule.yaml"
        cand.write_text(text)
        if run([SEMGREP, "scan", "--validate", "--metrics=off", "--config", str(cand)], cwd=tmp).returncode != 0:
            return {"kept": False, "why": "semgrep --validate rejected it"}
        src = Path(tmp) / "src"
        src.mkdir()
        (src / f"before{ext}").write_text(before)
        (src / f"after{ext}").write_text(after)
        res = run(base + ["--config", str(cand), "--json", "--no-git-ignore", "."], cwd=src)
        hits = {Path(r["path"]).name for r in json.loads(res.stdout).get("results", [])}
        if f"before{ext}" not in hits:
            return {"kept": False, "why": "does not match the pre-fix file"}
        if f"after{ext}" in hits:
            return {"kept": False, "why": "still matches the post-fix file"}
        cmd = base + ["--config", str(cand), "--json"]
        for ex in SCAN_EXCLUDES:
            cmd += ["--exclude", ex]
        res = run(cmd + ["."])
        others = {r["path"] for r in json.loads(res.stdout).get("results", [])} - busy - {path}
        if others:
            return {"kept": False, "why": "flags code the registry rules pass"}
    dest = TARGET / ".weaver" / "rules"
    dest.mkdir(parents=True, exist_ok=True)
    (dest / f"{rule['id']}.yaml").write_text(text)
    return {"kept": True, "rule": rule["id"], "file": f".weaver/rules/{rule['id']}.yaml",
            "backend": backend, "seconds": round(time.time() - t0, 2)}


# ---------------------------------------------------------------- verification


def run_tests() -> tuple[bool, str]:
    res = run(TEST_CMD, timeout=300)
    if TESTS == "npm":
        return res.returncode == 0, "npm test passes" if res.returncode == 0 else "npm test fails"
    tail = (res.stdout.strip().splitlines() or [""])[-1]
    m = re.search(r"(\d+) passed", tail)
    f = re.search(r"(\d+) failed", tail)
    if res.returncode == 0 and m:
        return True, f"{m.group(1)} tests pass"
    return False, f"{f.group(1) if f else 'some'} tests fail" if (
        f or m
    ) else "tests did not run"


def exploit_script(path: str) -> Path | None:
    p = TARGET / "exploits" / (path[:-3].replace("/", "__") + ".py")
    return p if p.exists() else None


def exploit_works(script: Path) -> tuple[bool, str]:
    """Exploit scripts exit 0 when the attack succeeds."""
    res = run([sys.executable, str(script)], cwd=TARGET / "exploits", timeout=60)
    return res.returncode == 0, (
        res.stdout.strip().splitlines() or [res.stderr.strip()[-120:]]
    )[-1]


# ---------------------------------------------------------------- the loop


class Weaver:
    def __init__(self):
        self.pool = ThreadPoolExecutor(max_workers=4)
        self.tears: dict[str, dict] = {}  # path -> open tear
        self.held: dict[str, str] = {}  # path -> content hash that failed
        self.n = 0
        self.own_commits: set[str] = set()
        self.tree_sig = ""
        self.rules_discarded = 0

    def emit_tree(self):
        tr = tree()
        sig = sha(json.dumps(tr))
        if sig != self.tree_sig:
            self.tree_sig = sig
            emit("tree", tree=tr)

    def scan(self, reason: str) -> dict[str, list[dict]]:
        emit("scan_start", reason=reason)
        t0 = time.time()
        found = semgrep()
        by_file: dict[str, list[dict]] = {}
        for f in found:
            by_file.setdefault(f["path"], []).append(f)
        emit(
            "scan_done",
            findings=len(found),
            files=len(by_file),
            seconds=round(time.time() - t0, 2),
        )
        return by_file

    def cycle(self, reason: str):
        self.emit_tree()
        by_file = self.scan(reason)
        work: list[tuple[dict, Future | None]] = []
        for path, findings in by_file.items():
            content = (TARGET / path).read_text()
            if self.held.get(path) == sha(content):
                continue
            self.held.pop(path, None)
            if path not in self.tears:
                self.n += 1
                lines = sorted({f["line"] for f in findings})
                src = content.splitlines()
                self.tears[path] = emit(
                    "tear",
                    id=f"t{self.n}",
                    path=path,
                    line=lines[0],
                    lines=lines,
                    rule=findings[0]["rule"],
                    rules=sorted({f["rule"] for f in findings}),
                    severity=findings[0]["severity"],
                    message=findings[0]["message"][:240],
                    code=src[lines[0] - 1].strip() if lines[0] <= len(src) else "",
                )
            # patches are written in parallel; apply and verify stay strictly one at a time
            work.append(
                (
                    self.tears[path],
                    None
                    if any(f["fix"] for f in findings)
                    else self.pool.submit(generate_patch, path, content, findings),
                )
            )
        for tear, fut in work:
            self.mend(tear, by_file[tear["path"]], fut)

    def mend(self, tear: dict, findings: list[dict], fut: Future | None):
        tid, path = tear["id"], tear["path"]
        file = TARGET / path
        original = file.read_text()
        emit("mend_start", id=tid, path=path)
        script = exploit_script(path)
        before = exploit_works(script)[0] if script else None
        feedback, checks, reason = "", [], "no patch"
        # Semgrep's own fix goes first; the LLM only writes when there is none or it does not verify
        tried_autofix = False
        fixable = any(f["fix"] for f in findings)
        for attempt in range(1, MAX_ATTEMPTS + (2 if fixable else 1)):
            checks = []
            try:
                patch = None
                if not tried_autofix:
                    tried_autofix = True
                    if any(f["fix"] for f in findings):
                        patch = semgrep_autofix(path, original)
                        if patch is None:
                            emit("autofix_none", id=tid)
                if patch is None:
                    patch = (
                        fut.result(timeout=240)
                        if fut is not None and attempt == 1
                        else generate_patch(path, original, findings, feedback)
                    )
                    if patch["based_on"] != sha(original):
                        patch = generate_patch(path, original, findings)
                new = patch["content"]
                if "nosemgrep" in new:
                    raise ValueError("patch tried to silence the scanner")
                if path.endswith(".py"):
                    ast.parse(new)
            except Exception as exc:  # patcher failure is a held strand, not a crash
                reason = f"patcher: {str(exc)[:120]}"
                feedback = f"\nYour previous reply was rejected: {reason}\n"
                emit("patch_failed", id=tid, attempt=attempt, reason=reason)
                continue
            d = diff_lines(original, new)
            file.write_text(new)
            emit(
                "patch",
                id=tid,
                attempt=attempt,
                diff=d,
                removed=sum(x.startswith("-") for x in d),
                added=sum(x.startswith("+") for x in d),
                seconds=patch["seconds"],
                model=patch["model"],
                backend=patch["backend"],
            )

            left = semgrep(path)
            ok = not left
            detail = (
                "rescan clean"
                if ok
                else f"rescan still finds {left[0]['rule'].split('.')[-1]}"
            )
            checks.append(detail)
            emit("verify", id=tid, step="rescan", ok=ok, detail=detail, attempt=attempt)
            if ok and TESTS == "none":
                checks.append("compiles")
                emit("verify", id=tid, step="tests", ok=True, detail="compiles", attempt=attempt)
            elif ok:
                ok, detail = run_tests()
                checks.append(detail)
                emit(
                    "verify",
                    id=tid,
                    step="tests",
                    ok=ok,
                    detail=detail,
                    attempt=attempt,
                )
            if ok and script:
                still, note = exploit_works(script)
                ok = not still
                detail = "exploit fails" if ok else "exploit still works"
                if before is False:
                    detail = "exploit check not applicable"
                checks.append(detail)
                emit(
                    "verify",
                    id=tid,
                    step="exploit",
                    ok=ok,
                    detail=detail,
                    note=note,
                    worked_before=before,
                    attempt=attempt,
                )
            if ok:
                rules = ", ".join(tear["rules"])
                git("add", "--", path)
                run(
                    [
                        "git",
                        "-c",
                        "user.name=Weaver",
                        "-c",
                        "user.email=weaver@localhost",
                        "commit",
                        "-q",
                        "-m",
                        f"weaver: mend {tear['rule'].split('.')[-1]} in {path}\n\nSemgrep: {rules}\nFixed by: {patch['backend']}\nVerified: {', '.join(checks)}",
                    ]
                )
                commit = git("rev-parse", "--short", "HEAD")
                self.own_commits.add(git("rev-parse", "HEAD"))
                self.tears.pop(path, None)
                seconds = round(time.time() - tear["t"], 2)
                key = aws.ledger_put(
                    {
                        "repo": TARGET.name,
                        "path": path,
                        "rules": tear["rules"],
                        "commit": git("rev-parse", "HEAD"),
                        "diff_sha256": hashlib.sha256("\n".join(d).encode()).hexdigest(),
                        "lines_removed": sum(x.startswith("-") for x in d),
                        "lines_added": sum(x.startswith("+") for x in d),
                        "verification": checks,
                        "attempts": attempt,
                        "fixed_by": patch["backend"],
                        "patcher_backend": patch["backend"],
                        "patcher_model": patch["model"],
                        "torn_at": tear["t"],
                        "mended_at": round(time.time(), 3),
                        "seconds_to_mend": seconds,
                    }
                )
                self.remember(tear, path, original, new, commit)
                emit(
                    "mended",
                    id=tid,
                    path=path,
                    commit=commit,
                    attempts=attempt,
                    seconds=seconds,
                    verify=" · ".join(checks),
                    backend=patch["backend"],
                    fixed_by=patch["backend"],
                    ledger="s3" if key else "local",
                    ledger_key=key,
                )
                return
            # verification failed: roll the strand back before anything else happens
            file.write_text(original)
            reason = checks[-1]
            emit("rollback", id=tid, attempt=attempt, reason=reason, backend=patch["backend"])
            feedback = (
                f"\nA previous attempt was rolled back because verification failed: {reason}."
                + (
                    f" Semgrep still reported: {left[0]['rule']} at line {left[0]['line']}."
                    if left
                    else ""
                )
                + " Try a different, still minimal fix.\n"
            )
            if patch["backend"] == "semgrep autofix":
                feedback = ""  # the LLM starts from the findings, not from Semgrep's attempt
        self.held[path] = sha(original)
        emit(
            "held",
            id=tid,
            path=path,
            reason=reason,
            attempts=MAX_ATTEMPTS,
            verify=" · ".join(checks) or reason,
        )

    def remember(self, tear: dict, path: str, before: str, after: str, commit: str):
        """After a verified mend, write a Semgrep rule for what was just fixed. Runs beside the loop."""
        if not MEMORY:
            return
        busy = set(self.tears)

        def job():
            try:
                out = write_rule(tear, path, before, after, commit, busy)
            except Exception as exc:
                out = {"kept": False, "why": f"rule writer: {str(exc)[:100]}"}
            if out.pop("kept"):
                emit("rule_written", id=tear["id"], path=path, **out)
            else:
                self.rules_discarded += 1
                emit("rule_discarded", id=tear["id"], path=path, discarded=self.rules_discarded, **out)

        self.pool.submit(job)

    def watch(self):
        exclude = TARGET / ".git" / "info" / "exclude"
        if exclude.parent.parent.is_dir():  # Weaver's own files never enter the repo's history
            exclude.parent.mkdir(exist_ok=True)
            if ".weaver/" not in (exclude.read_text() if exclude.exists() else ""):
                with exclude.open("a") as fh:
                    fh.write("\n.weaver/\n")
        emit(
            "init",
            repo=TARGET.name,
            head=git("rev-parse", "--short", "HEAD"),
            semgrep=semgrep_version(),
            rules=RULES.name,
            rule_count=rule_count(),
            tests=TESTS,
            memory=MEMORY,
            patcher=f"claude code cli ({MODEL})",
            bedrock=aws.bedrock_probe(),
            ledger="s3" if aws.ledger_bucket() else "local",
        )
        last = None
        while True:
            try:
                fp = fingerprint()
                if fp != last:
                    reason = "startup"
                    if last is not None:
                        if fp[0] != last[0] and fp[0] not in self.own_commits:
                            emit(
                                "commit",
                                sha=fp[0][:7],
                                subject=git("log", "-1", "--format=%s"),
                                author=git("log", "-1", "--format=%an"),
                                files=git(
                                    "show", "--name-only", "--format=", "HEAD"
                                ).splitlines(),
                            )
                            reason = "commit"
                        else:
                            emit(
                                "edit",
                                files=git("status", "--porcelain").splitlines()[:8],
                            )
                            reason = "edit"
                    self.cycle(reason)
                    last = fingerprint()
            except Exception as exc:
                emit("error", message=str(exc)[:300])
                last = None
                time.sleep(3)
            time.sleep(0.7)


# ---------------------------------------------------------------- http

app = FastAPI()


@app.get("/events")
async def sse():
    async def stream():
        cursor = 0
        while True:
            while cursor < len(events):
                yield f"data: {json.dumps(events[cursor])}\n\n"
                cursor += 1
            await asyncio.sleep(0.1)

    return StreamingResponse(
        stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"}
    )


@app.get("/replay/{name}")
def replay(name: str):
    path = ROOT / "demo" / os.path.basename(name)
    return FileResponse(path, media_type="text/plain")


@app.get("/bench/{name}.json")
def bench_results(name: str):
    return FileResponse(ROOT / "bench" / (os.path.basename(name) + ".json"), media_type="application/json")


@app.on_event("startup")
def start():
    if os.environ.get("WEAVER_NO_LOOP"):
        return
    if EVENTS_FILE.exists() and EVENTS_FILE.stat().st_size:
        runs = ROOT / "runs"
        runs.mkdir(exist_ok=True)
        EVENTS_FILE.rename(runs / f"events-{time.strftime('%Y%m%d-%H%M%S')}.jsonl")
    threading.Thread(target=Weaver().watch, daemon=True).start()


app.mount("/", StaticFiles(directory=ROOT / "public", html=True), name="public")
