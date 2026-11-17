"""Bounded, cancellable waveform generation with the bundled FFmpeg."""
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
import math
import subprocess
import threading
import time


def _fit_waveform(png, width, height):
    from PIL import Image

    with Image.open(BytesIO(png)) as source:
        image = source.convert("RGBA")
    bounds = image.getbbox()
    center = image.height // 2
    peak = max(center - bounds[1], bounds[3] - 1 - center) if bounds else 0
    # Preserve the horizontal timing and the relative heights. A 64× cap keeps
    # almost-silent noise from filling the display.
    half = min(center, max(peak + 1, math.ceil(center / 64)))
    draw_height = max(1, round(height * 0.88))
    if not peak:
        half = center
    wave = image.crop((0, center - half, image.width, center + half))
    canvas = Image.new("RGBA", (width, height))
    canvas.paste(wave.resize((width, draw_height), Image.Resampling.LANCZOS),
                 (0, (height - draw_height) // 2))
    output = BytesIO()
    canvas.save(output, format="PNG")
    return output.getvalue()


def render_waveform(ffmpeg, path, width, height, *, cancel, subprocess_kwargs, fit_display=False):
    scan_height = max(height, 1024) if fit_display else height
    peaks = ":filter=peak:draw=full" if fit_display else ""
    command = [
        ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-threads", "1",
        "-i", str(path), "-filter_complex_threads", "1", "-filter_complex",
        f"[0:a:0]showwavespic=s={width}x{scan_height}:colors=#4a9eff{peaks}[wave]",
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
                    return _fit_waveform(output, width, height) if fit_display else output
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

    def request(self, key, ffmpeg, path, width, height, callback, subprocess_kwargs, *, fit_display=False):
        self.cancel(key)
        cancelled = threading.Event()

        def work():
            try:
                data = render_waveform(ffmpeg, path, width, height, cancel=cancelled,
                                       subprocess_kwargs=subprocess_kwargs, fit_display=fit_display)
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
