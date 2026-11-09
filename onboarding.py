"""Local setup validation and browser checks; never submits X content."""
from __future__ import annotations

import ctypes
import json
import math
from pathlib import Path
import re
import sqlite3
import subprocess
from contextlib import closing
from urllib.parse import urlsplit
from urllib.request import urlopen
from runtime_paths import PACKAGED, ROOT

BROWSERS = {
    "arc": ("Arc", "/Applications/Arc.app"),
    "brave": ("Brave Browser", "/Applications/Brave Browser.app"),
    "chrome": ("Google Chrome", "/Applications/Google Chrome.app"),
}
PERMISSION_PANES = {
    "accessibility": "Privacy_Accessibility",
    "input": "Privacy_ListenEvent",
    "screen": "Privacy_ScreenCapture",
    "automation": "Privacy_Automation",
}
DEFAULT_VOICE = "Be thoughtful, specific, and conversational. Use only facts I supplied. Never invent personal experiences."
MODEL = "qwen2.5:3b"


def model_status() -> dict:
    with urlopen("http://127.0.0.1:11434/api/tags", timeout=5) as response:
        models = json.load(response).get("models", [])
    return {"ready": any(model.get("name") == MODEL for model in models), "model": MODEL}


def read_settings(path: Path) -> dict:
    if not path.exists():
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Settings must be a JSON object. Fix the local settings file before continuing.")
    return value


def write_settings(path: Path, settings: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(settings, indent=2, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def setup_required(settings: dict) -> bool:
    return bool(settings.get("setup_in_progress")) or (
        not settings.get("onboarding_completed") and not settings.get("account_handle"))


def validate_setup(payload: dict) -> dict:
    result = {}
    browser = payload.get("browser")
    if not isinstance(browser, str) or browser not in BROWSERS:
        raise ValueError("Choose Arc, Brave, or Google Chrome.")
    result["browser"] = browser
    for key, limit in [("account_handle", 15), ("profile_name", 100), ("profile_description", 4000),
                       ("attitude", 4000)]:
        value = payload.get(key)
        normalized = value.strip().removeprefix("@") if key == "account_handle" and isinstance(value, str) else value
        if not isinstance(normalized, str) or not normalized.strip() or len(normalized.strip()) > limit:
            raise ValueError(f"{key.replace('_', ' ').capitalize()} is required (maximum {limit} characters).")
        result[key] = normalized.strip()
    result["account_handle"] = result["account_handle"].removeprefix("@")
    if not re.fullmatch(r"[A-Za-z0-9_]{1,15}", result["account_handle"]):
        raise ValueError("Enter an X handle, not a profile URL.")
    webpage = payload.get("approved_webpage", "")
    if not isinstance(webpage, str):
        raise ValueError("Webpage must be an HTTPS URL or empty.")
    webpage = webpage.strip()
    if webpage and not re.fullmatch(r"https://[a-z0-9.-]+(?:/[a-zA-Z0-9/_-]*)?", webpage):
        raise ValueError("Use an exact HTTPS webpage without query strings or fragments.")
    if webpage and not urlsplit(webpage).hostname:
        raise ValueError("Webpage needs a valid hostname.")
    result["approved_webpage"] = webpage
    topics = payload.get("topics")
    if (not isinstance(topics, list) or not 1 <= len(topics) <= 12 or
            any(not isinstance(topic, str) or not topic.strip() or len(topic.strip()) > 80 for topic in topics)):
        raise ValueError("Choose 1 to 12 topics, each at most 80 characters.")
    result["topics"] = list(dict.fromkeys(topic.strip() for topic in topics))
    for key, maximum in [("posts_per_day", 20), ("replies_per_day", 100)]:
        value = payload.get(key)
        if type(value) is not int or not 0 <= value <= maximum:
            raise ValueError(f"{key.replace('_', ' ')} must be an integer between 0 and {maximum}.")
        result[key] = value
    if browser != "arc":
        result["replies_per_day"] = 0
    for key in ["min_gap_hours", "max_gap_hours"]:
        value = payload.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0.25 <= value <= 24:
            raise ValueError("Post gaps must be between 0.25 and 24 hours.")
        result[key] = value
    if result["max_gap_hours"] < result["min_gap_hours"]:
        raise ValueError("Maximum gap cannot be shorter than minimum gap.")
    result["first_day_no_sleep"] = False
    result["check_public_responses"] = bool(payload.get("check_public_responses", False))
    return result


def has_account_history(directory: Path) -> bool:
    if (directory / "approval-request.json").exists():
        return True
    database = directory / "posts.sqlite3"
    if not database.exists():
        return False
    with closing(sqlite3.connect(database)) as db:
        return bool(db.execute("SELECT 1 FROM sqlite_master WHERE name='posts'").fetchone()
                    and db.execute("SELECT 1 FROM posts LIMIT 1").fetchone())


def permission_status() -> dict:
    helper = ROOT / "takeover-helper"
    if not helper.is_file() or (ROOT / "takeover.swift").stat().st_mtime > helper.stat().st_mtime:
        if PACKAGED:
            raise RuntimeError("The bundled control helper is missing. Reinstall DumDev.")
        subprocess.run(["swiftc", str(ROOT / "takeover.swift"), "-o", str(helper)],
                       check=True, capture_output=True, text=True, timeout=120)
    result = subprocess.run([str(helper), "--check-permissions"], check=True,
                            capture_output=True, text=True, timeout=10)
    permissions = json.loads(result.stdout)
    services = ctypes.CDLL("/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices")
    services.AXIsProcessTrusted.restype = ctypes.c_bool
    permissions["accessibility"] = bool(services.AXIsProcessTrusted())
    return permissions


def open_setup_destination(destination: str) -> None:
    if not isinstance(destination, str) or destination not in PERMISSION_PANES:
        raise ValueError("Unknown permission destination.")
    if destination in {"input", "screen"}:
        permission_status()
        subprocess.run([str(ROOT / "takeover-helper"), f"--request-{destination}-permission"],
                       check=True, capture_output=True, text=True, timeout=30)
    subprocess.run(["open", "x-apple.systempreferences:com.apple.preference.security?"
                    + PERMISSION_PANES[destination]], check=True, capture_output=True, text=True, timeout=10)


def verify_arc_account(handle: str) -> str:
    from arc_poster import applescript_string, decode_arc_result, find_x_tab, run_script
    window, tab, _ = find_x_tab(handle)
    script = """JSON.stringify({origin:location.origin,
        profile:document.querySelector('[data-testid="AppTabBar_Profile_Link"]')?.getAttribute('href')})"""
    result = decode_arc_result(run_script(
        f'tell application "Arc" to tell tab id {applescript_string(tab)} '
        f'of window id {applescript_string(window)} to execute javascript {applescript_string(script)}'))
    if (not isinstance(result, dict) or result.get("origin") != "https://x.com" or
            str(result.get("profile", "")).casefold() != ("/" + handle).casefold()):
        raise ValueError(f"Arc is not signed in as @{handle}. In X, use the account menu to switch accounts, then check again.")
    return handle
