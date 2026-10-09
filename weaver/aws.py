"""AWS side of Weaver: Amazon Bedrock as the first-choice patcher, S3 as the mend ledger.

Credentials come from the environment only (run.sh sources ~/.config/weaver/aws.env
if it exists). Nothing here prints, logs or stores them. With no credentials every
call reports "unavailable" and Weaver carries on with its local patcher and file log.
"""

import hashlib
import json
import os
import threading
import time

REGION = os.environ.get("AWS_DEFAULT_REGION", "us-west-2")
BEDROCK_MODEL = os.environ.get("WEAVER_BEDROCK_MODEL", "us.anthropic.claude-sonnet-5-5")
RETRY_AFTER = 300  # seconds before Bedrock is tried again after a refusal
TAGS = [{"Key": "project", "Value": "weaver-hack"}]

_lock = threading.Lock()
_state = {"session": None, "bedrock_off_until": 0.0, "bedrock_error": "", "bucket": None, "s3_error": ""}


def enabled() -> bool:
    return bool(os.environ.get("AWS_ACCESS_KEY_ID")) and os.environ.get("WEAVER_NO_AWS") != "1"


def _client(name: str):
    import boto3
    from botocore.config import Config

    with _lock:
        if _state["session"] is None:
            _state["session"] = boto3.session.Session(region_name=REGION)
        return _state["session"].client(
            name, config=Config(connect_timeout=5, read_timeout=120, retries={"max_attempts": 1})
        )


def bedrock_status() -> str:
    if not enabled():
        return "no credentials"
    if time.time() < _state["bedrock_off_until"]:
        return _state["bedrock_error"]
    return "ready"


def bedrock_converse(system: str, prompt: str) -> str:
    """One Bedrock call. Raises on any failure; the caller falls back."""
    if not enabled():
        raise RuntimeError("no AWS credentials")
    if time.time() < _state["bedrock_off_until"]:
        raise RuntimeError(_state["bedrock_error"])
    try:
        res = _client("bedrock-runtime").converse(
            modelId=BEDROCK_MODEL,
            system=[{"text": system}],
            messages=[{"role": "user", "content": [{"text": prompt}]}],
            inferenceConfig={"maxTokens": 8000},
        )
        return "".join(b.get("text", "") for b in res["output"]["message"]["content"])
    except Exception as exc:
        code = getattr(exc, "response", {}).get("Error", {}).get("Code", type(exc).__name__)
        _state["bedrock_error"] = f"bedrock {code}"
        _state["bedrock_off_until"] = time.time() + RETRY_AFTER
        raise RuntimeError(_state["bedrock_error"]) from None


def bedrock_probe() -> str:
    """Ask Bedrock for one word. Returns "ready" or the refusal, e.g. "bedrock AccessDeniedException"."""
    if not enabled():
        return "no credentials"
    _state["bedrock_off_until"] = 0.0
    try:
        bedrock_converse("Reply with the single word ok.", "ok?")
        return "ready"
    except Exception as exc:
        return str(exc)


def ledger_bucket() -> str | None:
    """Create (once) and return the private ledger bucket, or None if S3 is unavailable."""
    if not enabled():
        return None
    with _lock:
        if _state["bucket"] or _state["s3_error"]:
            return _state["bucket"]
    try:
        account = _client("sts").get_caller_identity()["Account"]
        name = f"weaver-ledger-{account}-usw2"
        s3 = _client("s3")
        try:
            s3.head_bucket(Bucket=name)
        except Exception:
            s3.create_bucket(Bucket=name, CreateBucketConfiguration={"LocationConstraint": REGION})
        s3.put_public_access_block(
            Bucket=name,
            PublicAccessBlockConfiguration={
                "BlockPublicAcls": True,
                "IgnorePublicAcls": True,
                "BlockPublicPolicy": True,
                "RestrictPublicBuckets": True,
            },
        )
        s3.put_bucket_versioning(Bucket=name, VersioningConfiguration={"Status": "Enabled"})
        s3.put_bucket_tagging(Bucket=name, Tagging={"TagSet": TAGS})
        _state["bucket"] = name
    except Exception as exc:
        _state["s3_error"] = type(exc).__name__
    return _state["bucket"]


def ledger_put(record: dict) -> str | None:
    """Write one verified mend as a JSON object. Returns the key, or None if it did not land."""
    bucket = ledger_bucket()
    if not bucket:
        return None
    try:
        body = json.dumps(record, indent=1, sort_keys=True).encode()
        key = f"mends/{time.strftime('%Y-%m-%d', time.gmtime())}/{record['repo']}/{record['commit']}-{hashlib.sha256(body).hexdigest()[:8]}.json"
        _client("s3").put_object(
            Bucket=bucket, Key=key, Body=body, ContentType="application/json", ServerSideEncryption="AES256"
        )
        return key
    except Exception:
        return None
