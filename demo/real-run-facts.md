# Real run facts (isso), 2026-10-09

All numbers are measured from one continuous recorded shell session (demo/real.cast.json, demo/real-run.jsonl, demo/real-times.json).

## Project
- isso-comments/isso (self-hosted commenting server, a Disqus alternative), Python
- 5,310 GitHub stars (gh api, 15:10 PT), default branch master, last upstream commit 2026-09-15
- Pinned commit: 413d447a46437d0e9ccf206c8525119355e291d4 (413d447), 296 tracked files
- Why this one: of 6 candidates scanned (Zappa, bottle, isso, mycli, nikola, wfuzz) it had the clearest real issue in non-test application code: untrusted XML import dumps parsed with xml.etree.

## Findings
- Semgrep 1.180.0, 253 registry rules (rules/weaver.yaml), whole repo minus tests: 25 findings
  - 15 in docs/ HTML theme templates (not application code)
  - 10 in application code
- Mended by Weaver: 3 findings, 1 file
  - isso/migrate.py:15 python.lang.security.use-defused-xml.use-defused-xml
  - isso/migrate.py:85 python.lang.security.use-defused-xml-parse.use-defused-xml-parse
  - isso/migrate.py:207 python.lang.security.use-defused-xml-parse.use-defused-xml-parse
  - What it is: the Disqus and WordPress importers call ElementTree.parse on a user-supplied XML dump (XXE / entity expansion class).
  - Patch: `from xml.etree import ElementTree` becomes `from defusedxml import ElementTree` (1 line added, 1 removed), commit 51b9ad0, author "Weaver".
  - Attempt 1 was Semgrep's own autofix: rolled back, rescan still found use-defused-xml. Attempt 2 was the LLM patch (Claude Code CLI, sonnet): passed.
- Held: 0
- Skipped by hand as false positives, before the run (listed in .weaver/skip, and the terminal prints "skipping 5 paths marked false positive by hand"): 22 findings in 7 files
  - docs/_theme/layout.html (12), docs/_theme/search.html (2), docs/index.html (1): Sphinx theme templates
  - isso/templates/admin.html (3): var-in-href in the admin template
  - isso/utils/cache.py (2): avoid-pickle on an in-process cache that only pickles its own values
  - isso/views/comments.py (1): use-defused-xml, but ElementTree is only used to build the Atom feed
  - isso/ext/notifications.py (1): dynamic urllib URL that comes from the server's own config
- By-hand addition (NOT Weaver): one line in pyproject.toml adding defusedxml to dependencies, separate commit 9b5e727 "pyproject: add defusedxml dependency (by hand)". Said so in the PR body.

## Verification / test status
- Verified: Semgrep rescan of the patched file is clean, and the file compiles.
- isso's test suite was NOT run (untrusted project code is not executed). Do not say tests passed.
- Weaver also wrote one memory rule (.weaver/rules/weaver-mem-use-defused-xml-migrate.yaml), local only, not in the PR.
- Bedrock: still AccessDenied, patch came from the Claude Code CLI. No Bedrock claim. Ledger entry written to the existing S3 bucket (1 new object).

## Times (measured)
- Semgrep scan: 3.86 s
- Time to mend isso/migrate.py: 36.1 s from finding to verified commit (2 attempts: autofix 3.2 s then rolled back, LLM patch 25.7 s, plus rescans)
- Total wall time, Enter on `weaver watch` to PR URL printed: 81.0 s (includes about 11 s waiting for the memory rule and the typed git/gh commands)
- No comparison figure is used.

## PR
- https://github.com/PranavAchar01/isso/pull/1 (inside the fork: base PranavAchar01/isso master, head weaver/mend). Title "Mend 3 Semgrep findings in isso/migrate.py".
- Nothing opened on upstream isso-comments/isso (no PR, issue or comment).

## Shot list, demo/weaver-demo-60s.mp4 (58.5 s, silent, 1080p, round 6; round 5 is weaver-demo-60s-v5.mp4)
- 0.0 to 5.5   Terminal: `cd isso`, `weaver watch . --lang python --no-tests`. Real session, replayed at 1.5x, stated on screen.
- 5.5 to 20.3  Browser, 127.0.0.1:8765: the isso run (demo/real-run.jsonl) replayed at 3.6x, stated on screen. Left column: plain-text log of the recorded events and running totals.
- 20.3 to 27.5 Terminal again (same session): Ctrl-C, git log, branch, push to the fork, `gh pr create`. Replayed at 4x, stated.
- 27.5 to 32.0 Browser: one still of github.com/PranavAchar01/isso/pull/1 with the times strip.
- 32.0 to 33.5 Browser: one still of OWASP-Benchmark/BenchmarkPython on GitHub.
- 33.5 to 52.5 Browser: the web app replaying demo/bench-run.jsonl, a live `weaver watch . --no-tests` re-run on the 42-case benchmark sample recorded today (693 s, one file at a time), replayed at 44x, stated on screen. 29 files flagged, 28 mended, 1 held (cmdi/BenchmarkTest00167). Same per-case outcome as bench/results.json.
- 52.5 to 58.5 Score card from bench/results.json (16 / 17, 1 held), with a line saying the card is the earlier full run and that the live re-run gave the same result on these 17.
- The benchmark terminal session (`weaver bench --summary`) is no longer in the film.
