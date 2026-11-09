"""Use the bot repository's single release pipeline from the local console."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from release_tools import RELEASE_DIR, build_release, latest_release, next_patch, publish_release, version_tuple

__all__ = ["RELEASE_DIR", "build_release", "latest_release", "next_patch", "publish_release", "version_tuple"]
