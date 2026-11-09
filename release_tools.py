"""Build the committed DumDev bot and publish its download through the existing website."""
from __future__ import annotations

import hashlib
import json
import platform
from pathlib import Path
import plistlib
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from typing import Callable
from urllib.error import HTTPError
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parent
RELEASE_DIR = ROOT / "release"
SITE = ROOT / "dumdev-site"
PUBLIC_BASE = "https://dumdev-eight.vercel.app"
VERSION_RE = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")


def version_tuple(value: str) -> tuple[int, int, int]:
    if not isinstance(value, str) or not VERSION_RE.fullmatch(value):
        raise ValueError("Use a version like 0.3.1.")
    return tuple(int(part) for part in value.split("."))


def version_has_single_minor_patch(value: str) -> bool:
    major, minor, patch = version_tuple(value)
    return minor <= 9 and patch <= 9


def next_version(current: str) -> str:
    major, minor, patch = version_tuple(current)
    if minor > 9:
        return f"{major + 1}.0.0"
    if patch > 9:
        return f"{major}.{minor + 1}.0" if minor < 9 else f"{major + 1}.0.0"
    if patch < 9:
        return f"{major}.{minor}.{patch + 1}"
    if minor < 9:
        return f"{major}.{minor + 1}.0"
    return f"{major + 1}.0.0"


# Kept as a compatibility name for existing console callers; this is the custom
# digit-rollover sequence, not a SemVer patch increment.
next_patch = next_version


def run(command: list[str], *, cwd: Path = ROOT, timeout: int = 1200) -> str:
    result = subprocess.run(command, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError(f"{command[0]} failed: {(result.stderr or result.stdout)[-2500:]}")
    return result.stdout.strip()


def copy_standalone_site(site_root: Path, public_root: Path) -> str:
    """Copy DumDev's own landing page to a clean static deployment root."""
    source = site_root / "index.html"
    if not source.is_file():
        raise ValueError("The standalone DumDev landing page is missing.")
    public_root.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, public_root / "index.html")
    return "index.html"


def latest_release() -> dict | None:
    path = RELEASE_DIR / "latest.json"
    if not path.exists():
        return None
    result = json.loads(path.read_text())
    if not re.fullmatch(r"DumDev-\d+\.\d+\.\d+-macOS-arm64\.zip", result["file"]):
        raise ValueError("Invalid release archive filename.")
    if not (RELEASE_DIR / result["file"]).is_file():
        raise ValueError("Release archive is missing.")
    return result


