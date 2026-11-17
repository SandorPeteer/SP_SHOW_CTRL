import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from download_tools import install, resolve


class UpdateTests(unittest.TestCase):
    def test_update_replaces_existing_version_and_wins_over_bundle(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            user, bundled = root / "user", root / "bundle"
            destination = user / "ytdlp/bin/yt-dlp.exe"
            destination.parent.mkdir(parents=True)
            destination.write_bytes(b"MZ old")
            (bundled / "ytdlp").mkdir(parents=True)
            (bundled / "ytdlp/yt-dlp.exe").write_bytes(b"MZ bundled")
            payload = b"MZ new version"
            asset = {"name": "yt-dlp.exe", "browser_download_url": "https://github.com/yt-dlp/yt-dlp/releases/download/test/yt-dlp.exe",
                     "digest": "sha256:" + hashlib.sha256(payload).hexdigest()}
            def download(url, path):
                if url.endswith("/latest"):
                    path.write_text(json.dumps({"assets": [asset]}))
                else:
                    path.write_bytes(payload)
            with patch("download_tools.version", return_value="new"):
                actual, version = install("yt-dlp", destination.parent, download, {})
            self.assertEqual(actual, destination)
            self.assertEqual(version, "new")
            self.assertEqual(destination.read_bytes(), payload)
            self.assertEqual(resolve("yt-dlp", user, bundled), str(destination))

    def test_failed_download_or_validation_preserves_working_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            destination = folder / "yt-dlp.exe"
            destination.write_bytes(b"MZ working")
            payload = b"MZ new"
            asset = {"name": "yt-dlp.exe", "browser_download_url": "unused",
                     "digest": "sha256:" + hashlib.sha256(payload).hexdigest()}
            for actual in (b"partial", payload):
                def download(url, path):
                    path.write_bytes(actual)
                with patch("download_tools.version", side_effect=RuntimeError("Cannot launch")):
                    with self.assertRaises((ValueError, RuntimeError)):
                        install("yt-dlp", folder, download, {}, asset=asset)
                self.assertEqual(destination.read_bytes(), b"MZ working")
            self.assertEqual(list(folder.iterdir()), [destination])


if __name__ == "__main__":
    unittest.main()
