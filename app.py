"""Own the dashboard, setup/login sessions, and one configured agent worker."""
from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time
import webbrowser
from http.server import ThreadingHTTPServer
from runtime_paths import DATA_ROOT, LOGS, PACKAGED, ROOT, VERSION, python_command

import monitor_server
from onboarding import (BROWSERS, DEFAULT_VOICE, has_account_history, open_setup_destination,
                        MODEL, model_status, permission_status, read_settings, setup_required, validate_setup,
                        verify_arc_account, write_settings)


class Application:
    def __init__(self, root: Path = DATA_ROOT):
        self.root = root
        self.logs = root / "logs"
        self.settings_path = self.logs / "settings.json"
        self.worker = None
        self.login = None
        self.worker_log = None
        self.login_log = None
        self.lock = threading.RLock()
        self.editing = False
        self.verified = None
        self.updates = {"current_version": VERSION, "available": False, "state": "not_checked"}
        self.update_lock = threading.Lock()
        self.model_download = {"state": "idle"}

    def check_updates(self):
        if not self.update_lock.acquire(blocking=False):
            return
        try:
            from update_manager import check_update
            self.updates = {**check_update(), "state": "checked"}
        except (OSError, ValueError) as exc:
            self.updates = {"current_version": VERSION, "available": False,
                            "state": "unavailable", "error": str(exc)}
            print(f"Update check unavailable: {exc}", file=sys.stderr)
        finally:
            self.update_lock.release()

    def snapshot(self) -> dict:
        with self.lock:
            settings = read_settings(self.settings_path)
            return {"required": setup_required(settings), "editing": self.editing,
                    "settings": settings, "default_voice": DEFAULT_VOICE,
                    "browsers": [{"id": key, "name": name, "installed": Path(path).exists()}
                                 for key, (name, path) in BROWSERS.items()],
                    "verified_account": self.verified,
                    "running": self.worker is not None and self.worker.poll() is None}

    def stop_worker(self) -> None:
        if self.worker is not None:
            pid = self.worker.pid
            if self.worker.poll() is None:
                self.worker.terminate()
                try:
                    self.worker.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    raise RuntimeError("The agent is still stopping. Wait before changing setup or starting another session.")
            self.worker = None
            path = self.logs / "agent.pid"
            if path.exists() and path.read_text().strip() == str(pid):
                path.unlink()
        if self.worker_log:
            self.worker_log.close()
            self.worker_log = None

    def stop_login(self) -> None:
        if self.login is not None:
            if self.login.poll() is None:
                self.login.terminate()
                self.login.wait(timeout=15)
            if self.login.stdin:
                self.login.stdin.close()
            self.login = None
        if self.login_log:
            self.login_log.close()
            self.login_log = None

    def start_worker(self) -> None:
        settings = read_settings(self.settings_path)
        if setup_required(settings):
            return
        browser = settings.get("browser", "arc")
        if browser == "arc":
            permission_status()
        command = [*python_command("twitter_agent.py"),
                   "--settings-file", str(self.settings_path),
                   "--status-file", str(self.logs / "status.json"), "--loop"]
        if settings.get("require_review", False):
            command.append("--require-review")
        if browser == "arc":
            command += ["--post-mode", "arc", "--campaign", "--supervise"]
        else:
            command += ["--browser", browser, "--campaign", "--supervise",
                        "--user-data-dir", str(self.root / f"browser-profile-{browser}")]
        if not settings.get("onboarding_completed"):
            command += ["--repo", os.environ.get("TWITTER_AGENT_REPO", str(ROOT))]
        self.worker_log = (self.logs / "twitter-agent.out").open("a", encoding="utf-8")
        self.worker = subprocess.Popen(command, stdout=self.worker_log, stderr=subprocess.STDOUT)
        (self.logs / "agent.pid").write_text(str(self.worker.pid), encoding="utf-8")

    def action(self, action: str, payload: dict) -> dict:
        with self.lock:
            if action == "begin":
                self.stop_worker()
                self.stop_login()
                self.editing = True
                self.verified = None
                from twitter_agent import write_status
                write_status(str(self.logs / "status.json"), {"state": "setup_required", "error": None,
                                                            "note": "Agent paused while setup is open."})
                return self.snapshot()
            if not self.editing and not setup_required(read_settings(self.settings_path)):
                raise ValueError("Open setup before changing the automation account.")
            if action == "save":
                settings = validate_setup(payload)
                previous = read_settings(self.settings_path)
                old_handle = previous.get("account_handle", "")
                if (old_handle and old_handle.casefold() != settings["account_handle"].casefold()
                        and has_account_history(self.logs)):
                    raise ValueError("This installation has history or approvals for another account. Use a separate installation for a different account; history cannot be reassigned.")
                from approval_queue import read_request
                request = read_request(self.logs / "status.json")
                if request and request["kind"] == "reply" and settings["browser"] != "arc":
                    raise ValueError("Review or reject the saved reply in Arc before switching to a posting-only browser.")
                if not Path(BROWSERS[settings["browser"]][1]).exists():
                    raise ValueError("Install the selected browser before continuing.")
                self.stop_login()
                self.verified = None
                previous.update(settings)
                previous["setup_in_progress"] = True
                write_settings(self.settings_path, previous)
                return {"ok": True}
            settings = read_settings(self.settings_path)
            browser = settings.get("browser")
            handle = settings.get("account_handle")
            if action in {"login", "verify", "complete"} and (browser not in BROWSERS or not handle):
                raise ValueError("Save your browser and account details first.")
            if action == "permission":
                open_setup_destination(payload.get("destination"))
                return {"ok": True}
            if action == "model":
                return {**model_status(), "download": self.model_download}
            if action == "model-download":
                if self.model_download["state"] == "downloading":
                    return {"ok": True}
                from urllib.request import Request, urlopen

                def download():
                    try:
                        request = Request("http://127.0.0.1:11434/api/pull",
                                          data=json.dumps({"name": MODEL, "stream": False}).encode(),
                                          headers={"Content-Type": "application/json"})
                        with urlopen(request, timeout=1200) as response:
                            result = json.load(response)
                        if result.get("status") != "success":
                            raise ValueError("Ollama did not confirm a successful model download.")
                        self.model_download = {"state": "complete"}
                    except (OSError, ValueError) as exc:
                        self.model_download = {"state": "error", "error": str(exc)}
                        print(f"Model download failed: {exc}", file=sys.stderr)

                self.model_download = {"state": "downloading"}
                threading.Thread(target=download, daemon=True).start()
                return {"ok": True}
            if action == "permissions":
                return {"permissions": permission_status() if browser == "arc" else {}}
            if action == "login":
                if browser == "arc":
                    subprocess.run(["open", "-a", "Arc", "https://x.com/login"],
                                   check=True, capture_output=True, text=True, timeout=10)
                else:
                    if self.login is not None and self.login.poll() is None:
                        return {"ok": True, "note": "Login window is already open."}
                    self.stop_login()
                    self.login_log = (self.logs / "setup-login.out").open("a", encoding="utf-8")
                    self.login = subprocess.Popen(
                        [*python_command("twitter_agent.py"), "--login",
                         "--browser", browser, "--user-data-dir", str(self.root / f"browser-profile-{browser}"),
                         "--settings-file", str(self.settings_path), "--status-file", str(self.logs / "status.json")],
                        stdin=subprocess.PIPE, stdout=self.login_log, stderr=subprocess.STDOUT, text=True)
                return {"ok": True}
            if action == "verify":
                self.verified = None
                if browser == "arc":
                    verified = verify_arc_account(handle)
                else:
                    if self.login is None or self.login.poll() is not None:
                        raise ValueError("Open the login window first. If it closed, open it again.")
                    self.login.stdin.write("\n")
                    self.login.stdin.flush()
                    code = self.login.wait(timeout=35)
                    self.stop_login()
                    status = json.loads((self.logs / "status.json").read_text())
                    if code or status.get("state") != "login_ready" or str(status.get("account", "")).casefold() != handle.casefold():
                        raise ValueError("Could not verify the selected X account. Check the login window/account and logs/setup-login.out.")
                    verified = handle
                self.verified = {"browser": browser, "handle": verified}
                return {"ok": True, "account": verified}
            if action == "complete":
                if self.verified != {"browser": browser, "handle": handle}:
                    raise ValueError("Verify the selected signed-in account before finishing.")
                if browser == "arc":
                    permissions = permission_status()
                    if not all(permissions.get(key) for key in ("accessibility", "input", "screen")):
                        raise ValueError("Grant Accessibility, Input Monitoring, and Screen Recording, then check again.")
                    verify_arc_account(handle)
                if not model_status()["ready"]:
                    raise ValueError("Open Ollama and download the suggested model before finishing setup.")
                settings.update(validate_setup(settings))
                settings["onboarding_completed"] = True
                settings["setup_in_progress"] = False
                write_settings(self.settings_path, settings)
                self.start_worker()
                self.editing = False
                return {"ok": True}
            raise ValueError("Unknown setup action.")

    def close(self) -> None:
        with self.lock:
            self.stop_worker()
            self.stop_login()


