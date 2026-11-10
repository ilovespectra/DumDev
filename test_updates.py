import io
import json
import unittest
from unittest.mock import patch

import release_tools
from runtime_paths import UPDATE_URL, python_command
from update_manager import check_update


class UpdateTests(unittest.TestCase):
    def check(self, manifest):
        response = io.BytesIO(json.dumps(manifest).encode())
        response.url = UPDATE_URL
        with patch("update_manager.urlopen", return_value=response):
            return check_update()

    def manifest(self, version="99.0.0"):
        return {"version": version,
                "download_url": f"{release_tools.PUBLIC_BASE}/downloads/dumdev/DumDev-{version}-macOS-arm64.zip",
                "sha256": "a" * 64}

    def test_update_feed_and_package_use_the_canonical_domain(self):
        self.assertEqual(release_tools.PUBLIC_BASE, "https://dumdev.kolektivkrog.si")
        self.assertEqual(UPDATE_URL, f"{release_tools.PUBLIC_BASE}/downloads/dumdev/latest.json")

    def test_new_release_notifies_but_current_version_does_not(self):
        from runtime_paths import VERSION
        self.assertTrue(self.check(self.manifest())["available"])
        self.assertFalse(self.check(self.manifest(VERSION))["available"])

    def test_untrusted_or_invalid_updates_are_rejected(self):
        for manifest in [[], {}, self.manifest() | {"version": "bad"},
                         self.manifest() | {"download_url": "https://example.com/app.zip"},
                         self.manifest() | {"sha256": ""},
                         self.manifest() | {"download_url": "https://dumdev-eight.vercel.app/downloads/dumdev/DumDev-0.1.0-macOS-arm64.zip"}]:
            with self.subTest(manifest=manifest), self.assertRaises(ValueError):
                self.check(manifest)

    def test_packaged_workers_reenter_app_with_agent_flag(self):
        with patch("runtime_paths.PACKAGED", True):
            command = python_command("twitter_agent.py", "--loop")
            self.assertEqual(command[1:], ["--agent", "--loop"])


if __name__ == "__main__":
    unittest.main()
