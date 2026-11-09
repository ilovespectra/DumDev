"""Read-only public release discovery. Updates are installed by the user."""
import json
import re
from urllib.parse import urlsplit
from urllib.request import urlopen

from runtime_paths import UPDATE_URL, VERSION


def check_update() -> dict:
    with urlopen(UPDATE_URL, timeout=8) as response:
        if urlsplit(response.url).hostname != urlsplit(UPDATE_URL).hostname:
            raise ValueError("Update manifest redirected to an unexpected host.")
        raw = response.read(32769)
    if len(raw) > 32768:
        raise ValueError("Update manifest is too large.")
    manifest = json.loads(raw)
    if not isinstance(manifest, dict):
        raise ValueError("Update manifest must be an object.")
    version = manifest.get("version")
    if not isinstance(version, str) or not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ValueError("Update manifest has an invalid version.")
    link = manifest.get("download_url", "")
    parsed = urlsplit(link)
    if (parsed.scheme != "https" or parsed.hostname != urlsplit(UPDATE_URL).hostname or
            parsed.path != f"/downloads/dumdev/DumDev-{version}-macOS-arm64.zip" or
            parsed.query or parsed.fragment or parsed.username):
        raise ValueError("Update manifest has an untrusted download location.")
    if not re.fullmatch(r"[a-f0-9]{64}", str(manifest.get("sha256", ""))):
        raise ValueError("Update manifest has no valid SHA-256.")
    newer = tuple(map(int, version.split("."))) > tuple(map(int, VERSION.split(".")))
    return {"current_version": VERSION, "latest_version": version, "available": newer,
            "download_url": link, "sha256": manifest["sha256"]}
