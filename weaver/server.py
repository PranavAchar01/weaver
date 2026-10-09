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
RULES = Path(os.environ.get("WEAVER_RULES", ROOT / "rules" / "weaver.yaml"))
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
TEST_CMD = [sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider"]
MAX_ATTEMPTS = 3
SCAN_EXCLUDES = ["tests", "exploits"]

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
    if check_id.startswith(RULE_PREFIX):
        check_id = check_id[len(RULE_PREFIX):]
    parts = check_id.split(".")
    if len(parts) > 1 and parts[-1] == parts[-2]:
        parts = parts[:-1]
    return ".".join(parts)


def semgrep(path: str | None = None) -> list[dict]:
    """Run Semgrep on the repo (or one file). Findings come straight from its JSON output."""
    cmd = [
        SEMGREP,
        "scan",
        "--config",
        str(RULES),
        "--json",
        "--quiet",
        "--metrics=off",
        "--disable-version-check",
    ]
    for ex in SCAN_EXCLUDES:
        cmd += ["--exclude", ex]
    cmd.append(path or ".")
    res = run(cmd, timeout=300)
    data = json.loads(res.stdout)
    out = []
    for r in data.get("results", []):
        out.append(
            {
                "rule": short_rule(r["check_id"]),
                "path": r["path"],
                "line": r["start"]["line"],
                "severity": r["extra"].get("severity", ""),
                "message": " ".join(r["extra"].get("message", "").split()),
            }
        )
    out.sort(key=lambda f: (f["path"], f["line"], f["rule"]))
    return out


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
- Only the standard library and modules this file already imports. Flask and PyYAML are installed.
- Never silence the scanner: no `nosemgrep`, no comments about the fix, no renaming to dodge a rule.
- Secrets come from environment variables (the test suite sets SECRET_KEY).
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


def ask_claude(prompt: str) -> tuple[str, dict]:
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
        SYSTEM,
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


def diff_lines(old: str, new: str) -> list[str]:
    out = []
    for line in difflib.unified_diff(
        old.splitlines(), new.splitlines(), n=0, lineterm=""
    ):
        if line.startswith(("---", "+++", "@@")):
            continue
        out.append(line)
    return out


# ---------------------------------------------------------------- verification


def run_tests() -> tuple[bool, str]:
    res = run(TEST_CMD, timeout=300)
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
        work: list[tuple[dict, Future]] = []
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
                    self.pool.submit(generate_patch, path, content, findings),
                )
            )
        for tear, fut in work:
            self.mend(tear, by_file[tear["path"]], fut)

    def mend(self, tear: dict, findings: list[dict], fut: Future):
        tid, path = tear["id"], tear["path"]
        file = TARGET / path
        original = file.read_text()
        emit("mend_start", id=tid, path=path)
        script = exploit_script(path)
        before = exploit_works(script)[0] if script else None
        feedback, checks, reason = "", [], "no patch"
        for attempt in range(1, MAX_ATTEMPTS + 1):
            checks = []
            try:
                patch = (
                    fut.result(timeout=240)
                    if attempt == 1
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
            if ok:
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
                        f"weaver: mend {tear['rule'].split('.')[-1]} in {path}\n\nSemgrep: {rules}\nVerified: {', '.join(checks)}",
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
                        "patcher_backend": patch["backend"],
                        "patcher_model": patch["model"],
                        "torn_at": tear["t"],
                        "mended_at": round(time.time(), 3),
                        "seconds_to_mend": seconds,
                    }
                )
                emit(
                    "mended",
                    id=tid,
                    path=path,
                    commit=commit,
                    attempts=attempt,
                    seconds=seconds,
                    verify=" · ".join(checks),
                    backend=patch["backend"],
                    ledger="s3" if key else "local",
                    ledger_key=key,
                )
                return
            # verification failed: roll the strand back before anything else happens
            file.write_text(original)
            reason = checks[-1]
            emit("rollback", id=tid, attempt=attempt, reason=reason)
            feedback = (
                f"\nA previous attempt was rolled back because verification failed: {reason}."
                + (
                    f" Semgrep still reported: {left[0]['rule']} at line {left[0]['line']}."
                    if left
                    else ""
                )
                + " Try a different, still minimal fix.\n"
            )
        self.held[path] = sha(original)
        emit(
            "held",
            id=tid,
            path=path,
            reason=reason,
            attempts=MAX_ATTEMPTS,
            verify=" · ".join(checks) or reason,
        )

    def watch(self):
        emit(
            "init",
            repo=TARGET.name,
            head=git("rev-parse", "--short", "HEAD"),
            semgrep=semgrep_version(),
            rules=RULES.name,
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


@app.get("/bench/results.json")
def bench_results():
    return FileResponse(ROOT / "bench" / "results.json", media_type="application/json")


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
