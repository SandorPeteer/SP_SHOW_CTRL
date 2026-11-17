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
from output_monitor import load_preview

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

    def test_video_poster_and_legacy_video_preview(self):
        video = self.folder / "következő.mp4"
        subprocess.run([ffmpeg, "-v", "error", "-f", "lavfi", "-i",
                        "color=c=red:size=160x90:duration=0.5", "-c:v", "mpeg4", str(video)],
                       check=True, **subprocess_options)
        preview = load_preview(video, ffmpeg, subprocess_options)
        self.assertGreater(preview.getpixel((20, 20))[0], 200)
        Image.new("RGB", (300, 180), "blue").save(video.with_suffix(".preview.png"))
        preview = load_preview(video, None, subprocess_options)
        self.assertEqual(preview.getpixel((20, 20)), (0, 0, 255))


if __name__ == "__main__":
    unittest.main()
