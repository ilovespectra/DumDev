from __future__ import annotations

import math
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path


def campaign_action(database: Path, settings: dict, now: datetime | None = None) -> tuple[str | None, float, dict]:
    now = now or datetime.now().astimezone()
    caps = {}
    for kind, key in [("post", "posts_per_day"), ("reply", "replies_per_day")]:
        value = settings[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0 or int(value) != value:
            raise ValueError(f"{key} must be a nonnegative integer.")
        caps[kind] = int(value)
    database.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(database)) as db, db:
        db.execute("CREATE TABLE IF NOT EXISTS campaign_cycles (started_at REAL NOT NULL)")
        counts = {"post": 0, "reply": 0}
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='posts'").fetchone():
            if db.execute("SELECT 1 FROM posts WHERE state='pending'").fetchone():
                raise RuntimeError("Pending publication blocks campaign; verify it before resuming.")
            for kind, count in db.execute("SELECT kind,COUNT(*) FROM posts WHERE day=? AND state='verified' GROUP BY kind", (now.date().isoformat(),)):
                counts[kind] = count
        last = db.execute("SELECT MAX(started_at) FROM campaign_cycles").fetchone()[0]
    summary = {"day": now.date().isoformat(), "published": counts, "caps": caps}
    # Distribute standalone opportunities through waking hours, in local time.
    due = [now.replace(hour=9, minute=0, second=0, microsecond=0) + timedelta(hours=10*i/max(1,caps["post"]-1))
           for i in range(caps["post"])]
    summary["next_post_at"] = due[counts["post"]].isoformat() if counts["post"] < len(due) else None
    if counts["post"] < len(due) and now >= due[counts["post"]]:
        return "post", 0, summary
    reply_interval = max(60, 86400 / caps["reply"]) if caps["reply"] else 86400
    reply_wait = max(0, (last or 0) + reply_interval - now.timestamp())
    if counts["reply"] < caps["reply"] and reply_wait == 0:
        return "reply", 0, summary
    midnight = (now + timedelta(days=1)).replace(hour=0,minute=0,second=0,microsecond=0)
    waits = [(midnight-now).total_seconds()]
    if counts["post"] < len(due):
        waits.append(max(1,(due[counts["post"]]-now).total_seconds()))
    if counts["reply"] < caps["reply"]:
        waits.append(max(1,reply_wait))
    return None, min(waits), summary


def record_discovery_cycle(database: Path) -> None:
    with closing(sqlite3.connect(database)) as db, db:
        db.execute("INSERT INTO campaign_cycles VALUES (?)", (datetime.now().timestamp(),))
        db.execute("DELETE FROM campaign_cycles WHERE started_at < ?", (datetime.now().timestamp()-172800,))
