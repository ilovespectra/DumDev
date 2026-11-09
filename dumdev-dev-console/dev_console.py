#!/usr/bin/env python3
"""Local-only DumDev maintainer console; never included in DumDev user builds."""
from __future__ import annotations

import json
import os
import re
import hmac
import secrets
import threading
import webbrowser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import release_builder

HOST = "127.0.0.1"
PORT = 8781
CONSOLE_DIR = Path(__file__).resolve().parent
WEB_DIR = CONSOLE_DIR / "web"
APP_SUPPORT = Path.home() / "Library" / "Application Support" / "DumDev Developer Console"
SETTINGS_FILE = APP_SUPPORT / "settings.json"
REQUIRED = ("app.py", "twitter_agent.py", "runtime_paths.py", "update_manager.py",
            "dashboard.html", "setup.js", "takeover.swift")
_settings_lock = threading.Lock()
_build_lock = threading.Lock()
_build_status: dict[str, Any] = {"state": "idle", "message": "Ready when a complete DumDev source folder is selected."}
_CSRF_TOKEN = secrets.token_urlsafe(32)


def load_source() -> str:
    try:
        raw = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        value = raw.get("source_path", "") if isinstance(raw, dict) else ""
        return value if isinstance(value, str) else ""
    except (OSError, json.JSONDecodeError):
        return str(CONSOLE_DIR.parent)


def save_source(value: str) -> str:
    candidate = Path(value).expanduser().resolve()
    if not candidate.is_dir():
        raise ValueError("Choose an existing DumDev source folder.")
    APP_SUPPORT.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        APP_SUPPORT.chmod(0o700)
    except OSError:
        pass
    temp = SETTINGS_FILE.with_suffix(".tmp")
    temp.write_text(json.dumps({"source_path": str(candidate)}, indent=2) + "\n", encoding="utf-8")
    try:
        temp.chmod(0o600)
    except OSError:
        pass
    temp.replace(SETTINGS_FILE)
    return str(candidate)


