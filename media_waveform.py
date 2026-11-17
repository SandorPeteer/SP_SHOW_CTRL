"""Bounded, cancellable waveform generation with the bundled FFmpeg."""
from concurrent.futures import ThreadPoolExecutor
import subprocess
import threading
import time


def render_waveform(ffmpeg, path, width, height, *, cancel, subprocess_kwargs):
    command = [
        ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-threads", "1",
        "-i", str(path), "-filter_complex_threads", "1", "-filter_complex",
        f"[0:a:0]showwavespic=s={width}x{height}:colors=#4a9eff[wave]",
        "-map", "[wave]", "-an", "-frames:v", "1", "-threads", "1",
        "-f", "image2pipe", "-vcodec", "png", "-",
    ]
    if cancel.is_set():
        return None
    with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          **subprocess_kwargs) as process:
        deadline = time.monotonic() + 120
        try:
            while not cancel.is_set():
                try:
                    output, errors = process.communicate(timeout=0.25)
                    if process.returncode or not output:
                        raise RuntimeError(errors.decode(errors="replace").strip() or "Üres hullámforma.")
                    return output
                except subprocess.TimeoutExpired:
                    if time.monotonic() >= deadline:
                        raise TimeoutError("A hullámforma előállítása 120 másodperc után sem készült el.")
            return None
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate()


class WaveformWorker:
    def __init__(self, ui_queue):
        self.ui_queue = ui_queue
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="waveform")
        self.requests = {}

    def request(self, key, ffmpeg, path, width, height, callback, subprocess_kwargs):
        self.cancel(key)
        cancelled = threading.Event()

        def work():
            try:
                data = render_waveform(ffmpeg, path, width, height, cancel=cancelled,
                                       subprocess_kwargs=subprocess_kwargs)
                error = None
            except Exception as exc:
                data, error = None, str(exc)
            def deliver():
                if not cancelled.is_set():
                    callback(data, error)
            if not cancelled.is_set():
                self.ui_queue.put(deliver)

        future = self.executor.submit(work)
        self.requests[key] = (cancelled, future)

    def cancel(self, key):
        previous = self.requests.pop(key, None)
        if previous:
            previous[0].set()
            previous[1].cancel()

    def close(self):
        for key in list(self.requests):
            self.cancel(key)
        self.executor.shutdown(wait=False, cancel_futures=True)
