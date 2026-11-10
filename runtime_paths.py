import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent
PACKAGED = bool(getattr(sys, "frozen", False))
DATA_ROOT = Path(os.environ.get("DUMDEV_DATA_DIR",
    str(Path.home() / "Library" / "Application Support" / "DumDev") if PACKAGED else str(ROOT))).expanduser().resolve()
LOGS = DATA_ROOT / "logs"
VERSION = "0.3.1"
UPDATE_URL = "https://dumdev.kolektivkrog.si/downloads/dumdev/latest.json"


def python_command(script: str, *arguments: str) -> list[str]:
    if PACKAGED:
        return [sys.executable, "--agent", *arguments]
    return [sys.executable, "-u", str(ROOT / script), *arguments]
