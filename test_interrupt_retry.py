import unittest
from unittest.mock import MagicMock, patch
from pathlib import Path

from arc_poster import FocusInterruption, TransientArcError, UserInputInterruption, run_script
from interrupt_retry import retry_interrupted


class RetryTests(unittest.TestCase):
    def test_missing_active_arc_tab_is_transient_but_other_script_errors_are_not(self):
        process = MagicMock()
        process.returncode = 1
        process.poll.return_value = 1
        process.communicate.return_value = ("", "execution error: Arc got an error: Can’t get active tab of window 1. (-1728)\n")
        with patch("arc_poster.subprocess.Popen", return_value=process):
            with self.assertRaises(TransientArcError):
                run_script("read-only tab check")
            process.communicate.return_value = ("", "execution error: Arc tab changed (-1728)")
            with self.assertRaises(RuntimeError) as error:
                run_script("read-only tab check")
            self.assertNotIsInstance(error.exception, TransientArcError)

    def test_exact_foreground_error_pauses_and_retries_but_other_script_errors_do_not(self):
        process=MagicMock()
        process.returncode=1
        process.poll.return_value=1
        process.communicate.return_value=("", "184:211: execution error: Arc lost foreground focus (-2700)\n")
        with patch("arc_poster.subprocess.Popen",return_value=process):
            with self.assertRaises(FocusInterruption):
                run_script("guarded operation")
            process.communicate.return_value=("", "execution error: Arc tab changed (-2700)")
            with self.assertRaises(RuntimeError) as error:
                run_script("guarded operation")
            self.assertNotIsInstance(error.exception,UserInputInterruption)
        action=MagicMock(side_effect=[FocusInterruption("lost focus"),"verified"])
        with patch("interrupt_retry.pending_submission",return_value=False),patch("interrupt_retry.time.sleep") as sleep:
            self.assertEqual(retry_interrupted(action,Path("unused"),MagicMock(),"status"),"verified")
            sleep.assert_called_once_with(30)
        action=MagicMock(side_effect=FocusInterruption("lost focus"))
        with patch("interrupt_retry.pending_submission",return_value=True),patch("interrupt_retry.time.sleep") as sleep:
            with self.assertRaises(FocusInterruption):
                retry_interrupted(action,Path("unused"),MagicMock(),"status")
            sleep.assert_not_called()
        action.assert_called_once()

    def test_repeated_accidental_input_does_not_end_the_run(self):
        action = MagicMock(side_effect=[UserInputInterruption("mouse"),
                                       UserInputInterruption("mouse"), "verified"])
        report = MagicMock()
        with patch("interrupt_retry.pending_submission", return_value=False), patch("interrupt_retry.time.sleep") as sleep:
            self.assertEqual(retry_interrupted(action, Path("unused"), report, "status"), "verified")
            self.assertEqual(sleep.call_count, 2)
            self.assertEqual(report.call_args.args[1]["interruption_retries"], 2)
        self.assertEqual(action.call_count, 3)

    def test_mouse_interruption_retries_same_action_after_30_seconds(self):
        action = MagicMock(side_effect=[UserInputInterruption("mouse"), "verified"])
        report = MagicMock()
        with patch("interrupt_retry.pending_submission", return_value=False), patch("interrupt_retry.time.sleep") as sleep:
            self.assertEqual(retry_interrupted(action, Path("unused"), report, "status"), "verified")
            sleep.assert_called_once_with(30)
        self.assertEqual(action.call_count, 2)

    def test_possible_submission_never_retries(self):
        action = MagicMock(side_effect=UserInputInterruption("mouse"))
        with patch("interrupt_retry.pending_submission", return_value=True), patch("interrupt_retry.time.sleep") as sleep:
            with self.assertRaises(UserInputInterruption):
                retry_interrupted(action, Path("unused"), MagicMock(), "status")
            sleep.assert_not_called()
        action.assert_called_once()

    def test_other_errors_are_not_retried(self):
        for message in ["Escape pressed", "input monitor disabled", "permission required"]:
            action = MagicMock(side_effect=RuntimeError(message))
            with self.assertRaises(RuntimeError):
                retry_interrupted(action, Path("unused"), MagicMock(), "status")
            action.assert_called_once()
