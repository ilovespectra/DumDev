"""Local release staging helpers for DumDev's developer-only console."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import venv
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parent
RELEASE_DIR = ROOT / "release"
VERSION_RE = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")


def version_tuple(value: str) -> tuple[int, int, int]:
    match = VERSION_RE.fullmatch(value)
    if not match:
        raise ValueError("Use a release version like 0.2.0.")
    return tuple(int(part) for part in match.groups())


def next_patch(current: str) -> str:
    major, minor, patch = version_tuple(current)
    return f"{major}.{minor}.{patch + 1}"


def latest_release() -> dict[str, object] | None:
    manifest = RELEASE_DIR / "latest.json"
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
        filename = str(data.get("file", ""))
        if not re.fullmatch(r"DumDev-[0-9]+\.[0-9]+\.[0-9]+-macOS-arm64\.zip", filename):
            return None
        if not (RELEASE_DIR / filename).is_file():
            return None
        return {**data, "download_url": f"/api/release-file/{filename}"}
    except (OSError, json.JSONDecodeError, AttributeError, TypeError):
        return None


def _packager(root: Path) -> Path:
    override = os.environ.get("DUMDEV_PACKAGER_PYTHON", "")
    if override:
        check = subprocess.run([override, "-m", "PyInstaller", "--version"], capture_output=True, text=True, timeout=20)
        if check.returncode == 0:
            return Path(override)
        raise RuntimeError("The configured local packager is unavailable.")
    env_dir = root / ".build-venv"
    python = env_dir / "bin" / "python"
    check = subprocess.run([str(python), "-m", "PyInstaller", "--version"],
                           capture_output=True, text=True, timeout=20) if python.exists() else None
    if check and check.returncode == 0:
        return python
    if not python.exists():
        venv.EnvBuilder(with_pip=True).create(env_dir)
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin:/opt/homebrew/bin"),
           "HOME": str(env_dir), "PYTHONNOUSERSITE": "1"}
    result = subprocess.run([str(python), "-m", "pip", "install", "--disable-pip-version-check", "PyInstaller==6.22.3"],
                            capture_output=True, text=True, timeout=900, env=env)
    if result.returncode:
        raise RuntimeError("Could not prepare the local macOS packager. Check your internet connection and try again.")
    return python


def build_release(version: str, progress: Callable[[str], None] | None = None) -> dict[str, object]:
    progress = progress or (lambda _message: None)
    version_tuple(version)
    source_app = ROOT / "app.py"
    source = source_app.read_text(encoding="utf-8")
    match = re.search(r'(?m)^VERSION = "([^"]+)"$', source)
    if not match:
        raise RuntimeError("The current app version could not be read.")
    current = match.group(1)
    if version_tuple(version) < version_tuple(current):
        raise ValueError(f"Choose version {current} or newer.")

    RELEASE_DIR.mkdir(parents=True, exist_ok=True)
    archive = RELEASE_DIR / f"DumDev-{version}-macOS-arm64.zip"
    manifest = RELEASE_DIR / "latest.json"
    with tempfile.TemporaryDirectory(prefix="dumdev-release-") as temp_name:
        temp = Path(temp_name)
        source_root = temp / "source"
        source_root.mkdir()
        app_text = source.replace(f'VERSION = "{current}"', f'VERSION = "{version}"', 1)
        (source_root / "app.py").write_text(app_text, encoding="utf-8")
        shutil.copy2(ROOT / "release_tools.py", source_root / "release_tools.py")
        shutil.copytree(ROOT / "web", source_root / "web")
        shutil.copytree(ROOT / "assets", source_root / "assets")
        packager = _packager(ROOT)
        progress("Building the self-contained macOS app…")
        build_env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin:/opt/homebrew/bin"),
                     "HOME": str(temp), "PYTHONNOUSERSITE": "1", "TMPDIR": str(temp)}
        command = [str(packager), "-m", "PyInstaller", "--noconfirm", "--clean", "--windowed",
                   "--name", "DumDev", "--osx-bundle-identifier", "org.dumdev.local",
                   "--distpath", str(temp / "dist"), "--workpath", str(temp / "build"),
                   "--specpath", str(temp), "--add-data", f"{source_root / 'web'}:web",
                   "--add-data", f"{source_root / 'assets'}:assets", str(source_root / "app.py")]
        result = subprocess.run(command, cwd=source_root, env=build_env,
                                capture_output=True, text=True, timeout=1200)
        if result.returncode:
            short = (result.stderr or result.stdout)[-1800:]
            raise RuntimeError(f"The app build did not finish. {short}")
        app_bundle = temp / "dist" / "DumDev.app"
        if not app_bundle.exists():
            raise RuntimeError("The packager finished without creating DumDev.app.")
        progress("Signing the local app bundle and preparing the download…")
        sign = subprocess.run(["/usr/bin/codesign", "--force", "--deep", "--sign", "-", str(app_bundle)],
                              capture_output=True, text=True, timeout=60)
        if sign.returncode:
            raise RuntimeError("The local app bundle could not be ad-hoc signed. No package was staged.")
        temp_archive = temp / archive.name
        zipped = subprocess.run(["/usr/bin/ditto", "-c", "-k", "--sequesterRsrc", "--keepParent",
                                 str(app_bundle), str(temp_archive)], capture_output=True, text=True, timeout=300)
        if zipped.returncode:
            raise RuntimeError("The app was built but its download archive could not be created.")
        shutil.copy2(temp_archive, archive)

    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    data = {"version": version, "platform": "macOS arm64", "file": archive.name,
            "sha256": digest, "size_bytes": archive.stat().st_size,
            "install_note": "Open the zip, drag DumDev.app to Applications, and open it.",
            "ollama_required": True}
    manifest.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return {"version": version, "file": archive.name, "size_bytes": archive.stat().st_size,
            "sha256": digest, "manifest": str(manifest)}
