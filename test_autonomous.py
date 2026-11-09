import argparse
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from approval_queue import create_request, decide_request, read_request
from arc_poster import ControlSession, UserInputInterruption
from organic_replies import ReplyIdentityError
from twitter_agent import draft_from_candidates, read_project_summary, reply_to_candidates, run_once


class AutonomousTests(unittest.TestCase):
    def arguments(self, directory, **overrides):
        return argparse.Namespace(**{
            "settings_file": str(Path(directory) / "settings.json"),
            "status_file": str(Path(directory) / "status.json"),
            "login": False, "reply_to": None, "repo": None, "post_mode": "arc",
            "discover_reply": False, "dry_run": False, "model": "local", "count": 1,
            "require_review": False, **overrides})

    def test_default_project_identity_is_dumdev(self):
        self.assertEqual(read_project_summary(None)["name"], "DumDev")
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "README.md").write_text("# DumDev — local X bot\\n")
            self.assertEqual(read_project_summary(directory)["name"], "DumDev")

    def test_saved_dumdev_profile_is_used_without_wizard_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            args = self.arguments(directory, post_mode="playwright")
            profile = {
                "account_handle": "demo_account", "profile_name": "DumDev",
                "profile_description": "A local Mac X bot, separate from Cruise Control.",
                "attitude": "specific and honest", "approved_webpage": "https://tanny.kolektivkrog.si/dumdev",
            }
            Path(args.settings_file).write_text(json.dumps(profile))
            seen = []
            with patch("twitter_agent.read_project_summary") as project, \
                 patch("twitter_agent.generate_tweets", side_effect=lambda summary, **kw: (seen.append(summary) or ["a factual post"])), \
                 patch("twitter_agent.generate_campaign_plan", return_value={"queued_tweets": ["a factual post"]}), \
                 patch("twitter_agent.post_in_background", return_value="verified"):
                self.assertEqual(run_once(args), 0)
            project.assert_not_called()
            self.assertEqual(seen[0]["name"], "DumDev")
            self.assertTrue(seen[0]["user_configured"])
            self.assertEqual(seen[0]["account_handle"], "demo_account")

    def test_posts_publish_without_approval_queue(self):
        for mode in ("arc", "playwright"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                args = self.arguments(directory, post_mode=mode)
                with patch("twitter_agent.generate_tweets", return_value=["a factual post"]), \
                     patch("twitter_agent.generate_campaign_plan", return_value={"queued_tweets": ["a factual post"]}), \
                     patch("twitter_agent.post_in_arc", return_value="verified") as arc, \
                     patch("twitter_agent.post_in_background", return_value="verified") as background, \
                     patch("twitter_agent.await_approval") as approval:
                    self.assertEqual(run_once(args), 0)
                    approval.assert_not_called()
                    (arc if mode == "arc" else background).assert_called_once()
                    self.assertIsNone(read_request(args.status_file))

    def test_replies_discover_read_and_publish_without_draft_gate(self):
        target = {"url": "https://x.com/person/status/1", "text": "urban gardening soil question",
                  "discovery_source": "recent", "promoted": False, "quoted": False}
        choice = {"target": target, "text": "which soil mix did you use?", "reason": "specific question"}
        with tempfile.TemporaryDirectory() as directory:
            args = self.arguments(directory, discover_reply=True)
            with patch("twitter_agent.draft_contextual_reply", return_value=choice), \
                 patch("twitter_agent.post_in_arc", side_effect=[[target], [], target, "verified"]) as arc, \
                 patch("twitter_agent.await_approval") as approval:
                self.assertEqual(run_once(args), 0)
                self.assertEqual(arc.call_count, 4)
                self.assertEqual(arc.call_args_list[1].kwargs, {"scan_threads": [target]})
                self.assertEqual(arc.call_args.kwargs, {"reply_target": target})
                self.assertEqual(arc.call_args.args[1], choice["text"])
                approval.assert_not_called()

    def test_identity_rejection_skips_the_target_without_regenerating(self):
        candidate = {"url": "https://x.com/person/status/identity", "text": "thanks for praising heyclicky"}
        rejection = "Contextual reply risks accepting praise or credit for HeyClicky."
        with tempfile.TemporaryDirectory() as directory:
            args = self.arguments(directory)
            with patch("twitter_agent.draft_contextual_reply", side_effect=ReplyIdentityError(rejection)) as draft:
                self.assertIsNone(draft_from_candidates(args, [candidate], {}, {}))
            self.assertEqual(draft.call_count, 1)
            status = json.loads(Path(args.status_file).read_text())
            self.assertEqual(status["state"], "draft_retry")
            self.assertEqual(status["candidate_url"], candidate["url"])
            self.assertEqual(status["error"], rejection)
            self.assertEqual(status["draft_attempt"], 1)

    def test_identity_rejection_moves_to_the_next_cached_candidate(self):
        first = {"url": "https://x.com/person/status/first", "text": "praise for heyclicky"}
        second = {"url": "https://x.com/person/status/second", "text": "a useful product question"}
        choice = {"target": second, "text": "which part of the workflow helped most?", "reason": "specific question"}
        with tempfile.TemporaryDirectory() as directory:
            args = self.arguments(directory, discover_reply=True)
            with patch("twitter_agent.draft_contextual_reply", side_effect=[
                    ReplyIdentityError("identity mismatch"), choice, choice]) as draft, \
                 patch("twitter_agent.published_text", return_value=False), \
                 patch("twitter_agent.write_status", wraps=__import__("twitter_agent").write_status):
                def arc_action(text, **kwargs):
                    if "inspect_target" in kwargs:
                        return kwargs["inspect_target"]
                    if "reply_target" in kwargs:
                        return "verified"
                    return []
                reply_to_candidates(args, [first, second], {}, {}, arc_action)
            self.assertEqual(draft.call_count, 3)
            self.assertEqual(draft.call_args_list[1].args[0], [second])
            self.assertEqual(draft.call_args_list[2].args[0], [second])

    def test_saved_draft_resumes_automatically_but_rejection_is_respected(self):
        for rejected in (False, True):
            with self.subTest(rejected=rejected), tempfile.TemporaryDirectory() as directory:
                args = self.arguments(directory)
                request = create_request(args.status_file, "post", "a saved factual draft")
                if rejected:
                    decide_request(args.status_file, request["id"], "reject")
                with patch("twitter_agent.post_in_arc", return_value="verified") as arc, \
                     patch("twitter_agent.time.sleep") as sleep:
                    self.assertEqual(run_once(args), 0)
                    self.assertEqual(arc.call_count, 0 if rejected else 1)
                    sleep.assert_not_called()
                self.assertIsNone(read_request(args.status_file))

    def test_manual_review_remains_opt_in(self):
        with tempfile.TemporaryDirectory() as directory:
            args = self.arguments(directory, require_review=True)
            with patch("twitter_agent.generate_tweets", return_value=["a factual post"]), \
                 patch("twitter_agent.await_approval") as approval, \
                 patch("twitter_agent.post_in_arc") as arc:
                run_once(args)
                approval.assert_called_once()
                arc.assert_not_called()

    def test_dry_run_never_publishes_or_consumes_saved_draft(self):
        with tempfile.TemporaryDirectory() as directory:
            args = self.arguments(directory, dry_run=True)
            request = create_request(args.status_file, "post", "saved draft")
            with patch("twitter_agent.generate_tweets", return_value=["a factual post"]), \
                 patch("twitter_agent.post_in_arc") as arc, \
                 patch("twitter_agent.await_approval") as approval:
                run_once(args)
                arc.assert_not_called()
                approval.assert_not_called()
            self.assertEqual(read_request(args.status_file)["id"], request["id"])
            self.assertIsNone(read_request(args.status_file)["decision"])
            self.assertEqual(json.loads(Path(args.status_file).read_text())["state"], "dry_run_complete")


class ControlSessionTests(unittest.TestCase):
    def test_one_countdown_and_one_helper_across_search_read_and_publish(self):
        session = ControlSession()
        identity = ("window", "tab", "/account")
        with patch("arc_poster.Takeover") as takeover:
            guard = takeover.return_value
            report = MagicMock()
            for _ in range(4):
                current, countdown = session.acquire(Path("helper"), identity)
                session.arm(current, countdown, "status", report)
                session.release(True)
            takeover.assert_called_once()
            self.assertEqual(guard.wait.call_count, 5)
            self.assertEqual(guard.arm.call_count, 4)
            self.assertEqual(sum(call.args[0] == "IDLE" for call in guard.message.call_args_list), 4)
            self.assertEqual(sum(call.args[1]["state"] == "takeover_countdown"
                                 for call in report.call_args_list), 1)
            guard.close.assert_not_called()
            session.close()
            guard.close.assert_called_once_with(True, read_only=True)

    def test_failed_or_interrupted_action_requires_fresh_countdown(self):
        session = ControlSession()
        with patch("arc_poster.Takeover") as takeover:
            guard, countdown = session.acquire(Path("helper"), ("w", "t", "/account"))
            session.arm(guard, countdown, "status", MagicMock())
            session.release(False)
            guard.close.assert_called_once_with(False, read_only=True)
            _, countdown = session.acquire(Path("helper"), ("w", "t", "/account"))
            self.assertTrue(countdown)
            self.assertEqual(takeover.call_count, 2)
            session.close()

    def test_changed_tab_or_account_is_never_silently_adopted(self):
        for changed in [("other", "t", "/account"), ("w", "other", "/account"), ("w", "t", "/other")]:
            with self.subTest(changed=changed), patch("arc_poster.Takeover"):
                session = ControlSession()
                session.acquire(Path("helper"), ("w", "t", "/account"))
                with self.assertRaisesRegex(RuntimeError, "changed"):
                    session.acquire(Path("helper"), changed)
                self.assertIsNone(session.guard)

    def test_escape_and_input_events_are_not_ignored_between_actions(self):
        with patch("arc_poster.Takeover"):
            session = ControlSession()
            guard, _ = session.acquire(Path("helper"), ("w", "t", "/account"))
            session.release(True)
            guard.check.side_effect = UserInputInterruption("observed input")
            with self.assertRaises(UserInputInterruption):
                session.acquire(Path("helper"), ("w", "t", "/account"))
            session.close(False)


if __name__ == "__main__":
    unittest.main()