def inspect_source(path: str | None = None) -> dict[str, Any]:
    candidate = Path(path or load_source()).expanduser() if (path or load_source()) else None
    if candidate is None or not candidate.is_dir():
        return {"source_path": str(candidate) if candidate else "", "ready": False,
                "missing": list(REQUIRED), "version": None, "next_version": None,
                "message": "Select the complete DumDev bot Git checkout."}
    candidate = candidate.resolve()
    missing = [relative for relative in REQUIRED if not (candidate / relative).is_file()]
    version = None
    if not missing:
        source = (candidate / "runtime_paths.py").read_text(encoding="utf-8")
        match = re.search(r'(?m)^VERSION = "([^\"]+)"$', source)
        if match:
            version = match.group(1)
    ready = not missing and version is not None
    latest = release_builder.latest_release()
    newest = max((value for value in (version, latest["version"] if latest else None) if value),
                 key=release_builder.version_tuple, default=None)
    return {"source_path": str(candidate), "ready": ready, "missing": missing,
            "version": version,
            "next_version": release_builder.next_patch(newest) if newest else None,
            "message": ("Builds use this repository's latest clean commit. Publish deploys the verified binary and update feed to the DumDev website." if ready
                        else "The selected folder is not a complete DumDev app source yet.")}


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, directory=str(WEB_DIR), **kwargs)

    def log_message(self, *_args: Any) -> None:
        return

    def _host_ok(self) -> bool:
        host = self.headers.get("Host", "").lower()
        peer = self.client_address[0].lower()
        return (host in {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}
                and peer in {"127.0.0.1", "::1", "::ffff:127.0.0.1"})

    def _origin_ok(self, required: bool = False) -> bool:
        origin = self.headers.get("Origin")
        allowed = {f"http://127.0.0.1:{self.server.server_port}", f"http://localhost:{self.server.server_port}"}
        return (not required and origin is None) or origin in allowed

    def _authorized(self, require_origin: bool = False) -> bool:
        supplied = self.headers.get("X-DumDev-Console-Token", "")
        return (self._host_ok() and self._origin_ok(required=require_origin)
                and bool(supplied) and hmac.compare_digest(supplied, _CSRF_TOKEN))

    def end_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        super().end_headers()

    def _serve_index(self) -> None:
        page = (WEB_DIR / "index.html").read_text(encoding="utf-8")
        token = json.dumps(_CSRF_TOKEN)
        bootstrap = f'''<script>
window.DUMDEV_CONSOLE_TOKEN = {token};
(() => {{
  const originalFetch = window.fetch.bind(window);
  window.fetch = (input, init = {{}}) => {{
    const raw = input instanceof Request ? input.url : String(input);
    const target = new URL(raw, window.location.href);
    if (target.origin !== window.location.origin || !target.pathname.startsWith('/api/')) return originalFetch(input, init);
    const headers = new Headers(init.headers || (input instanceof Request ? input.headers : undefined));
    headers.set('X-DumDev-Console-Token', window.DUMDEV_CONSOLE_TOKEN);
    return originalFetch(input, {{ ...init, headers }});
  }};
}})();
</script>'''
        page = page.replace("</head>", f"{bootstrap}</head>", 1)
        raw = page.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Security-Policy", "default-src 'self' 'unsafe-inline'; connect-src 'self' http://127.0.0.1:11434; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(raw)

    def _json(self, status: int, value: dict[str, Any]) -> None:
        raw = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self) -> None:
        if not self._host_ok():
            return self._json(403, {"error": "Use the local developer-console address."})
        if self.headers.get("Origin") and not self._origin_ok():
            return self._json(403, {"error": "Requests must come from this local console."})
        if self.path.startswith("/api/") and not self._authorized():
            return self._json(403, {"error": "A valid local console session is required."})
        if self.path in {"/", "/index.html"}:
            return self._serve_index()
        if self.path == "/api/status":
            return self._json(200, {**inspect_source(), "build": dict(_build_status),
                                   "latest_release": release_builder.latest_release(),
                                   "release_dir": str(release_builder.RELEASE_DIR)})
        return super().do_GET()

    def do_POST(self) -> None:
        if not self._host_ok():
            return self._json(403, {"error": "Use the local developer-console address."})
        if not self._authorized(require_origin=True):
            return self._json(403, {"error": "A valid same-origin local console session is required."})
        if self.headers.get_content_type() != "application/json":
            return self._json(415, {"error": "Requests must use application/json."})
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length < 1 or length > 16_000:
                raise ValueError("Invalid request size.")
            body = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(body, dict):
                raise ValueError("Expected a settings object.")
            if self.path == "/api/source":
                saved = save_source(str(body.get("source_path", "")))
                return self._json(200, inspect_source(saved))
            if self.path in {"/api/build", "/api/publish"}:
                state = inspect_source()
                if not state["ready"]:
                    raise ValueError(state["message"])
                version = str(body.get("version", ""))
                if not _build_lock.acquire(blocking=False):
                    raise ValueError("A build is already in progress.")
                try:
                    _build_status.update(state="building", message="Preparing release…")
                    def progress(message):
                        _build_status.update(message=message)
                    if self.path == "/api/publish":
                        result = release_builder.publish_release(progress)
                        _build_status.update(state="complete", message="Release published. Installed apps can discover the update.", result=result)
                    else:
                        result = release_builder.build_release(Path(state["source_path"]), version, progress)
                        _build_status.update(state="complete", message=f"DumDev {version} is verified and staged. Click Publish to make it available.", result=result)
                except Exception as exc:
                    _build_status.update(state="error", message=str(exc)[:700])
                    raise
                finally:
                    _build_lock.release()
                return self._json(200, {"build": dict(_build_status), **inspect_source()})
            return self._json(404, {"error": "Not found."})
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            return self._json(400, {"error": str(exc)})
        except Exception as exc:
            return self._json(500, {"error": f"Build failed: {str(exc)[:600]}"})


def main() -> None:
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    server.daemon_threads = True
    url = f"http://{HOST}:{server.server_port}/"
    print(f"DumDev Developer Console is local at {url}")
    webbrowser.open(url)
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
