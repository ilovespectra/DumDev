from __future__ import annotations

from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
import re
import sqlite3


STOP_WORDS = set("""
about across after again agent agents also another anyone around because before being between
below build building built clicky could does doing drop each even every first
from have having here heyclicky into just like love made make making more most
much need only other over please post really same share should some something
still such than that their them then there these they thing think this those
through today under using very want what when where which while will with without work would
your yours https http www com thanks thank great cool amazing
""".split())


def connect(database: Path):
    database.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(database)
    db.execute("""CREATE TABLE IF NOT EXISTS discovery_runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT, scope TEXT NOT NULL, query TEXT NOT NULL)""")
    db.execute("""CREATE TABLE IF NOT EXISTS discovery_observations (
        scope TEXT NOT NULL, url TEXT NOT NULL, mode TEXT NOT NULL, text TEXT NOT NULL,
        created_at TEXT, observed_at REAL NOT NULL, PRIMARY KEY(scope,url,mode))""")
    return db


def discovery_scope(topics: list[str] | None) -> str:
    return "\n".join(sorted({topic.strip().casefold() for topic in topics or [] if topic.strip()})) or "heyclicky"


def remember_posts(database: Path, posts: list[dict], scope: str, mode: str) -> None:
    now = datetime.now(timezone.utc).timestamp()
    with closing(connect(database)) as db, db:
        for post in posts:
            if (not re.fullmatch(r"https://x\.com/[A-Za-z0-9_]+/status/\d+", post.get("url", ""))
                    or not isinstance(post.get("text"), str)
                    or post.get("promoted") or post.get("quoted")):
                continue
            db.execute("INSERT OR REPLACE INTO discovery_observations VALUES (?,?,?,?,?,?)",
                       (scope, post["url"], mode, post["text"][:3000], post.get("created_at"), now))
        db.execute("DELETE FROM discovery_observations WHERE observed_at < ?", (now - 7 * 86400,))
        db.execute("""DELETE FROM discovery_observations WHERE rowid NOT IN
                      (SELECT rowid FROM discovery_observations ORDER BY observed_at DESC LIMIT 1000)""")


def discovery_trends(database: Path, scope: str, *, min_authors: int = 2) -> list[dict]:
    now = datetime.now(timezone.utc)
    terms: dict[str, dict] = {}
    with closing(connect(database)) as db:
        rows = db.execute("""SELECT url,mode,text,created_at FROM discovery_observations
                             WHERE scope=? AND observed_at>=?""",
                          (scope, now.timestamp() - 7 * 86400)).fetchall()
    for url, mode, text, created_at in rows:
        if created_at:
            try:
                created = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
            except ValueError as exc:
                raise ValueError(f"Invalid discovery timestamp for {url}: {created_at}") from exc
            if created.tzinfo is None:
                raise ValueError(f"Discovery timestamp has no timezone for {url}.")
            if (now - created).total_seconds() > 14 * 86400:
                continue
        text = re.sub(r"https?://\S+|@[A-Za-z0-9_]+", " ", text.casefold())
        words = re.findall(r"[a-z][a-z-]{2,24}", text)[:150]
        found = {word for word in words if word not in STOP_WORDS and word not in scope.split()}
        found.update(a + " " + b for a, b in zip(words, words[1:])
                     if a in found and b in found)
        for term in found:
            evidence = terms.setdefault(term, {"urls": set(), "authors": set(), "modes": set()})
            evidence["urls"].add(url)
            evidence["authors"].add(url.split("/")[3].casefold())
            evidence["modes"].add("recent" if mode.startswith("recent") else "top")
    ranked = []
    for term, evidence in terms.items():
        if len(evidence["authors"]) < min_authors:
            continue
        ranked.append({"term": term, "posts": len(evidence["urls"]),
                       "authors": len(evidence["authors"]),
                       "modes": sorted(evidence["modes"]),
                       "sources": sorted(evidence["urls"])[:6],
                       "cross_mode": len(evidence["modes"]) == 2})
    return sorted(ranked, key=lambda row: (
        -row["cross_mode"], -row["authors"], -row["posts"],
        -len(row["term"].split()), row["term"]))[:20]


def next_search(database: Path, scope: str, profile: str, *, seed: bool,
                invitations: list[str] | None = None) -> str | None:
    with closing(connect(database)) as db, db:
        previous = [row[0] for row in db.execute(
            "SELECT query FROM discovery_runs WHERE scope=? ORDER BY id DESC LIMIT 24", (scope,))]
        cycle = db.execute("SELECT COUNT(*) FROM discovery_runs WHERE scope=?", (scope,)).fetchone()[0]
        if invitations:
            choices = list(invitations)
        else:
            anchors = scope.splitlines()
            since = (datetime.now(timezone.utc) - timedelta(days=(3, 7, 1, 14)[cycle % 4])).date()
            anchor = '"' + anchors[cycle % len(anchors)].replace('"', '') + '"'
            suffix = f" -from:{profile.lstrip('/')} since:{since.isoformat()}"
            if seed:
                choices = [anchor + suffix]
            else:
                trends = discovery_trends(database, scope, min_authors=1)
                choices = [anchor + ' "' + row["term"] + '"' + suffix for row in trends]
        def signature(query):
            return re.sub(r" since:\d{4}-\d{2}-\d{2}$", "", query)
        seen = set(previous if seed else [signature(query) for query in previous])
        query = next((query for query in choices if (query if seed else signature(query)) not in seen), None)
        if query is None and seed:
            query = choices[cycle % len(choices)]
        if query is not None:
            db.execute("INSERT INTO discovery_runs(scope,query) VALUES (?,?)", (scope, query))
        return query


def discovery_snapshot(database: Path, scope: str) -> dict:
    trends = discovery_trends(database, scope)
    with closing(connect(database)) as db:
        queries = [row[0] for row in db.execute(
            "SELECT query FROM discovery_runs WHERE scope=? ORDER BY id DESC LIMIT 6", (scope,))]
    return {"queries": queries, "trends": trends,
            "note": "Themes observed in sampled threads, not platform-wide trends."}
