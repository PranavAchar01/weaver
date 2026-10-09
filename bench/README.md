# Independent check: OWASP Benchmark for Python

This is a sample, not the full benchmark score.

## What was run

- Benchmark: OWASP BenchmarkPython v0.1, https://github.com/OWASP-Benchmark/BenchmarkPython
- Benchmark commit: `f1291485808b66e20ddb6b01b10dc71b3df8c8ba`
- Ground truth: `expectedresults-0.1.csv` from that commit (1230 cases, 14 categories)
- Scanner: Semgrep 1.180.0 with the same rule file Weaver uses (`rules/weaver.yaml`)
- Patcher: Claude Code CLI, headless, model sonnet, tools disabled
- Date: 2026-10-09

Commands, from the repo root:

```
git clone https://github.com/OWASP-Benchmark/BenchmarkPython bench/BenchmarkPython
git -C bench/BenchmarkPython checkout f1291485808b66e20ddb6b01b10dc71b3df8c8ba
.venv/bin/python bench/run_bench.py
.venv/bin/python bench/classify.py
```

The benchmark code is treated as untrusted. Nothing in it was installed or
run. Weaver only scanned and patched files.

## Selection rule

Written before the run: the first 3 real-vulnerability cases in each of the
14 categories, ordered by test number. That gives 42 cases. No case was added
or dropped after seeing results.

Case ids: 00001 00002 00003 00018 00019 00025 00026 00027 00054 00057 00058
00064 00065 00066 00067 00068 00072 00074 00078 00096 00097 00098 00099 00101
00153 00159 00160 00164 00165 00166 00167 00205 00258 00260 00265 00266 00269
00283 00284 00294 00427 00460 (each is `BenchmarkTestNNNNN`).

## What "mended" means here

These cases have no unit tests. So here "mended" means two things only: a
Semgrep rescan of the patched file is clean, and the file still compiles. It
is not test-verified, and nobody ran an exploit against the patched cases.

## Result

| | cases |
|---|---|
| Sampled real-vulnerability cases | 42 |
| Semgrep flagged the file at all | 29 |
| Semgrep flagged a rule in the benchmark's own category | 17 |
| of those, mended (rescan clean, compiles) | 17 |
| of those, held for a human | 0 |
| Semgrep flagged only a different issue | 12 |
| Semgrep flagged nothing | 13 |

Median time to mend: 13.1 s per case (patch written to rescan clean, 4 cases
in parallel, 24 on the first attempt and 5 on the second). Whole run: 268.9 s.

Read this plainly:

- Against the benchmark's ground truth, Weaver addressed 17 of 42 sampled
  vulnerabilities. The other 25 stay open because Semgrep, with these rules,
  did not find them. Weaver cannot mend what the scanner does not find.
- In 12 cases Semgrep flagged only something else in the file (11 times a
  cookie set without secure flags, once an XML parser rule on an XPath case).
  Weaver cleared those findings too, so all 29 flagged files are rescan-clean,
  but the benchmark's vulnerability in those 12 files is still there. They are
  not counted as mended on screen.
- Not flagged at all: xss 3, sqli 3, ldapi 3, trustbound 2, cmdi 1, redirect 1.
- Flagged for a different issue only: pathtraver 3, weakrand 3, xpathi 3,
  redirect 2, trustbound 1.
- The split between "benchmark category" and "different issue" comes from a
  mapping of rule ids to categories that we wrote (`classify.py`). It was added
  after the run, when the per-case rule list showed that many hits were the
  cookie rule. It changes no result, only how the 29 are reported.

## Files

- `results.json`: summary plus one record per case (rules fired, status,
  attempts, seconds, lines changed, whether a rule matches the category).
- `run.log`: output of the run.
- `results-run1-invalid.json`: an earlier run where every case was marked
  held. That was a bug in our harness (the compile check wrote to a bad
  path and raised), not a real result. It is kept so the record is complete.
- `run_bench.py`, `classify.py`: the harness.
