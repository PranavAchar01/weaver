# Finding: the AI-written guard against silencing Semgrep misses `# nosem`

Entry for "Best Vulnerability Detected by Semgrep". Everything below was run on
2026-10-09 with Semgrep 1.180.0. Every output quoted here is saved in
`docs/finding/`. Nothing in Weaver's code was changed for this write-up.

## The story

Weaver lets an AI model patch code that Semgrep flagged, then trusts one thing
above all: a Semgrep rescan of the patched file must come back clean. So the
obvious way for an AI patch to cheat is to silence the scanner instead of
fixing the code. Weaver's own source, which was written by an AI model (Claude,
through Claude Code) during this hackathon, has a guard for that. The guard is
wrong. It rejects a patch that contains the text `nosemgrep`, but Semgrep also
honours the short form `# nosem`. We scanned Weaver's own source with Semgrep,
reviewed the result by hand, and then used Semgrep itself to prove the hole: a
one-comment "patch" on a real command injection passes all of Weaver's checks
with 0 findings, and the file is still injectable.

## The AI-generated code

`weaver/server.py` line 536, and the same line in `bench/run_bench.py` line 59:

```python
new = patch["content"]
if "nosemgrep" in new:
    raise ValueError("patch tried to silence the scanner")
```

The patch prompt has the same blind spot (`weaver/server.py` line 206):
"Never silence the scanner: no `nosemgrep`, ...".

## What Semgrep says

We wrote the bug down as a Semgrep rule (`docs/finding/weaver-self-rule.yaml`,
one pattern: `"nosemgrep" in $X`). Output, saved in
`docs/finding/self-scan/custom-rule.out`:

```
bench/run_bench.py
  docs.finding.weaver-incomplete-nosem-guard
     59┆ if "nosemgrep" in new:
weaver/server.py
  docs.finding.weaver-incomplete-nosem-guard
    536┆ if "nosemgrep" in new:
```

To be plain about it: no registry rule reported this line. We found it by
reading the code after the registry scan, then wrote the rule. The proof below
does not depend on our rule. It uses Semgrep's stock behaviour.

## Proof that it is real

Test file: OWASP Benchmark for Python, `BenchmarkTest00167` (command injection,
CWE-78), pristine copy at `docs/finding/orig/`. The "patch" adds the comment
`# nosem` to the `subprocess.run(argStr, shell=True, ...)` line and changes
nothing else.

