import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from show_storage import save_show


class ShowStorageTests(unittest.TestCase):
    def test_save_as_from_portable_show_reuses_one_copy_across_both_decks(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "Video.mp4"
            source.write_bytes(b"same media used for audio and projection")
            original = root / "Original.json"
            payload = {"cues_a": [{"id": "a", "path": str(source), "start_sec": 2}],
                       "cues_b": [{"id": "b", "path": str(source), "start_sec": 8}],
                       "scenes": [{"id": "scene", "cue_ids_a": ["a"], "cue_ids_b": ["b"]}]}
            save_show(original, payload, {})
            loaded = json.loads(original.read_text())
            original_bytes = original.read_bytes()
            for key in ("cues_a", "cues_b"):
                loaded[key][0]["path"] = str(root / loaded[key][0]["path"])
            second = root / "Second.json"
            save_show(second, loaded, {})
            saved = json.loads(second.read_text())
            self.assertEqual(saved["cues_a"][0]["path"], saved["cues_b"][0]["path"])
            self.assertEqual(saved["cues_a"][0]["start_sec"], 2)
            self.assertEqual(saved["cues_b"][0]["start_sec"], 8)
            self.assertEqual(saved["scenes"], payload["scenes"])
            self.assertEqual(len(list(second.with_suffix(".media").glob("*/*.mp4"))), 1)
            self.assertEqual(original.read_bytes(), original_bytes)

    def test_cues_can_share_content_without_sharing_position(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'other').mkdir()
            first, second, third = root / 'theme.wav', root / 'opening.wav', root / 'other/theme.wav'
            first.write_bytes(b'track one')
            second.write_bytes(first.read_bytes())
            third.write_bytes(b'track two')
            path = root / 'school.json'
            data = {'cues_a': [{'id': str(i), 'path': str(source), 'start_sec': i}
                               for i, source in enumerate((first, second, third, first))], 'cues_b': []}
            cache = {}
            save_show(path, data, cache)
            saved = json.loads(path.read_text())
            one, two, three, four = saved['cues_a']
            self.assertEqual(one['path'], two['path'])
            self.assertEqual(one['path'], four['path'])
            self.assertNotEqual(one['path'], three['path'])
            self.assertEqual(two['media_name'], 'opening.wav')
            self.assertEqual([row['start_sec'] for row in saved['cues_a']], [0, 1, 2, 3])
            self.assertEqual(len(list(path.with_suffix('.media').glob('*/*.wav'))), 2)
            stored = root / one['path']
            with self.subTest('repeat save reuses media'):
                previous = stored.stat().st_mtime_ns
                save_show(path, data, cache)
                self.assertEqual(stored.stat().st_mtime_ns, previous)
            with self.subTest('failed save leaves previous manifest intact'):
                second.unlink()
                previous = path.read_bytes()
                with self.assertRaises(FileNotFoundError):
                    save_show(path, data, cache)
                self.assertEqual(path.read_bytes(), previous)
                self.assertEqual(stored.read_bytes(), b'track one')


if __name__ == '__main__':
    unittest.main()
