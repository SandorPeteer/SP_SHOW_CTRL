"""Regression tests for PowerPoint preparation without requiring an Office installation."""
import hashlib
import json
import queue
import tempfile
from pathlib import Path
import sys
import unittest
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ppt_media import SlidePreparer, _moving_slides, _open_copy, _stamp, prepare, read_timing


class ConversionTests(unittest.TestCase):
    def test_order_animation_and_media_detection(self):
        with tempfile.TemporaryDirectory() as tmp:
            ppt = Path(tmp) / "próba.pptx"
            with zipfile.ZipFile(ppt, "w") as deck:
                deck.writestr("ppt/presentation.xml", '<p xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sldIdLst><sldId r:id="b"/><sldId r:id="a"/><sldId r:id="c"/></sldIdLst></p>')
                deck.writestr("ppt/_rels/presentation.xml.rels", '<R><r Id="a" Target="slides/slide1.xml"/><r Id="b" Target="/ppt/slides/slide2.xml"/><r Id="c" Target="slides/slide3.xml"/></R>')
                deck.writestr("ppt/slides/slide1.xml", "<slide><timing/></slide>")
                deck.writestr("ppt/slides/slide2.xml", '<slide><text/><transition advTm="3500"/></slide>')
                deck.writestr("ppt/slides/slide3.xml", "<slide/>")
                deck.writestr("ppt/slides/_rels/slide3.xml.rels", '<R><r Target="../media/moving.gif"/></R>')
            self.assertEqual(_moving_slides(ppt), {2, 3})
            (Path(tmp) / "slides.json").write_text(json.dumps({"version": 1, "slides": ["png"] * 3}))
            files = [Path(tmp) / f"{i:04d}.png" for i in range(1, 4)]
            self.assertEqual(read_timing(ppt, files), [3.5, None, None])

    def test_cache_content_identity_and_incomplete_conversion(self):
        def render(source, stage, progress):
            (stage / "0001.png").write_bytes(b"page")
            return ["png"]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source, cache = root / "slides.ppt", root / "cache"
            source.write_bytes(b"old")
            files = prepare(source, cache, print, render)
            self.assertEqual(prepare(source, cache, print, None), files)
            source.write_bytes(b"changed")
            with self.assertRaises(ValueError):
                prepare(source, cache, print, lambda *_: ["png"])
            self.assertFalse((cache / hashlib.sha256(b"changed").hexdigest()).exists())
            new = prepare(source, cache, print, render)
            self.assertNotEqual(files, new)
            new[0].unlink()
            self.assertEqual(prepare(source, cache, print, render), new)

    def test_ready_deck_plays_while_another_is_converting(self):
        with tempfile.TemporaryDirectory() as tmp:
            path, slide = Path(tmp) / "deck.ppt", Path(tmp) / "0001.png"
            path.write_bytes(b"presentation")
            slide.write_bytes(b"slide")
            ui = queue.Queue()
            prep = SlidePreparer(Path(tmp), ui)
            prep.worker = object()  # A busy worker cannot service another conversion.
            prep.ready[path] = (_stamp(path), [slide])
            received = []
            prep.submit(path, print, lambda *result: received.append(result))
            ui.get_nowait()()
            self.assertEqual(received, [(path, [slide], None)])
            path.write_bytes(b"new presentation")
            prep.submit(path, print, None)
            self.assertTrue(ui.empty())
            self.assertIn(path, prep.pending)

    def test_open_hidden_copy_restores_automation_security_on_error(self):
        class Office:
            AutomationSecurity = 2
            @property
            def Presentations(self):
                return self
            def Open(self, *args):
                assert args == ("slides.ppt", -1, -1, 0) and self.AutomationSecurity == 3
                raise ValueError("password")
        app = Office()
        with self.assertRaises(ValueError):
            _open_copy(app, Path("slides.ppt"))
        self.assertEqual(app.AutomationSecurity, 2)


if __name__ == "__main__":
    unittest.main()
