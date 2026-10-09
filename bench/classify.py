"""Post-process bench/results.json: does a Semgrep rule that fired on a case
belong to the benchmark's own vulnerability category for that case?

No scanning or patching happens here. It only adds fields to results.json.
The mapping below is ours (rule id substrings per benchmark category) and is
the whole judgement; read it before trusting the split.
"""
import json
import pathlib

MATCH = {
    "hash": ["insecure-hash"],
    "securecookie": ["secure-set-cookie"],
    "codeinj": ["exec-detected", "eval-detected"],
    "deserialization": ["deserialization", "pickle", "pyyaml"],
    "cmdi": ["subprocess", "os-system", "command-injection"],
    "xxe": ["defused-xml", "xxe"],
    "xpathi": ["xpath"],
    "pathtraver": ["path-traversal"],
    "weakrand": ["random"],
    "redirect": ["redirect"],
    "sqli": ["sql"],
    "xss": ["xss", "raw-html-format", "template-string"],
    "ldapi": ["ldap"],
    "trustbound": ["trust-boundary", "session"],
}

p = pathlib.Path(__file__).with_name("results.json")
d = json.loads(p.read_text())
for c in d["cases"]:
    keys = MATCH.get(c["category"], [])
    c["rule_matches_category"] = any(k in r for r in c.get("rules", []) for k in keys)
cases = d["cases"]
flagged = [c for c in cases if c["status"] != "not_flagged"]
match = [c for c in flagged if c["rule_matches_category"]]
other = [c for c in flagged if not c["rule_matches_category"]]
s = d["summary"]
s["flagged_in_benchmark_category"] = len(match)
s["mended_in_benchmark_category"] = sum(c["status"] == "mended" for c in match)
s["held_in_benchmark_category"] = sum(c["status"] == "held" for c in match)
s["flagged_other_issue_only"] = len(other)
s["mended_other_issue_only"] = sum(c["status"] == "mended" for c in other)
s["category_match_note"] = (
    "a case counts as flagged in the benchmark category only when a Semgrep rule "
    "that fired belongs to that case's category (mapping in bench/classify.py); "
    "where only a different rule fired, the benchmark's vulnerability is still there"
)
p.write_text(json.dumps(d, indent=1) + "\n")
print({k: v for k, v in s.items() if "flagged" in k or "mended" in k or "held" in k or k in ("sampled", "not_flagged")})
print("other-only:", [(c["case"][-5:], c["category"]) for c in other])
