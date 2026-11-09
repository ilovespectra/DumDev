import sqlite3
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from campaign_schedule import campaign_action


class CampaignTests(unittest.TestCase):
    def test_both_action_types_caps_spacing_and_day_rollover(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory)/"posts.sqlite3"
            now = datetime(2026,11,8,10,0).astimezone()
            settings = {"posts_per_day":3,"replies_per_day":100}
            action, _, _ = campaign_action(db,settings,now)
            self.assertEqual(action,"post")
            with sqlite3.connect(db) as connection:
                connection.execute("CREATE TABLE posts(kind TEXT,day TEXT,state TEXT)")
                connection.execute("INSERT INTO posts VALUES('post','2026-11-08','verified')")
            self.assertEqual(campaign_action(db,settings,now)[0],"reply")
            with sqlite3.connect(db) as connection:
                connection.execute("INSERT INTO campaign_cycles VALUES(?)",(now.timestamp(),))
            action, wait, _ = campaign_action(db,settings,now)
            self.assertIsNone(action)
            self.assertEqual(wait,864)
            self.assertEqual(campaign_action(db,settings,now.replace(hour=14))[0],"post")
            with sqlite3.connect(db) as connection:
                connection.execute("INSERT INTO posts VALUES('post','2026-11-08','pending')")
            with self.assertRaisesRegex(RuntimeError,"Pending"):
                campaign_action(db,settings,now)

    def test_zero_caps_and_invalid_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            db=Path(directory)/"posts.sqlite3"
            self.assertIsNone(campaign_action(db,{"posts_per_day":0,"replies_per_day":0})[0])
            with self.assertRaises(ValueError):
                campaign_action(db,{"posts_per_day":3,"replies_per_day":-1})
