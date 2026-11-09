import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from approval_queue import create_request, decide_request, read_request
import monitor_server
import twitter_agent


class ControlApprovalTests(unittest.TestCase):
    def test_control_and_draft_decisions_are_independent(self):
        with tempfile.TemporaryDirectory() as directory:
            status = str(Path(directory) / "status.json")
            draft = create_request(status, "post", "exact draft")
            control = create_request(status, "control", "drive Arc", channel="control")
            self.assertFalse(decide_request(status, draft["id"], "approve", channel="control"))
            self.assertTrue(decide_request(status, control["id"], "approve", channel="control"))
            self.assertIsNone(read_request(status)["decision"])
            self.assertEqual(read_request(status, channel="control")["decision"], "approve")

    def test_restart_requires_fresh_permission_without_discarding_draft(self):
        with tempfile.TemporaryDirectory() as directory:
            status = str(Path(directory) / "status.json")
            old = create_request(status, "control", "old permission", channel="control")
            decide_request(status, old["id"], "approve", channel="control")
            draft = create_request(status, "post", "saved draft")

            def approve(_seconds):
                current = read_request(status, channel="control")
                self.assertNotEqual(current["id"], old["id"])
                self.assertIsNone(current["decision"])
                self.assertEqual(json.loads(Path(status).read_text())["state"], "awaiting_control_approval")
                decide_request(status, current["id"], "approve", channel="control")

            with patch("twitter_agent.time.sleep", side_effect=approve):
                self.assertTrue(twitter_agent.await_control_approval(
                    type("Args", (), {"status_file": status})()))
            self.assertEqual(read_request(status)["id"], draft["id"])
            self.assertIsNone(read_request(status, channel="control"))

    def test_denial_stops_before_any_cycle_or_reconciliation(self):
        with tempfile.TemporaryDirectory() as directory:
            status = str(Path(directory) / "status.json")

            def reject(_seconds):
                request = read_request(status, channel="control")
                decide_request(status, request["id"], "reject", channel="control")

            with patch.object(sys, "argv", ["agent", "--post-mode", "arc", "--campaign", "--loop",
                                           "--require-control-approval", "--status-file", status]), \
                 patch("twitter_agent.load_settings", return_value={}), \
                 patch("twitter_agent.save_settings"), \
                 patch("twitter_agent.time.sleep", side_effect=reject), \
                 patch("twitter_agent.post_in_arc") as arc, \
                 patch("twitter_agent.run_once") as cycle, \
                 patch("twitter_agent.pending_submission") as pending:
                self.assertEqual(twitter_agent.main(), 2)
                arc.assert_not_called()
                cycle.assert_not_called()
                pending.assert_not_called()
            self.assertEqual(json.loads(Path(status).read_text())["state"], "control_rejected")

    def test_waiting_control_approval_does_not_spam_activity(self):
        from activity_history import activity_summary
        with tempfile.TemporaryDirectory() as directory:
            status = str(Path(directory) / "status.json")
            for _ in range(3):
                twitter_agent.write_status(status, {"state": "awaiting_control_approval"})
            events = activity_summary(Path(directory) / "posts.sqlite3")["recent_events"]
            self.assertEqual(len([e for e in events if e["state"] == "awaiting_control_approval"]), 1)

    def test_saved_draft_resumes_even_when_campaign_has_no_due_action(self):
        class EndTest(BaseException):
            pass

        with tempfile.TemporaryDirectory() as directory:
            status = str(Path(directory) / "status.json")
            create_request(status, "reply", "saved reply",
                           {"url": "https://x.com/person/status/123", "text": "source"})
            with patch.object(sys, "argv", ["agent", "--post-mode", "arc", "--campaign", "--loop",
                                           "--status-file", status]), \
                 patch("twitter_agent.load_settings", return_value={}), \
                 patch("twitter_agent.save_settings"), \
                 patch("twitter_agent.pending_submission", return_value=False), \
                 patch("twitter_agent.campaign_action", return_value=(None, 864, {})), \
                 patch("twitter_agent.record_discovery_cycle"), \
                 patch("twitter_agent.run_once", side_effect=EndTest) as cycle:
                with self.assertRaises(EndTest):
                    twitter_agent.main()
                self.assertTrue(cycle.call_args.args[0].discover_reply)


class ControlEndpointTests(unittest.TestCase):
    def test_local_control_endpoint_requires_matching_id_and_preserves_draft(self):
        with tempfile.TemporaryDirectory() as directory:
            status = Path(directory) / "status.json"
            draft = create_request(status, "post", "saved post")
            control = create_request(status, "control", "drive Arc", channel="control")
            with patch.object(monitor_server, "STATUS_PATH", status):
                server = ThreadingHTTPServer(("127.0.0.1", 0), monitor_server.Handler)
                thread = threading.Thread(target=server.serve_forever)
                thread.start()
                base = f"http://127.0.0.1:{server.server_port}"
                try:
                    with urlopen(base + "/control") as response:
                        self.assertEqual(json.load(response)["pending_approval"]["id"], control["id"])
                    for request_id, origin, code in [
                        (control["id"], "https://example.com", 403),
                        (draft["id"], "http://127.0.0.1:8765", 409),
                    ]:
                        request = Request(base + "/control",
                                          data=json.dumps({"id": request_id, "decision": "approve"}).encode(),
                                          headers={"Content-Type": "application/json", "Origin": origin})
                        with self.assertRaises(HTTPError) as error:
                            urlopen(request)
                        self.assertEqual(error.exception.code, code)
                        error.exception.close()
                    request = Request(base + "/control",
                                      data=json.dumps({"id": control["id"], "decision": "approve"}).encode(),
                                      headers={"Content-Type": "application/json"})
                    with urlopen(request) as response:
                        self.assertTrue(json.load(response)["ok"])
                    self.assertIsNone(read_request(status)["decision"])
                    with urlopen(base + "/approval") as response:
                        self.assertEqual(json.load(response)["pending_approval"]["id"], draft["id"])
                finally:
                    server.shutdown()
                    thread.join()
                    server.server_close()


if __name__ == "__main__":
    unittest.main()
