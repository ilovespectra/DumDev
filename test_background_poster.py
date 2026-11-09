import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from playwright.sync_api import sync_playwright
from background_poster import attach_login_diagnostics, browser_executable, finish_attempt, matching_post, post_in_background, reserve_attempt, wait_for_login_confirmation
from twitter_agent import sanitize_tweet_text, write_status


class PosterTests(unittest.TestCase):
    def test_login_confirmation_keeps_browser_events_processing(self):
        import threading
        ready = threading.Event()
        page = MagicMock()
        page.is_closed.return_value = False
        page.wait_for_timeout.side_effect = lambda _: ready.set()
        with patch("builtins.input", side_effect=lambda: ready.wait(timeout=2)):
            wait_for_login_confirmation(page)
        page.wait_for_timeout.assert_called()

    def test_login_diagnostics_exclude_credentials_urls_and_error_messages(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "diagnostics.json"
            page = MagicMock()
            page.url = "https://x.com/login?private=secret"
            attach_login_diagnostics(page, path)
            handlers = {call.args[0]: call.args[1] for call in page.on.call_args_list}
            handlers["requestfailed"](SimpleNamespace(url="https://x.com/private/account?password=secret"))
            handlers["response"](SimpleNamespace(url="https://api.x.com/login?token=secret", status=403))
            handlers["pageerror"](Exception("password=secret"))
            content = path.read_text()
            self.assertNotIn("secret", content)
            self.assertNotIn("/login", content)
            self.assertNotIn("/private", content)
            self.assertEqual(len(json.loads(content)), 3)
    def test_installed_browser_selection_has_no_bundled_fallback(self):
        with patch("background_poster.Path.is_file", return_value=True):
            self.assertEqual(browser_executable("auto"), "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser")
            self.assertEqual(browser_executable("chrome"), "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
        with patch("background_poster.Path.is_file", return_value=False):
            with self.assertRaisesRegex(RuntimeError, "No fallback"):
                browser_executable("brave")
        with self.assertRaisesRegex(RuntimeError, "not Arc"):
            browser_executable("arc")

    def test_pending_blocks_restart_and_verified_counts_toward_cap(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "posts.sqlite3"
            attempt = reserve_attempt(database, "first", 1)
            with self.assertRaisesRegex(RuntimeError, "unverified"):
                reserve_attempt(database, "second", 1)
            finish_attempt(database, attempt, "verified", "https://x.com/me/status/123")
            with self.assertRaisesRegex(RuntimeError, "Daily post limit"):
                reserve_attempt(database, "second", 1)
            with self.assertRaisesRegex(RuntimeError, "Duplicate"):
                reserve_attempt(database, "first", 3)

    def test_status_timestamp_refresh_and_lowercase_handles(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "status.json"
            path.write_text('{"updated_at":"old","state":"posted"}')
            write_status(str(path), {"state": "failed_or_unverified"})
            payload = json.loads(path.read_text())
            self.assertNotEqual(payload["updated_at"], "old")
            self.assertEqual(payload["state"], "failed_or_unverified")
            self.assertEqual(sanitize_tweet_text("HEY @HeyClicky #promo"), "hey @heyclicky")

    def test_permalink_requires_exact_text_and_own_author(self):
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                page.set_content('''
                <article data-testid="tweet">
                  <div data-testid="tweetText">building cruise control</div>
                  <a href="/someone_else/status/100"><time>now</time></a>
                </article>
                <article data-testid="tweet">
                  <div data-testid="tweetText">building cruise control</div>
                  <a href="/me/status/101"><time>now</time></a>
                </article>''')
                self.assertEqual(matching_post(page, "building cruise control", "/me"), "https://x.com/me/status/101")
                self.assertIsNone(matching_post(page, "building cruise", "/me"))
            finally:
                browser.close()

    def test_submission_without_permalink_never_reports_success(self):
        with tempfile.TemporaryDirectory() as directory:
            args = SimpleNamespace(status_file=str(Path(directory) / "status.json"), user_data_dir=str(Path(directory) / "profile"), login=False)
            context = MagicMock()
            page = context.pages.__getitem__.return_value
            button = page.get_by_role.return_value.filter.return_value.last.locator.return_value
            button.is_enabled.return_value = True
            playwright = MagicMock()
            playwright.chromium.launch_persistent_context.return_value = context
            report = MagicMock()
            with patch("background_poster.sync_playwright") as runner, patch("background_poster.require_login", return_value="/me"), patch("background_poster.matching_post", return_value=None), patch("background_poster.time.sleep"):
                runner.return_value.__enter__.return_value = playwright
                with self.assertRaisesRegex(RuntimeError, "no exact published post"):
                    post_in_background(args, "building cruise control", {"posts_per_day": 3}, report)
            states = [call.args[1].get("state") for call in report.call_args_list]
            self.assertIn("failed_or_unverified", states)
            self.assertNotIn("verified_published", states)
            context.close.assert_called_once()
            launch = playwright.chromium.launch_persistent_context.call_args
            self.assertIn("/Applications/", launch.kwargs["executable_path"])
            self.assertTrue(launch.kwargs["headless"])
            self.assertTrue(launch.kwargs["chromium_sandbox"])

    def test_success_requires_profile_and_permalink_verification(self):
        with tempfile.TemporaryDirectory() as directory:
            args = SimpleNamespace(status_file=str(Path(directory) / "status.json"), user_data_dir=str(Path(directory) / "profile"), login=False)
            context = MagicMock()
            page = context.pages.__getitem__.return_value
            page.get_by_role.return_value.filter.return_value.last.locator.return_value.is_enabled.return_value = True
            playwright = MagicMock()
            playwright.chromium.launch_persistent_context.return_value = context
            report = MagicMock()
            url = "https://x.com/me/status/101"
            with patch("background_poster.sync_playwright") as runner, patch("background_poster.require_login", return_value="/me"), patch("background_poster.matching_post", side_effect=[None, url, url]):
                runner.return_value.__enter__.return_value = playwright
                self.assertEqual(post_in_background(args, "building cruise control", {"posts_per_day": 1}, report), url)
            verified = report.call_args.args[1]
            self.assertEqual(verified["state"], "verified_published")
            self.assertEqual(verified["post_url"], url)
            self.assertRegex(verified["evidence_after"], r"^/evidence/[a-f0-9]{32}/after.png$")
            with self.assertRaisesRegex(RuntimeError, "Daily post limit"):
                reserve_attempt(Path(directory) / "posts.sqlite3", "another", 1)


if __name__ == "__main__":
    unittest.main()
