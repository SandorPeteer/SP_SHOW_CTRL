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
from windows_theme import MINT, VIOLET


_PREVIEW_SIZE = (640, 360)
_OVERRIDE_RATIOS = {"4:3": 4 / 3, "16:9": 16 / 9}


def _fit_size(width, height):
    factor = min(_PREVIEW_SIZE[0] / width, _PREVIEW_SIZE[1] / height)
    return max(1, round(width * factor)), max(1, round(height * factor))


def _output_preview(frame, viewport):
    if not isinstance(viewport, dict):
        return ImageOps.contain(frame, _PREVIEW_SIZE)
    width, height = viewport.get("w", 0), viewport.get("h", 0)
    left, right = viewport.get("ml", 0), viewport.get("mr", 0)
    top, bottom = viewport.get("mt", 0), viewport.get("mb", 0)
    video_width, video_height = width - left - right, height - top - bottom
    if min(width, height, video_width, video_height) <= 0:
        return ImageOps.contain(frame, _PREVIEW_SIZE)
    par = viewport.get("par", 1) or 1
    w, h = _fit_size(width * par, height)
    # Convert the renderer's video rectangle into preview pixels. Negative margins
    # represent crop; clip first so even a zoomed image stays within the budget.
    x0, y0 = round(left * w / width), round(top * h / height)
    x1, y1 = round((width - right) * w / width), round((height - bottom) * h / height)
    x, y = max(0, x0), max(0, y0)
    end_x, end_y = min(w, x1), min(h, y1)
    canvas = Image.new("RGB", (w, h))
    if end_x > x and end_y > y:
        scale_x = frame.width / (x1 - x0)
        scale_y = frame.height / (y1 - y0)
        area = ((x - x0) * scale_x, (y - y0) * scale_y,
                min(frame.width, (end_x - x0) * scale_x), min(frame.height, (end_y - y0) * scale_y))
        canvas.paste(frame.resize((end_x - x, end_y - y), Image.Resampling.LANCZOS, box=area), (x, y))
    return canvas


def capture_output(session):
    if session is None or not session.is_alive() or session.get_property("idle-active"):
        return None
    before = session.get_property("path")
    if not before:
        return None
    # Software 'window' screenshots stretch the video to the entire viewport.
    # Capture only the video and apply the renderer's layout ourselves.
    with tempfile.TemporaryDirectory(prefix="sp-monitor-") as tmp:
        target = Path(tmp) / "frame.jpg"
        result = session.command(["screenshot-to-file", str(target), "subtitles"], timeout=2)
        if result.get("error") != "success":
            raise RuntimeError(result.get("error", "mpv screenshot failed"))
        viewport = session.get_property("osd-dimensions")
        if session.get_property("path") != before:
            return None
        with Image.open(target) as frame:
            frame.draft("RGB", _PREVIEW_SIZE)
            return _output_preview(frame.convert("RGB"), viewport)


def _next_preview(frame, aspect):
    image = frame.convert("RGB")
    if aspect in _OVERRIDE_RATIOS:
        return image.resize(_fit_size(_OVERRIDE_RATIOS[aspect], 1), Image.Resampling.LANCZOS)
    return ImageOps.contain(image, _PREVIEW_SIZE)


