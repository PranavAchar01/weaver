# Weaver

A spider never files a ticket. It mends the web.

Weaver watches a git repository. Every file is a strand in an orb web. When
Semgrep finds a problem in a file, that strand tears. A spider walks to the
tear, spins a patch, then plucks the strand to check it holds. Only a fix that
passes every check is committed. A fix that fails is rolled back and marked
"held for a human".

Built solo for the tokens& Cyberdefense Hackathon, 2026-10-09.

## Quick start

Needs `uv`, `semgrep` (`uv tool install semgrep`, no login) and the Claude Code
CLI (`claude`) on the path. Once, in this checkout: `uv sync`, then put
`.venv/bin` on your PATH (or use `uv run weaver`).

What you do: one command in your repo. Then nothing.

```
cd my-app
weaver watch .
```

It prints three lines and starts watching:

```
weaver: semgrep 1.180.0, 253 rules (python)
weaver: watching my-app (42 files), verify: rescan + pytest
weaver: http://127.0.0.1:8765
```

`weaver watch <path>` works on any git repo path. It picks Semgrep rule packs
from the files it sees and finds the tests on its own: pytest if there are
test files, `npm test` if `package.json` has a test script, otherwise it says
"verify: rescan + compile only (no tests found)" and does exactly that.
Weaver's own files live in `.weaver/` inside the repo and are kept out of git
through `.git/info/exclude`.

AWS is optional. If `~/.config/weaver/aws.env` exists it is read
(AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_DEFAULT_REGION). Without it
Weaver runs the same, with the local event log only.

The demo repo: `./run.sh` watches `target/` (built from `target-seed/` on first
start). `./demo/introduce.sh` lands a bad commit, `./demo/reset.sh` puts the
target back to its torn state. `./demo/record_run.sh` records a run the way a
user would start it (`cd my-app && weaver watch .`).

To replay the recorded run in the real UI:
http://127.0.0.1:8765/?replay=run.jsonl&speed=3.9

## Built on Semgrep

Every stage is Semgrep. Weaver is not affiliated with or endorsed by Semgrep;
it is built on the open source tool. The logo in the UI is Semgrep's own file
(`images/semgrep-logo-dark.svg` from github.com/semgrep/semgrep), recoloured to
plain white and otherwise unchanged.

- Detect: Semgrep 1.180.0 with registry rules. For Python that is
  `rules/weaver.yaml`: 253 rules from four registry packs (p/python, p/flask,
  p/security-audit, p/secrets), merged unmodified by `rules/build.py`.
- Fix, step 1: if a matched rule ships a Semgrep autofix, Weaver applies
  Semgrep's own fix first (`semgrep scan --autofix`) and verifies it.
- Fix, step 2: only when there is no autofix, or it does not pass
  verification, an LLM writes the patch: the Claude Code CLI, headless
  (`claude -p`, model sonnet, tools disabled). It gets the file and the
  findings and returns the whole file.
- Verify: a Semgrep rescan of the file must be clean. Then the repo's tests
  must pass (or the file must compile, if there are no tests). Then, in the
  demo repo, the exploit script for that file must fail. Up to 3 LLM attempts,
  each with the failure fed back. Then commit, or roll back and hold.
- Remember: after each verified mend Weaver writes one custom Semgrep rule for
  the code shape it just fixed into `.weaver/rules/` in the watched repo, and
  every later scan includes that folder. The LLM drafts the rule; Semgrep
  decides if it is kept. A rule is kept only if `semgrep --validate` accepts
  it, it matches the pre-fix file, it does not match the post-fix file, and it
  flags nothing else in the repo that the registry rules pass. Anything else
  is discarded and counted.
- Each ledger line, event, commit message and S3 object records what fixed it:
  "semgrep autofix" or the LLM backend.

## What else is real

- Patch on Amazon Bedrock: supported and tried first (`converse`, model
  `us.anthropic.claude-sonnet-5-5`, us-west-2), with automatic fallback to the
  CLI. On 2026-10-09 the AWS account was still pending verification and
  Bedrock answered AccessDeniedException every time, so every LLM patch and
  every rule draft in the recorded run and in the benchmark runs came from the
  Claude Code CLI.
- Ledger on Amazon S3: every verified mend is written as one JSON object to a
  private, versioned bucket (rule ids, file, diff hash, checks, commit sha,
  timestamps, what fixed it). The UI shows "ledger: s3" on a line only when
  that write succeeded. All 8 mends of the recorded run are in S3.
