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


def _slide_documents(package):
    ns = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
    presentation = ElementTree.fromstring(package.read("ppt/presentation.xml"))
    rels = {r.get("Id"): r.get("Target") for r in ElementTree.fromstring(package.read("ppt/_rels/presentation.xml.rels"))}
    for i, node in enumerate(presentation.findall("{*}sldIdLst/{*}sldId"), 1):
        target = rels[node.get(ns + "id")]
        name = posixpath.normpath(posixpath.join("ppt", target)).lstrip("/")
        yield i, name, ElementTree.fromstring(package.read(name))


def _moving_slides(source):
    """Read slide order, timing trees, transitions and media references from OOXML."""
    if not zipfile.is_zipfile(source):
        return None
    with zipfile.ZipFile(source) as package:
        numbers = set()
        for i, name, slide in _slide_documents(package):
            tags = {el.tag.rsplit("}", 1)[-1] for el in slide.iter()}
            transition = slide.find("{*}transition")
            visual_transition = transition is not None and len(transition) > 0
            if visual_transition or tags.intersection({"timing", "videoFile", "audioFile", "media", "oleObj"}):
                numbers.add(i)
                continue
            directory, filename = posixpath.split(name)
            try:
                raw = package.read(f"{directory}/_rels/{filename}.rels")
            except KeyError:
                continue
            if any((r.get("Target") or "").lower().endswith((".gif", ".apng", ".webp"))
                   or (r.get("Type") or "").rsplit("/", 1)[-1] in {"video", "audio", "media"}
                   for r in ElementTree.fromstring(raw)):
                numbers.add(i)
        return numbers


def read_timing(source, files):
    """Read new export metadata, or recover timing from an older PPTX without re-rendering."""
    try:
        manifest = json.loads((files[0].parent / "slides.json").read_text(encoding="utf-8"))
        seconds = manifest.get("seconds")
        if seconds is None and zipfile.is_zipfile(source):
            seconds = []
            with zipfile.ZipFile(source) as package:
                for _, _, slide in _slide_documents(package):
                    if slide.get("show") in ("0", "false"):
                        continue
                    transition = slide.find("{*}transition")
                    value = transition.get("advTm") if transition is not None else None
                    seconds.append(float(value) / 1000 if value is not None else None)
        if isinstance(seconds, list) and len(seconds) == len(files):
            return [float(value) if isinstance(value, (int, float)) and math.isfinite(value) and value > 0 else None
                    for value in seconds]
    except (OSError, ValueError, KeyError, zipfile.BadZipFile, ElementTree.ParseError):
        pass
    return [None] * len(files)


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
            seconds = []
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
                    slide.Export(str(out.with_suffix(".preview.png")), "PNG", width, 1080)
                    _render_video(ppt, source, i, out, max(5, length, math.ceil(transition.AdvanceTime)))
                else:
                    slide.Export(str(out), "PNG", width, 1080)
                if not out.is_file() or out.stat().st_size == 0:
                    raise RuntimeError(f"A PowerPoint nem készítette el a(z) {i}. diát.")
                kinds.append(kind)
                seconds.append(float(transition.AdvanceTime) if transition.AdvanceOnTime else None)
            if not kinds:
                raise ValueError("Nincs vetíthető dia a bemutatóban.")
            return {"slides": kinds, "seconds": seconds}
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


def _stamp(source):
    info = Path(source).stat()
    return info.st_size, info.st_mtime_ns, info.st_ctime_ns


def prepare(source, cache, progress, render=_render):
    source = Path(source).resolve(strict=True)
    initial = _stamp(source)
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
        rendered = render(source, stage, progress)
        metadata = rendered if isinstance(rendered, dict) else {"slides": rendered}
        metadata.update(version=1, sha256=digest)
        (stage / "slides.json").write_text(json.dumps(metadata), encoding="utf-8")
        if initial != _stamp(source):
            raise ValueError("A bemutató előkészítés közben megváltozott. Indítsd újra az előkészítést.")
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
        self.ready = {}

    def submit(self, path, progress, done):
        # Prepared decks must be selectable even while Office renders the next deck.
        known = self.ready.get(path)
        if known is not None:
            try:
                if (_stamp(path) == known[0] and known[1]
                        and all(slide.is_file() and slide.stat().st_size for slide in known[1])):
                    self.ui_queue.put(lambda: done(path, known[1], None))
                    return
            except OSError:
                pass
            self.ready.pop(path, None)
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
                stamp = _stamp(path)
                files = prepare(path, self.cache, lambda s: self.ui_queue.put(lambda msg=s, cb=progress: cb(msg)))
                if _stamp(path) != stamp:
                    raise ValueError("A bemutató közben megváltozott. Kérd újra az előkészítést.")
                error = None
            except Exception as exc:
                files, error, stamp = [], str(exc), None
            def finish(p=path, items=files, err=error, signature=stamp, cb=done):
                self.pending.discard(p)
                if not err:
                    self.ready[p] = (signature, items)
                cb(p, items, err)
            self.ui_queue.put(finish)
