"""Record a real zsh session in a pty: every byte the shell wrote, with its time.

The keystrokes are sent by this script at a human cadence; everything on screen is what zsh and
the commands really printed. The film replays the bytes through xterm.js and never edits them.

    .venv/bin/python demo/record_term.py intro   -> demo/intro.cast.json  (cd my-app; weaver watch .)
    .venv/bin/python demo/record_term.py bench   -> demo/bench.cast.json  (where the benchmark is, what ran)
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
import sys
import tempfile
import termios
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SESSIONS = {
    "intro": {"cwd": ROOT / "demo", "rows": 28, "tail": 1.3, "until": b"http://127.0.0.1:8765",
              "cmds": ["cd my-app", "weaver watch ."]},
    "bench": {"cwd": ROOT, "rows": 32, "tail": 1.0, "until": b"stay open",
              "cmds": ["git -C bench/BenchmarkPython remote get-url origin",
                       "git -C bench/BenchmarkPython rev-parse HEAD",
                       "wc -l bench/BenchmarkPython/expectedresults-0.1.csv",
                       "weaver bench --summary"]},
}
name = sys.argv[1]
S = SESSIONS[name]
COLS = 100
rng = random.Random(7)

zdot = tempfile.mkdtemp(prefix="weaver-zsh-")
Path(zdot, ".zshrc").write_text("PROMPT='pranav@MacBook-Air %1~ %# '\nunset zle_bracketed_paste\n")
env = dict(os.environ, ZDOTDIR=zdot, TERM="xterm-256color", PATH=f"{ROOT / '.venv' / 'bin'}:{os.environ['PATH']}")
for k in ("TERM_PROGRAM", "TERM_SESSION_ID", "PROMPT", "PS1", "VIRTUAL_ENV"):
    env.pop(k, None)

pid, fd = pty.fork()
if pid == 0:
    os.chdir(S["cwd"])
    os.execvpe("zsh", ["zsh", "-i"], env)
fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", S["rows"], COLS, 0, 0))

t0 = time.time()
events, seen = [], b""


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
            events.append([round(time.time() - t0, 4), base64.b64encode(data).decode()])
            seen += data


def wait_for(needle, timeout=30):
    end = time.time() + timeout
    while needle not in seen and time.time() < end:
        pump(0.05)


wait_for(b"% ")
pump(0.5)
for cmd in S["cmds"]:
    mark = len(seen)
    for ch in cmd:
        os.write(fd, ch.encode())
        pump(rng.uniform(0.045, 0.11) + (0.07 if ch == " " else 0))
    pump(0.22)
    os.write(fd, b"\r")
    if cmd is not S["cmds"][-1]:
        end = time.time() + 20
        while b"% " not in seen[mark + len(cmd):] and time.time() < end:
            pump(0.05)
        pump(0.3)
wait_for(S["until"], 60)
pump(S["tail"])
length = round(time.time() - t0, 3)

os.kill(pid, signal.SIGHUP)          # the shell this script started, and its foreground job
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
out = ROOT / "demo" / f"{name}.cast.json"
out.write_text(json.dumps({"cols": COLS, "rows": S["rows"], "seconds": length, "commands": S["cmds"], "events": events}))
print(f"{out.name}: {len(events)} chunks, {length} s")
