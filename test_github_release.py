import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import github_release


class GitHubDraftTests(unittest.TestCase):
    def test_non_owner_cannot_create_github_draft(self):
        with patch.object(github_release, "github_status", return_value={"enabled": False, "message": "local only"}), \
             patch.object(github_release, "_run") as run:
            with self.assertRaisesRegex(PermissionError, "local only"):
                github_release.create_github_draft(source_root=Path("."), release={})
            run.assert_not_called()

    def test_owner_uploads_checksum_and_verified_binary_as_unpublished_draft(self):
        source_commit = "a" * 40
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "DumDev-0.3.3-macOS-arm64.zip"
            archive.write_bytes(b"verified-package")
            digest = hashlib.sha256(archive.read_bytes()).hexdigest()
            release = {"version": "0.3.3", "file": archive.name, "source_commit": source_commit,
                       "sha256": digest, "platform": "macOS arm64", "notarized": False,
                       "ollama_required": True, "size_bytes": archive.stat().st_size}
            remote = subprocess.CompletedProcess([], 0, "https://github.com/ilovespectra/dumdev.git\n", "")
            owner = subprocess.CompletedProcess([], 0, "ilovespectra\n", "")
            permission = subprocess.CompletedProcess([], 0, "true\n", "")
            commit = subprocess.CompletedProcess([], 0, source_commit + "\n", "")
            summary = subprocess.CompletedProcess([], 0, "Fix update feed host\n", "")
            create = subprocess.CompletedProcess([], 0, "", "")
            release_data = {"id": 73, "draft": True, "tag_name": "untagged-demo", "assets": [
                {"name": archive.name, "size": archive.stat().st_size},
                {"name": archive.name + ".sha256", "size": 95},
            ]}
            list_count = 0
            patched = False
            calls = []
            def fake_run(args, **kwargs):
                nonlocal list_count, patched
                if args[:3] == ["git", "remote", "get-url"]: return remote
                if args[:3] == ["gh", "api", "user"]: return owner
                if args[:3] == ["gh", "api", "repos/ilovespectra/dumdev"]: return permission
                if args[:3] == ["git", "rev-parse", "HEAD"]: return commit
                if args[:3] == ["gh", "api", "repos/ilovespectra/dumdev/releases?per_page=100"]:
                    list_count += 1
                    return subprocess.CompletedProcess([], 0, "" if list_count == 1 else "73\n", "")
                if args[:3] == ["git", "show", "-s"]: return summary
                if args[:3] == ["gh", "release", "create"]:
                    calls.append(args)
                    return create
                if args[:3] == ["gh", "api", "repos/ilovespectra/dumdev/releases/73"] and "--method" in args:
                    patched = True
                    return subprocess.CompletedProcess([], 0, "{}", "")
                if args[:3] == ["gh", "api", "repos/ilovespectra/dumdev/releases/73"]:
                    data = dict(release_data)
                    if patched: data["tag_name"] = "v0.3.3"
                    return subprocess.CompletedProcess([], 0, json.dumps(data), "")
                raise AssertionError(args)
            with patch.object(github_release, "_run", side_effect=fake_run), \
                 patch.object(github_release, "_ollama_notes", return_value="## DumDev 0.3.3\n\n- Update feed uses the canonical domain."), \
                 patch("release_tools.RELEASE_DIR", Path(directory)):
                result = github_release.create_github_draft(source_root=Path("."), release=release)
            self.assertTrue(result["draft"])
            self.assertEqual(result["url"], "https://github.com/ilovespectra/dumdev/releases/edit/73")
            self.assertEqual(result["assets"], [archive.name, archive.name + ".sha256"])
            self.assertIn("--draft", calls[0])
            self.assertIn("--repo", calls[0])
            self.assertTrue(patched)

    def test_checksum_mismatch_blocks_remote_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "package.zip"
            archive.write_bytes(b"changed")
            release = {"version": "0.3.3", "file": archive.name, "source_commit": "a" * 40,
                       "sha256": "0" * 64}
            with patch.object(github_release, "github_status", return_value={"enabled": True}), \
                 patch("release_tools.RELEASE_DIR", Path(directory)), patch.object(github_release, "_run") as run:
                with self.assertRaisesRegex(ValueError, "checksum changed"):
                    github_release.create_github_draft(source_root=Path("."), release=release)
                run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
