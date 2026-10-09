# Weaver

A spider never files a ticket. It mends the web.

Weaver watches a git repository. Every file is a strand in an orb web. When
Semgrep finds a problem in a file, that strand tears. A spider walks to the
tear, spins a patch, then plucks the strand to check it holds. Only a fix that
passes every check is committed. A fix that fails is rolled back and marked
"held for a human".

Built solo for the tokens& Cyberdefense Hackathon, 2026-10-09.

## Run it

Needs `uv`, `semgrep` (`uv tool install semgrep`, no login) and the Claude Code
CLI (`claude`) on the path.

```
./run.sh
```

AWS is optional. If `~/.config/weaver/aws.env` exists, `run.sh` sources it
(AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_DEFAULT_REGION). Without it
Weaver runs the same, with the CLI patcher and the local event log only.

Then open http://127.0.0.1:8765. The first start builds the demo target in
`target/` from `target-seed/`. In a second terminal, land a bad commit and
watch it get mended:

```
./demo/introduce.sh
```

`./demo/reset.sh` puts the target back to its torn state.

To replay the recorded run in the real UI:
http://127.0.0.1:8765/?replay=run.jsonl&speed=1.75

## What is real

- Scan: Semgrep 1.180.0, local rule file `rules/weaver.yaml`. That file is the
  Python rules from four Semgrep registry packs (p/python, p/flask,
  p/security-audit, p/secrets), merged unmodified by `rules/build.py`.
- Patch: the Claude Code CLI, headless (`claude -p`, model sonnet, tools
  disabled). It gets the file and the findings and returns the whole file.
  There is no deterministic fallback in use.
- Patch on Amazon Bedrock: supported and tried first (`converse`, model
  `us.anthropic.claude-sonnet-5-5`, us-west-2), with automatic fallback to the
  CLI. On 2026-10-09 the AWS account was still pending verification and
  Bedrock answered AccessDeniedException, so every patch in the recorded run
  and in the benchmark run was written by the Claude Code CLI. Each ledger
  line and each event records the backend that really wrote the patch.
- Ledger on Amazon S3: every verified mend is written as one JSON object to a
  private, versioned bucket (rule ids, file, diff hash, checks, commit sha,
  timestamps, patcher backend). The UI shows "ledger: s3" on a line only when
  that write succeeded. All 8 mends of the recorded run are in S3.
- Verify, in order, for every patch:
  1. the file parses, and the patch does not use `nosemgrep`;
  2. Semgrep rescan of the file is clean;
  3. the target's tests pass (`pytest`, 14 tests);
  4. if an exploit script exists for the file (`target/exploits/`), it must
     now fail.
  Up to 3 attempts, each with the failure fed back. Then commit, or roll back
  and hold.
- Continuous: the watcher polls git HEAD and the working tree. A new commit
  or edit is rescanned within seconds.
- UI: every motion is driven by backend events over SSE. The same events are
  written to `events.jsonl`.

## The recorded run

`demo/run.jsonl` is one real run against `target/` (a small Flask app with
planted classic issues): 11 Semgrep findings in 7 files, all 7 files mended on
the first attempt in about 41 s. Then `demo/introduce.sh` committed a SQL
injection, Weaver found it and mended it in 9.5 s. Final: 0 open, 8 mended,
median 21.9 s from tear to mend. No fix was held in this run.

`demo/weaver-demo-45s.mp4` is the real UI replaying that file at 1.75x, captured
frame by frame (`demo/capture.py`). Nothing is added to the events. The closing
captions are overlaid by the UI.

## Independent check

The demo target is code we wrote, so we also ran Weaver on code we did not
write: a fixed sample of the OWASP Benchmark for Python (BenchmarkPython v0.1,
commit `f1291485808b66e20ddb6b01b10dc71b3df8c8ba`). This is a sample, not the
full benchmark score.

- 42 real-vulnerability cases: the first 3 per category by test number.
- Semgrep found the benchmark's vulnerability in 17. Weaver mended 17 of 17,
  0 held. Median 13.1 s per case.
- In 12 more, Semgrep flagged only a different issue in the file. In 13 it
  flagged nothing. Those 25 vulnerabilities stay open.
- "Mended" there means rescan clean and the file compiles. Those cases have no
  unit tests, so this is not test-verified.

Details, case ids and raw results: `bench/README.md`, `bench/results.json`.

## Honest limits

- Weaver only mends what Semgrep finds. On the benchmark sample that was 17 of
  42. Better rules would move that number, the mender would not.
- "Rescan clean" proves the rule no longer matches. It does not prove the code
  is safe. Tests and exploit checks add confidence only where they exist. 6
  of the 8 mended demo files have an exploit check.
- The demo target is small and its issues were planted by us.
- The hold and rollback path is implemented, but no fix failed all three
  attempts in the recorded run or the benchmark run, so the video does not
  show a hold.
- One file is one strand. Several findings in a file are mended together.
- Patches are whole-file rewrites by a language model. Review the commits.
- The watcher polls. It is not a git hook.
- Median time to mend in the UI counts from when the tear was seen, so files
  later in the queue show longer times.
- Bedrock has not written a single patch yet. The code path exists and falls
  back cleanly; it has only been seen refusing.
- The benchmark ran before the S3 ledger was added, so its results are in
  `bench/results.json` only.
- Local only. No auth. Do not expose the port.
