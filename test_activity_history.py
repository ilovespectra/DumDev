import argparse
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from activity_history import activity_summary, recently_skipped
from arc_poster import ReplyTargetUnavailable
from organic_replies import invitation_kind
from twitter_agent import reply_to_candidates, write_status


class ActivityTests(unittest.TestCase):
    def test_invalid_draft_retries_cached_candidates_without_more_browser_actions(self):
        args=argparse.Namespace(status_file="unused",model="local")
        target={"url":"https://x.com/a/status/1","text":"heyclicky task"}
        choice={"target":target,"text":"which task?","reason":"specific"}
        action=MagicMock(side_effect=[target,"published"])
        with patch("twitter_agent.draft_contextual_reply",side_effect=[
                ValueError("invalid emoji"),choice,ValueError("invalid link"),choice]) as draft, \
             patch("twitter_agent.published_text",return_value=False),patch("twitter_agent.write_status"):
            reply_to_candidates(args,[target],{}, {},action)
        self.assertEqual(action.call_count,2)
        self.assertEqual(draft.call_count,4)
        self.assertTrue(all(c.args[0]==[target] for c in draft.call_args_list))
        self.assertEqual(draft.call_args_list[1].kwargs["draft_feedback"],"invalid emoji")
        self.assertEqual(draft.call_args_list[3].kwargs["draft_feedback"],"invalid link")

    def test_exhausted_invalid_drafts_do_not_search_or_submit(self):
        args=argparse.Namespace(status_file="unused",model="local")
        target={"url":"https://x.com/a/status/1","text":"heyclicky task"}
        action=MagicMock()
        with patch("twitter_agent.draft_contextual_reply",side_effect=ValueError("invalid output")) as draft, \
             patch("twitter_agent.write_status"):
            reply_to_candidates(args,[target],{}, {},action)
        self.assertEqual(draft.call_count,3)
        action.assert_not_called()

    def test_invalid_first_candidate_does_not_starve_valid_second_candidate(self):
        args = argparse.Namespace(status_file="unused", model="local")
        a = {"url": "https://x.com/a/status/1", "text": "heyclicky praise"}
        b = {"url": "https://x.com/b/status/2", "text": "heyclicky workflow question"}
        choice = {"target": b, "text": "which workflow?", "reason": "specific"}
        action = MagicMock(side_effect=[b, "published"])
        with patch("twitter_agent.draft_contextual_reply", side_effect=[
                ValueError("identity"), ValueError("identity"), ValueError("identity"),
                choice, choice]) as draft, \
             patch("twitter_agent.published_text", return_value=False), \
             patch("twitter_agent.write_status") as report:
            reply_to_candidates(args, [a, b], {}, {}, action)
        self.assertEqual([call.args[0] for call in draft.call_args_list],
                         [[a], [a], [a], [b], [b]])
        self.assertTrue(any(call.args[1].get("state") == "reply_target_skipped"
                            and call.args[1].get("candidate_url") == a["url"]
                            for call in report.call_args_list))
        action.assert_any_call("which workflow?", reply_target=b)
        self.assertEqual(action.call_count, 2)

    def test_no_useful_draft_advances_to_next_candidate(self):
        args = argparse.Namespace(status_file="unused", model="local")
        a = {"url": "https://x.com/a/status/1", "text": "heyclicky praise"}
        b = {"url": "https://x.com/b/status/2", "text": "heyclicky question"}
        choice = {"target": b, "text": "which workflow?", "reason": "specific"}
        action = MagicMock(side_effect=[b, "published"])
        with patch("twitter_agent.draft_contextual_reply", side_effect=[None, choice, choice]), \
             patch("twitter_agent.published_text", return_value=False), \
             patch("twitter_agent.write_status"):
            reply_to_candidates(args, [a, b], {}, {}, action)
        action.assert_any_call("which workflow?", reply_target=b)
        self.assertEqual(action.call_count, 2)

    def test_heartbeat_is_not_activity_and_skips_are_persistent(self):
        with tempfile.TemporaryDirectory() as directory:
            path=str(Path(directory)/"status.json")
            db=Path(directory)/"posts.sqlite3"
            write_status(path,{"state":"reply_target_skipped","candidate_url":"https://x.com/b/status/1","error":"changed source"})
            before=activity_summary(db)
            write_status(path,{"state":"campaign_waiting"})
            self.assertEqual(activity_summary(db),before)
            self.assertIsNone(before["last_verified_at"])
            self.assertEqual(recently_skipped(db),{"https://x.com/b/status/1"})
            write_status(path,{"state":"verified_published","post_url":"https://x.com/owner/status/2"})
            self.assertIsNotNone(activity_summary(db)["last_verified_at"])

    def test_zero_word_and_unsupported_word_counts_do_not_qualify(self):
        for text in ["Pitch me your startup in 0 words","pitch me your startup in zero words",
                     "pitch me your startup in 3 words"]:
            self.assertIsNone(invitation_kind(text))

    def test_old_invitation_and_recently_skipped_target_are_excluded_from_discovery(self):
        from organic_replies import discover_posts
        posts=[{"url":"https://x.com/a/status/1","text":"post your landing page",
                "created_at":"2022-01-01T00:00:00Z"},
               {"url":"https://x.com/b/status/2","text":"post your landing page"}]
        def js(code):
            return True if code.startswith("location.href=") else posts
        with tempfile.TemporaryDirectory() as directory:
            path=str(Path(directory)/"status.json")
            write_status(path,{"state":"reply_target_skipped","candidate_url":posts[1]["url"],
                              "error":"not useful"})
            self.assertEqual(discover_posts(js,MagicMock(),"/owner",Path(directory)/"posts.sqlite3",invitations=True),[])

    def test_full_source_rejection_tries_another_candidate_and_publishes_only_once(self):
        args=argparse.Namespace(status_file="unused",model="local")
        a={"url":"https://x.com/a/status/1","text":"heyclicky task"}
        b={"url":"https://x.com/b/status/2","text":"heyclicky task"}
        choice_a={"target":a,"text":"first?","reason":"relevant"}
        choice_b={"target":b,"text":"second?","reason":"relevant"}
        action=MagicMock(side_effect=[a,b,"published"])
        with patch("twitter_agent.draft_contextual_reply",side_effect=[choice_a,None,choice_b,choice_b]) as draft, \
             patch("twitter_agent.published_text",return_value=False),patch("twitter_agent.write_status"):
            reply_to_candidates(args,[a,b],{}, {},action)
            self.assertEqual(draft.call_args_list[2].args[0],[b])
        self.assertEqual(sum("reply_target" in c.kwargs for c in action.call_args_list),1)

    def test_unknown_errors_never_advance_to_another_candidate(self):
        args=argparse.Namespace(status_file="unused",model="local")
        target={"url":"https://x.com/a/status/1","text":"heyclicky task"}
        choice={"target":target,"text":"question?","reason":"relevant"}
        action=MagicMock(side_effect=RuntimeError("possible submission"))
        with patch("twitter_agent.draft_contextual_reply",return_value=choice),patch("twitter_agent.write_status"):
            with self.assertRaisesRegex(RuntimeError,"possible submission"):
                reply_to_candidates(args,[target],{}, {},action)
        action.assert_called_once()

    def test_shortlist_is_bounded_to_three_rejected_targets(self):
        args=argparse.Namespace(status_file="unused",model="local")
        candidates=[{"url":f"https://x.com/a/status/{i}","text":"heyclicky task"} for i in range(6)]
        def draft(posts,*_args,**_kwargs):
            return {"target":posts[0],"text":"question?","reason":"relevant"}
        action=MagicMock(side_effect=ReplyTargetUnavailable("changed source"))
        with patch("twitter_agent.draft_contextual_reply",side_effect=draft),patch("twitter_agent.write_status"):
            reply_to_candidates(args,candidates,{}, {},action)
        self.assertEqual(action.call_count,3)


if __name__ == "__main__":
    unittest.main()
