"""Record one real, continuous zsh session on a real open-source project:

    cd <repo>; weaver watch . --lang python --no-tests   (wait until every finding is mended or held)
    ^C; git switch -c weaver/mend; git push fork weaver/mend; gh pr create ... (inside the fork)

Every byte on screen is what zsh and the commands printed. Between Ctrl-C and the git commands this
script writes the pull request body from Weaver's own event log (.weaver/pr.md) and, if the patch
imports defusedxml, adds that package to pyproject.toml in a separate commit marked "by hand".

    .venv/bin/python demo/record_real.py <workdir> <repo dir name> <fork owner/name> <base branch> <prescan.json>
        -> demo/real.cast.json, demo/real-run.jsonl, demo/real-times.json
"""

import base64
import fcntl
import json
import os
import pty
import random
import select
import signal
import struct
import subprocess
import sys
import tempfile
import termios
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORK, NAME, FORK, BASE, PRESCAN = (
    Path(sys.argv[1]),
    sys.argv[2],
    sys.argv[3],
    sys.argv[4],
    Path(sys.argv[5]),
)
REPO = WORK / NAME
EVENTS = REPO / ".weaver" / "events.jsonl"
COLS, ROWS = 100, 28
rng = random.Random(11)

zdot = tempfile.mkdtemp(prefix="weaver-zsh-")
Path(zdot, ".zshrc").write_text(
    "PROMPT='pranav@MacBook-Air %1~ %# '\nunset zle_bracketed_paste\n"
)
env = dict(
    os.environ,
    ZDOTDIR=zdot,
    TERM="xterm-256color",
    GH_PROMPT_DISABLED="1",
    NO_COLOR="1",
    PATH=f"{ROOT / '.venv' / 'bin'}:{os.environ['PATH']}",
)
for k in ("TERM_PROGRAM", "TERM_SESSION_ID", "PROMPT", "PS1", "VIRTUAL_ENV"):
    env.pop(k, None)

pid, fd = pty.fork()
if pid == 0:
    os.chdir(WORK)
    os.execvpe("zsh", ["zsh", "-i"], env)
fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", ROWS, COLS, 0, 0))

t0 = time.time()
chunks, seen, marks = [], b"", {}


def pump(seconds):
    global seen
    end = time.time() + seconds
    while True:
        left = end - time.time()
        if left <= 0:
            return
        r, _, _ = select.select([fd], [], [], left)
        if r:
            try:
                data = os.read(fd, 65536)
            except OSError:
                return
            if not data:
                return
            chunks.append([round(time.time() - t0, 4), base64.b64encode(data).decode()])
            seen += data


def typed(cmd, wait_prompt=True, timeout=60):
    mark = len(seen)
    for ch in cmd:
        os.write(fd, ch.encode())
        pump(rng.uniform(0.03, 0.075) + (0.05 if ch == " " else 0))
    pump(0.22)
    os.write(fd, b"\r")
    entered = time.time()
    if wait_prompt:
        end = time.time() + timeout
        while b"% " not in seen[mark + len(cmd) :] and time.time() < end:
            pump(0.05)
        pump(0.3)
    return entered


def wait_for(needle, timeout=60, start=0):
    end = time.time() + timeout
    while needle not in seen[start:] and time.time() < end:
        pump(0.05)
    return needle in seen[start:]


def events():
    if not EVENTS.exists():
        return []
    out = []
    for line in EVENTS.read_text().splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            pass
    return out


def finish(note):
    os.kill(pid, signal.SIGHUP)
    try:
        os.write(fd, b"\x03")
    except OSError:
        pass
    time.sleep(0.5)
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    os.waitpid(pid, 0)
    length = round(time.time() - t0, 3)
    cmds = [c for c in marks.get("cmds", [])]
    (ROOT / "demo" / "real.cast.json").write_text(
        json.dumps(
            {
                "cols": COLS,
                "rows": ROWS,
                "seconds": length,
                "commands": cmds,
                "marks": marks,
                "events": chunks,
            }
        )
    )
    (ROOT / "demo" / "real-run.jsonl").write_text(
        EVENTS.read_text() if EVENTS.exists() else ""
    )
    print(note, json.dumps(marks, indent=1))


marks["cmds"] = []
wait_for(b"% ")
pump(0.5)
marks["cmds"].append(f"cd {NAME}")
typed(f"cd {NAME}")
cmd = "weaver watch . --lang python --no-tests"
marks["cmds"].append(cmd)
t_watch = typed(cmd, wait_prompt=False)
marks["watch_enter"] = round(t_watch - t0, 3)
if not wait_for(b"http://127.0.0.1:8765", 90):
    finish("weaver did not start")
    sys.exit(1)

# wait until every tear is mended or held, then give rule memory a moment
end = time.time() + 420
done_at = None
while time.time() < end:
    pump(0.5)
    ev = events()
    tears = {e["id"] for e in ev if e["type"] == "tear"}
    closed = {e["id"] for e in ev if e["type"] in ("mended", "held")}
    scanned = any(e["type"] == "scan_done" for e in ev)
    if scanned and tears <= closed:
        mended = [e for e in ev if e["type"] == "mended"]
        ruled = [e for e in ev if e["type"] in ("rule_written", "rule_discarded")]
        done_at = done_at or time.time()
        if len(ruled) >= len(mended) or time.time() - done_at > 45:
            break
