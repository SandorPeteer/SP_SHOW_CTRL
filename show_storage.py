"""Portable show files with one managed copy for each distinct media file."""
import hashlib
import json
from pathlib import Path
import shutil
import tempfile


def write_json(path, data):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(data, stream, indent=2, ensure_ascii=False)
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _stamp(path):
    stat = path.stat()
    return stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns


def _digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _collect_one(source, media_dir, cache):
    stamp = _stamp(source)
    key = (source, media_dir)
    if key in cache:
        original, destination, recorded = cache[key]
        if original == stamp and destination.is_file() and _stamp(destination) == recorded:
            return destination

    digest = _digest(source)
    slot = media_dir / digest
    if source.parent == slot:
        cache[key] = stamp, source, stamp
        return source
    slot.mkdir(parents=True, exist_ok=True)
    for candidate in slot.iterdir():
        if not candidate.name.startswith(".") and candidate.is_file() and _digest(candidate) == digest:
            cache[key] = stamp, candidate, _stamp(candidate)
            return candidate
    destination = slot / source.name
    if destination.exists():
        raise ValueError(f"A managed file was changed. Choose another show folder: {destination}")
    temporary = None
    try:
        copied = hashlib.sha256()
        with source.open("rb") as original, tempfile.NamedTemporaryFile(dir=slot, prefix=".copy-", delete=False) as stream:
            temporary = Path(stream.name)
            while chunk := original.read(1024 * 1024):
                stream.write(chunk)
                copied.update(chunk)
        if copied.hexdigest() != digest or _stamp(source) != stamp:
            raise ValueError(f"Media changed during save: {source}")
        temporary.replace(destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    cache[key] = stamp, destination, _stamp(destination)
    return destination


def _copy_prepared_slides(folder, destination):
    from ppt_media import _read_deck

    digest = destination.parent.name
    sidecar = destination.with_name(destination.name + ".mpv")
    files = _read_deck(folder, digest)
    if not files or _read_deck(sidecar, digest):
        return
    with tempfile.TemporaryDirectory(dir=destination.parent) as temporary:
        stage = Path(temporary) / "slides"
        stage.mkdir()
        shutil.copyfile(folder / "slides.json", stage / "slides.json")
        for file in files:
            shutil.copyfile(file, stage / file.name)
            poster = file.with_suffix(".preview.png")
            if poster.is_file():
                shutil.copyfile(poster, stage / poster.name)
        if not _read_deck(stage, digest):
            raise ValueError(f"The prepared slides changed during save: {folder}")
        try:
            stage.rename(sidecar)
        except OSError:
            if not _read_deck(sidecar, digest):
                raise


def save_show(path, payload, cache, prepared=None):
    """Collect source copies before atomically replacing the show manifest."""
    path = path.resolve()
    media_dir = path.with_suffix(".media")
    result = dict(payload, media_paths="show-folder")
    current = {}
    for key in ("cues_a", "cues_b"):
        result[key] = []
        for cue in payload[key]:
            source = Path(cue["path"]).resolve(strict=True)
            if not source.is_file():
                raise ValueError(f"Media is not a file: {source}")
            if source not in current:
                destination = current[source] = _collect_one(source, media_dir, cache)
                if prepared and cue["path"] in prepared:
                    _copy_prepared_slides(prepared[cue["path"]], destination)
            result[key].append(dict(cue, media_name=cue.get("media_name") or source.name,
                                    path="./" + current[source].relative_to(path.parent).as_posix()))
    write_json(path, result)
