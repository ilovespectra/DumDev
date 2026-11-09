import unittest
from unittest.mock import MagicMock, patch
from pathlib import Path

from arc_poster import COMPOSER_JS, REPLY_DIALOG_JS, REPLY_SOURCE_JS, ReplyTargetUnavailable, Takeover, applescript_string, decode_arc_result, find_x_tab, native_command, post_in_arc, read_reply_source, verify_reply_dialog


class ArcSafetyTests(unittest.TestCase):
    def test_discovery_reads_recent_and_top_roots_before_bounded_reply_selection(self):
        import argparse
        from twitter_agent import run_once
        args = argparse.Namespace(settings_file="settings.json", status_file="status.json",
                                  login=False, reply_to=None, repo="project", post_mode="arc",
                                  discover_reply=True, dry_run=False, model="local")
        top = {"url": "https://x.com/a/status/1", "text": "heyclicky browser memory",
               "discovery_source": "top"}
        recent = {"url": "https://x.com/b/status/2", "text": "heyclicky browser memory",
                  "discovery_source": "recent"}
        with patch("twitter_agent.load_settings", return_value={}), \
             patch("twitter_agent.read_request", return_value=None), \
             patch("twitter_agent.read_project_summary", return_value={}), \
             patch("twitter_agent.write_status"), \
             patch("twitter_agent.draft_contextual_reply", return_value=None), \
             patch("twitter_agent.post_in_arc", side_effect=[[top, recent], []]) as action:
            self.assertEqual(run_once(args), 0)
        self.assertEqual(action.call_args_list[1].kwargs, {"scan_threads": [recent, top]})
        self.assertEqual(action.call_count, 2)

    def test_published_link_linebreaks_match_but_other_urls_or_words_do_not(self):
        from arc_poster import PUBLICATION_TEXT_JS
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser=p.chromium.launch(headless=True)
            try:
                page=browser.new_page()
                page.set_content('<article><div data-testid="tweetText">check this: <a href="https://t.co/example">https://<br>cruise.kolektivkrog.si</a></div></article>')
                script="expected=>{"+PUBLICATION_TEXT_JS+"return publicationMatches(document.querySelector('article'),expected);}"
                self.assertTrue(page.evaluate(script,"check this: https://cruise.kolektivkrog.si"))
                self.assertFalse(page.evaluate(script,"check this: https://evil.example"))
                self.assertFalse(page.evaluate(script,"different: https://cruise.kolektivkrog.si"))
            finally:
                browser.close()

    def test_configured_response_and_invitation_discovery_use_approved_link_end_to_end(self):
        import argparse
        from twitter_agent import run_once
        args=argparse.Namespace(settings_file="settings.json",status_file="status.json",login=False,
                               reply_to=None,repo="project",post_mode="arc",discover_reply=True,
                               dry_run=False,model="local")
        settings={"approved_webpage":"https://cruise.kolektivkrog.si","check_public_responses":True}
        target={"url":"https://x.com/b/status/1","text":"post your landing page","discovery_source":"recent"}
        full=dict(target,promoted=False,quoted=False)
        choice={"target":target,"text":"open-source heyclicky companion. "+settings["approved_webpage"],"reason":"invited"}
        with patch("twitter_agent.load_settings",return_value=settings), \
             patch("twitter_agent.read_request",return_value=None), \
             patch("twitter_agent.read_project_summary",return_value={}), \
             patch("twitter_agent.write_status"), \
             patch("twitter_agent.published_text",return_value=False), \
             patch("twitter_agent.draft_contextual_reply",side_effect=[choice,dict(choice,target=full)]) as draft, \
             patch("twitter_agent.await_approval",side_effect=lambda args,text,kind,target,publish: publish(text,reply_target=target) if target else publish(text)), \
             patch("twitter_agent.post_in_arc",side_effect=[[],[target],[],full,"published"]) as arc:
            self.assertEqual(run_once(args),0)
            self.assertEqual(arc.call_args_list[0].kwargs,{"responses":True})
            self.assertEqual(arc.call_args_list[1].kwargs,{"discover":True,"invitations":True})
            self.assertEqual(draft.call_args.kwargs,{"approved_webpage":settings["approved_webpage"]})
            self.assertEqual(arc.call_args.kwargs,{"reply_target":full})

    def test_reply_dialog_waits_for_full_source_and_refuses_unverified_text(self):
        guard = MagicMock()
        verify_reply_dialog(MagicMock(side_effect=[False, True]), guard, "full source")
        guard.wait.assert_called_once_with(0.2)
        with self.assertRaisesRegex(ReplyTargetUnavailable, "no submission attempted"):
            verify_reply_dialog(MagicMock(return_value=False), guard, "full source")

    def test_expanded_search_preview_is_read_but_never_accepted_for_publication(self):
        target = {"url": "https://x.com/builder/status/123", "text": "heyclicky is slow"}
        full = dict(target, text="heyclicky is slow when loading extensions", promoted=False, quoted=False)
        guard = MagicMock()
        self.assertEqual(read_reply_source(MagicMock(return_value=full), guard, target,
                                         allow_expansion=True), full)
        with self.assertRaisesRegex(ReplyTargetUnavailable, "text changed"):
            read_reply_source(MagicMock(return_value=full), guard, target)
        self.assertEqual(read_reply_source(MagicMock(return_value=full), guard, full), full)
        for changed in [dict(full, text="unrelated text"), dict(full, promoted=True),
                        dict(full, quoted=True), dict(full, url="https://x.com/other/status/123")]:
            with self.assertRaises(ReplyTargetUnavailable):
                read_reply_source(MagicMock(return_value=changed), guard, target, allow_expansion=True)
        with self.assertRaisesRegex(ReplyTargetUnavailable, "could not be reverified"):
            read_reply_source(MagicMock(return_value=None), guard, target)

    def test_read_completion_does_not_report_posted_or_interrupted(self):
        guard = object.__new__(Takeover)
        guard.process = MagicMock()
        guard.process.poll.return_value = None
        for success, read_only, message in [(True, True, "READ_DONE\n"),
                                           (True, False, "DONE\n"), (False, True, "STOP\n")]:
            guard.close(success, read_only=read_only)
            guard.process.stdin.write.assert_called_with(message)

    def test_reply_is_redrafted_from_full_source_before_publication(self):
        import argparse
        from twitter_agent import run_once
        args = argparse.Namespace(settings_file="settings.json", status_file="status.json",
                                  login=False, reply_to=None, repo="project", post_mode="arc",
                                  discover_reply=True, dry_run=False, model="local")
        preview = {"url": "https://x.com/builder/status/123", "text": "heyclicky is slow"}
        full = dict(preview, text="heyclicky is slow when loading extensions", promoted=False, quoted=False)
        initial = {"target": preview, "text": "preview question?", "reason": "preview"}
        final = {"target": full, "text": "which extension was loading?", "reason": "full source"}
        with patch("twitter_agent.load_settings", return_value={}), \
             patch("twitter_agent.read_request", return_value=None), \
             patch("twitter_agent.read_project_summary", return_value="project"), \
             patch("twitter_agent.draft_contextual_reply", side_effect=[initial, final]) as draft, \
             patch("twitter_agent.await_approval", side_effect=lambda args,text,kind,target,publish: publish(text,reply_target=target) if target else publish(text)), \
             patch("twitter_agent.post_in_arc", side_effect=[[preview], full, "published"]) as action, \
             patch("twitter_agent.write_status"):
            self.assertEqual(run_once(args), 0)
            self.assertEqual(draft.call_args.args[0], [full])
            self.assertEqual(action.call_args_list[1].kwargs, {"inspect_target": preview})
            self.assertEqual(action.call_args.args[1], final["text"])
            self.assertEqual(action.call_args.kwargs, {"reply_target": full})

    def test_top_thread_candidates_reach_selection_and_full_source_keeps_context(self):
        import argparse
        from twitter_agent import run_once
        args = argparse.Namespace(settings_file="settings.json", status_file="status.json",
                                  login=False, reply_to=None, repo="project", post_mode="arc",
                                  discover_reply=True, dry_run=False, model="local")
        root = {"url":"https://x.com/builder/status/123","text":"heyclicky task","discovery_source":"top"}
        target = {"url":"https://x.com/commenter/status/456","text":"permission prompts are slow",
                  "discovery_source":"top_thread", "thread_context":{"url":root["url"],"text":root["text"]}}
        full = dict(target,promoted=False,quoted=False)
        choice = {"target":target,"text":"which prompt?", "reason":"context"}
        final = dict(choice,target=full)
        with patch("twitter_agent.load_settings",return_value={}), \
             patch("twitter_agent.read_request",return_value=None), \
             patch("twitter_agent.read_project_summary",return_value="project"), \
             patch("twitter_agent.write_status"), \
             patch("twitter_agent.draft_contextual_reply",side_effect=[None,choice,final]) as draft, \
             patch("twitter_agent.await_approval",side_effect=lambda args,text,kind,target,publish: publish(text,reply_target=target) if target else publish(text)), \
             patch("twitter_agent.post_in_arc",side_effect=[[root],[target],full,"published"]) as action:
            self.assertEqual(run_once(args),0)
            self.assertEqual(action.call_args_list[1].kwargs,{"scan_threads":[root]})
            self.assertEqual(draft.call_args_list[0].args[0],[root])
            self.assertEqual(draft.call_args_list[1].args[0],[target])
            self.assertEqual(draft.call_args_list[2].args[0],[full])
            self.assertEqual(action.call_args.kwargs,{"reply_target":full})
        self.assertEqual(read_reply_source(MagicMock(return_value={k:full[k] for k in ("url","text","promoted","quoted")}),
                                          MagicMock(),target),full)

    def test_unavailable_reply_target_is_logged_skip_but_other_errors_propagate(self):
        import argparse
        from arc_poster import ReplyTargetUnavailable
        from twitter_agent import run_once
        args = argparse.Namespace(settings_file="settings.json", status_file="status.json",
                                  login=False, reply_to=None, repo="project", post_mode="arc",
                                  discover_reply=True, dry_run=False, model="local")
        choice = {"text": "what happened?", "target": {"url": "https://x.com/owner/status/1",
                                                     "text": "source"}, "reason": "relevant"}
        with patch("twitter_agent.load_settings", return_value={}), \
             patch("twitter_agent.read_project_summary", return_value="project"), \
             patch("twitter_agent.draft_contextual_reply", return_value=choice), \
             patch("twitter_agent.retry_interrupted") as action, \
             patch("twitter_agent.write_status") as report:
            action.side_effect = [[choice["target"]], ReplyTargetUnavailable("source unavailable")]
            self.assertEqual(run_once(args), 0)
            skipped = [call.args[1] for call in report.call_args_list if call.args[1].get("state")=="reply_target_skipped"]
            self.assertEqual(skipped[-1]["error"], "source unavailable")
            self.assertEqual(report.call_args.args[1]["state"],"no_relevant_reply")
            action.side_effect = [[choice["target"]], RuntimeError("permission denied")]
            with self.assertRaisesRegex(RuntimeError, "permission denied"):
                run_once(args)

    def test_shortcut_pastes_once_and_releases_keys_on_abort(self):
        guard = MagicMock()
        with patch("arc_poster.native_key") as key, patch("arc_poster.release_native_key") as release:
            native_command(9, guard)
            self.assertEqual([c.args[:2] for c in key.call_args_list], [(55, True), (9, True), (9, False)])
            self.assertEqual([c.args[0] for c in release.call_args_list], [9, 55])
        with patch("arc_poster.native_key", side_effect=RuntimeError("interrupted")), patch("arc_poster.release_native_key") as release:
            with self.assertRaises(RuntimeError):
                native_command(9, guard)
            self.assertEqual([c.args[0] for c in release.call_args_list], [9, 55])

    def test_overlength_or_uppercase_draft_never_launches_takeover(self):
        with patch("arc_poster.Takeover") as takeover:
            for text in ["", "a" * 241, "LOUD"]:
                with self.assertRaises(RuntimeError):
                    post_in_arc(None, text, {}, None)
            takeover.assert_not_called()

    def test_confirmed_account_reuses_active_composer_tab(self):
        with patch("arc_poster.run_script", return_value="w\tt1\thttps://x.com/home\tinactive\nw\tt2\thttps://x.com/compose/post\tactive"):
            self.assertEqual(find_x_tab("owner"), ("w", "t2", "/owner"))
    def test_only_topmost_standalone_composer_is_selected(self):
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                page.set_content('''
                  <div role="dialog" style="position:absolute;top:20px;left:20px">
                    <div id="stale" data-testid="tweetTextarea_0" contenteditable="true"
                         style="width:300px;height:100px">stale draft</div>
                  </div>
                  <div role="dialog" style="position:absolute;top:20px;left:20px;background:white">
                    <div id="active" data-testid="tweetTextarea_0" contenteditable="true"
                         style="width:300px;height:100px"></div>
                  </div>''')
                self.assertEqual(page.evaluate("(()=>{" + COMPOSER_JS + "return editor?.id;})()"), "active")
                page.set_content('<div data-testid="tweetTextarea_0" contenteditable="true">inline reply</div>')
                self.assertIsNone(page.evaluate("(()=>{" + COMPOSER_JS + "return editor?.id;})()"))
                page.set_content('''
                  <article data-testid="tweet"><a href="/other/status/123"><time>now</time></a>
                    <div data-testid="tweetText">wrong source</div></article>
                  <article data-testid="tweet"><a href="/builder/status/123"><time>now</time></a>
                    <div data-testid="tweetText">heyclicky is slow when loading extensions</div></article>''')
                source = page.evaluate("(()=>{" + REPLY_SOURCE_JS.replace("EXPECTED_URL", '"/builder/status/123"') + "})()")
                self.assertEqual(source, {"url": "https://x.com/builder/status/123",
                                         "text": "heyclicky is slow when loading extensions",
                                         "quoted": False, "promoted": False})
                page.set_content('''
                  <div role="dialog" style="width:500px;height:300px">
                    <div data-testid="tweetText">heyclicky is slow</div>
                    <button data-testid="tweet-text-show-more-link"
                      onclick="this.previousElementSibling.innerText='heyclicky is slow when loading extensions'">Show more</button>
                    <div data-testid="tweetTextarea_0" contenteditable="true" style="width:300px;height:100px"></div>
                  </div>''')
                script = "(()=>{" + REPLY_DIALOG_JS.replace("EXPECTED_TEXT", '"heyclicky is slow when loading extensions"') + "})()"
                self.assertFalse(page.evaluate(script))
                self.assertTrue(page.evaluate(script))
                self.assertFalse(page.evaluate(script.replace("heyclicky is slow when loading extensions", "different source")))
            finally:
                browser.close()

    def test_arc_json_wrapper_is_decoded_without_losing_boolean_or_url(self):
        import json
        for value in [True, False, None, {"origin": "https://x.com", "profile": "/owner"}, "https://x.com/owner/status/1"]:
            self.assertEqual(decode_arc_result(json.dumps(json.dumps(value))), value)
        self.assertEqual(decode_arc_result('{"profile":"/owner"}'), {"profile": "/owner"})
    def test_duplicate_tabs_of_same_account_pin_one_exact_tab(self):
        with patch("arc_poster.run_script", return_value="window\ttab1\thttps://x.com/owner\nwindow\ttab2\thttps://x.com/owner"):
            self.assertEqual(find_x_tab(), ("window", "tab1", "/owner"))

    def test_different_accounts_are_not_guessed(self):
        with patch("arc_poster.run_script", return_value="w\tt1\thttps://x.com/a\nw\tt2\thttps://x.com/b"):
            with self.assertRaisesRegex(RuntimeError, "refusing to guess"):
                find_x_tab()

    def test_home_tab_is_not_account_identity(self):
        with patch("arc_poster.run_script", return_value="w\tt\thttps://x.com/home"):
            with self.assertRaises(RuntimeError):
                find_x_tab()

    def test_user_input_aborts_before_further_commands(self):
        import queue
        guard = object.__new__(Takeover)
        guard.events = queue.Queue()
        guard.process = MagicMock()
        guard.process.poll.return_value = None
        guard.events.put("ABORT")
        with self.assertRaisesRegex(RuntimeError, "interrupted"):
            guard.message("posting")
        guard.process.stdin.write.assert_not_called()

    def test_applescript_text_cannot_escape_quoted_string(self):
        self.assertEqual(applescript_string('"\\\n'), '"\\"\\\\\\n"')


if __name__ == "__main__":
    unittest.main()
