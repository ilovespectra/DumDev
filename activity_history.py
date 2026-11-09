from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
import sqlite3


EVENT_STATES = {"discovering_posts", "checking_public_responses", "reading_reply_source",
                "draft_retry",
                "reply_ready", "reply_target_skipped", "no_relevant_reply",
                "verified_published", "interrupted_retry_wait", "recovery_wait",
                "needs_attention", "pending_reconciliation", "awaiting_approval",
                "awaiting_control_approval", "control_granted", "control_rejected",
                "approval_granted", "approval_rejected", "approval_already_verified"}


def record_activity(database: Path, payload: dict, snapshot: dict):
    state = payload.get("state")
    if state not in EVENT_STATES:
        return
    target = payload.get("candidate_url")
    if target is None and state in {"reading_reply_source", "reply_ready", "reply_target_skipped"}:
        target = snapshot.get("reply_context", {}).get("url")
    with closing(sqlite3.connect(database)) as db, db:
        db.execute("""CREATE TABLE IF NOT EXISTS activity_events (
            id INTEGER PRIMARY KEY, at TEXT NOT NULL, state TEXT NOT NULL,
            target TEXT, reason TEXT, publication_url TEXT)""")
        db.execute("INSERT INTO activity_events(at,state,target,reason,publication_url) VALUES (?,?,?,?,?)",
                   (snapshot["updated_at"],state,target,payload.get("error") or payload.get("note"),
                    payload.get("post_url") if state == "verified_published" else None))
        db.execute("DELETE FROM activity_events WHERE id NOT IN (SELECT id FROM activity_events ORDER BY id DESC LIMIT 1000)")


def activity_summary(database: Path) -> dict:
    result = {"recent_events":[], "verified_publications":[], "last_verified_at":None,
              "last_attempt_at":None}
    if not database.exists():
        return result
    with closing(sqlite3.connect(database)) as db:
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='activity_events'").fetchone():
            result["recent_events"] = [dict(zip(("at","state","target","reason","publication_url"),row))
                for row in db.execute("SELECT at,state,target,reason,publication_url FROM activity_events ORDER BY id DESC LIMIT 12")]
            for key,state in (("last_verified_at","verified_published"),("last_attempt_at","reply_ready")):
                result[key] = db.execute("SELECT MAX(at) FROM activity_events WHERE state=?",(state,)).fetchone()[0]
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='posts'").fetchone():
            result["verified_publications"] = [dict(zip(("kind","text","url","day"),row))
                for row in db.execute("SELECT kind,text,url,day FROM posts WHERE state='verified' ORDER BY id DESC LIMIT 8")]
    return result


def recently_skipped(database: Path) -> set[str]:
    if not database.exists():
        return set()
    with closing(sqlite3.connect(database)) as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='activity_events'").fetchone():
            return set()
        cutoff = datetime.now(timezone.utc).timestamp() - 21600
        return {target for at,target in db.execute(
            "SELECT at,target FROM activity_events WHERE state='reply_target_skipped' AND target IS NOT NULL")
            if datetime.fromisoformat(at.replace("Z","+00:00")).timestamp() >= cutoff}
