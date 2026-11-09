import json
from pathlib import Path
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parent


class StaticDashboard(BaseHTTPRequestHandler):
    def do_GET(self):
        path = ROOT / ("setup.js" if self.path == "/setup.js" else "dashboard.html")
        self.send_response(200)
        self.send_header("Content-Type", "text/javascript" if path.suffix == ".js" else "text/html")
        self.end_headers()
        self.wfile.write(path.read_bytes())

    def log_message(self, *_args):
        return


class DashboardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), StaticDashboard)
        cls.thread = threading.Thread(target=cls.server.serve_forever)
        cls.thread.start()
        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch(headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()
        cls.server.shutdown()
        cls.thread.join()
        cls.server.server_close()

    def test_guided_setup_and_layout_for_all_browsers_and_screen_sizes(self):
        for browser in ("arc", "brave", "chrome"):
            for width in (390, 1440):
                with self.subTest(browser=browser, width=width):
                    page = self.browser.new_page(viewport={"width": width, "height": 900})
                    state = {"completed": False, "saved": None, "actions": [], "permissions": False}
                    errors = []
                    page.on("pageerror", lambda error: errors.append(str(error)))

                    def respond(route):
                        path = urlsplit(route.request.url).path
                        if path in {"/", "/setup.js"}:
                            route.continue_()
                            return
                        if path == "/setup":
                            result = {"required": not state["completed"], "settings": {},
                                      "default_voice": "Curious, warm, and specific.",
                                      "browsers": [{"id": name, "installed": True} for name in ("arc", "brave", "chrome")]}
                        elif path == "/settings":
                            result = {"posts_per_day": 3, "replies_per_day": 10, "attitude": "original voice"}
                        elif path == "/status":
                            result = {"status": {"state": "setup_required", "repo": "long-context/" * 40,
                                                 "note": "long-note " * 100,
                                                 "discovery_trends": [{
                                                     "term": "browser tabs", "authors": 2,
                                                     "modes": ["recent", "top"],
                                                     "sources": ["https://x.com/a/status/1",
                                                                 "https://x.com/b/status/2"]}]},
                                      "activity": {"recent_events": []}}
                        elif path in {"/approval", "/control"}:
                            result = {"pending_approval": None}
                        elif path.startswith("/setup/"):
                            state["actions"].append(path)
                            result = {"ok": True}
                            if path == "/setup/save":
                                state["saved"] = route.request.post_data_json
                            if path == "/setup/verify":
                                result["account"] = "garden_user"
                            if path == "/setup/model":
                                result["ready"] = True
                            if path == "/setup/permissions":
                                result["permissions"] = {"accessibility": True, "input": state["permissions"],
                                                         "screen": True}
                            if path == "/setup/complete":
                                state["completed"] = True
                        else:
                            result = {}
                        route.fulfill(status=200, content_type="application/json", body=json.dumps(result))

                    page.route("**/*", respond)
                    try:
                        page.goto(f"http://127.0.0.1:{self.server.server_port}/")
                        page.locator("#setup-dialog[open]").wait_for()
                        self.assert_no_overflow(page)
                        page.locator("#setup-browser").select_option(browser)
                        page.locator("#setup-handle").fill("@garden_user")
                        page.locator("#setup-next").click()
                        page.locator("#setup-name").fill("Small Gardens")
                        page.locator("#setup-description").fill("Practical urban gardening notes.")
                        page.locator("#setup-topics").fill("urban gardening\nsoil health")
                        self.assert_no_overflow(page)
                        page.locator("#setup-next").click()
                        page.locator("#setup-attitude").fill("Direct and kind. Never hype.")
                        page.locator("#setup-posts").fill("0")
                        page.locator("#setup-next").click()
                        page.locator("#setup-login").wait_for(state="visible")
                        self.assertTrue(page.locator("#setup-next").is_disabled())
                        self.assertEqual(state["saved"]["attitude"], "Direct and kind. Never hype.")
                        self.assertEqual(state["saved"]["posts_per_day"], 0)
                        self.assertEqual(state["saved"]["topics"], ["urban gardening", "soil health"])
                        self.assertEqual(state["saved"]["replies_per_day"], 20 if browser == "arc" else 0)
                        page.locator("#setup-login").click()
                        page.locator("#setup-check-model").click()
                        page.locator("#setup-verify").click()
                        page.get_by_text("Verified signed-in account: @garden_user", exact=True).wait_for()
                        if browser == "arc":
                            page.locator("#setup-check-permissions").click()
                            page.get_by_text("Not allowed yet — enable it in System Settings", exact=True).wait_for()
                            self.assertTrue(page.locator("#setup-next").is_disabled())
                            state["permissions"] = True
                            page.locator("#setup-check-permissions").click()
                            page.wait_for_function("!document.getElementById('setup-next').disabled")
                        else:
                            self.assertTrue(page.locator("#setup-arc-permissions").is_hidden())
                        self.assert_no_overflow(page)
                        page.locator("#setup-next").click()
                        self.assertIn("Direct and kind", page.locator("#setup-review").inner_text())
                        page.locator("#setup-next").click()
                        page.locator("#setup-dialog").wait_for(state="hidden")
                        page.wait_for_function(
                            "document.getElementById('discovery-trends').textContent.includes('browser tabs')")
                        self.assertIn("browser tabs", page.locator("#discovery-trends").inner_text())
                        self.assertIn("recent + top", page.locator("#discovery-trends").inner_text())
                        self.assertTrue(state["completed"])
                        self.assertEqual(state["actions"].count("/setup/complete"), 1)
                        self.assert_no_overflow(page)
                        # Polling must not overwrite an in-progress settings edit.
                        page.locator("#attitude").fill("An unsaved edit")
                        page.evaluate("loadStatus()")
                        self.assertEqual(page.locator("#attitude").input_value(), "An unsaved edit")
                        self.assertEqual(errors, [])
                    finally:
                        page.close()

    def assert_no_overflow(self, page):
        self.assertTrue(page.evaluate("""() => {
            const dialog = document.getElementById('setup-dialog');
            return document.documentElement.scrollWidth <= innerWidth &&
                (!dialog.open || dialog.scrollWidth <= dialog.clientWidth + 1);
        }"""))


if __name__ == "__main__":
    unittest.main()