def main() -> int:
    root = DATA_ROOT
    smoke_test = "--smoke-test" in sys.argv
    application = Application(root)
    application.logs.mkdir(parents=True, exist_ok=True)
    if not PACKAGED and not smoke_test:
        subprocess.run(["bash", str(ROOT / "stop-twitter-agent.sh")], check=True)
    monitor_server.APPLICATION = application
    server = ThreadingHTTPServer(("127.0.0.1", 0 if smoke_test else 8765), monitor_server.Handler)
    (application.logs / "monitor.pid").write_text(str(os.getpid()), encoding="utf-8")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def stop(_signal, _frame):
        raise KeyboardInterrupt

    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, stop)
    try:
        if smoke_test:
            from urllib.request import urlopen
            base = f"http://127.0.0.1:{server.server_port}"
            with urlopen(base + "/") as response:
                if b"setup.js" not in response.read():
                    raise RuntimeError("The bundled dashboard is missing setup resources.")
            with urlopen(base + "/setup") as response:
                required = json.load(response)["required"]
            report = {"version": VERSION, "data_directory": str(root), "setup_required": required}
            (application.logs / "smoke-test.json").write_text(json.dumps(report), encoding="utf-8")
            print(json.dumps(report), flush=True)
            return 0
        if setup_required(read_settings(application.settings_path)):
            from twitter_agent import write_status
            write_status(str(application.logs / "status.json"),
                         {"state": "setup_required", "note": "Complete guided setup to start.", "error": None})
        else:
            application.start_worker()
        print("Dashboard: http://127.0.0.1:8765\nCtrl+C stops the agent and dashboard.", flush=True)
        threading.Thread(target=application.check_updates, daemon=True).start()
        if not webbrowser.open("http://127.0.0.1:8765/"):
            print("Open the dashboard URL above to continue.", file=sys.stderr)
        next_check = time.monotonic() + 3600
        while True:
            time.sleep(1)
            if time.monotonic() >= next_check:
                threading.Thread(target=application.check_updates, daemon=True).start()
                next_check = time.monotonic() + 3600
    except KeyboardInterrupt:
        return 0
    finally:
        application.close()
        server.shutdown()
        server.server_close()
        thread.join()
        for service in ("agent", "monitor"):
            path = application.logs / f"{service}.pid"
            if path.exists() and path.read_text().strip() in {
                    str(os.getpid()), str(getattr(application.worker, "pid", ""))}:
                path.unlink()


if __name__ == "__main__":
    if PACKAGED and (sys.stdout is None or sys.stderr is None):
        LOGS.mkdir(parents=True, exist_ok=True)
        filename = "twitter-agent.out" if "--agent" in sys.argv else "app.out"
        output = (LOGS / filename).open("a", encoding="utf-8", buffering=1)
        if sys.stdout is None:
            sys.stdout = output
        if sys.stderr is None:
            sys.stderr = output
    if "--agent" in sys.argv:
        sys.argv.remove("--agent")
        import twitter_agent
        raise SystemExit(twitter_agent.main())
    if "--help" in sys.argv or "-h" in sys.argv:
        print("Usage: app.py [--smoke-test]\nRuns DumDev's local dashboard and configured worker.")
        raise SystemExit(0)
    raise SystemExit(main())
