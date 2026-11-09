from contextlib import closing
from pathlib import Path
import sqlite3
import time

from arc_poster import UserInputInterruption


def pending_submission(database: Path) -> bool:
    if not database.exists():
        return False
    with closing(sqlite3.connect(database)) as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='posts'").fetchone():
            return False
        return db.execute("SELECT 1 FROM posts WHERE state='pending'").fetchone() is not None


def retry_interrupted(action, database: Path, report, status_file: str):
    interruptions = 0
    while True:
        try:
            return action()
        except UserInputInterruption as exc:
            interruptions += 1
            print(f"takeover paused (retry {interruptions}): {exc}", flush=True)
            if pending_submission(database):
                # Submission may have happened: never repeat the publication.
                report(status_file, {"state": "pending_reconciliation", "error": str(exc),
                                     "note": "Input or focus interrupted a possible submission. Publication retries are blocked."})
                raise
            report(status_file, {"state": "interrupted_retry_wait", "error": str(exc),
                                 "sleep_seconds": 30, "interruption_retries": interruptions,
                                 "note": "Retrying the same action after 30 seconds with a fresh countdown."})
            time.sleep(30)
