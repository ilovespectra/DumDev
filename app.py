#!/usr/bin/env python3
"""DumDev: local-only, human-reviewed social post drafting companion."""
from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import sys
import threading
import urllib.error
import urllib.request
import webbrowser
import release_tools
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

VERSION = "0.2.0"
HOST = "127.0.0.1"
PORT = int(os.environ.get("DUMDEV_PORT", "8766"))
OLLAMA = "http://127.0.0.1:11434"
RESOURCE_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
WEB_ROOT = RESOURCE_DIR / "web"
ASSET_ROOT = RESOURCE_DIR / "assets"
DATA_DIR = Path(os.environ.get("DUMDEV_DATA_DIR", str(Path.home() / "Library" / "Application Support" / "DumDev")))
CONFIG_PATH = DATA_DIR / "config.json"
BROWSERS = {"Safari": "Safari", "Chrome": "Google Chrome", "Brave": "Brave Browser", "Arc": "Arc", "Firefox": "Firefox"}
CADENCE_HOURS = {"1 day": 24, "2 days": 48, "3 days": 72, "1 week": 168}
DEFAULT_CONFIG: dict[str, Any] = {
    "setup_complete": False,
    "browser": "",
    "model": "",
    "topics": [],
    "voice": "",
    "cadence": "1 day",
}
_state_lock = threading.Lock()
_state: dict[str, Any] = {"paused": False, "generating": False, "last_error": ""}
_active_stop: threading.Event | None = None


def _secure_data_dir() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        DATA_DIR.chmod(0o700)
    except OSError:
        pass


def load_config() -> dict[str, Any]:
    config = dict(DEFAULT_CONFIG)
    try:
        stored = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        if isinstance(stored, dict):
            config.update(validate_config(stored))
    except (OSError, json.JSONDecodeError, ValueError, TypeError):
        pass
    return config


def validate_config(raw: dict[str, Any], merge: bool = False) -> dict[str, Any]:
    allowed = set(DEFAULT_CONFIG)
    if not isinstance(raw, dict) or set(raw) - allowed:
        raise ValueError("Unexpected settings field.")
    source = {**(load_config() if merge else DEFAULT_CONFIG), **raw}
    browser = source.get("browser", "")
    if browser not in ("", *BROWSERS.keys()):
        raise ValueError("Choose a listed browser.")
    model = str(source.get("model", "")).strip()
    if len(model) > 100 or (model and not re.fullmatch(r"[A-Za-z0-9._:/-]+", model)):
        raise ValueError("Use a valid local Ollama model name.")
    topics = source.get("topics", [])
    if isinstance(topics, str):
        topics = [item.strip() for item in re.split(r"[,\n]", topics) if item.strip()]
    if not isinstance(topics, list) or len(topics) > 12 or any(not isinstance(x, str) or len(x.strip()) > 60 for x in topics):
        raise ValueError("Add up to 12 short topic keywords.")
    topics = [item.strip() for item in topics if item.strip()]
    voice = str(source.get("voice", "")).strip()
    if len(voice) > 500:
        raise ValueError("Voice notes must be 500 characters or fewer.")
    cadence = source.get("cadence", "1 day")
    if cadence not in CADENCE_HOURS:
        raise ValueError("Choose a listed drafting cadence.")
    complete = source.get("setup_complete", False)
    if not isinstance(complete, bool):
        raise ValueError("Setup status must be a boolean.")
    if complete and not model:
        raise ValueError("Install Ollama, download a local model, and choose it before finishing setup.")
    return {"setup_complete": complete, "browser": browser, "model": model,
            "topics": topics, "voice": voice, "cadence": cadence}


def save_config(raw: dict[str, Any]) -> dict[str, Any]:
    config = validate_config(raw, merge=True)
    _secure_data_dir()
    temp = CONFIG_PATH.with_suffix(".tmp")
    temp.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    try:
        temp.chmod(0o600)
    except OSError:
        pass
    temp.replace(CONFIG_PATH)
    return config


def ollama_models() -> list[str]:
    req = urllib.request.Request(f"{OLLAMA}/api/tags", headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=3) as response:
        data = json.loads(response.read(1_000_000).decode("utf-8"))
    return [item["name"] for item in data.get("models", []) if isinstance(item, dict) and isinstance(item.get("name"), str)][:100]


def build_prompt(config: dict[str, Any], context: str) -> str:
    topics = ", ".join(config["topics"]) or "(no topic keywords supplied)"
    voice = config["voice"] or "Clear, warm, concise, and recognizably your own."
    return (
        "You are DumDev, a local writing assistant. Write exactly one concise draft for a human to review; do not publish or claim it was sent. "
        "Use only the details supplied below. Do not invent personal experience, facts, endorsements, or quotes. Do not imitate a real person, "
        "manufacture engagement, or suggest evading platform rules. If details are insufficient, ask for clarification instead of making claims. "
        "Return only the draft text, without quotation marks or commentary.\n\n"
        f"User's voice notes: {voice}\n"
        f"Topic keywords: {topics}\n"
        "Do not mention schedules, cadence, or app features unless the user explicitly asks in the notes.\n"
        f"Notes supplied for this draft: {context.strip()}"
    )


