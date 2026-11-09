import json
import os
import sys
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from campaign_health import heartbeat_expired, recovery_delay, supervise
from twitter_agent import sleep_with_heartbeat, write_status


class HealthTests(unittest.TestCase):
    def test_heartbeat_detects_stalled_worker_not_scheduled_sleep(self):
        status = {"worker_pid": 42, "updated_at": "2026-11-08T10:00:00Z"}
        from datetime import datetime
        stamp = datetime.fromisoformat(status["updated_at"].replace("Z", "+00:00")).timestamp()
        self.assertFalse(heartbeat_expired(status, 42, stamp + 100, stamp - 400))
        self.assertTrue(heartbeat_expired(status, 42, stamp + 301, stamp - 400))
        self.assertTrue(heartbeat_expired(status, 43, stamp + 301, stamp - 400))
        self.assertFalse(heartbeat_expired({}, 42, stamp + 10, stamp))

    def test_bounded_backoff(self):
        self.assertEqual([recovery_delay(i) for i in range(1, 5)], [30, 60, 120, None])

    def test_sleep_refreshes_heartbeat_and_shows_remaining_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            status = str(Path(directory) / "status.json")
            with patch("twitter_agent.time.monotonic", side_effect=[0, 0, 2, 4, 5]), \
                 patch("twitter_agent.time.sleep") as pause:
                self.assertFalse(sleep_with_heartbeat(status, 5,
                                                      {"state": "recovery_wait", "error": "temporary"},
                                                      interval=2))
            self.assertEqual([call.args[0] for call in pause.call_args_list], [2, 2, 1])
            saved = json.loads(Path(status).read_text())
            self.assertEqual(saved["state"], "recovery_wait")
            self.assertEqual(saved["sleep_seconds"], 1)
            self.assertIn("heartbeat_at", saved)

    def test_sleep_bump_flag_interrupts_without_waiting(self):
        with tempfile.TemporaryDirectory() as directory:
            status = str(Path(directory) / "status.json")
            bump = Path(directory) / "bump.flag"
            bump.write_text("run now")
            with patch("twitter_agent.time.sleep") as pause:
                self.assertTrue(sleep_with_heartbeat(status, 300, {"state": "sleeping"}, str(bump)))
            self.assertFalse(bump.exists())
            pause.assert_not_called()
            self.assertFalse(Path(status).exists())

    def test_status_preserves_outcome_and_worker_heartbeat_not_old_post_success(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "status.json")
            write_status(path, {"state": "verified_published", "post_url": "https://x.com/owner/status/1"})
            write_status(path, {"state": "no_relevant_reply", "note": "No useful match"})
            write_status(path, {"state": "campaign_waiting", "takeover_step": None,
                                "next_cycle_at": "2026-11-09T10:00:00Z"})
            s = json.loads(Path(path).read_text())
            self.assertEqual(s["last_cycle"]["state"], "no_relevant_reply")
            self.assertNotIn("post_url", s["last_cycle"])
            self.assertEqual(s["worker_pid"], os.getpid())
            self.assertEqual(s["heartbeat_at"], s["updated_at"])
            self.assertEqual(s["next_retry_at"], "2026-11-09T10:00:00Z")

    def test_recovery_status_persists_last_error_and_next_retry_then_clears_due_time(self):
        from datetime import datetime, timezone
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "status.json")
            write_status(path, {"state": "sleeping", "sleep_seconds": 60})
            sleeping = json.loads(Path(path).read_text())
            now = datetime.fromisoformat(sleeping["updated_at"].replace("Z", "+00:00"))
            retry = datetime.fromisoformat(sleeping["next_retry_at"].replace("Z", "+00:00"))
            self.assertGreaterEqual((retry - now).total_seconds(), 59)
            self.assertLessEqual((retry - now).total_seconds(), 61)
            write_status(path, {"state": "recovery_wait", "error": "temporary Arc timeout",
                                "sleep_seconds": 60})
            waiting = json.loads(Path(path).read_text())
            now = datetime.fromisoformat(waiting["updated_at"].replace("Z", "+00:00"))
            retry = datetime.fromisoformat(waiting["next_retry_at"].replace("Z", "+00:00"))
            self.assertGreaterEqual((retry - now).total_seconds(), 59)
            self.assertLessEqual((retry - now).total_seconds(), 61)
            self.assertEqual(waiting["last_error"]["message"], "temporary Arc timeout")
            write_status(path, {"state": "needs_attention", "error": "browser attention required"})
            stopped = json.loads(Path(path).read_text())
            self.assertNotIn("next_retry_at", stopped)
            self.assertEqual(stopped["last_error"]["message"], "browser attention required")
            self.assertEqual(stopped["heartbeat_at"], stopped["updated_at"])

    def test_supervisor_respects_deliberate_stop(self):
        for code in (0, 2, 130):
            worker = MagicMock()
            worker.poll.return_value = code
            worker.wait.return_value = code
            with patch("campaign_health.subprocess.Popen", return_value=worker) as launch:
                self.assertEqual(supervise(["worker"], "unused", MagicMock()), code)
                launch.assert_called_once()

    def test_crash_relaunches_once_then_stops_on_unsafe_exit(self):
        crashed = MagicMock()
        crashed.poll.return_value = 1
        crashed.wait.return_value = 1
        stopped = MagicMock()
        stopped.poll.return_value = 2
        stopped.wait.return_value = 2
        report = MagicMock()
        with patch("campaign_health.subprocess.Popen", side_effect=[crashed, stopped]) as launch, \
             patch("campaign_health.time.monotonic", side_effect=[0, 31]):
            self.assertEqual(supervise(["worker"], "unused", report), 2)
            self.assertEqual(launch.call_count, 2)
        self.assertEqual(report.call_args.args[1]["state"], "relaunch_wait")

    def test_real_child_crash_is_relaunched_but_unsafe_exit_is_not(self):
        with tempfile.TemporaryDirectory() as directory:
            counter = Path(directory) / "counter"
            code = ("from pathlib import Path; import sys; p=Path(sys.argv[1]); "
                    "n=int(p.read_text())+1 if p.exists() else 1; p.write_text(str(n)); "
                    "sys.exit(1 if n==1 else 2)")
            with patch("campaign_health.recovery_delay", return_value=0):
                self.assertEqual(supervise([sys.executable, "-c", code, str(counter)],
                                          str(Path(directory)/"status.json"), MagicMock()), 2)
            self.assertEqual(counter.read_text(), "2")

    def test_campaign_retries_transient_failure_then_stops_on_permission_error(self):
        import twitter_agent
        from arc_poster import TransientArcError
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory)/"status.json")
            bump_path = str(Path(directory)/"bump-now.txt")
            settings = {"posts_per_day":3,"replies_per_day":100}
            with patch.object(sys, "argv", ["agent", "--post-mode", "arc", "--campaign", "--loop",
                                           "--status-file", path]), \
                 patch("twitter_agent.DEFAULT_BUMP_PATH", bump_path), \
                 patch("twitter_agent.load_settings", return_value=settings), \
                 patch("twitter_agent.save_settings"), \
                 patch("twitter_agent.pending_submission", return_value=False), \
                 patch("twitter_agent.campaign_action", side_effect=[("post",0,{}),(None,864,{})]), \
                 patch("twitter_agent.run_once", side_effect=[TransientArcError("timeout"),
                                                              RuntimeError("permission required")]) as cycle, \
                 patch("twitter_agent.time.sleep") as sleep, \
                 patch("twitter_agent.time.monotonic", side_effect=[0, 0, 31]):
                self.assertEqual(twitter_agent.main(), 2)
                self.assertEqual(cycle.call_count, 2)
                sleep.assert_called_once_with(30)
            status = json.loads(Path(path).read_text())
            self.assertEqual(status["state"], "needs_attention")
            self.assertEqual(status["error"], "permission required")

    def test_pending_outcome_only_reconciles_and_never_runs_publication_cycle(self):
        import twitter_agent
        class EndTest(BaseException):
            pass
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory)/"status.json")
            with patch.object(sys, "argv", ["agent", "--post-mode", "arc", "--campaign", "--loop",
                                           "--status-file", path]), \
                 patch("twitter_agent.load_settings", return_value={}), \
                 patch("twitter_agent.save_settings"), \
                 patch("twitter_agent.pending_submission", return_value=True), \
                 patch("interrupt_retry.pending_submission", return_value=True), \
                 patch("twitter_agent.post_in_arc", side_effect=RuntimeError("Outcome cannot be reconciled")) as arc, \
                 patch("twitter_agent.run_once") as cycle, \
                 patch("twitter_agent.time.sleep", side_effect=EndTest):
                with self.assertRaises(EndTest):
                    twitter_agent.main()
                cycle.assert_not_called()
                self.assertTrue(arc.call_args.kwargs["reconcile"])
            self.assertEqual(json.loads(Path(path).read_text())["state"], "pending_reconciliation")


if __name__ == "__main__":
    unittest.main()
