"""Atomic, local-only approval requests for exact X drafts."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import uuid


REQUEST_NAME = "approval-request.json"
MAX_AGE_SECONDS = 24 * 60 * 60


def request_path(status_file: str | Path, *, channel: str = "draft") -> Path:
    names = {"draft": REQUEST_NAME, "control": "control-request.json"}
    return Path(status_file).resolve().parent / names[channel]


def _write_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def read_request(status_file: str | Path, *, channel: str = "draft") -> dict | None:
    path = request_path(status_file, channel=channel)
    if not path.exists():
        return None
    try:
        request = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("The approval request cannot be read; publication is blocked.") from exc
    if (not isinstance(request, dict) or
            not re.fullmatch(r"[0-9a-f]{32}", str(request.get("id", ""))) or
            request.get("kind") not in ({"control"} if channel == "control" else {"post", "reply"}) or
            not isinstance(request.get("text"), str) or not request["text"].strip()):
        raise ValueError("The approval request is invalid; publication is blocked.")
    if request["kind"] == "reply":
        target = request.get("target")
        if (not isinstance(target, dict) or
                not re.fullmatch(r"https://x\.com/[A-Za-z0-9_./-]+/status/\d+", str(target.get("url", ""))) or
                not isinstance(target.get("text"), str)):
            raise ValueError("The approved reply target is invalid; publication is blocked.")
    return request


def create_request(status_file: str | Path, kind: str, text: str,
                   target: dict | None = None, *, channel: str = "draft") -> dict:
    existing = read_request(status_file, channel=channel)
    if existing is not None:
        return existing
    if kind not in ({"control"} if channel == "control" else {"post", "reply"}) or not isinstance(text, str) or not text.strip():
        raise ValueError("Cannot request approval for an empty or unknown action.")
    if kind == "reply":
        if not isinstance(target, dict):
            raise ValueError("A reply approval requires its exact source target.")
        target = json.loads(json.dumps(target, ensure_ascii=False))
    else:
        target = None
    created_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    request = {"id": uuid.uuid4().hex, "kind": kind, "text": text,
               "target": target, "created_at": created_at, "decision": None}
    _write_atomic(request_path(status_file, channel=channel), request)
    return request


def decide_request(status_file: str | Path, request_id: str, decision: str, *, channel: str = "draft") -> bool:
    if decision not in {"approve", "reject"} or not re.fullmatch(r"[0-9a-f]{32}", str(request_id)):
        return False
    request = read_request(status_file, channel=channel)
    if request is None or request["id"] != request_id or request.get("decision") is not None:
        return False
    request["decision"] = decision
    request["decided_at"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    _write_atomic(request_path(status_file, channel=channel), request)
    return True


def clear_request(status_file: str | Path, request_id: str, *, channel: str = "draft") -> None:
    path = request_path(status_file, channel=channel)
    request = read_request(status_file, channel=channel)
    if request is not None and request["id"] == request_id:
        path.unlink(missing_ok=True)