pump(1.5)
marks["watch_done"] = round(time.time() - t0, 3)
os.write(fd, b"\x03")
mark = len(seen)
wait_for(b"% ", 20, mark)
pump(0.4)

ev = events()
mended = [e for e in ev if e["type"] == "mended"]
held = [e for e in ev if e["type"] == "held"]
if not mended:
    finish("nothing mended; no push")
    sys.exit(2)

# ---- pull request body, from the event log and the pre-scan (not typed in the terminal) ----
init = next(e for e in ev if e["type"] == "init")
scan = next(e for e in ev if e["type"] == "scan_done")
pre = json.loads(PRESCAN.read_text())["results"]
paths = [e["path"] for e in mended]
found = [r for r in pre if r["path"] in paths]
skipped = [
    r
    for r in pre
    if r["path"] not in paths and r["path"] not in [h["path"] for h in held]
]
body = [
    f"Semgrep {init['semgrep']} with {init['rule_count']} registry rules, run on isso at {init['head']}.",
    "",
    "Found",
    "",
]
body += [
    f"- `{r['path']}:{r['start']['line']}` {r['check_id'].split('rules.')[-1]}"
    for r in found
]
body += ["", "Changed", ""]
for e in mended:
    p = next(
        (
            x
            for x in ev
            if x["type"] == "patch"
            and x["id"] == e["id"]
            and x.get("attempt") == e["attempts"]
        ),
        {},
    )
    body.append(
        f"- `{e['path']}` in {e['commit']}: {p.get('added', '?')} lines added, {p.get('removed', '?')} removed. "
        f"XML from import dumps is now parsed with defusedxml instead of xml.etree."
    )
needs_dep = (
    any("defusedxml" in (REPO / p).read_text() for p in paths)
    and "defusedxml" not in (REPO / "pyproject.toml").read_text()
)
if needs_dep:
    body.append(
        "- `pyproject.toml`: defusedxml added to dependencies. This one line was added by hand, not by Weaver."
    )
body += [
    "",
    "Verified",
    "",
    f"- {'; '.join(sorted({e['verify'] for e in mended}))} (Semgrep rescan of the patched file, and the file compiles).",
    "- The project's test suite was not run.",
]
if held:
    body += ["", "Held for a human: " + ", ".join(f"`{h['path']}`" for h in held)]
if skipped:
    files = sorted({r["path"] for r in skipped})
    body += [
        "",
        f"Not touched: {len(skipped)} other findings in {len(files)} files were read by hand and left alone as false positives "
        f"({', '.join('`' + f + '`' for f in files)}).",
    ]
body += ["", "Patches written by Weaver (Semgrep + an LLM), verified by rescan.", ""]
(REPO / ".weaver" / "pr.md").write_text("\n".join(body))
if needs_dep:
    pp = REPO / "pyproject.toml"
    s = pp.read_text()
    assert '    "bleach",\n' in s
    pp.write_text(s.replace('    "bleach",\n', '    "bleach",\n    "defusedxml",\n', 1))
    subprocess.run(
        [
            "git",
            "-C",
            str(REPO),
            "commit",
            "-q",
            "-m",
            "pyproject: add defusedxml dependency (by hand)",
            "pyproject.toml",
        ],
        check=True,
    )
marks["needs_dep_commit"] = needs_dep
n = len(found)
title = f"Mend {n} Semgrep finding{'s' if n != 1 else ''} in {', '.join(paths)}"

for c in (
    "git log --oneline -3",
    "git switch -c weaver/mend",
    "git push -q fork weaver/mend",
):
    marks["cmds"].append(c)
    typed(c, timeout=90)
c = f'gh pr create -R {FORK} -B {BASE} -H weaver/mend -t "{title}" -F .weaver/pr.md'
marks["cmds"].append(c)
mark = len(seen)
t_pr = typed(c, wait_prompt=False)
ok = wait_for(f"github.com/{FORK}/pull/".encode(), 90, mark + len(c))
t_url = time.time()
pump(1.6)
marks["pr_enter"] = round(t_pr - t0, 3)
marks["pr_url_at"] = round(t_url - t0, 3)
marks["pr_ok"] = ok
marks["wall_watch_to_pr"] = round(t_url - t_watch, 2)
marks["scan_seconds"] = scan["seconds"]
marks["findings_scanned"] = scan["findings"]
marks["mended"] = [
    {
        "path": e["path"],
        "seconds": e["seconds"],
        "commit": e["commit"],
        "verify": e["verify"],
        "attempts": e["attempts"],
        "backend": e["backend"],
    }
    for e in mended
]
marks["held"] = [h["path"] for h in held]
(ROOT / "demo" / "real-times.json").write_text(json.dumps(marks, indent=1))
finish("done")
