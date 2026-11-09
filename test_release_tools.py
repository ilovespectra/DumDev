import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import os

import release_tools
from runtime_paths import ROOT, VERSION


class ReleaseTests(unittest.TestCase):
    def test_digit_rollover_and_legacy_versions(self):
        self.assertEqual(release_tools.next_version("1.0.8"), "1.0.9")
        self.assertEqual(release_tools.next_version("1.0.9"), "1.1.0")
        self.assertEqual(release_tools.next_version("1.9.9"), "2.0.0")
        self.assertEqual(release_tools.next_version("1.0.13"), "1.1.0")
        self.assertGreater(release_tools.version_tuple("1.1.0"), release_tools.version_tuple("1.0.13"))
        self.assertFalse(release_tools.version_has_single_minor_patch("1.0.13"))
        self.assertTrue(release_tools.version_has_single_minor_patch("2.0.0"))
        for value in ["../0.3.1", "0.03.1", "v0.3.1"]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                release_tools.version_tuple(value)

    def test_publish_copies_only_standalone_dumdev_site(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            site, public = root / "site", root / "deploy" / "public"
            site.mkdir()
            (site / "index.html").write_text("DumDev standalone page")
            (site / "portfolio.html").write_text("unrelated personal website")

            copied = release_tools.copy_standalone_site(site, public)

            self.assertEqual(copied, "index.html")
            self.assertEqual((public / "index.html").read_text(), "DumDev standalone page")
            self.assertFalse((public / "portfolio.html").exists())

    def test_dirty_source_is_never_packaged(self):
        with patch("release_tools.latest_release", return_value=None), \
             patch("release_tools.platform.system", return_value="Darwin"), \
             patch("release_tools.platform.machine", return_value="arm64"), \
             patch("release_tools.run", return_value=" M app.py") as run:
            with self.assertRaisesRegex(ValueError, "Commit your source changes"):
                release_tools.build_release(ROOT, VERSION)
            self.assertEqual(run.call_count, 1)

    def test_staged_versions_cannot_be_overwritten_or_downgraded(self):
        with patch("release_tools.latest_release", return_value={"version": "0.3.2"}):
            for value in ["0.3.2", "0.3.1"]:
                with self.subTest(value=value), self.assertRaisesRegex(ValueError, "newer"):
                    release_tools.build_release(ROOT, value)

    def test_smoke_uses_isolated_data_and_never_starts_a_worker(self):
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run([sys.executable, str(ROOT / "app.py"), "--smoke-test"],
                                    env=os.environ | {"DUMDEV_DATA_DIR": directory},
                                    text=True, capture_output=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads((Path(directory) / "logs" / "smoke-test.json").read_text())
            self.assertTrue(report["setup_required"])
            self.assertEqual(report["version"], VERSION)
            self.assertFalse((Path(directory) / "logs" / "agent.pid").exists())
            self.assertFalse((Path(directory) / "logs" / "settings.json").exists())


if __name__ == "__main__":
    unittest.main()
