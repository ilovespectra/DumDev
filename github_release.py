"""Owner-only GitHub draft releases for the local DumDev maintainer console."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
from urllib.request import Request, urlopen

OWNER = "ilovespectra"
REPO = "dumdev"
MODEL = "qwen2.5:3b"


def _run(args: list[str], *, timeout: int = 30, cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=timeout)


def _remote_is_canonical(source_root: Path) -> bool:
    result = _run(["git", "remote", "get-url", "origin"], cwd=source_root)
    if result.returncode:
        return False
    remote = result.stdout.strip().lower()
    return bool(re.fullmatch(r"(?:https://github\.com/|git@github\.com:)ilovespectra/dumdev(?:\.git)?", remote))


def github_status(source_root: Path) -> dict:
    """Report owner release capability without exposing any credential material."""
    if not _remote_is_canonical(source_root):
        return {"enabled": False, "account": "", "message": "This source is not the canonical ilovespectra/dumdev repository; builds stay local."}
    try:
        who = _run(["gh", "api", "user", "--jq", ".login"], timeout=15, cwd=source_root)
        if who.returncode:
            return {"enabled": False, "account": "", "message": "GitHub CLI is not authenticated here; builds stay local."}
        account = who.stdout.strip()
        if account.casefold() != OWNER.casefold():
            return {"enabled": False, "account": account, "message": "Only the ilovespectra owner account can create GitHub drafts; builds stay local."}
        permission = _run(["gh", "api", f"repos/{OWNER}/{REPO}", "--jq", ".permissions.push"], timeout=15, cwd=source_root)
        if permission.returncode or permission.stdout.strip().lower() != "true":
            return {"enabled": False, "account": account, "message": "The authenticated account lacks push access to the release repository; builds stay local."}
        return {"enabled": True, "account": account, "message": "Owner authenticated. Verified builds can be uploaded as unpublished GitHub drafts."}
    except (OSError, subprocess.TimeoutExpired):
        return {"enabled": False, "account": "", "message": "Could not verify GitHub CLI access; builds stay local."}


def _ollama_notes(release: dict, commit_summary: str) -> str:
    facts = {
        "version": release["version"],
        "platform": release["platform"],
        "source_commit": release["source_commit"],
        "verified_commit_summary": commit_summary.strip(),
        "sha256": release["sha256"],
        "notarized": bool(release.get("notarized")),
        "ollama_required": bool(release.get("ollama_required")),
    }
    prompt = (
        "Write factual, concise Markdown release notes for this DumDev release using only the supplied facts. "
        "Do not infer features, fixes, security claims, dates, or compatibility beyond them. "
        "Return a short heading and 1-3 bullets. Mention that this is an Apple silicon macOS package, "
        "state the verified change from the commit summary, and do not call it notarized.\n\n"
        f"Verified release facts (JSON):\n{json.dumps(facts, ensure_ascii=False)}"
    )
    payload = json.dumps({"model": MODEL, "prompt": prompt, "stream": False, "options": {"temperature": 0}}).encode()
    req = Request("http://127.0.0.1:11434/api/generate", data=payload, headers={"Content-Type": "application/json"})
    with urlopen(req, timeout=90) as response:
        result = json.load(response)
    notes = result.get("response", "").strip()
    if not notes:
        raise RuntimeError("Ollama returned empty release notes; no GitHub draft was created.")
    # Do not let a model response imply notarization when the signed manifest says otherwise.
    if not release.get("notarized") and re.search(r"\bnotarized\b", notes, re.I):
        notes = re.sub(r"\bnotarized\b", "not notarized", notes, flags=re.I)
    return notes


def _matching_release_ids(source_root: Path, tag: str, title: str) -> list[str]:
    query = f'.[] | select(.tag_name == {json.dumps(tag)} or .name == {json.dumps(title)}) | .id'
    result = _run(["gh", "api", f"repos/{OWNER}/{REPO}/releases?per_page=100", "--paginate", "--jq", query], cwd=source_root)
    if result.returncode:
        raise RuntimeError("Could not inspect GitHub releases: " + (result.stderr or result.stdout)[-500:])
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def _get_release(source_root: Path, release_id: str) -> dict:
    result = _run(["gh", "api", f"repos/{OWNER}/{REPO}/releases/{release_id}"], cwd=source_root)
    if result.returncode:
        raise RuntimeError("Could not read the GitHub draft back: " + (result.stderr or result.stdout)[-500:])
    return json.loads(result.stdout)


def create_github_draft(*, source_root: Path, release: dict | None = None,
                        progress=lambda _message: None) -> dict:
    """Upload a verified zip and checksum to an unpublished GitHub release draft."""
    import release_tools

    status = github_status(source_root)
    if not status["enabled"]:
        raise PermissionError(status["message"])
    release = release or release_tools.latest_release()
    if not release:
        raise ValueError("Build a verified package before creating a GitHub draft.")
    version = str(release.get("version", ""))
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ValueError("The staged release version is invalid.")
    archive = release_tools.RELEASE_DIR / str(release.get("file", ""))
    if not archive.is_file():
        raise ValueError("The staged release archive is missing.")
    with archive.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    if digest != release.get("sha256"):
        raise ValueError("Release checksum changed. Rebuild and verify before creating a draft.")
    source_commit = str(release.get("source_commit", ""))
    if not re.fullmatch(r"[0-9a-f]{40}", source_commit):
        raise ValueError("The staged source commit is invalid.")
    current = _run(["git", "rev-parse", "HEAD"], cwd=source_root)
    if current.returncode or current.stdout.strip() != source_commit:
        raise ValueError("The staged package is not from the selected source's current commit.")

    tag = f"v{version}"
    title = f"DumDev {version}"
    existing_ids = _matching_release_ids(source_root, tag, title)
    if existing_ids:
        raise ValueError(f"A GitHub release or draft already exists for {tag}; refusing to overwrite it.")

    summary = _run(["git", "show", "-s", "--format=%s%n%b", source_commit], cwd=source_root)
    if summary.returncode:
        raise RuntimeError("Could not read the verified source commit summary.")
    progress("Generating factual release notes with local Ollama…")
    notes = _ollama_notes(release, summary.stdout)
    with tempfile.TemporaryDirectory(prefix="dumdev-github-draft-") as directory:
        folder = Path(directory)
        checksum = folder / f"{archive.name}.sha256"
        checksum.write_text(f"{digest}  {archive.name}\n", encoding="utf-8")
        note_file = folder / "release-notes.md"
        note_file.write_text(notes.rstrip() + "\n", encoding="utf-8")
        progress(f"Uploading {archive.name} and its SHA-256 checksum to an unpublished GitHub draft…")
        created = _run([
            "gh", "release", "create", tag,
            "--repo", f"{OWNER}/{REPO}", "--target", source_commit,
            "--title", f"DumDev {version}", "--draft", "--notes-file", str(note_file),
            str(archive), str(checksum),
        ], timeout=300, cwd=source_root)
        checksum_size = checksum.stat().st_size
        if created.returncode:
            # A partially created draft may remain if a network/upload error occurs.
            partial_ids = _matching_release_ids(source_root, tag, title)
            if partial_ids:
                edit_url = f"https://github.com/{OWNER}/{REPO}/releases/edit/{partial_ids[0]}"
                raise RuntimeError(f"GitHub draft may exist but upload did not finish; inspect {edit_url}. {created.stderr[-500:]}")
            raise RuntimeError("GitHub draft upload failed: " + (created.stderr or created.stdout)[-1000:])

    created_ids = _matching_release_ids(source_root, tag, title)
    if not created_ids:
        raise RuntimeError("Draft creation returned, but no matching release appeared in GitHub's release list.")
    release_id = created_ids[0]
    data = _get_release(source_root, release_id)
    if data.get("tag_name") != tag:
        # GitHub CLI initially labels a draft as untagged when the tag ref does not
        # yet exist. Set the exact intended tag on the still-unpublished draft.
        patched = _run(["gh", "api", f"repos/{OWNER}/{REPO}/releases/{release_id}", "--method", "PATCH",
                        "-F", f"tag_name={tag}", "-F", f"target_commitish={source_commit}"], cwd=source_root)
        if patched.returncode:
            raise RuntimeError(f"Draft exists but the intended tag {tag} could not be set; review it before publishing.")
        data = _get_release(source_root, release_id)
    assets = {item.get("name"): item for item in data.get("assets", [])}
    expected = {archive.name, checksum.name}
    if data.get("draft") is not True or data.get("tag_name") != tag or not expected.issubset(assets):
        raise RuntimeError("GitHub draft exists, but its unpublished state, tag, or required assets did not verify.")
    if assets[archive.name].get("size") != archive.stat().st_size or assets[checksum.name].get("size") != checksum_size:
        raise RuntimeError("GitHub draft asset sizes did not match the verified local binary and checksum.")
    edit_url = f"https://github.com/{OWNER}/{REPO}/releases/edit/{data['id']}"
    progress("GitHub draft verified. It remains unpublished until you publish it manually.")
    return {"version": version, "tag": tag, "draft": True, "url": edit_url,
            "assets": sorted(expected), "sha256": digest, "size_bytes": archive.stat().st_size,
            "notes": notes}
