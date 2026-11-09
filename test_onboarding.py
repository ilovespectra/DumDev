import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import MagicMock, patch
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from app import Application
from background_poster import LoginRequired, post_in_background
import monitor_server
from onboarding import (has_account_history, read_settings, setup_required,
                        validate_setup, verify_arc_account, write_settings)


def configuration(browser="arc"):
    return {"browser": browser, "account_handle": "garden_user", "profile_name": "Small Gardens",
            "profile_description": "I share practical urban gardening notes. I do not sell products.",
            "approved_webpage": "https://example.com/garden", "topics": ["urban gardening", "soil health"],
            "attitude": "Curious, warm, and specific. No hype.", "posts_per_day": 2, "replies_per_day": 5,
            "min_gap_hours": 0.5, "max_gap_hours": 2.5}


class ConfigurationTests(unittest.TestCase):
    def test_new_and_existing_config_detection(self):
        self.assertTrue(setup_required({}))
        self.assertFalse(setup_required({"account_handle": "existing_user"}))
        self.assertFalse(setup_required({"onboarding_completed": True}))
        self.assertTrue(setup_required({"account_handle": "existing_user", "setup_in_progress": True}))

    def test_validation_preserves_personal_context_and_posting_only_limits(self):
        value = configuration("brave")
        value["account_handle"] = "@garden_user"
        result = validate_setup(value)
        self.assertEqual(result["account_handle"], "garden_user")
        self.assertEqual(result["topics"], value["topics"])
        self.assertEqual(result["attitude"], value["attitude"])
        self.assertEqual(result["profile_description"], value["profile_description"])
        self.assertEqual(result["replies_per_day"], 0)
        self.assertFalse(result["first_day_no_sleep"])
        value["posts_per_day"] = 0
        self.assertEqual(validate_setup(value)["posts_per_day"], 0)

    def test_invalid_setup_is_explicitly_rejected(self):
        for key, value in [("browser", "safari"), ("account_handle", "https://x.com/person"),
                           ("topics", []), ("topics", ["x" * 81]), ("profile_description", ""),
                           ("attitude", ""), ("posts_per_day", -1), ("posts_per_day", True),
                           ("replies_per_day", 101), ("min_gap_hours", float("nan")),
                           ("approved_webpage", "http://example.com"), ("max_gap_hours", 0.25)]:
            with self.subTest(key=key, value=value):
                with self.assertRaises(ValueError):
                    validate_setup(configuration() | {key: value})

    def test_arc_verifies_signed_in_identity_not_viewed_profile(self):
        with patch("arc_poster.find_x_tab", return_value=("1", "2", "/garden_user")), \
             patch("arc_poster.run_script", return_value=json.dumps(
                 {"origin": "https://x.com", "profile": "/someone_else"})):
            with self.assertRaisesRegex(ValueError, "not signed in"):
                verify_arc_account("garden_user")
        with patch("arc_poster.find_x_tab", return_value=("1", "2", "/garden_user")), \
             patch("arc_poster.run_script", return_value=json.dumps(
                 {"origin": "https://x.com", "profile": "/Garden_User"})):
            self.assertEqual(verify_arc_account("garden_user"), "garden_user")


