import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from activity_history import activity_summary
from approval_queue import create_request, decide_request, read_request
from twitter_agent import await_approval, write_status


class ApprovalQueueTests(unittest.TestCase):
    def test_exact_post_requires_explicit_decision(self):
        with tempfile.TemporaryDirectory() as directory:
            status = str(Path(directory) / "status.json")
            sent = []

            def approve_on_poll(_seconds):
                request = read_request(status)
                self.assertEqual(request["text"], "the exact post text")
                self.assertTrue(decide_request(status, request["id"], "approve"))

            with patch("twitter_agent.time.sleep", side_effect=approve_on_poll):
                result = await_approval(SimpleNamespace(status_file=status),
                                        "the exact post text", "post", None,
                                        lambda text, reply_target=None: sent.append((text, reply_target)))

            self.assertTrue(result)
            self.assertEqual(sent, [("the exact post text", None)])
            self.assertIsNone(read_request(status))
            state = json.loads(Path(status).read_text())
            self.assertEqual(state["state"], "approval_granted")

    def test_rejection_never_calls_publisher(self):
        with tempfile.TemporaryDirectory() as directory:
            status = str(Path(directory) / "status.json")
            sent = []

            def reject_on_poll(_seconds):
                request = read_request(status)
                self.assertTrue(decide_request(status, request["id"], "reject"))

            with patch("twitter_agent.time.sleep", side_effect=reject_on_poll):
                result = await_approval(SimpleNamespace(status_file=status),
                                        "reply draft", "reply",
                                        {"url": "https://x.com/user/status/123", "text": "source"},
                                        lambda text, reply_target=None: sent.append(text))

            self.assertFalse(result)
            self.assertEqual(sent, [])
            self.assertEqual(json.loads(Path(status).read_text())["state"], "approval_rejected")

    def test_waiting_approval_is_recent_activity_without_heartbeat_spam(self):
        with tempfile.TemporaryDirectory() as directory:
            status = str(Path(directory) / "status.json")
            write_status(status, {"state": "awaiting_approval", "note": "Review this exact draft."})
            write_status(status, {"state": "awaiting_approval", "note": "Review this exact draft."})
            events = activity_summary(Path(directory) / "posts.sqlite3")["recent_events"]
            awaiting = [event for event in events if event["state"] == "awaiting_approval"]
            self.assertEqual(len(awaiting), 1)

    def test_published_pending_draft_is_reconciled_without_user_approval(self):
        with tempfile.TemporaryDirectory() as directory:
            status = str(Path(directory) / "status.json")
            database = Path(directory) / "posts.sqlite3"
            text = "this exact post was already published"
            with sqlite3.connect(database) as db:
                db.execute("CREATE TABLE posts (text TEXT, state TEXT)")
                db.execute("INSERT INTO posts(text, state) VALUES (?, 'verified')", (text,))
            request = create_request(status, "post", text)
            sent = []

            result = await_approval(SimpleNamespace(status_file=status), None, None, None,
                                    lambda approved_text, reply_target=None: sent.append(approved_text))

            self.assertTrue(result)
            self.assertEqual(sent, [])
            self.assertIsNone(read_request(status))
            state = json.loads(Path(status).read_text())
            self.assertEqual(state["state"], "approval_already_verified")
            self.assertIsNone(state.get("pending_approval"))

    def test_newly_generated_duplicate_is_not_queued_for_approval(self):
        with tempfile.TemporaryDirectory() as directory:
            status = str(Path(directory) / "status.json")
            database = Path(directory) / "posts.sqlite3"
            text = "this exact post was already published"
            with sqlite3.connect(database) as db:
                db.execute("CREATE TABLE posts (text TEXT, state TEXT)")
                db.execute("INSERT INTO posts(text, state) VALUES (?, 'verified')", (text,))

            result = await_approval(SimpleNamespace(status_file=status), text, "post", None,
                                    lambda approved_text, reply_target=None: self.fail("duplicate must not be published"))

            self.assertTrue(result)
            self.assertIsNone(read_request(status))
            self.assertEqual(json.loads(Path(status).read_text())["state"], "approval_already_verified")

    def test_request_id_is_single_use_and_draft_is_not_replaceable(self):
        with tempfile.TemporaryDirectory() as directory:
            status = str(Path(directory) / "status.json")
            first = create_request(status, "post", "first draft")
            second = create_request(status, "post", "different draft")
            self.assertEqual(first["id"], second["id"])
            self.assertEqual(second["text"], "first draft")
            self.assertFalse(decide_request(status, "0" * 32, "approve"))
            self.assertTrue(decide_request(status, first["id"], "reject"))
            self.assertFalse(decide_request(status, first["id"], "approve"))


if __name__ == "__main__":
    unittest.main()
