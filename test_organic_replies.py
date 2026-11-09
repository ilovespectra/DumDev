import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from background_poster import finish_attempt, reserve_attempt
from organic_replies import INVITATION_QUERIES, SEARCH_QUERIES, THREAD_POSTS_JS, discover_posts, discover_thread_replies, discover_responses, draft_contextual_reply, growth_summary, invitation_kind, next_post_experiment, prioritize_candidates, published_text, recent_publications, record_outreach, relevant_post


class OrganicReplyTests(unittest.TestCase):
    def test_unicode_reply_validation_rejects_emoji_sequences_but_keeps_unicode_words(self):
        from organic_replies import has_emoji
        for emoji in ("🙂", "❤", "🇺🇸", "1️⃣", "🏳️‍🌈"):
            self.assertTrue(has_emoji(emoji), emoji)
        self.assertFalse(has_emoji("¿qué extensión se estaba cargando?"))
        with patch("organic_replies.ollama.generate", side_effect=[
                {"response": '{"index":0,"reason":"specific source"}'},
                {"response": '{"reply":"which extension was loading? 🇺🇸"}'}]):
            with self.assertRaisesRegex(ValueError, "emoji"):
                draft_contextual_reply([{"url":"https://x.com/b/status/1",
                                         "text":"heyclicky was slow loading extensions"}], {}, "local")
        with patch("organic_replies.ollama.generate", side_effect=[
                {"response": '{"index":0,"reason":"specific source"}'},
                {"response": '{"reply":"¿que\\u0301 extensión se estaba cargando?"}'}]):
            result = draft_contextual_reply([{"url":"https://x.com/b/status/1",
                                               "text":"heyclicky was slow loading extensions"}], {}, "local")
        self.assertEqual(result["text"], "¿qué extensión se estaba cargando?")

    def test_identity_guard_does_not_accept_heyclicky_praise_or_own_thread_credit(self):
        from organic_replies import ReplyIdentityError, validate_reply_identity
        for source in ["heyclicky is amazing", "love this app!", "cruise control and heyclicky are great"]:
            for reply in ["thanks! glad you like it", "appreciate your support", "happy you enjoyed it"]:
                with self.assertRaises(ReplyIdentityError):
                    validate_reply_identity(reply,source)
        with self.assertRaises(ReplyIdentityError):
            validate_reply_identity("we built heyclicky for this","heyclicky task")
        validate_reply_identity("thanks for sharing those details, which extension was loading?",
                                "heyclicky was slow loading extensions")
        with self.assertRaises(ReplyIdentityError):
            validate_reply_identity("thanks for the feedback!","cruise control worked well for me")
        validate_reply_identity("thanks for the feedback!","dumdev worked well for me")
        validate_reply_identity("which workflow did you try?","heyclicky is amazing")
        validate_reply_identity("thanks for sharing, which local model handles the responses?",
                                "heyclicky responses run through ollama")
        for reply in ("thanks for sharing, glad you like it", "thanks for sharing your love of our app"):
            with self.assertRaises(ReplyIdentityError):
                validate_reply_identity(reply, "heyclicky is amazing")

    def test_both_model_stages_receive_distinct_account_identity(self):
        with patch("organic_replies.ollama.generate",side_effect=[
                {"response":'{"index":0,"reason":"question"}'},
                {"response":'{"reply":"which workflow did you try?"}'}]) as model:
            draft_contextual_reply([{"url":"https://x.com/b/status/1","text":"heyclicky is great"}],{},"local")
            for call in model.call_args_list:
                self.assertIn("The posting account is the one configured by the owner",call.kwargs["prompt"])
                self.assertIn("Cruise Control, the separate HeyClicky",call.kwargs["prompt"])
                self.assertIn("Praise for HeyClicky belongs",call.kwargs["prompt"])

    def test_both_model_stages_receive_rejection_feedback_and_no_gratitude_rule(self):
        feedback = "Contextual reply risks accepting praise or credit for HeyClicky"
        with patch("organic_replies.ollama.generate", side_effect=[
                {"response": '{"index":0,"reason":"specific question"}'},
                {"response": '{"reply":"which local model are you using for responses?"}'}]) as model:
            result = draft_contextual_reply(
                [{"url": "https://x.com/person/status/1",
                  "text": "built a local ollama tool for heyclicky responses"}],
                {}, "local", draft_feedback=feedback)
        self.assertEqual(result["text"], "which local model are you using for responses?")
        for call in model.call_args_list:
            self.assertEqual(call.kwargs["options"]["num_ctx"], 16384)
            self.assertIn(feedback, call.kwargs["prompt"])
            self.assertIn("Do not use gratitude or praise acknowledgements", call.kwargs["prompt"])
            self.assertIn("Do not invent a task, problem, or experience", call.kwargs["prompt"])
        self.assertIn("choose another candidate", model.call_args_list[0].kwargs["prompt"])
        self.assertIn("History is only for avoiding duplicates",
                      model.call_args_list[1].kwargs["system"])
        self.assertTrue(model.call_args_list[1].kwargs["prompt"].rstrip().endswith(
            '"built a local ollama tool for heyclicky responses"'))

    def test_history_cannot_be_republished_or_supply_new_numbers(self):
        target = {"url": "https://x.com/person/status/1", "text": "heyclicky responses use ollama"}
        history = ["curious if the 150 agent messages include all communications?"]
        for reply, error in [(history[0], "duplicates recent"), ("is the limit 150 messages?", "invents numbers")]:
            with patch("organic_replies.ollama.generate", side_effect=[
                    {"response": '{"index":0,"reason":"specific question"}'},
                    {"response": json.dumps({"reply": reply})}]):
                with self.assertRaisesRegex(ValueError, error):
                    draft_contextual_reply([target], {}, "local", recent_replies=history)
        with patch("organic_replies.ollama.generate", side_effect=[
                {"response": '{"index":0,"reason":"specific question"}'},
                {"response": '{"reply":"does that 150-message limit reset daily?"}'}]):
            result = draft_contextual_reply([dict(target, text="heyclicky has a 150-message limit")],
                                            {}, "local", recent_replies=history)
        self.assertIn("150", result["text"])

    def test_experiments_rotate_only_on_verified_posts_and_history_is_bounded(self):
        with tempfile.TemporaryDirectory() as directory:
            db=Path(directory)/"posts.sqlite3"
            self.assertEqual(next_post_experiment(db)[0],"observation")
            pending=reserve_attempt(db,"unpublished",10)
            self.assertEqual(next_post_experiment(db)[0],"observation")
            finish_attempt(db,pending,"not_submitted")
            for i,label in enumerate(("question","useful_tip","observation")):
                attempt=reserve_attempt(db,f"verified post {i}",10)
                finish_attempt(db,attempt,"verified")
                self.assertEqual(next_post_experiment(db)[0],label)
            reply=reserve_attempt(db,"reply history",10,"reply","https://x.com/b/status/1")
            finish_attempt(db,reply,"verified")
            self.assertEqual(next_post_experiment(db)[0],"observation")
            self.assertEqual(recent_publications(db,"post",2),["verified post 2","verified post 1"])
            self.assertEqual(recent_publications(db,"reply"),["reply history"])

    def test_invalid_reply_drafts_and_no_matches_skip_without_submission(self):
        from types import SimpleNamespace
        import twitter_agent
        with tempfile.TemporaryDirectory() as directory:
            args = SimpleNamespace(status_file=str(Path(directory) / "status.json"), model="test")
            target = {"url":"https://x.com/person/status/1", "text":"heyclicky is stuck"}
            arc_action = MagicMock()
            with patch("twitter_agent.draft_contextual_reply", side_effect=ValueError("invalid reply")) as draft:
                twitter_agent.reply_to_candidates(args, [target], {}, {}, arc_action)
            self.assertEqual(draft.call_count, 3)
            self.assertEqual(json.loads(Path(args.status_file).read_text())["state"], "no_relevant_reply")
            arc_action.assert_not_called()
            with patch("twitter_agent.draft_contextual_reply") as draft:
                twitter_agent.reply_to_candidates(args, [], {}, {}, arc_action)
            draft.assert_not_called()
            arc_action.assert_not_called()
            self.assertEqual(json.loads(Path(args.status_file).read_text())["state"], "no_relevant_reply")

    def test_reply_drafting_keeps_old_reply_facts_out_of_both_model_stages(self):
        history=["did you have to wait for permission prompts?"]
        with patch("organic_replies.ollama.generate",side_effect=[
                {"response":'{"index":0,"reason":"specific source"}'},
                {"response":'{"reply":"which extension was loading?"}'}]) as model:
            draft_contextual_reply([{"url":"https://x.com/b/status/1","text":"heyclicky was slow opening extensions"}],
                                   {},"local",recent_replies=history)
            for call in model.call_args_list:
                self.assertNotIn(history[0],call.kwargs["prompt"])
                self.assertIn("1 recent replies",call.kwargs["prompt"])
            self.assertIn("Do not assume slow tasks involve permissions",model.call_args_list[0].kwargs["prompt"])

    def test_post_drafting_receives_experiment_and_history(self):
        from twitter_agent import generate_tweets
        with patch("twitter_agent.ollama.generate",return_value={"response":'{"tweets":["a useful workflow fact"]}'}) as model:
            self.assertEqual(generate_tweets({},count=1,experiment="workflow tip",
                                            recent_posts=["old verified post"]),["a useful workflow fact"])
            self.assertIn("workflow tip",model.call_args.kwargs["prompt"])
            self.assertIn("old verified post",model.call_args.kwargs["prompt"])
            self.assertIn("no image-dependent meme captions",model.call_args.kwargs["prompt"])
        for text in ["manual mode automatically approves requests","hey @heyclicky, try this","set it and forget it"]:
            with patch("twitter_agent.ollama.generate",return_value={"response":json.dumps({"tweets":[text]})}):
                with self.assertRaises(ValueError):
                    generate_tweets({},count=1)

    def test_dumdev_post_guard_rejects_separate_cruise_control_features(self):
        from twitter_agent import generate_campaign_plan, generate_tweets
        summary = {"name":"DumDev", "user_configured":True,
                   "description":"A standalone X-agent app."}
        false_claim = "in dumdev, you can approve takeover requests. cruise control does this for you."
        with patch("twitter_agent.ollama.generate", return_value={
                "response":json.dumps({"tweets":[false_claim]})}) as model:
            with self.assertRaisesRegex(ValueError, "separate Cruise Control/HeyClicky"):
                generate_tweets(summary, count=1, recent_posts=[
                    "an older dumdev fact", "cruise control approves takeover requests"])
            prompt = model.call_args.kwargs["prompt"]
            self.assertIn("Write only about the verified DumDev capabilities", prompt)
            self.assertIn("an older dumdev fact", prompt)
            self.assertNotIn("cruise control", prompt.casefold())
            self.assertNotIn("heyclicky", prompt.casefold())
        with patch("twitter_agent.ollama.generate", return_value={
                "response":json.dumps({"queued_tweets":[false_claim]})}) as model:
            with self.assertRaisesRegex(ValueError, "separate Cruise Control/HeyClicky"):
                generate_campaign_plan(summary, count=1)
            prompt = model.call_args.kwargs["prompt"]
            self.assertIn("Write only about the verified DumDev capabilities", prompt)
            self.assertNotIn("Cruise Control", prompt)
            self.assertNotIn("HeyClicky", prompt)

    def test_growth_priority_requires_measured_samples_and_preserves_followups(self):
        invitation={"url":"https://x.com/b/status/1","text":"post your landing page","discovery_source":"top"}
        conversation={"url":"https://x.com/b/status/2","text":"heyclicky task","discovery_source":"recent"}
        followup={"url":"https://x.com/b/status/3","text":"which permission prompt was that?","discovery_source":"followup"}
        with tempfile.TemporaryDirectory() as directory:
            db=Path(directory)/"posts.sqlite3"
            for i in range(4):
                record_outreach(db,f"https://x.com/owner/status/{i}","invitation_link",{"replies":3,"likes":None})
            record_outreach(db,"https://x.com/owner/status/99","recent",{"replies":None,"likes":None})
            evidence=growth_summary(db)
            self.assertFalse(evidence[0]["enough_evidence"])
            self.assertEqual(prioritize_candidates([conversation,invitation],evidence),[conversation,invitation])
            record_outreach(db,"https://x.com/owner/status/4","invitation_link",{"replies":3,"likes":None})
            evidence=growth_summary(db)
            self.assertTrue(evidence[0]["enough_evidence"])
            self.assertEqual(evidence[0]["measured_posts"],5)
            self.assertEqual(prioritize_candidates([conversation,invitation,followup],evidence),[followup,invitation,conversation])

    def test_public_notifications_and_own_threads_exclude_self_and_duplicate_targets(self):
        navigations=[]
        mention={"url":"https://x.com/b/status/1","text":"heyclicky permission task"}
        response={"url":"https://x.com/c/status/2","text":"i tried it but the permission prompt still waits"}
        with tempfile.TemporaryDirectory() as directory:
            db=Path(directory)/"posts.sqlite3"
            attempt=reserve_attempt(db,"which prompt was waiting?",10)
            finish_attempt(db,attempt,"verified","https://x.com/owner/status/10")
            def js(code):
                if code.startswith("location.href="):
                    navigations.append(code)
                    return True
                if 'const count=' in code:
                    return {"likes":None,"replies":2}
                if '/notifications/mentions' in code:
                    return [mention,dict(mention,url="https://x.com/owner/status/12")]
                return [response,dict(response,url="https://x.com/owner/status/13")]
            posts=discover_responses(js,MagicMock(),"/owner",db)
            self.assertEqual([p["url"] for p in posts],[mention["url"],response["url"]])
            self.assertEqual(posts[1]["thread_context"]["url"],"https://x.com/owner/status/10")
            self.assertTrue(posts[1]["thread_context"]["own_publication"])
            self.assertTrue(all("messages" not in code for code in navigations))
            self.assertEqual(growth_summary(db)[0]["average_replies"],2)
            reply=reserve_attempt(db,"a contextual response",10,"reply",response["url"])
            finish_attempt(db,reply,"verified","https://x.com/owner/status/11")
            # New root still yields the same candidate, but it must now be excluded.
            self.assertNotIn(response["url"],[p["url"] for p in discover_responses(js,MagicMock(),"/owner",db)])

    def test_explicit_invitation_detection_and_constraints(self):
        for text, kind in [("shill me your startup!", "pitch"),
                           ("post your landing page", "link"),
                           ("pitch me your startup with one word", "one_word"),
                           ("share your startup website, no links", "pitch")]:
            self.assertEqual(invitation_kind(text),kind)
        for text in ["don't shill me your startup", "no startup pitches please",
                     "i shared my landing page yesterday", "great startup!", "post your password"]:
            self.assertIsNone(invitation_kind(text))

    def test_invitation_links_only_use_approved_url_and_one_word_is_enforced(self):
        approved = "https://cruise.kolektivkrog.si"
        def draft(source, reply, webpage=approved):
            with patch("organic_replies.ollama.generate", side_effect=[
                    {"response":'{"index":0,"reason":"invited"}'},
                    {"response":json.dumps({"reply":reply})}]):
                return draft_contextual_reply([{"url":"https://x.com/b/status/1","text":source}],
                                              {"name":"Cruise Control"},"local",approved_webpage=webpage)
        self.assertEqual(draft("post your landing page","cruise control: open-source heyclicky companion. "+approved)["text"],
                         "cruise control: open-source heyclicky companion. "+approved)
        self.assertEqual(draft("post your landing page","cruise control: open-source heyclicky companion.")["text"],
                         "cruise control: open-source heyclicky companion. "+approved)
        for prose in ["",approved,"https://evil.example","visit evil.example",
                      "x"*220]:
            with self.assertRaises(ValueError):
                draft("post your landing page",prose)
        self.assertEqual(draft("pitch me your startup in one word","autopilot")["text"],"autopilot")
        for source, reply in [("post your landing page","https://evil.example"),
                              ("pitch me your startup in one word","cruise control"),
                              ("shill me your startup",approved),
                              ("heyclicky task is slow",approved),
                              ("post your landing page",approved+" https://evil.example"),
                              ("pitch me your startup in one sentence","first sentence. second sentence.")]:
            with self.assertRaises(ValueError):
                draft(source,reply)
        with self.assertRaises(ValueError):
            draft("post your landing page","hello",None)

    def test_invitation_search_keeps_explicit_invites_not_incidental_keywords(self):
        navigations = []
        def js(code):
            if code.startswith("location.href="):
                navigations.append(code)
                return True
            return [{"url":"https://x.com/b/status/1","text":"post your landing page"},
                    {"url":"https://x.com/c/status/2","text":"my startup uses heyclicky"}]
        with tempfile.TemporaryDirectory() as directory:
            posts = discover_posts(js,MagicMock(),"/owner",Path(directory)/"posts.sqlite3",invitations=True)
        self.assertEqual(len(navigations),2)
        self.assertEqual([p["url"] for p in posts],["https://x.com/b/status/1"])

    def test_searches_top_and_recent_and_retains_both_in_bounded_candidates(self):
        navigations = []
        def js(code):
            if code.startswith("location.href="):
                navigations.append(json.loads(code[len("location.href="):].split(";return")[0]))
                return True
            mode = "recent" if "&f=live" in navigations[-1] else "top"
            return [{"url":f"https://x.com/{mode}/status/{i}", "text":"heyclicky permission task"}
                    for i in range(12)]
        with tempfile.TemporaryDirectory() as directory:
            result = discover_posts(js, MagicMock(), "/owner", Path(directory)/"posts.sqlite3")
        self.assertEqual(len(navigations), 6)
        self.assertEqual(sum("&f=live" in u for u in navigations), 3)
        self.assertEqual(len(result), 12)
        self.assertEqual([p["discovery_source"] for p in result], ["recent", "top"]*6)
        self.assertEqual(len({p["url"] for p in result}), 12)

    def test_top_thread_replies_use_context_exclude_duplicates_self_ads_and_unrelated(self):
        root = {"url":"https://x.com/builder/status/1","text":"heyclicky permission task"}
        valid = {"url":"https://x.com/replier/status/2","text":"it gets stuck waiting for permission prompts"}
        self.assertFalse(relevant_post(valid, "owner"))
        self.assertTrue(relevant_post(valid, "owner", root))
        self.assertFalse(relevant_post(dict(valid,text="nice!"), "owner", root))
        posts = [valid, valid, dict(valid,url="https://x.com/owner/status/3"),
                 dict(valid,url="https://x.com/ad/status/4",promoted=True),
                 dict(valid,url="https://x.com/quote/status/5",quoted=True),
                 dict(valid,url="https://x.com/offtopic/status/6",text="buy my course"), root]
        def js(code):
            return True if code.startswith("location.href=") else posts
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory)/"posts.sqlite3"
            result = discover_thread_replies(js, MagicMock(), [root], "/owner", db)
            self.assertEqual(len(result),1)
            self.assertEqual(result[0]["thread_context"],root)
            self.assertEqual(result[0]["discovery_source"],"top_thread")
            attempt = reserve_attempt(db,"prior reply",10,"reply",valid["url"])
            finish_attempt(db,attempt,"verified")
            self.assertEqual(discover_thread_replies(js,MagicMock(),[root],"/owner",db),[])

    def test_thread_dom_extracts_only_articles_after_exact_root(self):
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                page.route("https://x.com/**", lambda route: route.fulfill(body=""))
                page.goto("https://x.com/builder/status/1")
                page.set_content('''
                  <article data-testid="tweet"><a href="/ancestor/status/0"><time>now</time></a>
                    <div data-testid="tweetText">ancestor</div></article>
                  <article data-testid="tweet"><a href="/builder/status/1"><time>now</time></a>
                    <div data-testid="tweetText">heyclicky</div></article>
                  <article data-testid="tweet"><a href="/reply/status/2"><time>now</time></a>
                    <div data-testid="tweetText">permission prompts are slow</div></article>''')
                script = "(()=>{"+THREAD_POSTS_JS.replace("EXPECTED_ROOT",'"/builder/status/1"')+"})()"
                posts = page.evaluate(script)
                self.assertEqual([post["url"] for post in posts],["https://x.com/reply/status/2"])
                self.assertIsNone(page.evaluate(script.replace("/builder/status/1","/missing/status/9")))
            finally:
                browser.close()

    def test_thread_scan_reads_candidates_after_one_scroll_per_root_and_is_bounded(self):
        roots = [{"url":f"https://x.com/builder/status/{i}","text":"heyclicky task"} for i in range(3)]
        scrolls = []
        navigations = []
        scrolled = False
        def js(code):
            nonlocal scrolled
            if code.startswith("location.href="):
                navigations.append(code)
                scrolled = False
                return True
            if "scrollIntoView" in code:
                scrolls.append(code)
                scrolled = True
                return True
            if not scrolled:
                return []
            return [{"url":f"https://x.com/commenter/status/{i+100}",
                     "text":"permission prompts are slow"} for i in range(12)]
        with tempfile.TemporaryDirectory() as directory:
            result = discover_thread_replies(js,MagicMock(),roots,"/owner",Path(directory)/"posts.sqlite3")
        self.assertEqual(len(navigations),2)
        self.assertEqual(len(scrolls),2)
        self.assertEqual(len(result),6)

    def test_relevance_excludes_self_ads_and_ambiguous_quotes(self):
        post = {"url": "https://x.com/builder/status/123", "text": "heyclicky keeps asking for permission during a task"}
        self.assertTrue(relevant_post(post, "owner"))
        self.assertFalse(relevant_post(post, "builder"))
        self.assertFalse(relevant_post(dict(post, promoted=True), "owner"))
        self.assertFalse(relevant_post(dict(post, quoted=True), "owner"))
        self.assertFalse(relevant_post(dict(post, text="cruise control in my car"), "owner"))

    def test_model_cannot_pick_unknown_post_or_publish_invalid_text(self):
        candidates = [{"url": "https://x.com/b/status/1", "text": "heyclicky task"}]
        for result in [{"index": 9, "reply": "hello"}, {"index": 0, "reply": ""},
                       {"index": 0, "reply": "https://example.com/fix"}]:
            with patch("organic_replies.ollama.generate", return_value={"response": json.dumps(result)}):
                with self.assertRaises(ValueError):
                    draft_contextual_reply(candidates, {}, "test")
        with patch("organic_replies.ollama.generate", return_value={"response": '{"index":null}'}):
            self.assertIsNone(draft_contextual_reply(candidates, {}, "test"))

    def test_reply_cap_is_independent_and_parent_is_deduplicated(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "posts.sqlite3"
            post = reserve_attempt(db, "standalone", 1)
            finish_attempt(db, post, "verified")
            reply = reserve_attempt(db, "specific reply", 1, "reply", "https://x.com/b/status/1")
            finish_attempt(db, reply, "verified")
            self.assertTrue(published_text(db,"specific reply"))
            self.assertFalse(published_text(db,"fresh reply"))
            with self.assertRaisesRegex(RuntimeError, "Already replied"):
                reserve_attempt(db, "different text", 5, "reply", "https://x.com/b/status/1")
            with self.assertRaisesRegex(RuntimeError, "Daily post limit"):
                reserve_attempt(db, "next reply", 1, "reply", "https://x.com/b/status/2")


if __name__ == "__main__":
    unittest.main()
