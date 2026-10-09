"""Merge Semgrep registry packs (p/python, p/flask, p/security-audit, p/secrets) into one
local Python-only rule file so scans are fast and work offline. Rules are unmodified."""
import pathlib
import yaml

here = pathlib.Path(__file__).parent
seen, out = set(), []
for pack in ("python", "flask", "security-audit", "secrets"):
    for rule in yaml.safe_load((here / f"{pack}.yaml").read_text())["rules"]:
        langs = set(rule.get("languages", []))
        if rule["id"] in seen or not langs & {"python", "generic", "regex"}:
            continue
        seen.add(rule["id"])
        out.append(rule)
(here / "weaver.yaml").write_text(yaml.safe_dump({"rules": out}, sort_keys=False, width=1000))
print(len(out), "rules")
