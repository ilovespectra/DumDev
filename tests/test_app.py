import json
import os
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from unittest.mock import patch

import app


class LocalOllamaHandler(BaseHTTPRequestHandler):
    requests_seen = []

    def do_GET(self):
        if self.path == "/api/tags":
            return self._json(200, {"models": [{"name": "test-model:latest"}]})
        self._json(404, {"error": "not found"})

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length).decode("utf-8"))
        type(self).requests_seen.append((self.path, payload))
        if self.path == "/api/generate":
            return self._json(200, {"response": "A small, thoughtful draft for you to review."})
        self._json(404, {"error": "not found"})

    def _json(self, status, payload):
        data = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *_args):
        return


class DumDevTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.data = Path(self.temp.name)
        self.patches = [
            patch.object(app, "DATA_DIR", self.data),
            patch.object(app, "CONFIG_PATH", self.data / "config.json"),
            patch.dict(os.environ, {"DUMDEV_TESTING": "1"}),
        ]
        for item in self.patches:
            item.start()
        with app._state_lock:
            app._state.update({"paused": False, "generating": False, "last_error": ""})
            app._active_stop = None

        LocalOllamaHandler.requests_seen = []
        self.ollama = ThreadingHTTPServer(("127.0.0.1", 0), LocalOllamaHandler)
        self.ollama_thread = threading.Thread(target=self.ollama.serve_forever, daemon=True)
        self.ollama_thread.start()
        self.ollama_patch = patch.object(app, "OLLAMA", f"http://127.0.0.1:{self.ollama.server_port}")
        self.ollama_patch.start()

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), app.DumDevHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.ollama.shutdown()
        self.ollama.server_close()
        self.ollama_thread.join(timeout=2)
        self.ollama_patch.stop()
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def post(self, path, payload, headers=None):
        request = Request(self.base + path, data=json.dumps(payload).encode(),
                          headers={"Content-Type": "application/json", **(headers or {})}, method="POST")
        try:
            with urlopen(request, timeout=3) as response:
                return response.status, json.loads(response.read())
        except HTTPError as error:
            try:
                return error.code, json.loads(error.read())
            finally:
                error.close()

    def test_local_dashboard_and_health(self):
        with urlopen(self.base + "/", timeout=3) as response:
            html = response.read().decode()
        self.assertIn("DumDev", html)
        self.assertIn("NO AUTOPUBLISH", html)
        self.assertIn("Choose your browser", html)
        self.assertIn("Cadence help", html)
        self.assertIn(".tooltip:hover .tip", html)
        self.assertIn(".tooltip:focus .tip", html)
        with urlopen(self.base + "/api/health", timeout=3) as response:
            health = json.loads(response.read())
        self.assertTrue(health["ok"])
        self.assertFalse(health["generating"])

    def test_rejects_untrusted_host_header(self):
        status, result = self.get("/api/health", headers={"Host": "attacker.invalid"})
        self.assertEqual(status, 403)
        self.assertIn("local DumDev", result["error"])

    def test_saves_only_valid_local_preferences(self):
        status, saved = self.post("/api/config", {
            "setup_complete": True, "browser": "Brave", "model": "llama3.2:3b",
            "topics": ["local art", "open source"], "voice": "Warm, direct.", "cadence": "2 days"
        })
        self.assertEqual(status, 200)
        self.assertEqual(saved["browser"], "Brave")
        self.assertTrue((self.data / "config.json").exists())
        self.assertFalse((self.data / "config.json").stat().st_mode & 0o077)
        status, result = self.post("/api/config", {"password": "not allowed"})
        self.assertEqual(status, 400)
        self.assertNotIn("password", (self.data / "config.json").read_text())

    def test_local_model_list_and_draft_never_publish(self):
        status, models = self.get("/api/models")
        self.assertEqual(status, 200)
        self.assertEqual(models["models"], ["test-model:latest"])
        status, _ = self.post("/api/config", {"model": "test-model:latest", "setup_complete": True})
        self.assertEqual(status, 200)
        status, result = self.post("/api/draft", {"context": "A user-provided note."})
        self.assertEqual(status, 200)
        self.assertFalse(result["published"])
        self.assertEqual(result["draft"], "A small, thoughtful draft for you to review.")
        path, payload = LocalOllamaHandler.requests_seen[-1]
        self.assertEqual(path, "/api/generate")
        self.assertEqual(payload["model"], "test-model:latest")
        self.assertIn("do not publish", payload["prompt"].lower())
        self.assertIn("A user-provided note.", payload["prompt"])

    def get(self, path, headers=None):
        request = Request(self.base + path, headers=headers or {})
        try:
            with urlopen(request, timeout=3) as response:
                return response.status, json.loads(response.read())
        except HTTPError as error:
            try:
                return error.code, json.loads(error.read())
            finally:
                error.close()

    def test_browser_open_is_manual_and_test_mode_does_not_launch(self):
        status, result = self.post("/api/open-x", {"browser": "Arc"})
        self.assertEqual(status, 200)
        self.assertEqual(result["browser"], "Arc")
        self.assertFalse(result["opened"])
        self.assertIn("no browser was launched", result["message"])

        with patch.dict(os.environ, {"DUMDEV_TESTING": "0"}), patch.object(app.subprocess, "run") as launch:
            launch.return_value.returncode = 0
            app.open_x_login("Arc")
        launch.assert_called_once()
        args = launch.call_args.args[0]
        self.assertEqual(args[:3], ["open", "-a", "Arc"])
        self.assertEqual(args[-1], "https://x.com/i/flow/login")

    def test_prompt_is_only_a_draft_and_rejects_imitation(self):
        config = {"topics": ["open-source"], "voice": "Warm and concise", "cadence": "1 day"}
        prompt = app.build_prompt(config, "My own note")
        self.assertIn("do not publish", prompt.lower())
        self.assertIn("Do not imitate a real person", prompt)
        self.assertIn("My own note", prompt)
        self.assertIn("Do not mention schedules", prompt)
        self.assertNotIn("Preferred cadence", prompt)
        self.assertNotIn("http://", prompt)

    def test_stop_marks_active_generation_for_discard(self):
        stop_event = threading.Event()
        with app._state_lock:
            app._active_stop = stop_event
            app._state["generating"] = True
        status, result = self.post("/api/stop", {})
        self.assertEqual(status, 200)
        self.assertTrue(result["stopped"])
        self.assertTrue(stop_event.is_set())

    def test_paused_state_blocks_generation(self):
        status, result = self.post("/api/pause", {"paused": True})
        self.assertEqual(status, 200)
        self.assertTrue(result["paused"])
        status, result = self.post("/api/draft", {"context": "A short note"})
        self.assertEqual(status, 409)
        self.assertIn("paused", result["error"].lower())


if __name__ == "__main__":
    unittest.main()