def build_release(source_root: Path, version: str, progress: Callable[[str], None] | None = None) -> dict:
    source_root = source_root.resolve()
    progress = progress or print
    version_tuple(version)
    if not version_has_single_minor_patch(version):
        raise ValueError("New releases must keep the middle and final version digits from 0 to 9.")
    previous = latest_release()
    if previous and version_tuple(version) <= version_tuple(previous["version"]):
        raise ValueError("Choose a version newer than the latest staged release.")
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        raise RuntimeError("The current release target is Apple silicon macOS.")
    if run(["git", "status", "--porcelain"], cwd=source_root):
        raise ValueError("Commit your source changes before building. Releases are built from the latest commit, not a dirty worktree.")
    commit = run(["git", "rev-parse", "HEAD"], cwd=source_root)
    RELEASE_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="dumdev-build-") as directory:
        temp = Path(directory)
        source = temp / "source"
        source.mkdir()
        archive = temp / "source.tar"
        run(["git", "archive", "--format=tar", "-o", str(archive), commit], cwd=source_root)
        with tarfile.open(archive) as bundle:
            bundle.extractall(source, filter="data")
        # A committed source archive is the only input: never copy logs or browser profiles.
        paths = source / "runtime_paths.py"
        original = paths.read_text()
        match = re.search(r'^VERSION = "([^"]+)"$', original, re.M)
        if not match or version_tuple(version) < version_tuple(match[1]):
            raise ValueError("Release version cannot be older than the committed app version.")
        paths.write_text(re.sub(r'^VERSION = "[^"]+"$', f'VERSION = "{version}"', original, count=1, flags=re.M))
        helper = source / "takeover-helper"
        progress("Compiling and testing screen-control helper…")
        run(["swiftc", str(source / "takeover.swift"), "-o", str(helper)])
        run([str(helper), "--test-motion"])
        run([str(helper), "--test-glow"])
        progress("Bundling Python, local model client, browser driver and dashboard…")
        command = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--windowed",
                   "--name", "DumDev", "--osx-bundle-identifier", "org.dumdev.local",
                   "--distpath", str(temp / "dist"), "--workpath", str(temp / "build"),
                   "--specpath", str(temp), "--collect-all", "playwright",
                   "--hidden-import", "Quartz", "--hidden-import", "AppKit",
                   "--hidden-import", "twitter_agent"]
        for name in ("dashboard.html", "setup.js", "takeover-helper", "takeover.swift", "LICENSE"):
            command += ["--add-data", f"{source / name}:."]
        command += [str(source / "app.py")]
        run(command, cwd=source)
        app = temp / "dist" / "DumDev.app"
        plist = app / "Contents" / "Info.plist"
        metadata = plistlib.loads(plist.read_bytes())
        metadata.update(CFBundleShortVersionString=version, CFBundleVersion=version,
                        NSAppleEventsUsageDescription="DumDev controls your selected Arc X tab for the bot you configured.",
                        NSHighResolutionCapable=True)
        plist.write_bytes(plistlib.dumps(metadata))
        run(["codesign", "--force", "--deep", "--sign", "-", str(app)])
        run(["codesign", "--verify", "--deep", "--strict", str(app)])
        progress("Verifying packaged clean-install startup…")
        executable = app / "Contents" / "MacOS" / "DumDev"
        import os
        environment = os.environ | {"DUMDEV_DATA_DIR": str(temp / "fresh-install")}
        check = subprocess.run([str(executable), "--smoke-test"], env=environment,
                               capture_output=True, text=True, timeout=45)
        if check.returncode:
            raise RuntimeError("Packaged startup check failed: " + (check.stderr or check.stdout)[-2000:])
        report_path = temp / "fresh-install" / "logs" / "smoke-test.json"
        if not report_path.is_file():
            raise RuntimeError("Packaged startup did not verify the clean-install dashboard.")
        report = json.loads(report_path.read_text())
        if report.get("version") != version or report.get("setup_required") is not True:
            raise RuntimeError("Packaged startup did not present fresh-user setup at the correct version.")
        worker = subprocess.run([str(executable), "--agent", "--help"], env=environment,
                                capture_output=True, text=True, timeout=45)
        if worker.returncode:
            raise RuntimeError("Packaged bot subprocess failed: " + (worker.stderr or worker.stdout)[-2000:])
        filename = f"DumDev-{version}-macOS-arm64.zip"
        destination = RELEASE_DIR / filename
        run(["ditto", "-c", "-k", "--sequesterRsrc", "--keepParent", str(app), str(temp / filename)])
        shutil.copy2(temp / filename, destination)
    with destination.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    result = {"version": version, "file": filename, "source_commit": commit,
              "download_url": f"{PUBLIC_BASE}/downloads/dumdev/{filename}",
              "sha256": digest,
              "size_bytes": destination.stat().st_size, "platform": "macOS arm64",
              "install_note": "Quit DumDev, unzip, and replace DumDev.app in Applications. Your local settings are retained.",
              "notarized": False, "ollama_required": True}
    (RELEASE_DIR / "latest.json").write_text(json.dumps(result, indent=2) + "\n")
    progress("Package verified and staged. Publish to make the update available.")
    return result


def publish_release(progress: Callable[[str], None] | None = None) -> dict:
    progress = progress or print
    release = latest_release()
    if release is None:
        raise ValueError("Build a verified package first.")
    try:
        with urlopen(f"{PUBLIC_BASE}/downloads/dumdev/latest.json", timeout=15) as response:
            published = json.load(response)
    except HTTPError as exc:
        if exc.code != 404:
            raise
        exc.close()
    else:
        if not isinstance(published, dict) or "version" not in published:
            raise ValueError("The live release feed is invalid; refusing to replace it.")
        if version_tuple(release["version"]) <= version_tuple(published["version"]):
            raise ValueError("This version is already published or older than the live release.")
    archive = RELEASE_DIR / release["file"]
    with archive.open("rb") as stream:
        if hashlib.file_digest(stream, "sha256").hexdigest() != release["sha256"]:
            raise ValueError("Release checksum changed. Rebuild before publishing.")
    if run(["git", "status", "--porcelain"], cwd=ROOT):
        raise ValueError("Commit source and landing-page changes before publishing.")
    if not (SITE / "index.html").is_file():
        raise ValueError("The standalone DumDev landing page is missing.")
    progress("Staging the standalone DumDev site and verified package…")
    with tempfile.TemporaryDirectory(prefix="dumdev-publish-") as directory:
        deploy = Path(directory) / "site"
        public = deploy / "public"
        copy_standalone_site(SITE, public)
        downloads = public / "downloads" / "dumdev"
        downloads.mkdir(parents=True, exist_ok=True)
        shutil.copy2(archive, downloads / archive.name)
        shutil.copy2(RELEASE_DIR / "latest.json", downloads / "latest.json")
        progress("Deploying the release and update feed to DumDev's separate website…")
        run(["vercel", "link", "--yes", "--project", "dumdev"], cwd=deploy, timeout=120)
        output = run(["vercel", "--prod", "--yes"], cwd=deploy)
    progress("Release published. Installed apps discover it at their next update check.")
    return {**release, "published": True, "deployment": output}