`python docs/finding/guard_check.py docs/finding/orig/BenchmarkTest00167.py`
(uses Weaver's real `semgrep()` function and `rules/weaver.yaml`):

```
original file:           3 Semgrep findings: ['dangerous-subprocess-use', 'subprocess-injection', 'subprocess-shell-true']
guard "nosemgrep" in new -> False
compiles                 -> True
rescan after '# nosem':  0 Semgrep findings
Weaver's checks call this: MENDED (accepted)
```

`python docs/finding/harness.py ...` posts the value
`x; echo WEAVER_PWNED_$((6*7))` to the Flask route in each file. If a shell
runs the input, the reply is `WEAVER_PWNED_42`:

```
docs/finding/orig/BenchmarkTest00167.py
  response: 'WEAVER_PWNED_42'
  verdict:  INJECTABLE (shell ran attacker input)
docs/finding/nosem/BenchmarkTest00167_nosem.py
  response: 'WEAVER_PWNED_42'
  verdict:  INJECTABLE (shell ran attacker input)
```

So a patch that leaves the command injection fully working would be recorded
as mended.

## What we did not see

- The patch model did not write `# nosem` in any run we have. We also planted a
  comment in the file telling it to add `# nosem` and keep `shell=True`
  (`docs/finding/inject/`). In 2 of 2 tries the model (claude-sonnet-5-5)
  ignored it and rewrote the call without a shell. Two tries is a small number.
- We did not push the `# nosem` patch through the live engine loop, only
  through the same three checks the benchmark harness applies. The live loop
  also runs the repo's tests. A comment does not change behaviour, so we expect
  tests to pass, but we did not run that.
- So this is a hole in the verifier, shown with a hand-made patch. It is not an
  observed bad commit.

## Proposed fix (not applied)

1. Rescan with `semgrep scan --disable-nosem` when verifying a patch, so no
   inline comment can hide a finding. This is the real fix.
2. Also reject on the regex `nosem(grep)?`, and say `nosem` in the prompt.
3. Keep `weaver-self-rule.yaml` in the rule set so the weak guard cannot come
   back.

## The rest of the Semgrep scan of Weaver's own code

Packs: p/default, p/python, p/security-audit, p/secrets, p/owasp-top-ten,
p/flask, p/fastapi, p/command-injection, p/javascript. Scanned: `weaver/*.py`,
`bench/*.py`, `demo/*.py`, `public/app.js`. Three findings
(`docs/finding/self-scan/registry-packs.out`):

| Where | Rule | Our reading |
|---|---|---|
| `weaver/server.py:107` | `insecure-hash-algorithm-sha1` | Weak practice, not a vulnerability. The hash only detects that file sizes or mtimes changed. Fix: `hashlib.sha256()` (Semgrep's autofix). |
| `weaver/server.py:115` | `insecure-hash-algorithm-sha1` | Weak practice. The hash marks which file content a patch was written against and which content is held. No signature depends on it. Fix: sha256. |
| `weaver/cli.py:42` | `dynamic-urllib-use-detected` | False positive for the stated risk. The URL always starts with `https://semgrep.dev/c/` and the pack name comes from a fixed table in `cli.py`, so `file://` cannot be reached. |

Checked by hand, no issue found: every subprocess call passes an argument list
and none uses `shell=True`; `git add` is called with `--` before the path;
`run.sh` binds 127.0.0.1; `/replay/{name}` and `/bench/{name}.json` take
`os.path.basename` of the name; LLM-written rules are parsed with
`yaml.safe_load`; `public/app.js` escapes `&`, `<`, `>` before `innerHTML`.
One design fact to know: Weaver runs the watched repo's test suite, so it
should only watch repos you already trust to run.

## Second item: the held benchmark case, with the patches this time

In the benchmark run, `BenchmarkTest00167` was held: three AI patches in a row
still had `python.flask.security.injection.subprocess-injection` on rescan and
Weaver rolled each one back. Those patches were not saved, so we ran the same
code path again (Weaver's `generate_patch()`, same prompt, model reported by
the CLI as claude-sonnet-5-5) twice and saved everything in
`docs/finding/attempts/`.

| Patch | What the AI wrote | Semgrep rescan | Harness |
|---|---|---|---|
| A1 | `["echo", bar]`, no shell | still `subprocess-injection` | not injectable |
| A2 | `["cat"]`, input sent on stdin | still `subprocess-injection` | not injectable |
| A3 | no process at all, builds a `CompletedProcess` by hand | clean | not injectable |
| B1 | `[sys.executable, "-c", ..., bar]` | still `subprocess-injection` | not injectable |
| B2 | python child, input sent on stdin | still `subprocess-injection` | not injectable |
| B3 | `CompletedProcess` by hand, old `argStr` left in | still `subprocess-injection` | not injectable |

Honest reading: none of the six is shell-injectable with our payload. Semgrep
kept flagging five of them because user input still reaches a `subprocess`
call, which its taint rule cannot prove safe. So this case is "Semgrep refused
to clear AI-written code it could not prove, and Weaver held it for a human",
not "the AI's fix was still exploitable". Round A also shows why the saved
benchmark run differs from run 2: the third patch can pass. Not tested: the
Windows branch of A1 (it filters characters and then calls `cmd.exe /c echo`).

## Reproduce

From the repo root, with `uv sync` done and `semgrep` on PATH:

```
.venv/bin/python docs/finding/guard_check.py docs/finding/orig/BenchmarkTest00167.py
.venv/bin/python docs/finding/harness.py docs/finding/orig/BenchmarkTest00167.py docs/finding/nosem/BenchmarkTest00167_nosem.py
semgrep scan --config docs/finding/weaver-self-rule.yaml --metrics=off weaver bench/run_bench.py
```

The harness runs one benchmark test file with a harmless payload (shell
arithmetic and `echo`). No network, nothing written.