def open_x_login(browser: str) -> None:
    if not isinstance(browser, str) or browser not in BROWSERS:
        raise ValueError("Choose a browser in Settings first.")
    if os.environ.get("DUMDEV_TESTING") == "1":
        return
    if sys.platform != "darwin":
        raise RuntimeError("Automatic browser launching is available on macOS; open x.com manually on this device.")
    result = subprocess.run(["open", "-a", BROWSERS[browser], "https://x.com/i/flow/login"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
                            timeout=8, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"Could not open {browser}. Check that it is installed, then open x.com manually.")


class DumDevHandler(SimpleHTTPRequestHandler):
    server_version = f"DumDev/{VERSION}"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, directory=str(WEB_ROOT), **kwargs)

    def log_message(self, _format: str, *_args: Any) -> None:
        # Keep request bodies, prompts, and local settings out of logs.
        return

    def _valid_host(self) -> bool:
        port = self.server.server_port
        host = self.headers.get("Host", "").lower()
        return host in {f"127.0.0.1:{port}", f"localhost:{port}"}

    def end_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
        super().end_headers()

    def do_GET(self) -> None:
        if not self._valid_host():
            return self._json(403, {"error": "Use the local DumDev address."})
        if self.path == "/api/health":
            with _state_lock:
                payload = {"ok": True, "version": VERSION, **_state}
            return self._json(200, payload)
        if self.path == "/api/config":
            return self._json(200, load_config())
        if self.path == "/api/models":
            try:
                return self._json(200, {"models": ollama_models()})
            except (OSError, urllib.error.URLError, TimeoutError, json.JSONDecodeError, KeyError, TypeError):
                return self._json(503, {"error": "Ollama is not responding at 127.0.0.1:11434. Open Ollama and try again."})
        if self.path == "/api/release-status" and os.environ.get("DUMDEV_DEVELOPER_MODE") == "1":
            latest = release_tools.latest_release()
            base_version = latest["version"] if latest and release_tools.version_tuple(str(latest["version"])) > release_tools.version_tuple(VERSION) else VERSION
            return self._json(200, {"version": VERSION, "next_version": release_tools.next_patch(str(base_version)),
                                    "latest": latest})
        if self.path.startswith("/api/release-file/") and os.environ.get("DUMDEV_DEVELOPER_MODE") == "1":
            name = self.path.removeprefix("/api/release-file/")
            if not re.fullmatch(r"DumDev-[0-9]+\.[0-9]+\.[0-9]+-macOS-arm64\.zip", name):
                return self._json(404, {"error": "Not found."})
            return self._send_file(release_tools.RELEASE_DIR / name, "application/zip")
        if self.path.startswith("/assets/"):
            name = self.path.removeprefix("/assets/")
            if name not in {"dumdev-mark.svg"}:
                return self._json(404, {"error": "Not found."})
            return self._send_file(ASSET_ROOT / name, "image/svg+xml; charset=utf-8")
        if self.path == "/" or self.path == "/index.html":
            return self._send_file(WEB_ROOT / "index.html", "text/html; charset=utf-8")
        return self._json(404, {"error": "Not found."})

    def do_POST(self) -> None:
        if not self._valid_host():
            return self._json(403, {"error": "Use the local DumDev address."})
        origin = self.headers.get("Origin")
        expected = f"http://{HOST}:{self.server.server_port}"
        if origin and origin not in {expected, f"http://localhost:{self.server.server_port}"}:
            return self._json(403, {"error": "Requests must come from this local DumDev page."})
        try:
            body = self._read_body()
            if self.path == "/api/config":
                return self._json(200, save_config(body))
            if self.path == "/api/ollama-pull":
                model = body.get("model", "")
                if not isinstance(model, str) or not re.fullmatch(r"[A-Za-z0-9._:/-]{1,100}", model):
                    raise ValueError("Enter a model name, for example qwen2.5:3b.")
                req = urllib.request.Request(f"{OLLAMA}/api/pull", data=json.dumps({"name": model, "stream": False}).encode(),
                                             headers={"Content-Type": "application/json"}, method="POST")
                with urllib.request.urlopen(req, timeout=1800) as response:
                    result = json.loads(response.read(2_000_000).decode("utf-8"))
                return self._json(200, {"model": model, "status": result.get("status", "success")})
            if self.path == "/api/build-release" and os.environ.get("DUMDEV_DEVELOPER_MODE") == "1":
                result = release_tools.build_release(body.get("version", ""))
                return self._json(200, result)
            if self.path == "/api/pause":
                paused = body.get("paused")
                if not isinstance(paused, bool):
                    raise ValueError("Pause status must be true or false.")
                with _state_lock:
                    _state["paused"] = paused
                return self._json(200, {"paused": paused})
            if self.path == "/api/stop":
                with _state_lock:
                    active = _active_stop
                    generating = bool(_state["generating"])
                    if active:
                        active.set()
                    _state["last_error"] = "Generation stopped by you." if generating else ""
                return self._json(200, {"stopped": generating, "message": "Stop requested. Any draft returned after this request will be discarded."})
            if self.path == "/api/open-x":
                browser = body.get("browser") or load_config().get("browser")
                open_x_login(browser)
                opened = os.environ.get("DUMDEV_TESTING") != "1"
                message = ("X login opened in the selected browser. Sign in there yourself; DumDev does not read or control that browser."
                           if opened else "Test mode: X login would open in the selected browser; no browser was launched.")
                return self._json(200, {"browser": browser, "opened": opened, "message": message})
            if self.path == "/api/draft":
                return self._generate(body)
            return self._json(404, {"error": "Not found."})
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            return self._json(400, {"error": str(exc)})
        except (RuntimeError, subprocess.TimeoutExpired) as exc:
            return self._json(503, {"error": str(exc) or "Could not open the selected browser."})
        except Exception:
            return self._json(500, {"error": "DumDev hit a local error. Check Ollama and try again."})

    def _generate(self, body: dict[str, Any]) -> None:
        global _active_stop
        context = body.get("context", "")
        if not isinstance(context, str) or len(context) > 2_000:
            return self._json(400, {"error": "Draft notes must be 2,000 characters or fewer."})
        config = load_config()
        with _state_lock:
            if _state["paused"]:
                return self._json(409, {"error": "Drafting is paused. Resume drafting to continue."})
            if _state["generating"]:
                return self._json(409, {"error": "A draft is already being generated."})
            if not config["model"]:
                return self._json(400, {"error": "Choose an installed Ollama model in setup first."})
            stop_event = threading.Event()
            _active_stop = stop_event
            _state["generating"] = True
            _state["last_error"] = ""
        payload = json.dumps({"model": config["model"], "prompt": build_prompt(config, context),
                              "stream": False, "options": {"temperature": 0.7}}).encode("utf-8")
        req = urllib.request.Request(f"{OLLAMA}/api/generate", data=payload,
                                     headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=120) as response:
                result = json.loads(response.read(2_000_000).decode("utf-8"))
            draft = result.get("response", "").strip()
            if stop_event.is_set():
                return self._json(409, {"error": "Generation stopped; the late result was discarded."})
            if not draft:
                return self._json(502, {"error": "Ollama returned an empty draft. Try again or choose another model."})
            return self._json(200, {"draft": draft, "model": config["model"], "published": False})
        except (OSError, urllib.error.URLError, TimeoutError, json.JSONDecodeError, KeyError, TypeError):
            return self._json(502, {"error": "Ollama could not complete this draft. Check that the local model is installed and try again."})
        finally:
            with _state_lock:
                _state["generating"] = False
                if _active_stop is stop_event:
                    _active_stop = None

    def _read_body(self) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise ValueError("Invalid request length.") from exc
        if length < 0 or length > 32_000:
            raise ValueError("Request is too large.")
        if self.headers.get_content_type() != "application/json":
            raise ValueError("Use application/json.")
        data = json.loads(self.rfile.read(length).decode("utf-8"))
        if not isinstance(data, dict):
            raise ValueError("Expected a JSON object.")
        return data

    def _send_file(self, path: Path, content_type: str) -> None:
        try:
            data = path.read_bytes()
        except OSError:
            return self._json(404, {"error": "Not found."})
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _json(self, status: int, payload: dict[str, Any]) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def make_server() -> ThreadingHTTPServer:
    ThreadingHTTPServer.daemon_threads = True
    for port in range(PORT, PORT + 15):
        try:
            return ThreadingHTTPServer((HOST, port), DumDevHandler)
        except OSError:
            continue
    raise RuntimeError("No available local port in the DumDev range 8766–8780.")


def main() -> None:
    server = make_server()
    url = f"http://{HOST}:{server.server_port}/"
    print(f"DumDev {VERSION} is running on {url}. Only this Mac can connect.")
    print("Press Ctrl+C to stop. Drafts are never posted automatically.")
    if "--no-browser" not in sys.argv and os.environ.get("DUMDEV_TESTING") != "1":
        webbrowser.open(url)
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        print("\nDumDev stopped.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