class ApplicationTests(unittest.TestCase):
    def test_new_install_never_starts_automation(self):
        with tempfile.TemporaryDirectory() as directory:
            application = Application(Path(directory))
            with patch("app.subprocess.Popen") as process:
                application.start_worker()
                process.assert_not_called()
            self.assertTrue(application.snapshot()["required"])

    def test_setup_persistence_and_completion_gate(self):
        with tempfile.TemporaryDirectory() as directory:
            application = Application(Path(directory))
            application.action("begin", {})
            with patch("app.BROWSERS", {"arc": ("Arc", str(application.root))}):
                application.action("save", configuration())
            settings = read_settings(application.settings_path)
            self.assertTrue(settings["setup_in_progress"])
            self.assertTrue(setup_required(settings))
            with self.assertRaisesRegex(ValueError, "Verify"):
                application.action("complete", {})
            with patch("app.verify_arc_account", return_value="garden_user"):
                application.action("verify", {})
                with patch("app.permission_status", return_value={"accessibility": True, "input": False, "screen": True}):
                    with self.assertRaisesRegex(ValueError, "Grant"):
                        application.action("complete", {})
                with patch("app.permission_status", return_value={"accessibility": True, "input": True, "screen": True}), \
                     patch("app.model_status", return_value={"ready": True}), \
                     patch.object(application, "start_worker") as start:
                    application.action("complete", {})
                    start.assert_called_once()
            saved = read_settings(application.settings_path)
            self.assertTrue(saved["onboarding_completed"])
            self.assertFalse(saved["setup_in_progress"])
            self.assertEqual(saved["profile_description"], configuration()["profile_description"])

    def test_existing_account_history_cannot_be_reassigned(self):
        from approval_queue import create_request
        with tempfile.TemporaryDirectory() as directory:
            application = Application(Path(directory))
            write_settings(application.settings_path, {"account_handle": "old_user"})
            create_request(application.logs / "status.json", "post", "draft for old_user")
            self.assertTrue(has_account_history(application.logs))
            application.action("begin", {})
            with self.assertRaisesRegex(ValueError, "another account"):
                application.action("save", configuration())
            self.assertEqual(read_settings(application.settings_path)["account_handle"], "old_user")

    def test_correct_worker_mode_and_account_config_are_wired(self):
        for browser in ("arc", "brave", "chrome"):
            with self.subTest(browser=browser), tempfile.TemporaryDirectory() as directory:
                application = Application(Path(directory))
                write_settings(application.settings_path, configuration(browser) | {"onboarding_completed": True})
                worker = MagicMock(pid=1234)
                with patch("app.subprocess.Popen", return_value=worker) as launch, \
                     patch("app.permission_status"):
                    application.start_worker()
                    command = launch.call_args.args[0]
                    self.assertIn("--campaign", command)
                    self.assertNotIn("--repo", command)
                    if browser == "arc":
                        self.assertNotIn("--require-control-approval", command)
                    else:
                        self.assertIn(browser, command)
                        self.assertIn(str(application.root / f"browser-profile-{browser}"), command)
                    worker.poll.return_value = 0
                    application.close()
                self.assertFalse((application.logs / "agent.pid").exists())

    def test_setup_context_reaches_both_reply_prompts_without_other_identity(self):
        from organic_replies import draft_contextual_reply
        settings = configuration()
        summary = {"name": settings["profile_name"], "description": settings["profile_description"],
                   "account_handle": settings["account_handle"], "attitude": settings["attitude"],
                   "user_configured": True}
        with patch("organic_replies.ollama.generate", side_effect=[
                {"response": '{"index":0,"reason":"garden topic"}'},
                {"response": '{"reply":"which soil mix worked best on your balcony?"}'}]) as model:
            result = draft_contextual_reply(
                [{"url": "https://x.com/person/status/1", "text": "Balcony gardening soil question"}], summary, "local")
            self.assertIsNotNone(result)
            for call in model.call_args_list:
                prompt = call.kwargs["prompt"]
                self.assertIn("garden_user", prompt)
                self.assertIn(settings["attitude"], prompt)
                self.assertIn(settings["profile_description"], prompt)
                self.assertIn(settings["account_handle"], prompt)
                self.assertNotIn("Cruise Control", prompt)

    def test_setup_context_reaches_original_post_prompt_without_legacy_facts(self):
        from twitter_agent import generate_tweets
        summary = {"name": "Small Gardens", "description": "Practical urban gardening notes.",
                   "user_configured": True}
        with patch("twitter_agent.ollama.generate",
                   return_value={"response": '{"tweets":["a smaller pot dries out faster in the sun"]}'}) as model:
            generate_tweets(summary, count=1, attitude="Direct, no hype.", topics=["urban gardening"])
            prompt = model.call_args.kwargs["prompt"]
            self.assertIn("urban gardening", prompt)
            self.assertIn("Direct, no hype.", prompt)
            self.assertIn(summary["description"], prompt)
            self.assertNotIn("heyclicky", prompt.lower())
            self.assertNotIn("cruise control", prompt.lower())

    def test_selected_topics_apply_to_conversation_context_without_legacy_product(self):
        from organic_replies import relevant_post
        root = {"url": "https://x.com/person/status/1", "text": "urban gardening soil question"}
        response = {"url": "https://x.com/other/status/2", "text": "which compost mix worked best on that balcony?"}
        self.assertTrue(relevant_post(root, "garden_user", topics=["urban gardening"]))
        self.assertTrue(relevant_post(response, "garden_user", root, topics=["urban gardening"]))
        self.assertFalse(relevant_post(response, "garden_user", topics=["urban gardening"]))
        self.assertFalse(relevant_post(response | {"promoted": True}, "garden_user", root,
                                       topics=["urban gardening"]))

    def test_background_wrong_account_cannot_submit(self):
        with tempfile.TemporaryDirectory() as directory:
            args = type("Args", (), {"status_file": str(Path(directory) / "status.json"),
                                     "user_data_dir": str(Path(directory) / "profile"), "login": False})()
            with patch("background_poster.sync_playwright") as runner, \
                 patch("background_poster.browser_executable", return_value="/Applications/Brave"), \
                 patch("background_poster.require_login", return_value="/wrong_user"), \
                 patch("background_poster.reserve_attempt") as reserve:
                with self.assertRaises(LoginRequired):
                    post_in_background(args, "a draft", configuration(), MagicMock())
                reserve.assert_not_called()


class SetupHTTPTests(unittest.TestCase):
    def test_errors_are_json_and_setup_requires_local_origin(self):
        application = MagicMock()
        application.action.side_effect = ValueError("Select an account first.")
        with patch.object(monitor_server, "APPLICATION", application):
            server = ThreadingHTTPServer(("127.0.0.1", 0), monitor_server.Handler)
            thread = threading.Thread(target=server.serve_forever)
            thread.start()
            try:
                for origin, expected in [("https://example.com", 403), ("http://127.0.0.1:8765", 409)]:
                    request = Request(f"http://127.0.0.1:{server.server_port}/setup/complete",
                                      data=b"{}", headers={"Origin": origin, "Content-Type": "application/json"})
                    with self.assertRaises(HTTPError) as error:
                        urlopen(request)
                    self.assertEqual(error.exception.code, expected)
                    self.assertIn("error", json.load(error.exception))
                    error.exception.close()
                application.action.assert_called_once()
            finally:
                server.shutdown()
                thread.join()
                server.server_close()


if __name__ == "__main__":
    unittest.main()
