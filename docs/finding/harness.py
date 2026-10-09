"""Is each version of BenchmarkTest00167 still command-injectable? Test it, do not assert it.

For every file given, this loads the Flask route from the file, POSTs a hostile value, and looks
at what comes back. The payload is harmless: it asks the shell to do arithmetic and echo a marker.

    payload:  x; echo WEAVER_PWNED_$((6*7))

If a shell interprets attacker input, the response contains "WEAVER_PWNED_42" (the shell did the
multiplication). If the input is only data, the response contains the literal text "$((6*7))".

No network, no files written, nothing destructive. `helpers.utils` from the benchmark is replaced
by a two-line stub so that no other benchmark code is imported.

    python harness.py FILE.py [FILE.py ...]
"""

import importlib.util
import sys
import types

from flask import Flask

PAYLOAD = "x; echo WEAVER_PWNED_$((6*7))"
CASE = "BenchmarkTest00167"

helpers = types.ModuleType("helpers")
utils = types.ModuleType("helpers.utils")
utils.escape_for_html = lambda s: s
utils.commandOutput = lambda proc: (proc.stdout or "") + (proc.stderr or "")
helpers.utils = utils
sys.modules["helpers"] = helpers
sys.modules["helpers.utils"] = utils


def probe(path: str) -> str:
    spec = importlib.util.spec_from_file_location("case", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    app = Flask("harness")
    mod.init(app)
    res = app.test_client().post(f"/benchmark/cmdi-00/{CASE}", data={CASE: PAYLOAD})
    return res.get_data(as_text=True)


for f in sys.argv[1:]:
    out = probe(f)
    verdict = (
        "INJECTABLE (shell ran attacker input)"
        if "WEAVER_PWNED_42" in out
        else "not injectable (input stayed data)"
    )
    print(f"{f}\n  response: {out.strip()!r}\n  verdict:  {verdict}")
