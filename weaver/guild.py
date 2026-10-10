"""Guild AI side of Weaver: a reviewer agent hosted on Guild signs off on each patch before it is committed.

Weaver sends the Semgrep finding and the diff that already passed the rescan to the agent
(guild/weaver-review/agent.ts) as one Guild session, and reads back APPROVE or REJECT.
The session is the governed, logged record of that decision.

Settings come from the environment only (cli.py reads ~/.config/weaver/guild.env if it exists):
GUILD_API_KEY, GUILD_WORKSPACE, GUILD_AGENT_ID. With any of them missing every call reports
"off" and Weaver carries on without the review. Nothing here prints, logs or stores the key.
"""

import json
import os
import time
import urllib.error
import urllib.request

API = os.environ.get("GUILD_API_URL", "https://api.guild.ai/v1")
WAIT = 60  # seconds to wait for the agent's answer


def enabled() -> bool:
    return all(
        os.environ.get(k)
        for k in ("GUILD_API_KEY", "GUILD_WORKSPACE", "GUILD_AGENT_ID")
    )


def _call(method: str, path: str, body: dict | None = None) -> dict:
    req = urllib.request.Request(
        API + path,
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={
            "Authorization": f"Bearer {os.environ['GUILD_API_KEY']}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=20) as res:
        return json.loads(res.read() or b"{}")


def review(rule: str, path: str, diff: list[str]) -> dict:
    """One review. Returns {"verdict": "approve" | "reject" | "unavailable", "reason", "session"}."""
    if not enabled():
        return {"verdict": "unavailable", "reason": "off", "session": None}
    prompt = (
        f"Semgrep rule: {rule}\nFile: {path}\n"
        "The patch below already passed a Semgrep rescan. Review it.\n\n"
        + "\n".join(diff)[:6000]
    )
    session = None
    try:
        made = _call(
            "POST",
            f"/workspaces/{os.environ['GUILD_WORKSPACE']}/sessions",
            {
                "session_type": "chat",
                "agent_id": os.environ["GUILD_AGENT_ID"],
                "initial_prompt": prompt,
            },
        )
        session = made["id"]
        deadline = time.time() + WAIT
        while time.time() < deadline:
            for event in _call("GET", f"/sessions/{session}/events").get("items", []):
                text = (
                    (event.get("content") or {}).get("text")
                    if event.get("type") == "runtime_done"
                    else None
                )
                if text:
                    first = text.strip().split(None, 1)
                    verdict = (
                        "reject"
                        if first and first[0].upper().startswith("REJECT")
                        else "approve"
                    )
                    return {
                        "verdict": verdict,
                        "reason": text.strip()[:200],
                        "session": session,
                    }
            time.sleep(2)
        return {
            "verdict": "unavailable",
            "reason": "no answer in time",
            "session": session,
        }
    except (urllib.error.URLError, KeyError, ValueError, TimeoutError) as exc:
        return {"verdict": "unavailable", "reason": str(exc)[:120], "session": session}
