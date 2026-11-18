"""Exercise the real decoder, cancellation, and slide poster fallback."""
from io import BytesIO
import os
from pathlib import Path
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))
from PIL import Image
from media_waveform import WaveformWorker, render_waveform
from output_monitor import _output_preview, load_preview

ffmpeg = str(root / "dist/SP Show Control/_internal/tools/ffmpeg/ffmpeg.exe") if sys.platform == "win32" else shutil.which("ffmpeg")
subprocess_options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}


class MediaTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="médiapróba ")
        self.folder = Path(self.tmp.name)
        self.audio = self.folder / "ének.wav"
        subprocess.run([ffmpeg, "-v", "error", "-f", "lavfi", "-i",
                        "sine=frequency=1000:duration=1", str(self.audio)], check=True,
                       **subprocess_options)

    def tearDown(self):
        self.tmp.cleanup()

    def test_long_analysis_survives_old_ten_second_limit(self):
        popen = subprocess.Popen

        def slow_read(command, **kwargs):
            delayed = (
                "import subprocess,sys,time; "
                "result=subprocess.run(sys.argv[1:],capture_output=True); "
                "time.sleep(10.3); "
                "sys.stdout.buffer.write(result.stdout); "
                "sys.stderr.buffer.write(result.stderr); "
                "sys.exit(result.returncode)"
            )
            return popen([sys.executable, "-c", delayed, *command], **kwargs)

        start = time.monotonic()
        with patch("media_waveform.subprocess.Popen", slow_read):
            png = render_waveform(ffmpeg, self.audio, 600, 60, cancel=threading.Event(),
                                  subprocess_kwargs=subprocess_options)
        self.assertGreater(time.monotonic() - start, 10)
        with Image.open(BytesIO(png)) as image:
            self.assertEqual(image.size, (600, 60))
            self.assertGreater(image.convert("RGB").getextrema()[2][1], 200)

    def test_replaced_request_cannot_deliver_stale_waveform(self):
        ui = queue.Queue()
        worker = WaveformWorker(ui)
        received = []
        try:
            worker.request("A", ffmpeg, self.folder / "missing.wav", 600, 60,
                           lambda *args: received.append("stale"), subprocess_options)
            worker.request("A", ffmpeg, self.audio, 600, 60,
                           lambda png, err: received.append((bool(png), err)), subprocess_options)
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and not received:
                try:
                    ui.get(timeout=0.1)()
                except queue.Empty:
                    pass
            self.assertEqual(received, [(True, None)])
        finally:
            worker.close()

    def test_quiet_mp4_fills_the_display_but_keeps_a_raw_level_map(self):
        quiet = self.folder / "quiet.mp4"
        subprocess.run([ffmpeg, "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=160x90:r=10:d=1",
                        "-i", str(self.audio), "-af", "volume=0.18", "-c:v", "mpeg4", "-c:a", "aac",
                        str(quiet)], check=True, **subprocess_options)

        def height(fit):
            png = render_waveform(ffmpeg, quiet, 320, 60, cancel=threading.Event(),
                                  subprocess_kwargs=subprocess_options, fit_display=fit)
            with Image.open(BytesIO(png)) as image:
                self.assertEqual(image.size, (320, 60))
                bounds = image.getbbox()
                return bounds[3] - bounds[1]

        self.assertLess(height(False), 10)
        self.assertGreaterEqual(height(True), 45)

    def test_video_poster_and_legacy_video_preview(self):
        video = self.folder / "következő.mp4"
        subprocess.run([ffmpeg, "-v", "error", "-f", "lavfi", "-i",
                        "color=c=red:size=160x90:duration=0.5", "-c:v", "mpeg4", str(video)],
                       check=True, **subprocess_options)
        with patch("output_monitor.subprocess.Popen", wraps=subprocess.Popen) as decoder:
            preview = load_preview(video, ffmpeg, subprocess_options)
        self.assertEqual(decoder.call_count, 1, "A visible first frame needs no extra search")
        self.assertGreater(preview.getpixel((20, 20))[0], 200)
        Image.new("RGB", (300, 180), "blue").save(video.with_suffix(".preview.png"))
        preview = load_preview(video, None, subprocess_options)
        self.assertEqual(preview.getpixel((20, 20)), (0, 0, 255))
        Image.new("RGB", (300, 180), "black").save(video.with_suffix(".preview.png"))
        with patch("output_monitor.subprocess.Popen", side_effect=AssertionError("Explicit slide poster must not be replaced")):
            preview = load_preview(video, ffmpeg, subprocess_options, aspect="4:3")
        self.assertEqual(preview.size, (480, 360))
        self.assertEqual(preview.getpixel((20, 20)), (0, 0, 0))

    def video_with_intro(self, name, black_seconds, visible_seconds=0):
        target = self.folder / name
        graph = f"color=c=black:s=160x90:r=10:d={black_seconds}"
        if visible_seconds:
            graph += (f"[black];color=c=red:s=160x90:r=10:d={visible_seconds}[visible];"
                      "[black][visible]concat=n=2:v=1:a=0")
        subprocess.run([ffmpeg, "-v", "error", "-f", "lavfi", "-i", graph,
                        "-c:v", "mpeg4", str(target)], check=True, **subprocess_options)
        return target

    def test_video_black_intro_uses_first_visible_preview_without_editing_media(self):
        video = self.video_with_intro("black-intro.mp4", 1, 1)
        original = video.read_bytes()
        preview = load_preview(video, ffmpeg, subprocess_options)
        self.assertGreater(preview.getpixel((20, 20))[0], 200)
        self.assertLess(preview.getpixel((20, 20))[1], 30)
        self.assertEqual(video.read_bytes(), original)
        # Preview selection must not change where playback itself begins.
        first = subprocess.run([ffmpeg, "-v", "error", "-i", str(video), "-frames:v", "1",
                                "-vf", "scale=1:1", "-pix_fmt", "rgb24", "-f", "rawvideo", "-"],
                               check=True, capture_output=True, **subprocess_options).stdout
        self.assertEqual(first, bytes(3))

    def test_black_and_short_videos_keep_a_valid_fallback_with_bounded_search(self):
        for name, black, visible in (("all-black.mp4", 1, 0), ("short-black.mp4", .1, 0),
                                     ("late-content.mp4", 5.5, .5)):
            with self.subTest(name=name):
                video = self.video_with_intro(name, black, visible)
                preview = load_preview(video, ffmpeg, subprocess_options)
                self.assertEqual(preview.size, (640, 360))
                self.assertEqual(preview.getpixel((20, 20)), (0, 0, 0))

    def test_cancelled_black_intro_search_kills_the_real_decoder_promptly(self):
        video = self.video_with_intro("cancel-search.mp4", 6)
        cancel, searching = threading.Event(), threading.Event()
        original_popen = subprocess.Popen
        processes = []
        def launch(command, **kwargs):
            command = list(command)
            if processes:
                command.insert(command.index("-i"), "-re")
            process = original_popen(command, **kwargs)
            processes.append(process)
            if len(processes) == 2:
                searching.set()
            return process
        def interrupt():
            if searching.wait(5):
                cancel.set()
        worker = threading.Thread(target=interrupt, daemon=True)
        worker.start()
        start = time.monotonic()
        with patch("output_monitor.subprocess.Popen", side_effect=launch):
            preview = load_preview(video, ffmpeg, subprocess_options, cancel=cancel)
        worker.join(timeout=1)
        self.assertTrue(searching.is_set(), "A black opening should start the bounded search")
        self.assertIsNone(preview)
        self.assertLess(time.monotonic() - start, 3)
        self.assertTrue(all(process.poll() is not None for process in processes))

    def test_live_preview_uses_video_margins(self):
        frame = Image.new("RGB", (640, 360), "red")
        projector = {"w": 1920, "h": 1080, "ml": 240, "mr": 240, "mt": 0, "mb": 0}
        preview = _output_preview(frame, projector)
        self.assertEqual(preview.size, (640, 360))
        self.assertEqual(preview.getbbox(), (80, 0, 560, 360))

    def test_next_preview_uses_display_aspect_and_override(self):
        video = self.folder / "anamorphic.mp4"
        subprocess.run([ffmpeg, "-v", "error", "-f", "lavfi", "-i",
                        "color=c=blue:size=160x90:duration=0.2", "-vf", "setsar=4/3",
                        "-c:v", "mpeg4", str(video)], check=True, **subprocess_options)
        self.assertEqual(load_preview(video, ffmpeg, subprocess_options).size, (640, 270))
        preview = load_preview(video, ffmpeg, subprocess_options, aspect="4:3")
        self.assertEqual(preview.size, (480, 360))


if __name__ == "__main__":
    unittest.main()