- Continuous: the watcher polls git HEAD and the working tree. A new commit
  or edit is rescanned within seconds.
- UI: every motion is driven by backend events over SSE. The same events are
  written to `.weaver/events.jsonl` in the watched repo.

## The recorded run

`demo/run.jsonl` is one real run, started with `cd my-app && weaver watch .`
where `my-app` is a fresh copy of the demo repo (a small Flask app with
planted classic issues). `demo/terminal.txt` is what that command printed.

- 11 Semgrep findings in 7 files. All 7 files mended in about 80 s.
- Two findings had a Semgrep autofix. On `routes/tools.py` Semgrep's fix was
  applied, the rescan still found `subprocess-injection`, it was rolled back
  and the LLM patch passed on the next attempt. On
  `services/config_loader.py` Semgrep's fix changed nothing, so the LLM wrote
  the patch. So in this run no mend was made by Semgrep autofix alone; all 8
  were LLM patches.
- Then `demo/introduce.sh` committed a SQL injection. Weaver found it and
  mended it in 10.4 s.
- 8 rules written into `.weaver/rules/`, 0 discarded.
- Final: 0 open, 8 mended, median 40.8 s from tear to mend. No fix was held.

`demo/weaver-demo-45s.mp4` is the real UI replaying that file at 3.9x, captured
frame by frame (`demo/capture.py`). Nothing is added to the events. Three parts
of the video are not the run itself: the opening terminal card (the two
commands typed out, then the three lines from `demo/terminal.txt`), a
grayscale headless capture of the BenchmarkPython GitHub page (content
unedited, `public/broll/github.png`), and the score card, whose numbers are
read from `bench/results.json` and `bench/baseline.json`.

## Independent check

The demo target is code we wrote, so we also ran Weaver on code we did not
write: a fixed sample of the OWASP Benchmark for Python (BenchmarkPython v0.1,
commit `f1291485808b66e20ddb6b01b10dc71b3df8c8ba`). This is a sample, not the
full benchmark score.

- 42 real-vulnerability cases: the first 3 per category by test number.
- Semgrep found the benchmark's vulnerability in 17. Weaver mended 16 of 17
  (94%): 1 by Semgrep autofix, 15 by the LLM patch. 1 could not be verified
  in three attempts, so it was rolled back and held for a human.
- Baseline, measured on the same 17 with the same rules: Semgrep autofix alone
  fixed 1 of 17 (6%). That is the only comparison we ran.
- In 12 more, Semgrep flagged only a different issue in the file. In 13 it
  flagged nothing. Those 25 vulnerabilities stay open.
- "Mended" there means rescan clean and the file compiles. Those cases have no
  unit tests, so this is not test-verified.
- An earlier run with the LLM patch only got 17 of 17. The number on screen is
  from the engine that ships. We did not re-roll it.

Details, case ids and raw results: `bench/README.md`, `bench/results.json`,
`bench/baseline.json`.

## Honest limits

- Weaver only mends what Semgrep finds. On the benchmark sample that was 17 of
  42. Better rules would move that number, the mender would not.
- "Rescan clean" proves the rule no longer matches. It does not prove the code
  is safe. Tests and exploit checks add confidence only where they exist. 6
  of the 8 mended demo files have an exploit check.
- The demo target is small and its issues were planted by us.
- Python is the tested path. `weaver watch` was run on the demo repo and on a
  4-file sample of the benchmark (no tests there, so rescan + compile only).
  JavaScript and TypeScript repos get registry packs (p/javascript,
  p/typescript) and `npm test`, but that path was not run today, and the
  compile check exists for Python only.
- The video shows a rollback (Semgrep's autofix on `routes/tools.py`) but no
  hold. The one hold today is in the benchmark run.
- The written rules are checked by Semgrep against the before and after file,
  not reviewed by a person. In the recorded run none of them fired on later
  code. They are drafts of memory, not a tuned rule pack.
- One file is one strand. Several findings in a file are mended together.
- LLM patches are whole-file rewrites. Review the commits.
- The watcher polls. It is not a git hook.
- Median time to mend in the UI counts from when the tear was seen, and mends
  are applied one at a time, so files later in the queue show longer times.
- Bedrock has not written a single patch yet. The code path exists and falls
  back cleanly; it has only been seen refusing.
- The benchmark runs do not write to S3; their results are in `bench/` only.
- Local only. No auth. Do not expose the port.
