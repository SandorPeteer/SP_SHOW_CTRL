"""Controller previews of the live mpv frame and the next selected slide."""
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import tkinter as tk
from tkinter import ttk

from PIL import Image, ImageOps, ImageTk


def capture_output(session):
    if session is None or not session.is_alive() or session.get_property("idle-active"):
        return None
    before = session.get_property("path")
    if not before:
        return None
    # Software screenshots run off the presentation renderer's thread.
    with tempfile.TemporaryDirectory(prefix="sp-monitor-") as tmp:
        target = Path(tmp) / "frame.jpg"
        result = session.command(["screenshot-to-file", str(target), "window"], timeout=2)
        if result.get("error") != "success":
            raise RuntimeError(result.get("error", "mpv screenshot failed"))
        if session.get_property("path") != before:
            return None
        with Image.open(target) as frame:
            return ImageOps.contain(frame.convert("RGB"), (640, 360))


def load_preview(path, ffmpeg, subprocess_kwargs, cancel=None):
    path = Path(path)
    poster = path.with_suffix(".preview.png")
    if poster.is_file():
        path = poster
    try:
        with Image.open(path) as frame:
            return ImageOps.contain(ImageOps.exif_transpose(frame).convert("RGB"), (640, 360))
    except (OSError, ValueError):
        pass
    if not ffmpeg:
        raise RuntimeError("Az FFmpeg nem található a csomagban.")
    command = [
        ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-threads", "1",
        "-i", str(path), "-map", "0:v:0", "-an", "-frames:v", "1", "-threads", "1",
        "-vf", "scale=640:360:force_original_aspect_ratio=decrease",
        "-f", "image2pipe", "-vcodec", "png", "-",
    ]
    with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **subprocess_kwargs) as process:
        deadline = time.monotonic() + 20
        try:
            while True:
                if cancel is not None and cancel.is_set():
                    return None
                try:
                    output, errors = process.communicate(timeout=0.25)
                    break
                except subprocess.TimeoutExpired:
                    if time.monotonic() >= deadline:
                        raise TimeoutError("Az előnézet előállítása túllépte a 20 másodpercet.")
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate()
        if process.returncode or not output:
            raise RuntimeError(errors.decode(errors="replace").strip() or "Üres előnézet.")
    with Image.open(BytesIO(output)) as frame:
        return frame.convert("RGB")


class OutputMonitor(ttk.Frame):
    def __init__(self, parent, ui_queue, ffmpeg, subprocess_kwargs, log):
        super().__init__(parent)
        self.ui_queue = ui_queue
        self.ffmpeg = ffmpeg
        self.subprocess_kwargs = subprocess_kwargs
        self.log = log
        self.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="output-monitor")
        self.closed = False
        self.cancelled = threading.Event()
        self.busy = {"live": False, "next": False}
        self.targets = {"live": None, "next": None}
        self.frames = {"live": None, "next": None}
        self.photos = {}
        self.errors = {}
        self.canvases = {}
        self.captions = {}
        for column, (key, title) in enumerate((("live", "MOST A KIVETÍTŐN"), ("next", "KÖVETKEZŐ DIA"))):
            self.columnconfigure(column, weight=1, uniform="monitor")
            box = ttk.LabelFrame(self, text=title, padding=2)
            box.grid(row=0, column=column, sticky="nsew", padx=(0, 3) if column == 0 else (3, 0))
            canvas = tk.Canvas(box, width=1, height=140, bg="black", highlightthickness=0)
            canvas.pack(fill="x")
            self.canvases[key] = canvas
            self.captions[key] = tk.StringVar()
            ttk.Label(box, textvariable=self.captions[key], width=1, anchor="w").pack(fill="x")
            canvas.bind("<Configure>", lambda event, item=key: self._draw(item))

    def _draw(self, key, status=""):
        if self.closed:
            return
        canvas = self.canvases[key]
        canvas.delete("all")
        width, height = max(1, canvas.winfo_width()), max(1, canvas.winfo_height())
        frame = self.frames[key]
        if frame is None:
            self.photos.pop(key, None)
            canvas.create_text(width // 2, height // 2, text=status, width=max(1, width - 12), fill="#bdbdbd")
            return
        photo = ImageTk.PhotoImage(ImageOps.contain(frame, (width, height)))
        self.photos[key] = photo
        canvas.create_image(width // 2, height // 2, image=photo)

    def update_targets(self, session, next_path, next_caption, live_caption):
        if self.closed:
            return
        self.captions["live"].set(live_caption)
        self.captions["next"].set(next_caption)
        live_target = (session, live_caption)
        if live_target != self.targets["live"]:
            self.frames["live"] = None
            self._draw("live")
        self.targets["live"] = live_target
        if next_path != self.targets["next"]:
            self.targets["next"] = next_path
            self.frames["next"] = None
            self.errors.pop("next", None)
            self._draw("next", "Előnézet készül…" if next_path else "")
        self._request("live", lambda: capture_output(session))
        if next_path and self.frames["next"] is None and not self.errors.get("next"):
            self._request("next", lambda: load_preview(next_path, self.ffmpeg(), self.subprocess_kwargs(), self.cancelled))

    def _request(self, key, work):
        if self.busy[key]:
            return
        target = self.targets[key]
        self.busy[key] = True

        def run():
            try:
                frame, error = work(), None
            except Exception as exc:
                frame, error = None, str(exc)
            def apply():
                self.busy[key] = False
                if self.closed or self.targets[key] != target:
                    return
                self.frames[key] = frame
                self._draw(key, "Előnézet nem érhető el" if error else "")
                if error and self.errors.get(key) != error:
                    self.log(f"Előnézet ({key}): {error}")
                self.errors[key] = error
            self.ui_queue.put(apply)

        self.executor.submit(run)

    def close(self):
        self.closed = True
        self.cancelled.set()
        self.executor.shutdown(wait=False, cancel_futures=True)
