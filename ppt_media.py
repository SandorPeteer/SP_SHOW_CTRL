"""Prepare numbered PowerPoint slides for the persistent mpv output."""
import hashlib
import json
import math
import posixpath
from pathlib import Path
import queue
import tempfile
import threading
import time
import zipfile
from xml.etree import ElementTree


def _read_deck(directory, digest):
    try:
        manifest = json.loads((directory / "slides.json").read_text(encoding="utf-8"))
        kinds = manifest["slides"]
        if manifest["version"] != 1 or manifest["sha256"] != digest or not isinstance(kinds, list) or not kinds:
            return None
        if any(k not in ("png", "mp4") for k in kinds):
            return None
        files = [directory / f"{i:04d}.{kind}" for i, kind in enumerate(kinds, 1)]
        if all(f.is_file() and f.stat().st_size for f in files):
            return files
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None


def _moving_slides(source):
    """Read slide order, timing trees, transitions and media references from OOXML."""
    if not zipfile.is_zipfile(source):
        return None  # Binary .ppt: conservatively use the video renderer.
    with zipfile.ZipFile(source) as z:
        ns = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
        pres = ElementTree.fromstring(z.read("ppt/presentation.xml"))
        rels = {r.get("Id"): r.get("Target") for r in ElementTree.fromstring(z.read("ppt/_rels/presentation.xml.rels"))}
        numbers = set()
        for i, node in enumerate(pres.findall("{*}sldIdLst/{*}sldId"), 1):
            target = rels[node.get(ns + "id")]
            name = posixpath.normpath(posixpath.join("ppt", target)).lstrip("/")
            slide = ElementTree.fromstring(z.read(name))
            tags = {el.tag.rsplit("}", 1)[-1] for el in slide.iter()}
            if tags.intersection({"timing", "transition", "videoFile", "audioFile", "media", "oleObj"}):
                numbers.add(i)
                continue
            directory, filename = posixpath.split(name)
            try:
                raw = z.read(f"{directory}/_rels/{filename}.rels")
            except KeyError:
                continue
            if any((r.get("Target") or "").lower().endswith((".gif", ".apng", ".webp"))
                   or (r.get("Type") or "").rsplit("/", 1)[-1] in {"video", "audio", "media"}
                   for r in ElementTree.fromstring(raw)):
                numbers.add(i)
        return numbers


def _media_length(shapes):
    duration = -1
    for shape in shapes:
        if shape.Type == 6:  # group
            duration = max(duration, _media_length(shape.GroupItems))
        elif shape.Type == 16:  # audio or video
            try:
                seconds = math.ceil(shape.MediaFormat.Length / 1000)
            except Exception:  # Linked or legacy media can lack MediaFormat metadata.
                seconds = 0
            duration = max(duration, seconds)
        elif shape.Type in (7, 10, 26):  # OLE or web video
            duration = max(duration, 0)
    return duration


def _open_copy(app, source):
    old_security = app.AutomationSecurity
    try:
        app.AutomationSecurity = 3  # msoAutomationSecurityForceDisable
        return app.Presentations.Open(str(source), -1, -1, 0)
    finally:
        app.AutomationSecurity = old_security


def _render(source, stage, progress):
    import pythoncom
    from win32com.client import Dispatch

    pythoncom.CoInitialize()
    try:
        ppt = Dispatch("PowerPoint.Application")
        # Untitled opens a private copy; WithWindow=False never opens an editor or a show.
        doc = _open_copy(ppt, source)
        try:
            moving_slides = _moving_slides(source)
            kinds = []
            width = max(1, round(1080 * doc.PageSetup.SlideWidth / doc.PageSetup.SlideHeight))
            for i in range(1, doc.Slides.Count + 1):
                slide = doc.Slides.Item(i)
                transition = slide.SlideShowTransition
                if transition.Hidden:
                    continue
                length = _media_length(slide.Shapes)
                moving = length >= 0 or moving_slides is None or i in moving_slides
                kind = "mp4" if moving else "png"
                out = stage / f"{len(kinds)+1:04d}.{kind}"
                progress(f"Dia {i}/{doc.Slides.Count} • {kind.upper()}")
                if moving:
                    _render_video(ppt, source, i, out, max(5, length, math.ceil(transition.AdvanceTime)))
                else:
                    slide.Export(str(out), "PNG", width, 1080)
                if not out.is_file() or out.stat().st_size == 0:
                    raise RuntimeError(f"A PowerPoint nem készítette el a(z) {i}. diát.")
                kinds.append(kind)
            if not kinds:
                raise ValueError("Nincs vetíthető dia a bemutatóban.")
            return kinds
        finally:
            doc.Saved = -1  # This is an untitled working copy only.
            doc.Close()
    finally:
        pythoncom.CoUninitialize()


def _render_video(app, source, index, output, seconds):
    # Keep the original slide master, embedded assets and source location for relative links.
    copy = _open_copy(app, source)
    try:
        for n in range(copy.Slides.Count, 0, -1):
            if n != index:
                copy.Slides.Item(n).Delete()
        copy.CreateVideo(str(output), True, seconds, 1080, 30, 85)
        deadline = time.monotonic() + max(300, seconds * 8)
        while time.monotonic() < deadline:
            status = copy.CreateVideoStatus
            if status == 3:  # ppMediaTaskStatusDone
                return
            if status == 4:  # ppMediaTaskStatusFailed
                break
            time.sleep(0.25)
        raise RuntimeError(f"A(z) {index}. dia videóexportja nem fejeződött be.")
    finally:
        copy.Saved = -1
        copy.Close()


def prepare(source, cache, progress, render=_render):
    source = Path(source).resolve(strict=True)
    with source.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    target = cache / digest
    for folder in (source.with_name(source.name + ".mpv"), target):
        slides = _read_deck(folder, digest)
        if slides:
            return slides
    cache.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=cache) as tmp:
        stage = Path(tmp) / "deck"
        stage.mkdir()
        kinds = render(source, stage, progress)
        (stage / "slides.json").write_text(json.dumps({"version": 1, "sha256": digest, "slides": kinds}), encoding="utf-8")
        if not _read_deck(stage, digest):
            raise ValueError("Hiányos PowerPoint-export.")
        try:
            stage.rename(target)
        except OSError:
            if not _read_deck(target, digest):
                # A damaged cache must not block a new export.
                import uuid
                if not target.is_dir():
                    raise
                target.rename(cache / (digest + ".invalid-" + uuid.uuid4().hex))
                stage.rename(target)
    return _read_deck(target, digest)


class SlidePreparer:
    """One daemon owns Office conversions; results and progress return on the Tk thread."""
    def __init__(self, cache, ui_queue):
        self.cache = cache
        self.ui_queue = ui_queue
        self.jobs = queue.Queue()
        self.worker = None
        self.pending = set()

    def submit(self, path, progress, done):
        if path in self.pending:
            return
        self.pending.add(path)
        self.jobs.put((path, progress, done))
        if self.worker is None:
            self.worker = threading.Thread(target=self._run, daemon=True)
            self.worker.start()

    def _run(self):
        while True:
            path, progress, done = self.jobs.get()
            try:
                files = prepare(path, self.cache, lambda s: self.ui_queue.put(lambda msg=s, cb=progress: cb(msg)))
                error = None
            except Exception as exc:
                files, error = [], str(exc)
            def finish(p=path, items=files, err=error, cb=done):
                self.pending.discard(p)
                cb(p, items, err)
            self.ui_queue.put(finish)