def load_preview(path, ffmpeg, subprocess_kwargs, cancel=None, aspect="Source"):
    path = Path(path)
    poster = path.with_suffix(".preview.png")
    if poster.is_file():
        path = poster
    try:
        with Image.open(path) as frame:
            return _next_preview(ImageOps.exif_transpose(frame), aspect)
    except (OSError, ValueError):
        pass
    if not ffmpeg:
        raise RuntimeError("Az FFmpeg nem található a csomagban.")
    command = [
        ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-threads", "1",
        "-i", str(path), "-map", "0:v:0", "-an", "-frames:v", "1", "-threads", "1",
        "-vf", "scale=640:360:force_original_aspect_ratio=decrease:reset_sar=1",
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
        return _next_preview(frame, aspect)


class OutputMonitor(ttk.Frame):
    def __init__(self, parent, icons, ui_queue, ffmpeg, subprocess_kwargs, log):
        super().__init__(parent, style="Shell.TFrame")
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
        self.titles = {}
        self.rowconfigure(0, weight=1)
        self.live_badge = tk.StringVar()
        for column, (key, title, icon, accent) in enumerate((
                ("live", "On screen", "screen", MINT), ("next", "Preview", "slides", VIOLET))):
            self.columnconfigure(column, weight=1, uniform="monitor")
            box = ttk.Frame(self, padding=6, style="WorkspaceCard.TFrame")
            box.grid(row=0, column=column, sticky="nsew", padx=(0, 4) if column == 0 else (4, 0))
            heading = ttk.Frame(box)
            heading.pack(fill="x", pady=(0, 5))
            self.titles[key] = ttk.Label(heading, text=title, image=icons.image(icon, accent, 16),
                                         compound="left", style="SignalTitle.TLabel")
            self.titles[key].pack(side="left")
            if key == "live":
                ttk.Label(heading, textvariable=self.live_badge, style="LiveMonitor.TLabel").pack(side="right")
            self.captions[key] = tk.StringVar()
            ttk.Label(box, textvariable=self.captions[key], width=1, anchor="w",
                      style="SignalCaption.TLabel").pack(side="bottom", fill="x", pady=(4, 0))
            canvas = tk.Canvas(box, width=1, height=1, bg="#070a10", highlightthickness=0)
            canvas.pack(fill="both", expand=True)
            self.canvases[key] = canvas
            canvas.bind("<Configure>", lambda event, item=key: self._draw(item))

    def _draw(self, key, status=""):
        if self.closed:
            return
        canvas = self.canvases[key]
        canvas.delete("all")
        width, height = max(1, canvas.winfo_width()), max(1, canvas.winfo_height())
        frame = self.frames[key]
        if key == "live":
            badge = "● Live" if frame is not None else ""
            if self.live_badge.get() != badge:
                self.live_badge.set(badge)
        if frame is None:
            self.photos.pop(key, None)
            canvas.create_text(width // 2, height // 2, text=status, width=max(1, width - 12), fill="#bdbdbd")
            return
        photo = ImageTk.PhotoImage(ImageOps.contain(frame, (width, height)))
        self.photos[key] = photo
        canvas.create_image(width // 2, height // 2, image=photo)

    def update_targets(self, session, next_path, next_caption, live_caption, aspect="Source", *, next_is_slide=False):
        if self.closed:
            return
        if self.captions["live"].get() != live_caption:
            self.captions["live"].set(live_caption)
        if self.captions["next"].get() != next_caption:
            self.captions["next"].set(next_caption)
        next_title = "Next slide" if next_is_slide else "Preview"
        if self.titles["next"].cget("text") != next_title:
            self.titles["next"].configure(text=next_title)
        live_target = (session, live_caption, aspect)
        if live_target != self.targets["live"]:
            self.frames["live"] = None
            self._draw("live")
        self.targets["live"] = live_target
        next_target = (next_path, aspect) if next_path else None
        if next_target != self.targets["next"]:
            self.targets["next"] = next_target
            self.frames["next"] = None
            self.errors.pop("next", None)
            self._draw("next", "Loading preview…" if next_path else "")
        if session is not None and live_caption:
            self._request("live", lambda: capture_output(session))
        if next_path and self.frames["next"] is None and not self.errors.get("next"):
            self._request("next", lambda: load_preview(next_path, self.ffmpeg(), self.subprocess_kwargs(),
                                                       self.cancelled, aspect))

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
                self._draw(key, "Preview unavailable" if error else "")
                if error and self.errors.get(key) != error:
                    self.log(f"Preview ({key}): {error}")
                self.errors[key] = error
            self.ui_queue.put(apply)

        self.executor.submit(run)

    def close(self):
        self.closed = True
        self.cancelled.set()
        self.executor.shutdown(wait=False, cancel_futures=True)
