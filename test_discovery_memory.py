from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock
from urllib.parse import parse_qs, urlsplit

from discovery_memory import discovery_scope, discovery_snapshot, discovery_trends, next_search, remember_posts
from organic_replies import discover_posts, discover_thread_replies


class DiscoveryMemoryTests(unittest.TestCase):
    def test_trends_combine_independent_authors_in_top_and_recent_with_sources(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "posts.sqlite3"
            for mode, author in (("top", "a"), ("recent", "b")):
                remember_posts(db, [{"url": f"https://x.com/{author}/status/1",
                                     "text": "heyclicky browser tabs need better memory"}], "heyclicky", mode)
            trends = discovery_trends(db, "heyclicky")
            trend = next(row for row in trends if row["term"] == "browser tabs")
            self.assertEqual(trend["authors"], 2)
            self.assertEqual(trend["modes"], ["recent", "top"])
            self.assertTrue(trend["cross_mode"])
            self.assertEqual(len(trend["sources"]), 2)
            self.assertEqual(discovery_trends(db, "gardening"), [])

    def test_duplicate_author_stale_promoted_and_quoted_posts_do_not_make_trends(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "posts.sqlite3"
            old = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
            for mode in ("top", "recent"):
                remember_posts(db, [
                    {"url": "https://x.com/a/status/1", "text": "heyclicky browser tabs"},
                    {"url": "https://x.com/a/status/2", "text": "heyclicky browser tabs"},
                    {"url": "https://x.com/b/status/3", "text": "heyclicky browser tabs", "created_at": old},
                    {"url": "https://x.com/c/status/4", "text": "heyclicky browser tabs", "promoted": True},
                    {"url": "https://x.com/d/status/5", "text": "heyclicky browser tabs", "quoted": True},
                ], "heyclicky", mode)
            self.assertEqual(discovery_trends(db, "heyclicky"), [])

    def test_queries_adapt_within_cycle_and_rotate_across_restarts(self):
        queries = []
        current = {}

        def js(code):
            if code.startswith("location.href="):
                url = json.loads(code[len("location.href="):].split(";return")[0])
                query = parse_qs(urlsplit(url).query)["q"][0]
                current["mode"] = "recent" if "&f=live" in url else "top"
                if current["mode"] == "top":
                    queries.append(query)
                return True
            mode = current["mode"]
            return [{"url": f"https://x.com/{mode}/status/1",
                     "text": "heyclicky browser tabs forget memory"}]

        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "posts.sqlite3"
            first = discover_posts(js, MagicMock(), "/owner", db)
            first_queries = list(queries)
            self.assertEqual(len(first_queries), 3)
            self.assertIn('"browser tabs"', first_queries[1])
            self.assertTrue(all('-from:owner' in query for query in first_queries))
            self.assertEqual(len(first), 2)
            discover_posts(js, MagicMock(), "/owner", Path(str(db)))
            self.assertNotEqual(first_queries, queries[3:])
            self.assertTrue(discovery_snapshot(db, "heyclicky")["trends"])

    def test_single_seed_observed_in_both_modes_keeps_provenance(self):
        def js(code):
            return True if code.startswith("location.href=") else [
                {"url": "https://x.com/a/status/1", "text": "heyclicky browser tabs"}]
        with tempfile.TemporaryDirectory() as directory:
            posts = discover_posts(js, MagicMock(), "/owner", Path(directory) / "posts.sqlite3")
        self.assertEqual(len(posts), 1)
        self.assertEqual(posts[0]["search_modes"], ["top", "recent"])

    def test_recent_thread_details_feed_future_queries(self):
        root = {"url": "https://x.com/a/status/1", "text": "heyclicky browser memory",
                "discovery_source": "recent"}
        reply = {"url": "https://x.com/b/status/2", "text": "browser memory disappears between tasks"}
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "posts.sqlite3"
            remember_posts(db, [root], "heyclicky", "top")
            posts = discover_thread_replies(
                lambda code: True if code.startswith("location.href=") else
                False if "scrollIntoView" in code else [reply],
                MagicMock(), [root], "/owner", db)
            self.assertEqual(posts[0]["discovery_source"], "recent_thread")
            query = next_search(db, "heyclicky", "/owner", seed=False)
            self.assertIn('"browser memory"', query)
            self.assertEqual(discovery_trends(db, "heyclicky")[0]["modes"], ["recent", "top"])

    def test_selected_topics_scope_and_seed_are_not_legacy_product_queries(self):
        with tempfile.TemporaryDirectory() as directory:
            scope = discovery_scope(["Gardening", "Soil health"])
            query = next_search(Path(directory) / "posts.sqlite3", scope, "/gardener", seed=True)
            self.assertIn('"gardening"', query)
            self.assertNotIn("heyclicky", query)

    def test_invitation_rotation_continues_after_every_query_has_been_used(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "posts.sqlite3"
            choices = ["first invitation", "second invitation", "third invitation"]
            queries = [next_search(db, "invites", "/owner", seed=True, invitations=choices)
                       for _ in range(7)]
            self.assertEqual(queries, choices + choices + choices[:1])


if __name__ == "__main__":
    unittest.main()
