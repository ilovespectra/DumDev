from __future__ import annotations

import re
import sqlite3
import sys
import time
import uuid
from datetime import datetime
from contextlib import closing
from urllib.parse import urlsplit
import json
import threading
from pathlib import Path

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright


class LoginRequired(RuntimeError):
    pass


def browser_executable(browser: str) -> str:
    executables = {
        "brave": "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
        "chrome": "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    }
    if browser == "auto":
        browser = next((name for name, path in executables.items() if Path(path).is_file()), "")
    if browser not in executables:
        raise RuntimeError("Background mode supports installed Brave or Google Chrome, not Arc or bundled Chromium. Select --browser brave or --browser chrome.")
    executable = executables[browser]
    if not Path(executable).is_file():
        raise RuntimeError(f"Selected browser is not installed: {executable}. No fallback to Chrome for Testing.")
    return executable


def attach_login_diagnostics(page, path: Path) -> None:
    events: list[dict] = []

    def record(kind: str, url: str, status: int | None = None) -> None:
        host = urlsplit(url).hostname
        if not host:
            return
        events.append({"event": kind, "host": host, "status": status,
                       "time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
        path.write_text(json.dumps(events[-50:], indent=2), encoding="utf-8")

    page.on("requestfailed", lambda request: record("request_failed", request.url))
    page.on("response", lambda response: record("http_error", response.url, response.status)
            if response.status >= 400 else None)
    page.on("pageerror", lambda error: record("javascript_error", page.url))


def wait_for_login_confirmation(page) -> None:
    finished = threading.Event()
    errors: list[Exception] = []

    def read_confirmation() -> None:
        try:
            input()
        except Exception as exc:
            errors.append(exc)
        finally:
            finished.set()

    threading.Thread(target=read_confirmation, daemon=True).start()
    while not finished.is_set():
        if page.is_closed():
            raise RuntimeError("Login window was closed before confirmation.")
        page.wait_for_timeout(250)
    if errors:
        raise RuntimeError("Terminal confirmation could not be read.") from errors[0]


def reserve_attempt(database: Path, text: str, limit: int, kind: str = "post", parent: str | None = None) -> int:
    database.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(database)) as db, db:
        db.execute("CREATE TABLE IF NOT EXISTS posts (id INTEGER PRIMARY KEY, day TEXT, text TEXT, state TEXT, url TEXT)")
        columns = {row[1] for row in db.execute("PRAGMA table_info(posts)")}
        if "kind" not in columns:
            db.execute("ALTER TABLE posts ADD COLUMN kind TEXT NOT NULL DEFAULT 'post'")
        if "parent" not in columns:
            db.execute("ALTER TABLE posts ADD COLUMN parent TEXT")
        db.execute("BEGIN IMMEDIATE")
        if db.execute("SELECT 1 FROM posts WHERE state = 'pending'").fetchone():
            raise RuntimeError("An earlier submission is unverified. Check your X profile before clearing its pending ledger entry; automatic retries are blocked.")
        day = datetime.now().astimezone().date().isoformat()
        count = db.execute("SELECT COUNT(*) FROM posts WHERE day = ? AND kind = ? AND state IN ('pending', 'verified')", (day, kind)).fetchone()[0]
        if count >= limit:
            raise RuntimeError("Daily post limit reached. Restarting or bumping does not override it.")
        if db.execute("SELECT 1 FROM posts WHERE text = ? AND state = 'verified'", (text,)).fetchone():
            raise RuntimeError("Duplicate published text refused.")
        if parent and db.execute("SELECT 1 FROM posts WHERE parent = ? AND state IN ('pending', 'verified')", (parent,)).fetchone():
            raise RuntimeError("Already replied to this post; refusing repeated outreach.")
        cursor = db.execute("INSERT INTO posts (day, text, state, kind, parent) VALUES (?, ?, 'pending', ?, ?)", (day, text, kind, parent))
        return cursor.lastrowid


def finish_attempt(database: Path, attempt: int, state: str, url: str | None = None) -> None:
    with closing(sqlite3.connect(database)) as db, db:
        db.execute("UPDATE posts SET state = ?, url = ? WHERE id = ?", (state, url, attempt))


def require_login(page) -> str:
    page.goto("https://x.com/home", wait_until="domcontentloaded")
    profile = page.locator('[data-testid="AppTabBar_Profile_Link"]')
    try:
        profile.wait_for(state="visible", timeout=20000)
    except PlaywrightTimeoutError as exc:
        raise LoginRequired("Dedicated browser is not logged in, or X requires a challenge. Run start-twitter-agent.sh --login, sign in manually, then restart. No restriction bypass is attempted.") from exc
    path = profile.get_attribute("href") or ""
    if not re.fullmatch(r"/[A-Za-z0-9_]+", path):
        raise RuntimeError("Cannot identify the signed-in account's profile.")
    return path


def matching_post(page, text: str, profile: str) -> str | None:
    articles = page.locator('[data-testid="tweet"]')
    for index in range(articles.count()):
        article = articles.nth(index)
        body = article.locator('[data-testid="tweetText"]')
        if body.count() != 1 or body.inner_text().strip() != text:
            continue
        href = article.locator('a:has(time)').first.get_attribute("href") or ""
        if re.fullmatch(re.escape(profile) + r"/status/\d+", href, flags=re.I):
            return "https://x.com" + href
    return None


def post_in_background(args, text: str, settings: dict, report) -> str:
    executable = browser_executable(getattr(args, "browser", "auto"))
    root = Path(args.status_file).resolve().parent
    evidence_id = uuid.uuid4().hex
    evidence = root / "evidence" / evidence_id
    evidence.mkdir(parents=True, exist_ok=True)
    database = root / "posts.sqlite3"
    profile_dir = Path(args.user_data_dir or Path(__file__).parent / "browser-profile").resolve()
    if "Application Support" in profile_dir.parts:
        raise RuntimeError("Refusing to use a personal browser profile. Use the agent's dedicated browser-profile directory.")
    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            str(profile_dir), executable_path=executable, headless=not args.login,
            chromium_sandbox=True,
            viewport={"width": 1280, "height": 1000},
        )
        try:
            report(args.status_file, {"browser": executable, "browser_user_data_dir": str(profile_dir)})
            page = context.pages[0] if context.pages else context.new_page()
            page.set_default_timeout(15000)
            if args.login:
                diagnostics = root / "login-diagnostics.json"
                diagnostics.write_text("[]", encoding="utf-8")
                attach_login_diagnostics(page, diagnostics)
                page.goto("https://x.com/login", wait_until="domcontentloaded")
                print("Sign in to X in this dedicated browser profile. Your existing Arc/Brave login is not copied. Then press Enter here. No tweet will be sent.", flush=True)
                print(f"If sign-in stalls, HTTP failures and JavaScript error counts are recorded in {diagnostics}. No credentials, request bodies, or URL paths/query strings are recorded.", flush=True)
                wait_for_login_confirmation(page)
                profile = require_login(page)
                expected = settings.get("account_handle")
                if expected and profile[1:].casefold() != expected.casefold():
                    raise LoginRequired(f"Signed in as {profile}; switch to @{expected} and verify again.")
                report(args.status_file, {"state": "login_ready", "account": profile[1:], "error": None})
                return ""
            profile = require_login(page)
            expected = settings.get("account_handle")
            if expected and profile[1:].casefold() != expected.casefold():
                raise LoginRequired(f"Signed-in account does not match @{expected}; publication blocked.")
            report(args.status_file, {"account": profile[1:], "state": "checking_account"})
            page.goto("https://x.com" + profile, wait_until="domcontentloaded")
            page.locator('[data-testid="primaryColumn"]').wait_for()
            page.screenshot(path=str(evidence / "before.png"))
            if matching_post(page, text, profile):
                raise RuntimeError("This text already appears on your account; refusing to publish it again.")
            page.goto("https://x.com/compose/post", wait_until="domcontentloaded")
            dialog = page.get_by_role("dialog").filter(has=page.locator('[data-testid="tweetTextarea_0"]')).last
            dialog.locator('[data-testid="tweetTextarea_0"]').fill(text)
            button = dialog.locator('[data-testid="tweetButton"]')
            button.wait_for(state="visible")
            if not button.is_enabled():
                raise RuntimeError("X has disabled submission. Check the composer or account restriction.")
            attempt = reserve_attempt(database, text, int(settings["posts_per_day"]))
            report(args.status_file, {"state": "posting_tweet", "error": None})
            # Anything after reservation is ambiguous until a published permalink is verified.
            button.click()
            report(args.status_file, {"state": "verifying_post"})
            for _ in range(3):
                page.goto("https://x.com" + profile, wait_until="domcontentloaded")
                page.locator('[data-testid="tweet"]').first.wait_for()
                url = matching_post(page, text, profile)
                if url:
                    page.goto(url, wait_until="domcontentloaded")
                    page.locator('[data-testid="tweetText"]').first.wait_for()
                    if matching_post(page, text, profile) != url:
                        raise RuntimeError("Published permalink did not contain the exact text.")
                    page.screenshot(path=str(evidence / "after.png"))
                    finish_attempt(database, attempt, "verified", url)
                    report(args.status_file, {
                        "state": "verified_published", "post_url": url,
                        "last_verified_tweet": text,
                        "posted_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                        "evidence_before": f"/evidence/{evidence_id}/before.png",
                        "evidence_after": f"/evidence/{evidence_id}/after.png", "error": None,
                    })
                    return url
                time.sleep(5)
            raise RuntimeError("Submission attempted, but no exact published post was found. Retry blocked to avoid duplicates.")
        except Exception as exc:
            report(args.status_file, {
                "state": "login_required" if isinstance(exc, LoginRequired) else "failed_or_unverified",
                "error": str(exc),
            })
            if context.pages:
                try:
                    context.pages[0].screenshot(path=str(evidence / "failure.png"))
                except Exception as screenshot_error:
                    print(f"Failure screenshot could not be captured: {screenshot_error}", file=sys.stderr)
            raise
        finally:
            context.close()
