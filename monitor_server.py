#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from activity_history import activity_summary
from approval_queue import decide_request, read_request
from onboarding import DEFAULT_VOICE, read_settings, write_settings
from runtime_paths import DATA_ROOT, LOGS, ROOT, VERSION

STATUS_PATH = LOGS / "status.json"
SETTINGS_PATH = LOGS / "settings.json"
LOG_PATH = LOGS / "twitter-agent.out"
BUMP_PATH = LOGS / "bump-now.txt"
HTML = ROOT / "dashboard.html"
APPLICATION = None

DEFAULT_SETTINGS = {
    "posts_per_day": 3,
    "replies_per_day": 100,
    "min_gap_hours": 0.5,
    "max_gap_hours": 2.5,
    "first_day_no_sleep": True,
    "attitude": DEFAULT_VOICE,
}


def read_json_file(path: Path, fallback: dict):
    try:
        if not path.exists():
            return fallback.copy()
        loaded = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            merged = fallback.copy()
            merged.update(loaded)
            return merged
    except Exception:
        pass
    return fallback.copy()


class Handler(BaseHTTPRequestHandler):
    def json_response(self, payload: dict, status: int = 200):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/updates":
            self.json_response(APPLICATION.updates if APPLICATION is not None else
                               {"current_version": VERSION, "state": "unavailable", "available": False})
            return
        if self.path == "/setup.js":
            body = (ROOT / "setup.js").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/javascript; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path == "/setup":
            if APPLICATION is None:
                self.json_response({"error": "Guided setup requires npm run dev."}, 503)
                return
            try:
                self.json_response(APPLICATION.snapshot())
            except (OSError, ValueError) as exc:
                print(f"Setup read failed: {exc}", file=sys.stderr)
                self.json_response({"error": str(exc)}, 409)
            return
        if re.fullmatch(r"/evidence/[a-f0-9]{32}/(?:before|after|failure)\.png", self.path):
            path = DATA_ROOT / "logs" / self.path.lstrip("/")
            if not path.exists():
                self.send_response(404)
                self.end_headers()
                return
            body = path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path == "/":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(HTML.read_bytes())
            return

        if self.path in {"/approval", "/control"}:
            channel = "control" if self.path == "/control" else "draft"
            try:
                request = read_request(STATUS_PATH, channel=channel)
                pending = request if request and request.get("decision") is None else None
                result = {"pending_approval": pending}
                status = 200
            except ValueError as exc:
                result = {"pending_approval": None, "error": str(exc)}
                status = 409
            body = json.dumps(result, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if self.path == "/status":
            payload = {"status": {}, "log_tail": []}
            if STATUS_PATH.exists():
                try:
                    payload["status"] = json.loads(STATUS_PATH.read_text(encoding="utf-8"))
                except Exception:
                    payload["status"] = {"state": "status_unavailable"}
            if not payload["status"].get("settings"):
                payload["status"]["settings"] = read_json_file(SETTINGS_PATH, DEFAULT_SETTINGS)
            payload["activity"] = activity_summary(LOGS / "posts.sqlite3")
            if LOG_PATH.exists():
                try:
                    lines = LOG_PATH.read_text(encoding="utf-8", errors="ignore").splitlines()
                    payload["log_tail"] = lines[-80:]
                except Exception:
                    payload["log_tail"] = []

            body = json.dumps(payload).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if self.path == "/settings":
            body = json.dumps(read_json_file(SETTINGS_PATH, DEFAULT_SETTINGS)).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        self.send_response(404)
        self.end_headers()

    def do_POST(self):
        origin = self.headers.get("Origin")
        if origin and origin not in {"http://127.0.0.1:8765", "http://localhost:8765"}:
            self.json_response({"error": "Actions must come from the local dashboard."}, 403)
            return
        if self.path == "/updates/check":
            if APPLICATION is None:
                self.json_response({"error": "Run the app with npm run dev."}, 503)
            else:
                threading.Thread(target=APPLICATION.check_updates, daemon=True).start()
                self.json_response({"ok": True})
            return
        if self.path.startswith("/setup/"):
            if APPLICATION is None:
                self.json_response({"error": "Guided setup requires npm run dev."}, 503)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 32768:
                    raise ValueError("Invalid setup request size.")
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
                if not isinstance(payload, dict):
                    raise ValueError("Setup request must be an object.")
                self.json_response(APPLICATION.action(self.path.removeprefix("/setup/"), payload))
            except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
                print(f"Setup action failed: {exc}", file=sys.stderr)
                self.json_response({"error": str(exc)}, 409)
            return
        if self.path in {"/approval", "/control", "/settings", "/bump"} and APPLICATION is not None and APPLICATION.editing:
            self.json_response({"error": "Finish setup before changing live agent settings or approvals."}, 409)
            return
        if self.path in {"/approval", "/control"}:
            channel = "control" if self.path == "/control" else "draft"
            origin = self.headers.get("Origin")
            if origin and origin not in {"http://127.0.0.1:8765", "http://localhost:8765"}:
                self.send_error(403, "Approval must come from the local dashboard")
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                length = 0
            if length <= 0 or length > 4096:
                self.send_error(400, "Invalid approval request")
                return
            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                self.send_error(400, "Invalid JSON")
                return
            if not isinstance(payload, dict):
                self.send_error(400, "Invalid approval request")
                return
            try:
                current = read_request(STATUS_PATH, channel=channel)
            except ValueError as exc:
                self.send_error(409, str(exc))
                return
            request_id = payload.get("id")
            if (not isinstance(request_id, str) or not current or current.get("decision") is not None or
                    current.get("id") != request_id):
                self.send_error(409, "This request is no longer awaiting review")
                return
            decision = payload.get("decision")
            if decision not in {"approve", "reject"} or not decide_request(STATUS_PATH, request_id, decision, channel=channel):
                self.send_error(409, "Could not record this review decision")
                return
            body = json.dumps({"ok": True, "decision": decision}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if self.path == "/bump":
            BUMP_PATH.parent.mkdir(parents=True, exist_ok=True)
            BUMP_PATH.write_text("manual_bump\n", encoding="utf-8")
            body = json.dumps({"ok": True}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if self.path == "/settings":
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 16384:
                    raise ValueError("Invalid settings request size.")
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
                if not isinstance(payload, dict):
                    raise ValueError("Settings must be an object.")
                if set(payload) - {"posts_per_day", "replies_per_day", "min_gap_hours",
                                   "max_gap_hours", "first_day_no_sleep", "attitude"}:
                    raise ValueError("Change browser, account, and profile through guided setup.")
                merged = DEFAULT_SETTINGS | read_settings(SETTINGS_PATH) | payload
                from onboarding import validate_setup
                if merged.get("onboarding_completed"):
                    validate_setup(merged)
                write_settings(SETTINGS_PATH, merged)
            except (OSError, ValueError) as exc:
                print(f"Settings save failed: {exc}", file=sys.stderr)
                self.json_response({"error": str(exc)}, 400)
                return
            body = json.dumps(merged).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        self.send_response(404)
        self.end_headers()

    def log_message(self, format, *args):
        return


if __name__ == "__main__":
    server = ThreadingHTTPServer(("127.0.0.1", 8765), Handler)
    print("Monitoring dashboard available at http://127.0.0.1:8765")
    server.serve_forever()
