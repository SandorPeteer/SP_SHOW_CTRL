#!/usr/bin/env python3

from __future__ import annotations

import json
import datetime
import math
import os
import platform
import queue
import shutil
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import webbrowser
import zipfile
from urllib import request as urlrequest
from urllib.error import URLError
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Literal

import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

CueKind = Literal["audio", "video", "ppt"]

APP_NAME = "S.P. Show Control"
APP_VERSION = "v01"

_APP_DIR = Path(__file__).resolve().parent
_FFTOOLS_CACHE: dict[str, str] = {}


def _is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False)) and hasattr(sys, "_MEIPASS")


def _resource_path(*parts: str) -> Path:
    base = Path(getattr(sys, "_MEIPASS")) if _is_frozen() else _APP_DIR
    return base.joinpath(*parts)


def _user_data_dir() -> Path:
    sysname = platform.system()
    if sysname == "Windows":
        root = os.environ.get("APPDATA")
        base = Path(root) if root else (Path.home() / "AppData" / "Roaming")
    elif sysname == "Darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        root = os.environ.get("XDG_DATA_HOME")
        base = Path(root) if root else (Path.home() / ".local" / "share")
    return base / "SP_Show_Control"


def _fftools_bin_dir() -> Path:
    return _user_data_dir() / "tools" / "ffmpeg" / "bin"


def _tool_exe_name(tool: str) -> str:
    if platform.system() == "Windows":
        return f"{tool}.exe"
    return tool


def _is_probably_executable_binary(path: Path) -> bool:
    try:
        if not path.exists() or not path.is_file():
            return False
    except Exception:
        return False
    try:
        head = path.open("rb").read(4)
    except Exception:
        return False
    if not head or len(head) < 2:
        return False
    sysname = platform.system()
    if sysname == "Windows":
        return head[:2] == b"MZ"
    if sysname == "Darwin":
        # Mach-O (32/64) or Fat binary magics (and reverse-endian variants).
        return head in (b"\xfe\xed\xfa\xce", b"\xfe\xed\xfa\xcf", b"\xca\xfe\xba\xbe", b"\xce\xfa\xed\xfe", b"\xcf\xfa\xed\xfe", b"\xbe\xba\xfe\xca")
    # Linux/others: ELF
    return head == b"\x7fELF"


def _resolve_fftool(tool: str) -> str | None:
    tool = str(tool or "").strip()
    if not tool:
        return None
    cached = _FFTOOLS_CACHE.get(tool)
    if cached:
        try:
            if Path(cached).exists():
                return cached
        except Exception:
            pass

    # Optional override: point to a folder that contains ffmpeg/ffplay/ffprobe.
    env_dir = os.environ.get("SP_SHOW_CTRL_FFMPEG_DIR") or os.environ.get("SP_SHOW_CTRL_TOOLS_DIR")
    if env_dir:
        try:
            cand = Path(env_dir).expanduser().resolve()
            p = cand / _tool_exe_name(tool)
            if p.exists():
                _FFTOOLS_CACHE[tool] = str(p)
                return str(p)
        except Exception:
            pass

    try:
        p = _fftools_bin_dir() / _tool_exe_name(tool)
        if p.exists() and _is_probably_executable_binary(p):
            _FFTOOLS_CACHE[tool] = str(p)
            return str(p)
    except Exception:
        pass

    # macOS: app bundles often start with a minimal PATH; check common Homebrew locations.
    if platform.system() == "Darwin":
        for base in (Path("/opt/homebrew/bin"), Path("/usr/local/bin")):
            try:
                p = base / tool
                if p.exists() and _is_probably_executable_binary(p):
                    _FFTOOLS_CACHE[tool] = str(p)
                    return str(p)
            except Exception:
                continue

    try:
        found = shutil.which(tool)
        if found:
            fp = Path(found)
            if _is_probably_executable_binary(fp):
                _FFTOOLS_CACHE[tool] = str(found)
                return str(found)
    except Exception:
        pass
    return None


def _download_url_to_file(
    url: str,
    dest: Path,
    *,
    on_progress: Callable[[int, int], None] | None = None,
    cancel_event: threading.Event | None = None,
) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    req = urlrequest.Request(url, headers={"User-Agent": "SP-Show-Control/1.0"})

    def _open_with_context(ctx: ssl.SSLContext | None):
        if ctx is None:
            return urlrequest.urlopen(req, timeout=30)
        return urlrequest.urlopen(req, timeout=30, context=ctx)

    # 1) Try system defaults.
    ctx_default: ssl.SSLContext | None = None
    try:
        ctx_default = ssl.create_default_context()
    except Exception:
        ctx_default = None

    # 2) Prepare certifi context (helps in PyInstaller bundles on macOS).
    ctx_certifi: ssl.SSLContext | None = None
    certifi_cafile: str | None = None
    try:
        import certifi  # type: ignore

        certifi_cafile = certifi.where()
    except Exception:
        certifi_cafile = None
    if certifi_cafile:
        try:
            ctx_certifi = ssl.create_default_context(cafile=certifi_cafile)
        except Exception:
            ctx_certifi = None

    def _should_retry_ssl(err: Exception) -> bool:
        s = str(err)
        return ("CERTIFICATE_VERIFY_FAILED" in s) or ("certificate verify failed" in s)

    try:
        resp_ctx = ctx_default
        resp = _open_with_context(resp_ctx)
    except URLError as e:
        if ctx_certifi is not None and _should_retry_ssl(e):
            resp = _open_with_context(ctx_certifi)
        else:
            raise
    except Exception as e:
        if ctx_certifi is not None and _should_retry_ssl(e):
            resp = _open_with_context(ctx_certifi)
        else:
            raise

    with resp:
        total = 0
        try:
            total = int(resp.headers.get("Content-Length") or 0)
        except Exception:
            total = 0
        done = 0
        with open(dest, "wb") as f:
            while True:
                if cancel_event is not None and cancel_event.is_set():
                    raise RuntimeError("Download canceled.")
                chunk = resp.read(256 * 1024)
                if not chunk:
                    break
                f.write(chunk)
                done += int(len(chunk))
                if on_progress is not None:
                    try:
                        on_progress(done, total)
                    except Exception:
                        pass


def _extract_zip_to_dir(zip_path: Path, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "r") as zf:
        zf.extractall(out_dir)


def _ensure_executable(path: Path) -> None:
    if platform.system() == "Windows":
        return
    try:
        mode = int(path.stat().st_mode)
        os.chmod(str(path), mode | 0o111)
    except Exception:
        return


def _find_tool_in_extracted_dir(out_dir: Path, tool: str) -> Path | None:
    tool = str(tool or "").strip()
    if not tool:
        return None
    want = _tool_exe_name(tool)
    best: Path | None = None
    try:
        for p in out_dir.rglob("*"):
            try:
                if not p.is_file():
                    continue
            except Exception:
                continue
            name = p.name
            if name == want or name == tool:
                return p
            if name.startswith(tool):
                if best is None:
                    best = p
                else:
                    # Prefer shorter names like "ffplay" over "ffplay-2025-01-01".
                    try:
                        if len(name) < len(best.name):
                            best = p
                    except Exception:
                        pass
    except Exception:
        return None
    return best


def _is_zipfile(path: Path) -> bool:
    try:
        return bool(zipfile.is_zipfile(path))
    except Exception:
        return False


def _install_ffmpeg_tools(
    *,
    on_status: Callable[[str], None] | None = None,
    on_progress: Callable[[int, int, int, int], None] | None = None,
    cancel_event: threading.Event | None = None,
) -> None:
    sysname = platform.system()
    if sysname not in ("Windows", "Darwin"):
        raise RuntimeError("Auto-install is supported on Windows and macOS only.")

    tools = ("ffmpeg", "ffplay", "ffprobe")
    bin_dir = _fftools_bin_dir()
    bin_dir.mkdir(parents=True, exist_ok=True)

    def _status(msg: str) -> None:
        if on_status is None:
            return
        try:
            on_status(msg)
        except Exception:
            pass

    # If already present (either in our tool dir or on the system), do nothing.
    # Also, if a file exists but is not a real executable (e.g. a 7z archive), delete it so we can replace it.
    missing: list[str] = []
    for t in tools:
        p = bin_dir / _tool_exe_name(t)
        try:
            if p.exists() and not _is_probably_executable_binary(p):
                try:
                    p.unlink()
                except Exception:
                    pass
        except Exception:
            pass
        if _resolve_fftool(t) is None:
            missing.append(t)
    if not missing:
        return

    with tempfile.TemporaryDirectory(prefix="sp_show_ctrl_ffmpeg_") as tmp:
        tmp_dir = Path(tmp)
        if sysname == "Windows":
            url = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
            zip_path = tmp_dir / "ffmpeg.zip"
            _status("Downloading FFmpeg (Windows)…")
            total_steps = 1

            def _p(done: int, total: int) -> None:
                if on_progress is not None:
                    on_progress(0, total_steps, done, total)

            _download_url_to_file(url, zip_path, on_progress=_p, cancel_event=cancel_event)
            _status("Extracting…")
            out_dir = tmp_dir / "unzip"
            _extract_zip_to_dir(zip_path, out_dir)

            def _find_exe(name: str) -> Path:
                matches = list(out_dir.glob(f"**/bin/{name}.exe"))
                if not matches:
                    matches = list(out_dir.glob(f"**/{name}.exe"))
                if not matches:
                    raise RuntimeError(f"Downloaded archive missing {name}.exe")
                return matches[0]

            for idx, t in enumerate(tools):
                _status(f"Installing {t}…")
                src = _find_exe(t)
                dst = bin_dir / f"{t}.exe"
                shutil.copy2(src, dst)
            if on_progress is not None:
                on_progress(total_steps, total_steps, 0, 0)

        else:  # macOS
            arch = str(platform.machine() or "").lower()
            # Prefer a single bundle that includes ffmpeg/ffplay/ffprobe to avoid per-tool archive quirks.
            bundle_candidates: list[str] = []
            if arch == "arm64":
                bundle_candidates += [
                    "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-macosarm64-gpl.zip",
                    "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-macosarm64-lgpl.zip",
                ]
            else:
                bundle_candidates += [
                    "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-macos64-gpl.zip",
                    "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-macos64-lgpl.zip",
                ]

            total_steps = 2  # download + extract/install
            errors: list[str] = []
            for attempt, url in enumerate(bundle_candidates, start=1):
                zip_path = tmp_dir / f"ffmpeg_bundle_{attempt}.zip"
                _status(f"Downloading FFmpeg tools (macOS)… ({attempt}/{len(bundle_candidates)})")

                def _p(done: int, total: int) -> None:
                    if on_progress is not None:
                        on_progress(0, total_steps, done, total)

                try:
                    _download_url_to_file(url, zip_path, on_progress=_p, cancel_event=cancel_event)
                    if not _is_zipfile(zip_path):
                        try:
                            head = zip_path.open("rb").read(4)
                        except Exception:
                            head = b""
                        errors.append(f"Downloaded file is not a zip (header={head!r}) [url={url}]")
                        continue
                    _status("Extracting…")
                    out_dir = tmp_dir / f"unzip_bundle_{attempt}"
                    _extract_zip_to_dir(zip_path, out_dir)
                    if on_progress is not None:
                        on_progress(1, total_steps, 0, 0)

                    for t in tools:
                        src = _find_tool_in_extracted_dir(out_dir, t)
                        if src is None:
                            names: list[str] = []
                            try:
                                with zipfile.ZipFile(zip_path, "r") as zf:
                                    names = [n for n in zf.namelist() if n][:30]
                            except Exception:
                                names = []
                            hint = f" (zip entries: {', '.join(names)})" if names else ""
                            raise RuntimeError(f"Downloaded archive missing {t}{hint} [url={url}]")
                        dst = bin_dir / t
                        shutil.copy2(src, dst)
                        _ensure_executable(dst)
                        if not _is_probably_executable_binary(dst):
                            raise RuntimeError(f"Installed {t} is not executable (maybe wrong format) [url={url}]")

                    if on_progress is not None:
                        on_progress(2, total_steps, 0, 0)
                    errors.clear()
                    break
                except Exception as e:
                    errors.append(str(e))
                    continue

            if errors:
                raise RuntimeError("\n".join(errors[-3:]))

    # Warm cache
    for t in tools:
        p = bin_dir / _tool_exe_name(t)
        if p.exists() and _is_probably_executable_binary(p):
            _FFTOOLS_CACHE[t] = str(p)


def _parse_timecode(value: str) -> float | None:
    value = (value or "").strip()
    if not value:
        return None
    if ":" not in value:
        return float(value)
    parts = value.split(":")
    if len(parts) == 2:
        minutes, seconds = parts
        return float(minutes) * 60.0 + float(seconds)
    if len(parts) == 3:
        hours, minutes, seconds = parts
        return float(hours) * 3600.0 + float(minutes) * 60.0 + float(seconds)
    raise ValueError(f"Invalid timecode: {value!r}")


def _format_timecode(seconds: float | None, with_ms: bool = False) -> str:
    if seconds is None:
        return ""
    if with_ms:
        # Format with milliseconds: mm:ss.mmm
        total_sec = max(0, float(seconds))
        ms = int(round((total_sec % 1) * 1000))
        total = int(total_sec)
        s = total % 60
        m = (total // 60) % 60
        h = total // 3600
        if h:
            return f"{h}:{m:02d}:{s:02d}.{ms:03d}"
        return f"{m}:{s:02d}.{ms:03d}"
    else:
        # Standard format without milliseconds
        total = max(0, int(round(seconds)))
        s = total % 60
        m = (total // 60) % 60
        h = total // 3600
        if h:
            return f"{h}:{m:02d}:{s:02d}"
        return f"{m}:{s:02d}"


def _shorten_middle(text: str, max_len: int = 48) -> str:
    text = str(text or "")
    if len(text) <= max_len:
        return text
    if max_len < 10:
        return text[:max_len]
    head = max_len // 2 - 2
    tail = max_len - head - 3
    return text[:head] + "…" + text[-tail:]


def _clamp_int(value: int, low: int, high: int) -> int:
    return max(low, min(high, int(value)))


def _clamp_float(value: object, low: float, high: float, default: float) -> float:
    try:
        v = float(value)
    except Exception:
        v = float(default)
    return max(float(low), min(float(high), float(v)))


def _shell_quote(s: str) -> str:
    if s == "":
        return "''"
    safe = all(ch.isalnum() or ch in "._-/:=+" for ch in s)
    if safe:
        return s
    return "'" + s.replace("'", "'\"'\"'") + "'"


def _extract_last_json_object(text: str) -> dict | None:
    try:
        s = str(text or "")
        # loudnorm prints a JSON block; stderr may contain prefixes/newlines.
        start = s.rfind("{")
        if start < 0:
            return None
        end = s.find("}", start)
        last_end = s.rfind("}")
        if last_end <= start:
            return None
        chunk = s[start : last_end + 1].strip()
        return json.loads(chunk)
    except Exception:
        return None


@dataclass
class Settings:
    second_screen_left: int = 1920
    second_screen_top: int = 0
    video_fullscreen: bool = True
    startup_volume: int = 100
    normalize_enabled: bool = False
    normalize_target_i_lufs: float = -14.0
    normalize_true_peak_db: float = -1.0

    def to_dict(self) -> dict:
        return {
            "second_screen_left": self.second_screen_left,
            "second_screen_top": self.second_screen_top,
            "video_fullscreen": self.video_fullscreen,
            "startup_volume": self.startup_volume,
            "normalize_enabled": self.normalize_enabled,
            "normalize_target_i_lufs": self.normalize_target_i_lufs,
            "normalize_true_peak_db": self.normalize_true_peak_db,
        }

    @staticmethod
    def from_dict(data: dict) -> "Settings":
        s = Settings()
        if not isinstance(data, dict):
            return s
        s.second_screen_left = int(data.get("second_screen_left", s.second_screen_left))
        s.second_screen_top = int(data.get("second_screen_top", s.second_screen_top))
        s.video_fullscreen = bool(data.get("video_fullscreen", s.video_fullscreen))
        s.startup_volume = int(data.get("startup_volume", s.startup_volume))
        s.normalize_enabled = bool(data.get("normalize_enabled", s.normalize_enabled))
        s.normalize_target_i_lufs = _clamp_float(data.get("normalize_target_i_lufs", s.normalize_target_i_lufs), -30.0, -5.0, -14.0)
        s.normalize_true_peak_db = _clamp_float(data.get("normalize_true_peak_db", s.normalize_true_peak_db), -9.0, 0.0, -1.0)
        return s


@dataclass
class Cue:
    id: str
    kind: CueKind
    path: str
    note: str = ""
    start_sec: float = 0.0
    stop_at_sec: float | None = None
    fade_at_sec: float | None = None
    fade_dur_sec: float = 5.0
    fade_to_percent: int = 100
    open_on_second_screen: bool = True
    volume_percent: int | None = None
    vu_profile_q: list[int] | None = None  # 0..1000, downsampled envelope
    loudness_i_lufs: float | None = None
    true_peak_db: float | None = None

    def display_name(self) -> str:
        return Path(self.path).name

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind,
            "path": self.path,
            "note": self.note,
            "start_sec": self.start_sec,
            "stop_at_sec": self.stop_at_sec,
            "fade_at_sec": self.fade_at_sec,
            "fade_dur_sec": self.fade_dur_sec,
            "fade_to_percent": self.fade_to_percent,
            "open_on_second_screen": self.open_on_second_screen,
            "volume_percent": self.volume_percent,
            "vu_profile_q": self.vu_profile_q,
            "loudness_i_lufs": self.loudness_i_lufs,
            "true_peak_db": self.true_peak_db,
        }

    @staticmethod
    def from_dict(data: dict) -> "Cue":
        stop = data.get("stop_at_sec", None)
        if stop in ("", "null"):
            stop = None
        stop_val = None if stop is None else float(stop)
        fade_at = data.get("fade_at_sec", None)
        if fade_at in ("", "null"):
            fade_at = None
        fade_val = None if fade_at is None else float(fade_at)
        vu_profile_q = data.get("vu_profile_q", None)
        if not isinstance(vu_profile_q, list):
            vu_profile_q = None
        loud_i = data.get("loudness_i_lufs", None)
        if loud_i in ("", "null"):
            loud_i = None
        tp = data.get("true_peak_db", None)
        if tp in ("", "null"):
            tp = None
        return Cue(
            id=str(data.get("id") or uuid.uuid4()),
            kind=data.get("kind", "audio"),
            path=str(data.get("path", "")),
            note=str(data.get("note", "")),
            start_sec=float(data.get("start_sec", 0.0)),
            stop_at_sec=stop_val,
            fade_at_sec=fade_val,
            fade_dur_sec=float(data.get("fade_dur_sec", 5.0)),
            fade_to_percent=int(data.get("fade_to_percent", 100)),
            open_on_second_screen=bool(data.get("open_on_second_screen", True)),
            volume_percent=(None if data.get("volume_percent", None) in (None, "", "null") else int(data.get("volume_percent"))),
            vu_profile_q=vu_profile_q,
            loudness_i_lufs=(None if loud_i is None else float(loud_i)),
            true_peak_db=(None if tp is None else float(tp)),
        )


class MediaRunner:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._proc: subprocess.Popen | None = None
        self._playing_cue: Cue | None = None
        self._started_at_monotonic: float | None = None
        self._playing_seek_sec: float | None = None
        self.last_args: list[str] | None = None
        self.last_exit_code: int | None = None
        self.last_stderr_tail: list[str] = []

    def is_playing(self) -> bool:
        if self._proc is None:
            return False
        rc = self._proc.poll()
        if rc is None:
            return True
        self.last_exit_code = rc
        return False

    def current_cue(self) -> Cue | None:
        return self._playing_cue

    def stop(self) -> None:
        proc = self._proc
        self._proc = None
        self._playing_cue = None
        self._started_at_monotonic = None
        self._playing_seek_sec = None
        if not proc:
            return
        try:
            proc.terminate()
            proc.wait(timeout=1.5)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    def _spawn_ffplay(self, args: list[str]) -> subprocess.Popen:
        self.last_args = args
        self.last_exit_code = None
        self.last_stderr_tail = []

        proc = subprocess.Popen(
            args,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )

        def _read_stderr():
            if proc.stderr is None:
                return
            try:
                for line in proc.stderr:
                    line = (line or "").rstrip()
                    if not line:
                        continue
                    self.last_stderr_tail.append(line)
                    if len(self.last_stderr_tail) > 80:
                        self.last_stderr_tail = self.last_stderr_tail[-80:]
            except Exception:
                pass

        threading.Thread(target=_read_stderr, daemon=True).start()
        return proc

    def debug_text(self) -> str:
        args = self.last_args
        rc = self.last_exit_code
        tail = self.last_stderr_tail
        if not args:
            return "No ffplay command yet."
        msg = "Backend: ffplay\n\nCommand:\n" + " ".join(_shell_quote(a) for a in args)
        if rc is not None:
            msg += f"\n\nExit code: {rc}"
        if tail:
            msg += "\n\nstderr (tail):\n" + "\n".join(tail[-30:])
        return msg

    def play(self, cue: Cue) -> None:
        if cue.kind == "ppt":
            self.stop()
            ppt_open_and_start(
                cue.path,
                on_second_screen=bool(getattr(cue, "open_on_second_screen", False)),
                second_screen_left=int(getattr(self.settings, "second_screen_left", 0)),
                second_screen_top=int(getattr(self.settings, "second_screen_top", 0)),
            )
            return

        ffplay = _resolve_fftool("ffplay")
        if not ffplay:
            raise RuntimeError("ffplay not found (install FFmpeg).")

        self.stop()
        duration_limit = None
        if cue.stop_at_sec is not None and cue.stop_at_sec > cue.start_sec:
            duration_limit = float(cue.stop_at_sec) - float(cue.start_sec)
        vol_override = None
        try:
            if cue.volume_percent is not None:
                vol_override = int(cue.volume_percent)
        except Exception:
            vol_override = None
        args = self._build_ffplay_args(ffplay, cue, duration_limit=duration_limit, volume_override=vol_override)
        self._proc = self._spawn_ffplay(args)
        self._playing_cue = cue
        self._started_at_monotonic = time.monotonic()
        self._playing_seek_sec = float(cue.start_sec)

    def play_at(self, cue: Cue, position_sec: float, *, volume_override: int | None = None) -> None:
        if cue.kind == "ppt":
            self.stop()
            ppt_open_and_start(
                cue.path,
                on_second_screen=bool(getattr(cue, "open_on_second_screen", False)),
                second_screen_left=int(getattr(self.settings, "second_screen_left", 0)),
                second_screen_top=int(getattr(self.settings, "second_screen_top", 0)),
            )
            return

        ffplay = _resolve_fftool("ffplay")
        if not ffplay:
            raise RuntimeError("ffplay not found (install FFmpeg).")

        pos = max(0.0, float(position_sec))
        if cue.stop_at_sec is not None and pos >= float(cue.stop_at_sec):
            self.stop()
            return

        duration_limit = None
        if cue.stop_at_sec is not None and float(cue.stop_at_sec) > pos:
            duration_limit = float(cue.stop_at_sec) - pos

        self.stop()
        args = self._build_ffplay_args(
            ffplay,
            cue,
            seek_override=float(pos),
            duration_limit=duration_limit,
            volume_override=volume_override,
        )
        self._proc = self._spawn_ffplay(args)
        self._playing_cue = cue
        self._started_at_monotonic = time.monotonic()
        self._playing_seek_sec = float(pos)

    def playback_position_sec(self) -> float | None:
        if not self.is_playing():
            return None
        cue = self._playing_cue
        if not cue or cue.kind == "ppt":
            return None
        if self._started_at_monotonic is None or self._playing_seek_sec is None:
            return None
        elapsed = max(0.0, time.monotonic() - self._started_at_monotonic)
        return max(0.0, float(self._playing_seek_sec) + elapsed)

    def _build_ffplay_args(
        self,
        ffplay: str,
        cue: Cue,
        *,
        seek_override: float | None = None,
        audio_filter: str | None = None,
        duration_limit: float | None = None,
        volume_override: int | None = None,
    ) -> list[str]:
        args: list[str] = [
            ffplay,
            "-hide_banner",
            "-loglevel",
            "error",
            "-autoexit",
            "-volume",
            str(_clamp_int(self.settings.startup_volume if volume_override is None else volume_override, 0, 100)),
        ]

        seek = cue.start_sec if seek_override is None else float(seek_override)
        if seek > 0:
            args += ["-ss", f"{seek:.3f}"]

        if cue.kind == "audio":
            args += ["-nodisp"]

        if cue.kind == "video":
            if cue.open_on_second_screen:
                args += [
                    "-left",
                    str(int(self.settings.second_screen_left)),
                    "-top",
                    str(int(self.settings.second_screen_top)),
                ]
            else:
                args += ["-left", "80", "-top", "80", "-x", "960", "-y", "540"]
            if cue.open_on_second_screen and self.settings.video_fullscreen:
                args += ["-fs"]
            args += ["-alwaysontop"]

        if duration_limit is not None:
            args += ["-t", f"{float(duration_limit):.3f}"]

        filters: list[str] = []
        try:
            if (
                cue.kind in ("audio", "video")
                and bool(getattr(self.settings, "normalize_enabled", False))
                and cue.loudness_i_lufs is not None
            ):
                target_i = float(getattr(self.settings, "normalize_target_i_lufs", -14.0))
                tp_limit = float(getattr(self.settings, "normalize_true_peak_db", -1.0))
                gain_db = float(target_i) - float(cue.loudness_i_lufs)
                # Prevent pushing past true peak when known.
                if cue.true_peak_db is not None:
                    gain_db = min(gain_db, float(tp_limit) - float(cue.true_peak_db))
                gain_db = max(-18.0, min(18.0, gain_db))
                if abs(gain_db) >= 0.05:
                    filters.append(f"volume={gain_db:.2f}dB")
                if gain_db > 0.25:
                    filters.append("alimiter=limit=0.97")
        except Exception:
            pass

        if audio_filter:
            filters.append(audio_filter)
        if filters:
            args += ["-af", ",".join(filters)]

        args.append(cue.path)
        return args

    def restart_at(self, position_sec: float, *, volume_override: int | None = None) -> None:
        cue = self._playing_cue
        if cue is None or cue.kind == "ppt":
            return
        ffplay = _resolve_fftool("ffplay")
        if not ffplay:
            return
        pos = max(0.0, float(position_sec))
        if cue.stop_at_sec is not None and pos >= float(cue.stop_at_sec):
            self.stop()
            return
        duration_limit = None
        if cue.stop_at_sec is not None and cue.stop_at_sec > pos:
            duration_limit = float(cue.stop_at_sec) - float(pos)

        self.stop()
        args = self._build_ffplay_args(
            ffplay,
            cue,
            seek_override=float(pos),
            duration_limit=duration_limit,
            volume_override=volume_override,
        )
        self._proc = self._spawn_ffplay(args)
        self._playing_cue = cue
        self._started_at_monotonic = time.monotonic()
        self._playing_seek_sec = float(pos)

    def restart_with_volume(self, volume_percent: int) -> None:
        pos = self.playback_position_sec()
        if pos is None:
            return
        self.restart_at(pos, volume_override=int(volume_percent))


def _build_volume_fade_filter(cue: Cue, seek_sec: float) -> str | None:
    # Fade is intentionally disabled (user request).
    return None


def _osascript(script: str, argv: list[str] | None = None) -> subprocess.CompletedProcess:
    cmd = ["osascript", "-e", script]
    if argv:
        cmd.append("--")
        cmd.extend(argv)
    return subprocess.run(cmd, capture_output=True, text=True)


def ppt_open_and_start(
    ppt_path: str,
    *,
    on_second_screen: bool = False,
    second_screen_left: int = 0,
    second_screen_top: int = 0,
) -> None:
    path = str(Path(ppt_path).expanduser().resolve())
    system = platform.system()
    if system == "Darwin":
        # Use `open -a` to avoid AppleScript dictionary parsing issues in some environments.
        try:
            subprocess.run(["open", "-a", "Microsoft PowerPoint", path], check=False)
        except Exception:
            subprocess.run(["open", path], check=False)
        # Try to start Slide Show (presenter-remote style: keystrokes).
        # If configured, also move/resize to the 2nd screen origin.
        last_err: Exception | None = None
        for _ in range(5):
            try:
                time.sleep(0.7)
                if bool(on_second_screen):
                    ppt_fullscreen_second_screen(
                        left=int(second_screen_left),
                        top=int(second_screen_top),
                        start_if_needed=True,
                    )
                else:
                    ppt_start_slideshow()
                last_err = None
                break
            except Exception as e:
                last_err = e
                continue
        if last_err is not None:
            raise last_err
        return

    if system == "Windows":
        try:
            import os

            os.startfile(path)  # type: ignore[attr-defined]
        except Exception as e:
            raise RuntimeError(f"Failed to open PPT: {e}") from e
        return

    subprocess.run(["xdg-open", path])


def ppt_next_slide() -> None:
    if platform.system() != "Darwin":
        return
    script = r'''
tell application "Microsoft PowerPoint" to activate
tell application "System Events"
  key code 124 -- right arrow
end tell
'''
    _osascript(script)


def ppt_prev_slide() -> None:
    if platform.system() != "Darwin":
        return
    script = r'''
tell application "Microsoft PowerPoint" to activate
tell application "System Events"
  key code 123 -- left arrow
end tell
'''
    _osascript(script)


def ppt_end_show() -> None:
    if platform.system() != "Darwin":
        return
    script = r'''
tell application "Microsoft PowerPoint" to activate
tell application "System Events"
  key code 53 -- esc
end tell
'''
    _osascript(script)

def ppt_start_slideshow() -> None:
    """Start PowerPoint Slide Show using the same mechanism as USB presenters (keyboard shortcuts).

    This is more robust than relying on PowerPoint's AppleScript dictionary, which can fail to parse
    in some packaged app environments.
    """
    if platform.system() != "Darwin":
        return
    script = r'''
tell application "Microsoft PowerPoint" to activate
delay 0.2
tell application "System Events"
  tell process "Microsoft PowerPoint"
    set frontmost to true
    -- From Beginning (commonly Cmd+Shift+Return)
    try
      key code 36 using {command down, shift down} -- Return
    end try
    delay 0.1
    -- Fallback: try F5 (key code 96 on most macOS layouts)
    try
      key code 96
    end try
  end tell
end tell
'''
    res = _osascript(script)
    if res.returncode != 0:
        msg = (res.stderr or res.stdout or "Failed to start Slide Show.").strip()
        if "not allowed assistive access" in msg.lower() or "not authorized" in msg.lower():
            msg += "\n\nmacOS: Enable Accessibility permission for this app to control Microsoft PowerPoint."
        raise RuntimeError(msg)


def ppt_fullscreen_second_screen(left: int, top: int, start_if_needed: bool = True) -> None:
    """Start Slide Show and move the show window to the 2nd screen origin (presenter-style, via UI scripting).

    Requires macOS Accessibility permission (System Events).
    """
    if platform.system() != "Darwin":
        return

    x1 = int(left)
    y1 = int(top)
    w = 8000
    h = 8000

    script = r'''
on run argv
  set startIfNeeded to item 1 of argv
  set x1 to (item 2 of argv) as integer
  set y1 to (item 3 of argv) as integer
  set w to (item 4 of argv) as integer
  set h to (item 5 of argv) as integer

  tell application "Microsoft PowerPoint" to activate
  delay 0.2

  tell application "System Events"
    tell process "Microsoft PowerPoint"
      set frontmost to true
      if startIfNeeded is "1" then
        try
          key code 36 using {command down, shift down} -- Return
        end try
        delay 0.1
        try
          key code 96 -- F5
        end try
        delay 0.2
      end if
      try
        set position of front window to {x1, y1}
        set size of front window to {w, h}
      end try
      try
        set position of window 1 to {x1, y1}
        set size of window 1 to {w, h}
      end try
    end tell
  end tell
  return "OK"
end run
'''

    res = _osascript(script, ["1" if start_if_needed else "0", str(x1), str(y1), str(w), str(h)])
    out = (res.stdout or "").strip()
    err = (res.stderr or "").strip()
    if res.returncode != 0:
        msg = (err or out or "PowerPoint control failed").strip()
        if "not allowed assistive access" in msg.lower() or "not authorized" in msg.lower():
            msg += "\n\nmacOS: Enable Accessibility permission for this app to control Microsoft PowerPoint."
        raise RuntimeError(msg)
    if out and out != "OK":
        raise RuntimeError(out)
    return


def probe_media_duration_sec(path: str, timeout_sec: float = 3.0) -> float | None:
    ffprobe = _resolve_fftool("ffprobe")
    if not ffprobe:
        return None
    try:
        res = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=nw=1:nk=1",
                path,
            ],
            capture_output=True,
            text=True,
            timeout=float(timeout_sec),
        )
        if res.returncode != 0:
            return None
        dur = float((res.stdout or "").strip())
        if dur <= 0:
            return None
        return dur
    except Exception:
        return None


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("S.P. Show Control – MVP (ffplay)")
        self.geometry("1280x720")
        self.minsize(1100, 650)
        self._app_icon_image: tk.PhotoImage | None = None
        try:
            icon_path = _resource_path("assets", "logo.png")
            if icon_path.exists():
                self._app_icon_image = tk.PhotoImage(file=str(icon_path))
                self.iconphoto(True, self._app_icon_image)
        except Exception:
            self._app_icon_image = None

        self.settings = Settings()
        self.audio_runner = MediaRunner(self.settings)
        self.title("S.P. Show Control – MVP (ffplay)")
        self.video_runner = MediaRunner(self.settings)
        self._active_runner = self.audio_runner

        self._show_path: Path | None = None
        self._loaded_preset_path: Path | None = None
        self._cues: list[Cue] = []  # Legacy - now using _cues_a and _cues_b
        self._loading_editor = False
        self._duration_cache: dict[str, float] = {}
        self._current_duration: float | None = None
        self._was_playing = False
        self._was_playing_a = False
        self._was_playing_b = False
        self._inhibit_auto_advance = False
        self._log_max_lines = 800
        self._log_buffer: list[str] = []
        self._global_setup_win: tk.Toplevel | None = None
        self._about_logo_image: tk.PhotoImage | None = None
        self._analysis_batch_ids: set[str] = set()
        self._analysis_batch_total: int = 0
        self._analysis_batch_done: int = 0
        self._analysis_progress_var: tk.DoubleVar = tk.DoubleVar(value=0.0)
        self._analysis_progress_text_var: tk.StringVar = tk.StringVar(value="")
        self._analysis_progressbar = None
        self._vol_restart_after_id: str | None = None
        self._paused_cue_id: str | None = None
        self._paused_kind: CueKind | None = None
        self._paused_pos_sec: float | None = None
        self._paused_a: tuple[str, float] | None = None
        self._paused_b: tuple[str, float] | None = None
        self._suppress_finish: dict[str, str] = {}
        self._preview_proc: subprocess.Popen | None = None
        self._preview_debounce_after_id: str | None = None
        self._preview_request: tuple[str, float, float, int | None] | None = None
        self._loop_a_enabled: bool = False
        self._loop_b_enabled: bool = False
        # Transport button colors (works reliably via tk.Label-based buttons).
        self._btn_off_bg = "#4a4a4a"
        self._btn_off_fg = "#ffffff"
        self._btn_play_on_bg = "#2e7d32"
        self._btn_stop_on_bg = "#c62828"
        self._btn_loop_on_bg = "#f9a825"
        self._btn_loop_on_fg = "#111111"
        self._playing_iid_a: str | None = None
        self._playing_iid_b: str | None = None
        self._cueid_to_iid_a: dict[str, str] = {}
        self._cueid_to_iid_b: dict[str, str] = {}
        self._now_time_cache: dict[str, str] = {"A": "", "B": ""}
        self._now_fg_cache: dict[str, str | None] = {"A": None, "B": None}
        self._transport_visual_cache: dict[str, tuple[object, ...] | None] = {"A": None, "B": None}
        self._vu_profile_cache: dict[str, tuple[int, int, list[float]]] = {}
        self._vu_req_inflight: set[str] = set()
        self._loud_req_inflight: set[str] = set()
        self._loud_fail_once: set[str] = set()
        self._loudness_sem = threading.Semaphore(2)
        self._vu_items: dict[str, dict[str, int] | None] = {"A": None, "B": None}
        self._vu_state: dict[str, dict[str, float]] = {
            "A": {"level": 0.0, "peak": 0.0, "last_t": float(time.monotonic()), "peak_hold_until": 0.0},
            "B": {"level": 0.0, "peak": 0.0, "last_t": float(time.monotonic()), "peak_hold_until": 0.0},
        }
        self._vu_visible: dict[str, bool] = {"A": False, "B": False}
        self._vu_db_cache: dict[str, str] = {"A": "", "B": ""}
        self._ui_tasks: queue.SimpleQueue = queue.SimpleQueue()
        self._wave_req_seq: dict[str, int] = {"A": 0, "B": 0}
        self._wave_req_cue_id: dict[str, str | None] = {"A": None, "B": None}
        self._playback_items: dict[str, dict[str, int] | None] = {"A": None, "B": None}
        self._playback_visible: dict[str, bool] = {"A": False, "B": False}

        # Global display settings (2nd screen placement + fullscreen)
        self.var_left = tk.StringVar(value=str(self.settings.second_screen_left))
        self.var_top = tk.StringVar(value=str(self.settings.second_screen_top))
        self.var_fs = tk.BooleanVar(value=self.settings.video_fullscreen)
        for var in (self.var_left, self.var_top, self.var_fs):
            var.trace_add("write", lambda *_: self._apply_settings_from_vars())

        self._build_ui()
        self.after(0, self._bring_to_front)
        self._poll_playback()
        self.after(0, self._startup_sequence)

    def _startup_sequence(self) -> None:
        def _after_deps() -> None:
            loaded = self._auto_load_preset()
            if not loaded:
                self._refresh_tree()
                self._load_selected_into_editor()

        self._ensure_ffmpeg_tools_async(_after_deps)

    def _wave_help_text(self) -> str:
        # Keep this short-ish to avoid affecting layout.
        return "Click to seek (during playback)"

    def _make_transport_button(self, parent, text_or_var, command):
        if isinstance(text_or_var, tk.StringVar):
            label = tk.Label(
                parent,
                textvariable=text_or_var,
                bg=self._btn_off_bg,
                fg=self._btn_off_fg,
                font=("Helvetica", 13, "bold"),
                padx=12,
                pady=8,
                bd=0,
                cursor="hand2",
            )
        else:
            label = tk.Label(
                parent,
                text=str(text_or_var),
                bg=self._btn_off_bg,
                fg=self._btn_off_fg,
                font=("Helvetica", 13, "bold"),
                padx=12,
                pady=8,
                bd=0,
                cursor="hand2",
            )

        def _on_click(_e=None):
            try:
                command()
            except Exception:
                return

        label.bind("<Button-1>", _on_click)
        return label

    def _ppt_fullscreen_2nd_screen(self) -> None:
        try:
            ppt_fullscreen_second_screen(
                left=int(getattr(self.settings, "second_screen_left", 0)),
                top=int(getattr(self.settings, "second_screen_top", 0)),
                start_if_needed=True,
            )
            self._log("PPT: moved to 2nd screen (fullscreen).")
        except Exception as e:
            try:
                messagebox.showerror("PPT", str(e), parent=self)
            except Exception:
                pass
            try:
                self._log(f"PPT: 2nd screen failed ({e})")
            except Exception:
                pass

    def _set_wave_title(self, deck: str, cue: Cue | None) -> None:
        # Show help only when nothing is selected; once a track is loaded/selected,
        # the title is just the track name.
        if cue is None:
            text = f"Waveform - {self._wave_help_text()}"
        elif cue.kind in ("audio", "video"):
            text = _shorten_middle(cue.display_name(), 42)
        else:
            text = "Waveform"
        try:
            if deck == "A":
                self.wave_a_frame.configure(text=text)
            else:
                self.wave_b_frame.configure(text=text)
        except Exception:
            pass

    def _preset_path(self) -> Path:
        if _is_frozen():
            try:
                d = _user_data_dir()
                d.mkdir(parents=True, exist_ok=True)
                return d / "show_preset.json"
            except Exception:
                return Path.home() / "show_preset.json"
        return Path.cwd() / "show_preset.json"

    def _ensure_ffmpeg_tools_async(self, on_ready: Callable[[], None]) -> None:
        required = ("ffmpeg", "ffplay", "ffprobe")
        missing = [t for t in required if _resolve_fftool(t) is None]
        if not missing:
            try:
                on_ready()
            except Exception:
                return
            return

        win = tk.Toplevel(self)
        win.title("Installing FFmpeg tools")
        win.configure(bg="#2b2b2b")
        win.resizable(False, False)
        try:
            win.transient(self)
        except Exception:
            pass

        status_var = tk.StringVar(value="Preparing…")
        detail_var = tk.StringVar(value="")
        pb_var = tk.DoubleVar(value=0.0)

        body = tk.Frame(win, bg="#2b2b2b", padx=14, pady=12)
        body.pack(fill="both", expand=True)

        tk.Label(
            body,
            text="FFmpeg tools are required for playback, waveform and analysis.\nDownloading on first run…",
            bg="#2b2b2b",
            fg="#e8e8e8",
            justify="left",
            font=("Helvetica", 12),
        ).pack(anchor="w")

        tk.Label(body, textvariable=status_var, bg="#2b2b2b", fg="#cfd8dc", font=("Helvetica", 11)).pack(anchor="w", pady=(10, 0))
        tk.Label(body, textvariable=detail_var, bg="#2b2b2b", fg="#90a4ae", font=("Helvetica", 10)).pack(anchor="w", pady=(2, 10))

        pb = ttk.Progressbar(body, mode="determinate", maximum=100.0, variable=pb_var)
        pb.pack(fill="x", expand=True)

        btn_row = tk.Frame(body, bg="#2b2b2b")
        btn_row.pack(fill="x", pady=(10, 0))

        cancel_event = threading.Event()

        def _cancel() -> None:
            try:
                cancel_event.set()
                status_var.set("Canceling…")
            except Exception:
                pass

        ttk.Button(btn_row, text="Cancel", command=_cancel).pack(side="right")

        try:
            win.grab_set()
        except Exception:
            pass

        def _ui_status(msg: str) -> None:
            def _apply() -> None:
                try:
                    status_var.set(str(msg))
                except Exception:
                    pass

            self._ui_tasks.put(_apply)

        def _ui_progress(step: int, total_steps: int, done_bytes: int, total_bytes: int) -> None:
            frac = 0.0
            try:
                if total_bytes and total_bytes > 0:
                    frac = max(0.0, min(1.0, float(done_bytes) / float(total_bytes)))
            except Exception:
                frac = 0.0
            try:
                base = max(0.0, min(float(total_steps), float(step) + frac))
                pct = 100.0 * (base / float(max(1, int(total_steps))))
            except Exception:
                pct = 0.0

            def _apply() -> None:
                try:
                    pb_var.set(float(max(0.0, min(100.0, pct))))
                    if total_bytes and total_bytes > 0:
                        detail_var.set(f"{int(done_bytes/1024/1024)} / {int(total_bytes/1024/1024)} MB")
                    else:
                        detail_var.set("")
                except Exception:
                    pass

            self._ui_tasks.put(_apply)

        def _finish(success: bool, err: str | None = None) -> None:
            def _apply() -> None:
                try:
                    try:
                        win.grab_release()
                    except Exception:
                        pass
                    win.destroy()
                except Exception:
                    pass
                if not success:
                    dest = ""
                    try:
                        dest = str(_fftools_bin_dir())
                    except Exception:
                        dest = ""
                    message = (
                        "FFmpeg tools could not be installed automatically.\n\n"
                        "Please install FFmpeg (ffmpeg/ffplay/ffprobe) manually,\n"
                        "or restart the app with internet access.\n\n"
                        + (f"Install folder: {dest}\n\n" if dest else "")
                        + f"Details: {err or 'unknown error'}"
                    )
                    try:
                        messagebox.showerror("FFmpeg install failed", message, parent=self)
                    except Exception:
                        pass
                try:
                    on_ready()
                except Exception:
                    pass

            self._ui_tasks.put(_apply)

        def _worker() -> None:
            try:
                _install_ffmpeg_tools(on_status=_ui_status, on_progress=_ui_progress, cancel_event=cancel_event)
                for t in required:
                    if _resolve_fftool(t) is None:
                        raise RuntimeError(f"{t} not available after install")
                _finish(True, None)
            except Exception as e:
                _finish(False, str(e))

        threading.Thread(target=_worker, daemon=True).start()

        try:
            win.update_idletasks()
            x = self.winfo_rootx() + max(0, (self.winfo_width() - win.winfo_width()) // 2)
            y = self.winfo_rooty() + max(0, (self.winfo_height() - win.winfo_height()) // 2)
            win.geometry(f"+{int(x)}+{int(y)}")
        except Exception:
            pass

    def _bring_to_front(self) -> None:
        try:
            self.deiconify()
        except Exception:
            pass
        try:
            self.lift()
        except Exception:
            pass
        try:
            self.focus_force()
        except Exception:
            pass

        # macOS: sometimes the window starts behind VSCode/Terminal; a brief topmost toggle helps.
        if platform.system() == "Darwin":
            try:
                self.attributes("-topmost", True)
                self.update_idletasks()
                self.after(150, lambda: self.attributes("-topmost", False))
            except Exception:
                pass

    def _log(self, msg: str) -> None:
        msg = (msg or "").rstrip()
        if not msg:
            return

        ts = time.strftime("%H:%M:%S")
        line = f"[{ts}] {msg}\n"
        self._log_buffer.append(line)
        if len(self._log_buffer) > int(self._log_max_lines):
            excess = len(self._log_buffer) - int(self._log_max_lines)
            if excess > 0:
                del self._log_buffer[:excess]

        # Only touch Tk widgets on the main thread, and only when the Setup dialog is open.
        if threading.current_thread() is not threading.main_thread():
            return
        win = getattr(self, "_global_setup_win", None)
        if win is None:
            return
        try:
            if not win.winfo_exists():
                return
        except Exception:
            return

        txt = getattr(self, "log_text", None)
        if txt is None:
            return
        try:
            if not txt.winfo_exists():
                self.log_text = None  # type: ignore[attr-defined]
                return
        except Exception:
            return

        try:
            txt.configure(state="normal")
            txt.insert("end", line)
            try:
                lines = int(txt.index("end-1c").split(".")[0])
            except Exception:
                lines = 0
            if lines > self._log_max_lines:
                txt.delete("1.0", f"{lines - self._log_max_lines}.0")
            txt.see("end")
        finally:
            try:
                txt.configure(state="disabled")
            except Exception:
                pass

    def _copy_log(self) -> None:
        txt = getattr(self, "log_text", None)
        content = ""
        if txt is not None:
            try:
                if txt.winfo_exists():
                    content = txt.get("1.0", "end-1c")
            except Exception:
                content = ""
        if not content:
            content = "".join(self._log_buffer).rstrip("\n")
        try:
            self.clipboard_clear()
            self.clipboard_append(content)
            self._log("Log copied to clipboard.")
        except Exception:
            pass

    def _clear_log(self) -> None:
        self._log_buffer = []
        txt = getattr(self, "log_text", None)
        if txt is None:
            return
        try:
            txt.configure(state="normal")
            txt.delete("1.0", "end")
        except Exception:
            pass
        finally:
            try:
                txt.configure(state="disabled")
            except Exception:
                pass
        self._log("Log cleared.")

    def _open_global_setup(self) -> None:
        win = getattr(self, "_global_setup_win", None)
        try:
            if win is not None and win.winfo_exists():
                try:
                    win.deiconify()
                except Exception:
                    pass
                try:
                    win.lift()
                    win.focus_force()
                except Exception:
                    pass
                return
        except Exception:
            pass

        win = tk.Toplevel(self)
        self._global_setup_win = win
        win.title("Setup")
        try:
            win.configure(bg="#2b2b2b")
        except Exception:
            pass
        try:
            win.geometry("820x480")
        except Exception:
            pass

        def _on_close() -> None:
            try:
                if getattr(self, "_global_setup_win", None) is win:
                    self._global_setup_win = None
            except Exception:
                pass
            try:
                self.log_text = None  # type: ignore[attr-defined]
            except Exception:
                pass
            try:
                self._analysis_progressbar = None
            except Exception:
                pass
            try:
                win.destroy()
            except Exception:
                pass

        win.protocol("WM_DELETE_WINDOW", _on_close)

        outer = tk.Frame(win, bg="#2b2b2b", bd=0, highlightthickness=0)
        outer.pack(fill="both", expand=True, padx=10, pady=10)

        nb = ttk.Notebook(outer, style="Deck.TNotebook")
        try:
            nb.configure(takefocus=0)
        except Exception:
            pass
        nb.pack(fill="both", expand=True)

        tab_display = tk.Frame(nb, bg="#2b2b2b")
        tab_audio = tk.Frame(nb, bg="#2b2b2b")
        tab_log = tk.Frame(nb, bg="#2b2b2b")
        tab_about = tk.Frame(nb, bg="#2b2b2b")
        nb.add(tab_display, text="Display")
        nb.add(tab_audio, text="Audio")
        nb.add(tab_log, text="Log")
        nb.add(tab_about, text="About")

        # Display settings
        disp = ttk.Frame(tab_display, padding=12)
        disp.pack(fill="x", anchor="n")
        ttk.Label(disp, text="Second screen position (px):").grid(row=0, column=0, sticky="w")
        ttk.Entry(disp, textvariable=self.var_left, width=7).grid(row=0, column=1, sticky="w", padx=(8, 0))
        ttk.Label(disp, text=",").grid(row=0, column=2, sticky="w", padx=(6, 0))
        ttk.Entry(disp, textvariable=self.var_top, width=7).grid(row=0, column=3, sticky="w", padx=(6, 0))
        ttk.Checkbutton(disp, text="Fullscreen (video)", variable=self.var_fs).grid(row=0, column=4, sticky="w", padx=(14, 0))
        disp.columnconfigure(5, weight=1)

        ttk.Separator(tab_display, orient="horizontal").pack(fill="x", pady=(6, 0))
        ttk.Label(
            tab_display,
            text="Applies to video playback target on the second screen.",
            padding=(12, 8),
        ).pack(anchor="w")

        # Audio settings (global)
        audio_wrap = ttk.Frame(tab_audio, padding=12)
        audio_wrap.pack(fill="both", expand=True)

        var_norm_enabled = tk.BooleanVar(value=bool(getattr(self.settings, "normalize_enabled", False)))
        # macOS ttk.Scale can behave poorly with negative ranges; use a positive UI scale and map it.
        target_min, target_max = -30.0, -5.0
        tp_min, tp_max = -9.0, 0.0
        target_i = _clamp_float(getattr(self.settings, "normalize_target_i_lufs", -14.0), target_min, target_max, -14.0)
        tp_limit = _clamp_float(getattr(self.settings, "normalize_true_peak_db", -1.0), tp_min, tp_max, -1.0)
        var_target_i_ui = tk.DoubleVar(value=float(target_i - target_min))  # 0..(max-min)
        var_tp_ui = tk.DoubleVar(value=float(tp_limit - tp_min))  # 0..(max-min)
        var_target_i_label = tk.StringVar(value=f"{float(target_i):.1f} LUFS")
        var_tp_limit_label = tk.StringVar(value=f"{float(tp_limit):.1f} dBTP")
        var_audio_status = tk.StringVar(value="")

        def _ui_to_target() -> float:
            return float(target_min) + float(var_target_i_ui.get())

        def _ui_to_tp() -> float:
            return float(tp_min) + float(var_tp_ui.get())

        def _apply_audio_settings() -> None:
            try:
                self.settings.normalize_enabled = bool(var_norm_enabled.get())
            except Exception:
                pass
            try:
                self.settings.normalize_target_i_lufs = _clamp_float(_ui_to_target(), target_min, target_max, -14.0)
            except Exception:
                pass
            try:
                self.settings.normalize_true_peak_db = _clamp_float(_ui_to_tp(), tp_min, tp_max, -1.0)
            except Exception:
                pass

        def _analyze_missing() -> None:
            _apply_audio_settings()
            cues: list[Cue] = []
            try:
                cues.extend(list(self._cues_a))
            except Exception:
                pass
            try:
                cues.extend(list(self._cues_b))
            except Exception:
                pass
            todo = [c for c in cues if c.kind in ("audio", "video") and (c.loudness_i_lufs is None or c.true_peak_db is None)]
            self._analysis_batch_ids = {c.id for c in todo}
            self._analysis_batch_total = int(len(todo))
            self._analysis_batch_done = 0
            try:
                self._analysis_progress_var.set(0.0)
                self._analysis_progress_text_var.set("0/0" if self._analysis_batch_total <= 0 else f"0/{self._analysis_batch_total}")
            except Exception:
                pass
            try:
                if self._analysis_progressbar is not None and self._analysis_progressbar.winfo_exists():
                    self._analysis_progressbar.configure(maximum=max(1, self._analysis_batch_total))
            except Exception:
                pass
            var_audio_status.set(f"Queued {len(todo)} item(s) for analysis. Save preset to persist.")
            for c in todo:
                self._request_loudness_analysis(c, track_progress=True)

        def _clear_analysis() -> None:
            cues: list[Cue] = []
            try:
                cues.extend(list(self._cues_a))
            except Exception:
                pass
            try:
                cues.extend(list(self._cues_b))
            except Exception:
                pass
            cleared = 0
            for c in cues:
                if c.kind not in ("audio", "video"):
                    continue
                if c.loudness_i_lufs is not None or c.true_peak_db is not None:
                    cleared += 1
                c.loudness_i_lufs = None
                c.true_peak_db = None
            try:
                self._loud_req_inflight.clear()
            except Exception:
                pass
            var_audio_status.set(f"Cleared analysis for {cleared} item(s). Save preset to persist.")

        chk = ttk.Checkbutton(
            audio_wrap,
            text="Auto normalize loudness (precomputed)",
            variable=var_norm_enabled,
            command=_apply_audio_settings,
        )
        chk.grid(row=0, column=0, columnspan=5, sticky="w")

        ttk.Label(audio_wrap, text="Target loudness:").grid(row=1, column=0, sticky="w", pady=(12, 0))
        ttk.Label(audio_wrap, textvariable=var_target_i_label).grid(row=1, column=1, sticky="w", padx=(10, 0), pady=(12, 0))

        def _on_target_i_change(_v=None) -> None:
            try:
                val = _clamp_float(_ui_to_target(), target_min, target_max, -14.0)
                var_target_i_label.set(f"{val:.1f} LUFS")
            except Exception:
                pass
            _apply_audio_settings()

        s_i = ttk.Scale(
            audio_wrap,
            from_=0.0,
            to=float(target_max - target_min),
            orient="horizontal",
            length=260,
            variable=var_target_i_ui,
            command=_on_target_i_change,
        )
        s_i.grid(row=2, column=0, columnspan=5, sticky="ew", pady=(6, 0))

        ttk.Label(audio_wrap, text="True peak limit:").grid(row=3, column=0, sticky="w", pady=(10, 0))
        ttk.Label(audio_wrap, textvariable=var_tp_limit_label).grid(row=3, column=1, sticky="w", padx=(10, 0), pady=(10, 0))

        def _on_tp_change(_v=None) -> None:
            try:
                val = _clamp_float(_ui_to_tp(), tp_min, tp_max, -1.0)
                var_tp_limit_label.set(f"{val:.1f} dBTP")
            except Exception:
                pass
            _apply_audio_settings()

        s_tp = ttk.Scale(
            audio_wrap,
            from_=0.0,
            to=float(tp_max - tp_min),
            orient="horizontal",
            length=260,
            variable=var_tp_ui,
            command=_on_tp_change,
        )
        s_tp.grid(row=4, column=0, columnspan=5, sticky="ew", pady=(6, 0))

        btns = ttk.Frame(audio_wrap)
        btns.grid(row=5, column=0, columnspan=5, sticky="w", pady=(12, 0))
        ttk.Button(btns, text="Analyze missing", command=_analyze_missing).pack(side="left")
        ttk.Button(btns, text="Clear analysis", command=_clear_analysis).pack(side="left", padx=(8, 0))

        pb_row = ttk.Frame(audio_wrap)
        pb_row.grid(row=6, column=0, columnspan=5, sticky="ew", pady=(10, 0))
        pb = ttk.Progressbar(
            pb_row,
            mode="determinate",
            maximum=max(1, int(self._analysis_batch_total)),
            variable=self._analysis_progress_var,
        )
        self._analysis_progressbar = pb
        pb.pack(side="left", fill="x", expand=True)
        ttk.Label(pb_row, textvariable=self._analysis_progress_text_var, width=18).pack(side="left", padx=(10, 0))

        ttk.Label(audio_wrap, textvariable=var_audio_status, padding=(0, 10)).grid(row=7, column=0, columnspan=5, sticky="w")
        ttk.Label(
            audio_wrap,
            text="Normalization is applied at playback time via an audio filter (audio + video).",
            padding=(0, 6),
        ).grid(row=8, column=0, columnspan=5, sticky="w")

        ttk.Label(
            audio_wrap,
            text="Limits: Target loudness [-30..-5] LUFS, True peak [-9..0] dBTP.",
            padding=(0, 0),
        ).grid(row=9, column=0, columnspan=5, sticky="w")

        audio_wrap.columnconfigure(4, weight=1)

        # Log panel (hidden unless Setup is opened)
        log_wrap = ttk.Frame(tab_log, padding=10)
        log_wrap.pack(fill="both", expand=True)
        self.log_text = ScrolledText(  # type: ignore[assignment]
            log_wrap,
            height=12,
            wrap="none",
            bg="#1f1f1f",
            fg="#e0e0e0",
            insertbackground="#ffffff",
            highlightthickness=0,
            bd=0,
        )
        self.log_text.pack(fill="both", expand=True)
        try:
            self.log_text.configure(state="normal")
            self.log_text.delete("1.0", "end")
            self.log_text.insert("end", "".join(self._log_buffer))
            self.log_text.see("end")
            self.log_text.configure(state="disabled")
        except Exception:
            pass

        log_btns = ttk.Frame(log_wrap)
        log_btns.pack(fill="x", pady=(8, 0))
        ttk.Button(log_btns, text="Copy", command=self._copy_log).pack(side="left")
        ttk.Button(log_btns, text="Clear", command=self._clear_log).pack(side="left", padx=(6, 0))
        ttk.Button(log_btns, text="Close", command=_on_close).pack(side="right")

        # About
        about_wrap = ttk.Frame(tab_about, padding=14)
        about_wrap.pack(fill="both", expand=True)

        today = datetime.date.today().isoformat()
        website_url = "https://sprendezveny.hu"
        email_addr = "sprendezveny@gmail.com"
        email_url = f"mailto:{email_addr}"

        about_text = (
            f"{APP_NAME}\n"
            f"Version: {APP_VERSION}\n"
            f"Build date: {today}\n\n"
            f"Copyright © 2025 Sándor Péter\n"
            f"Kistokaj\n"
            f"Email: {email_addr}\n"
            f"Web: {website_url}\n"
        )
        about_info_text = (
            f"{APP_NAME}\n"
            f"Version: {APP_VERSION}\n"
            f"Build date: {today}\n\n"
            f"Copyright © 2025 Sándor Péter\n"
            f"Kistokaj\n"
        )

        card = tk.Frame(
            about_wrap,
            bg="#2b2b2b",
            highlightthickness=1,
            highlightbackground="#3b3b3b",
            highlightcolor="#3b3b3b",
            bd=0,
        )
        card.pack(fill="both", expand=True)
        card_inner = tk.Frame(card, bg="#2b2b2b")
        card_inner.pack(fill="both", expand=True, padx=14, pady=12)

        # Optional logo: drop a file into the repo (e.g. assets/logo.png) and it will show up here.
        logo_path: Path | None = None
        for p in (
            _resource_path("assets", "logo.png"),
            _resource_path("assets", "logo.gif"),
            _resource_path("logo.png"),
            _resource_path("logo.gif"),
        ):
            try:
                if p.exists():
                    logo_path = p
                    break
            except Exception:
                continue

        if logo_path is not None:
            try:
                img = tk.PhotoImage(file=str(logo_path))
                max_w, max_h = 260, 120
                try:
                    w, h = int(img.width()), int(img.height())
                except Exception:
                    w, h = 0, 0
                if w > 0 and h > 0 and (w > max_w or h > max_h):
                    factor = int(max((w / max_w), (h / max_h)))
                    factor = max(1, factor)
                    img = img.subsample(factor, factor)
                self._about_logo_image = img
                tk.Label(card_inner, image=self._about_logo_image, bg="#2b2b2b").pack(anchor="w", pady=(0, 10))
            except Exception:
                self._about_logo_image = None

        tk.Label(
            card_inner,
            text=about_info_text,
            justify="left",
            anchor="nw",
            bg="#2b2b2b",
            fg="#e8e8e8",
            font=("Helvetica", 13),
        ).pack(fill="x", anchor="w")

        def _open_url(url: str) -> None:
            try:
                webbrowser.open(url)
            except Exception:
                return

        contact = tk.Frame(card_inner, bg="#2b2b2b")
        contact.pack(fill="x", anchor="w", pady=(12, 0))

        tk.Label(contact, text="Email:", bg="#2b2b2b", fg="#e8e8e8", font=("Helvetica", 13)).grid(
            row=0, column=0, sticky="w"
        )
        lbl_email = tk.Label(
            contact,
            text=email_addr,
            bg="#2b2b2b",
            fg="#64b5f6",
            cursor="hand2",
            font=("Helvetica", 13, "underline"),
        )
        lbl_email.grid(row=0, column=1, sticky="w", padx=(8, 0))
        lbl_email.bind("<Button-1>", lambda _e: _open_url(email_url))

        tk.Label(contact, text="Web:", bg="#2b2b2b", fg="#e8e8e8", font=("Helvetica", 13)).grid(
            row=1, column=0, sticky="w", pady=(6, 0)
        )
        lbl_web = tk.Label(
            contact,
            text=website_url,
            bg="#2b2b2b",
            fg="#64b5f6",
            cursor="hand2",
            font=("Helvetica", 13, "underline"),
        )
        lbl_web.grid(row=1, column=1, sticky="w", padx=(8, 0), pady=(6, 0))
        lbl_web.bind("<Button-1>", lambda _e: _open_url(website_url))

        contact.columnconfigure(2, weight=1)

        def _copy_about() -> None:
            try:
                self.clipboard_clear()
                self.clipboard_append(about_text.strip())
            except Exception:
                pass

        about_btns = ttk.Frame(about_wrap)
        about_btns.pack(fill="x", pady=(10, 0))
        ttk.Button(about_btns, text="Copy", command=_copy_about).pack(side="left")

    def _copy_selected_path(self) -> None:
        cue = self._selected_cue()
        if not cue:
            return
        try:
            self.clipboard_clear()
            self.clipboard_append(cue.path)
            self._log("Path copied.")
        except Exception:
            pass

    # ── UI ──────────────────────────────────────────────────────────────
    def _build_ui(self) -> None:
        root = ttk.Frame(self, padding=8)
        root.pack(fill="both", expand=True)

        # Styles: flatter notebook (less padding/border) to save vertical space.
        try:
            style = ttk.Style(self)
            theme = ""
            try:
                theme = str(style.theme_use() or "")
            except Exception:
                theme = ""
            style.layout("Deck.TNotebook", [("Notebook.client", {"sticky": "nswe"})])
            # On macOS "aqua" theme, ttk ignores tab background colors but may still apply foreground,
            # which can create unreadable white text in Light Mode. Only force colors on themes
            # that honor them consistently.
            if theme.lower() == "aqua":
                style.configure("Deck.TNotebook", borderwidth=0, relief="flat", padding=0)
            else:
                style.configure("Deck.TNotebook", borderwidth=0, relief="flat", padding=0, background="#2b2b2b")
            style.configure("Deck.TNotebook.Tab", padding=(12, 6))
            if theme.lower() != "aqua":
                style.map(
                    "Deck.TNotebook.Tab",
                    background=[("selected", "#3a3a3a"), ("active", "#333333")],
                    foreground=[("selected", "#ffffff"), ("active", "#ffffff")],
                )

            # DJ-ish transport buttons (best-effort; some themes ignore colors, so we also change text).
            style.configure("DJ.Transport.TButton", padding=(10, 6))
            style.configure("DJ.Play.Off.TButton", padding=(10, 6))
            style.configure("DJ.Stop.Off.TButton", padding=(10, 6))
            style.configure("DJ.Loop.Off.TButton", padding=(10, 6))

            style.configure("DJ.Play.On.TButton", padding=(10, 6), foreground="#ffffff", background="#2e7d32")
            style.map(
                "DJ.Play.On.TButton",
                background=[("pressed", "#1b5e20"), ("active", "#388e3c")],
                foreground=[("disabled", "#bdbdbd")],
            )
            style.configure("DJ.Stop.On.TButton", padding=(10, 6), foreground="#ffffff", background="#c62828")
            style.map(
                "DJ.Stop.On.TButton",
                background=[("pressed", "#8e0000"), ("active", "#d32f2f")],
                foreground=[("disabled", "#bdbdbd")],
            )
            style.configure("DJ.Loop.On.TButton", padding=(10, 6), foreground="#111111", background="#f9a825")
            style.map(
                "DJ.Loop.On.TButton",
                background=[("pressed", "#c17900"), ("active", "#ffb300")],
                foreground=[("disabled", "#bdbdbd")],
            )
        except Exception:
            pass

        # File / preset toolbar
        filebar = ttk.Frame(root)
        filebar.pack(fill="x", pady=(0, 6))

        ttk.Button(filebar, text="New", command=self._new_show).pack(side="left")
        ttk.Button(filebar, text="Open…", command=self._open_show).pack(side="left", padx=(6, 0))
        ttk.Button(filebar, text="Save", command=self._save_show).pack(side="left", padx=(12, 0))
        ttk.Button(filebar, text="Save As…", command=self._save_show_as).pack(side="left", padx=(6, 0))

        ttk.Separator(filebar, orient="vertical").pack(side="left", fill="y", padx=12)
        ttk.Button(filebar, text="Save preset", command=self._save_preset).pack(side="left")
        ttk.Button(filebar, text="Load preset", command=self._load_preset).pack(side="left", padx=(6, 0))

        self.var_showfile = tk.StringVar(value="")
        ttk.Button(filebar, text="⚙", width=3, command=self._open_global_setup).pack(side="right", padx=(6, 0))
        self.lbl_showfile = ttk.Label(filebar, textvariable=self.var_showfile, anchor="e", width=28)
        self.lbl_showfile.pack(side="right")

        # DUAL DECKS - side by side, full height
        decks_container = ttk.Frame(root)
        decks_container.pack(fill="both", expand=True, pady=(0, 6))
        decks_container.columnconfigure(0, weight=1, uniform="decks")
        decks_container.columnconfigure(1, weight=1, uniform="decks")
        decks_container.rowconfigure(0, weight=1)

        # A DECK (left) - full height
        deck_a = ttk.LabelFrame(decks_container, text="DECK A", padding=4)
        deck_a.grid(row=0, column=0, sticky="nsew", padx=(0, 3))

        self.tree_a = ttk.Treeview(
            deck_a,
            columns=("idx", "kind", "name", "start", "stop"),
            show="headings",
            selectmode="browse",
        )
        self.tree_a.heading("idx", text="#")
        self.tree_a.heading("kind", text="Type")
        self.tree_a.heading("name", text="File")
        self.tree_a.heading("start", text="Start")
        self.tree_a.heading("stop", text="Stop")
        self.tree_a.column("idx", width=30, stretch=False, anchor="e")
        self.tree_a.column("kind", width=50, stretch=False)
        self.tree_a.column("name", width=250, stretch=True)
        self.tree_a.column("start", width=55, stretch=False, anchor="e")
        self.tree_a.column("stop", width=55, stretch=False, anchor="e")
        self.tree_a.pack(fill="both", expand=True)
        try:
            self.tree_a.tag_configure("playing", background="#2e7d32", foreground="#ffffff")
        except Exception:
            pass
        self.tree_a.bind("<<TreeviewSelect>>", lambda _e: self._on_deck_a_select())
        self.tree_a.bind("<Double-1>", lambda _e: self._play_deck_a())

        btn_a = ttk.Frame(deck_a, padding=(0, 4, 0, 0))
        btn_a.pack(fill="x")
        for i in range(4):
            btn_a.columnconfigure(i, weight=1, uniform="cuebtn_a_main")
        btn_a.columnconfigure(4, weight=0, minsize=36)
        btn_a.columnconfigure(5, weight=0, minsize=36)
        ttk.Button(btn_a, text="+ Audio", command=lambda: self._add_cue_a("audio")).grid(row=0, column=0, sticky="ew", padx=2)
        ttk.Button(btn_a, text="+ Video", command=lambda: self._add_cue_a("video")).grid(row=0, column=1, sticky="ew", padx=2)
        ttk.Button(btn_a, text="+ PPT", command=lambda: self._add_cue_a("ppt")).grid(row=0, column=2, sticky="ew", padx=2)
        ttk.Button(btn_a, text="Remove", command=self._remove_a).grid(row=0, column=3, sticky="ew", padx=2)
        ttk.Button(btn_a, text="▲", width=2, command=lambda: self._move_a(-1)).grid(row=0, column=4, sticky="ew", padx=2)
        ttk.Button(btn_a, text="▼", width=2, command=lambda: self._move_a(1)).grid(row=0, column=5, sticky="ew", padx=2)

        # Waveform placeholder (Deck A)
        self.wave_a_frame = ttk.LabelFrame(deck_a, text=f"Waveform - {self._wave_help_text()}", padding=2)
        self.wave_a_frame.pack(fill="x", pady=(4, 0))
        self.canvas_a = tk.Canvas(self.wave_a_frame, height=60, bg="#2b2b2b", highlightthickness=0, cursor="crosshair")
        self.canvas_a.pack(fill="x")
        self.canvas_a.bind("<Button-1>", lambda e: self._waveform_click(e, "A", "IN"))
        self.canvas_a.bind("<Button-2>", lambda e: self._waveform_click(e, "A", "OUT"))
        self.canvas_a.bind("<Button-3>", lambda e: self._waveform_click(e, "A", "OUT"))

        # Playback block (Deck A) - under waveform
        now_a = ttk.Frame(deck_a, padding=(0, 4, 0, 0))
        now_a.pack(fill="x")
        self.var_now_a_time = tk.StringVar(value="—")
        now_a_top = ttk.Frame(now_a)
        now_a_top.pack(fill="x")
        now_a_top.columnconfigure(2, weight=1)
        self.vu_canvas_a = tk.Canvas(
            now_a_top,
            width=120,
            height=10,
            bg="#2b2b2b",
            highlightthickness=0,
            bd=0,
        )
        self.vu_canvas_a.grid(row=0, column=0, sticky="w", pady=(4, 0))
        self.var_vu_a_db = tk.StringVar(value="")  # e.g. "-12.3 dB"
        self.lbl_vu_a_db = tk.Label(now_a_top, textvariable=self.var_vu_a_db, anchor="w", font=("Courier", 10), fg="#cfcfcf")
        self.lbl_vu_a_db.grid(row=0, column=1, sticky="w", padx=(8, 0), pady=(2, 0))
        self.lbl_now_a_time = tk.Label(now_a_top, textvariable=self.var_now_a_time, anchor="e", font=("Courier", 14, "bold"))
        self.lbl_now_a_time.grid(row=0, column=2, sticky="e", padx=(10, 0))
        self._now_time_default_fg_a = self.lbl_now_a_time.cget("fg")

        now_a_ctrl = ttk.Frame(now_a)
        now_a_ctrl.pack(fill="x", pady=(4, 0))
        now_a_ctrl.columnconfigure(0, weight=1, uniform="playrow_a")
        now_a_ctrl.columnconfigure(1, weight=1, uniform="playrow_a")
        now_a_ctrl.columnconfigure(2, weight=1, uniform="playrow_a")
        self.var_play_a = tk.StringVar(value="▶ PLAY")
        self.btn_play_a = self._make_transport_button(now_a_ctrl, self.var_play_a, self._play_deck_a)
        self.btn_play_a.grid(row=0, column=0, sticky="ew", padx=2)
        self.btn_stop_a = self._make_transport_button(now_a_ctrl, "⏹ STOP", self._stop_deck_a)
        self.btn_stop_a.grid(row=0, column=1, sticky="ew", padx=2)
        self.var_loop_a = tk.StringVar(value="⟲ LOOP OFF")
        self.btn_loop_a = self._make_transport_button(now_a_ctrl, self.var_loop_a, lambda: self._toggle_loop("A"))
        self.btn_loop_a.grid(row=0, column=2, sticky="ew", padx=2)

        # Tabs under playback block (Deck A)
        tabs_a_panel = tk.Frame(
            deck_a,
            bg="#2b2b2b",
            highlightthickness=1,
            highlightbackground="#3b3b3b",
            highlightcolor="#3b3b3b",
            bd=0,
            takefocus=0,
        )
        tabs_a_panel.pack(fill="x", pady=(4, 0))
        self.tabs_a = ttk.Notebook(tabs_a_panel, style="Deck.TNotebook")
        try:
            self.tabs_a.configure(takefocus=0)
        except Exception:
            pass
        self.tabs_a.pack(fill="both", expand=True)
        self.tab_a_setup = tk.Frame(self.tabs_a, bg="#2b2b2b")
        self.tab_a_inout = tk.Frame(self.tabs_a, bg="#2b2b2b")
        self.tab_a_more = tk.Frame(self.tabs_a, bg="#2b2b2b")
        self.tabs_a.add(self.tab_a_setup, text="Setup")
        self.tabs_a.add(self.tab_a_inout, text="IN/OUT")
        self.tabs_a.add(self.tab_a_more, text="More")

        # Setup tab (Deck A) - per-cue target
        setup_a = ttk.Frame(self.tab_a_setup, padding=6)
        setup_a.pack(fill="x")
        ttk.Label(setup_a, text="Target (video):").pack(side="left")
        self.var_target_a = tk.StringVar(value="window")
        self.rb_a_window = ttk.Radiobutton(
            setup_a,
            text="Window",
            value="window",
            variable=self.var_target_a,
            command=lambda: self._apply_target_setting("A"),
        )
        self.rb_a_second = ttk.Radiobutton(
            setup_a,
            text="2nd screen",
            value="second",
            variable=self.var_target_a,
            command=lambda: self._apply_target_setting("A"),
        )
        self.rb_a_window.pack(side="left", padx=(8, 0))
        self.rb_a_second.pack(side="left", padx=(6, 0))

        ttk.Separator(setup_a, orient="vertical").pack(side="left", fill="y", padx=10)
        ttk.Label(setup_a, text="Start volume:").pack(side="left")
        self.var_cue_vol_a = tk.IntVar(value=int(self.settings.startup_volume))
        self.var_cue_vol_a_label = tk.StringVar(value=str(int(self.settings.startup_volume)))
        self.scale_cue_vol_a = ttk.Scale(
            setup_a,
            from_=0.0,
            to=100.0,
            orient="horizontal",
            length=140,
            command=lambda _v: self._apply_volume_setting("A"),
        )
        self.scale_cue_vol_a.pack(side="left", padx=(6, 2), fill="x", expand=True)
        self.scale_cue_vol_a.configure(variable=self.var_cue_vol_a)
        ttk.Label(setup_a, textvariable=self.var_cue_vol_a_label, width=3).pack(side="left")

        # IN/OUT tab (Deck A)
        inout_a = ttk.Frame(self.tab_a_inout, padding=6)
        inout_a.pack(fill="x")
        mark_a = ttk.Frame(inout_a)
        mark_a.pack(fill="x")
        mark_a.columnconfigure(0, weight=1, uniform="inout_a")
        mark_a.columnconfigure(1, weight=1, uniform="inout_a")
        in_frame_a = ttk.LabelFrame(mark_a, text="IN", padding=2)
        in_frame_a.grid(row=0, column=0, sticky="ew", padx=(0, 4))
        self.var_in_a = tk.StringVar(value="0:00.000")
        in_row_a = ttk.Frame(in_frame_a)
        in_row_a.pack(fill="x")
        in_row_a.columnconfigure(0, weight=1)
        in_row_a.columnconfigure(1, weight=0)
        in_row_a.columnconfigure(2, weight=0)
        entry_in_a = ttk.Entry(in_row_a, textvariable=self.var_in_a)
        entry_in_a.grid(row=0, column=0, sticky="ew")
        ttk.Button(in_row_a, text="−", width=3, command=lambda: self._nudge_in("A", -0.05)).grid(row=0, column=1, sticky="e", padx=(6, 2))
        ttk.Button(in_row_a, text="+", width=3, command=lambda: self._nudge_in("A", 0.05)).grid(row=0, column=2, sticky="e", padx=(0, 2))
        entry_in_a.bind("<Return>", lambda _e: self._adjust_in("A"))
        entry_in_a.bind("<KP_Enter>", lambda _e: self._adjust_in("A"))
        entry_in_a.bind("<FocusOut>", lambda _e: self._adjust_in("A"))
        entry_in_a.bind("<Up>", lambda e: self._nudge_in_event("A", +1, e))
        entry_in_a.bind("<Down>", lambda e: self._nudge_in_event("A", -1, e))
        entry_in_a.bind("<MouseWheel>", lambda e: self._nudge_in_wheel("A", e))
        entry_in_a.bind("<Button-4>", lambda e: self._nudge_in_event("A", +1, e))
        entry_in_a.bind("<Button-5>", lambda e: self._nudge_in_event("A", -1, e))

        out_frame_a = ttk.LabelFrame(mark_a, text="OUT", padding=2)
        out_frame_a.grid(row=0, column=1, sticky="ew", padx=(4, 0))
        self.var_out_a = tk.StringVar(value="—")
        out_row_a = ttk.Frame(out_frame_a)
        out_row_a.pack(fill="x")
        out_row_a.columnconfigure(0, weight=1)
        out_row_a.columnconfigure(1, weight=0)
        out_row_a.columnconfigure(2, weight=0)
        entry_out_a = ttk.Entry(out_row_a, textvariable=self.var_out_a)
        entry_out_a.grid(row=0, column=0, sticky="ew")
        ttk.Button(out_row_a, text="−", width=3, command=lambda: self._nudge_out("A", -0.05)).grid(row=0, column=1, sticky="e", padx=(6, 2))
        ttk.Button(out_row_a, text="+", width=3, command=lambda: self._nudge_out("A", 0.05)).grid(row=0, column=2, sticky="e", padx=(0, 2))
        entry_out_a.bind("<Return>", lambda _e: self._adjust_out("A"))
        entry_out_a.bind("<KP_Enter>", lambda _e: self._adjust_out("A"))
        entry_out_a.bind("<FocusOut>", lambda _e: self._adjust_out("A"))
        entry_out_a.bind("<Up>", lambda e: self._nudge_out_event("A", +1, e))
        entry_out_a.bind("<Down>", lambda e: self._nudge_out_event("A", -1, e))
        entry_out_a.bind("<MouseWheel>", lambda e: self._nudge_out_wheel("A", e))
        entry_out_a.bind("<Button-4>", lambda e: self._nudge_out_event("A", +1, e))
        entry_out_a.bind("<Button-5>", lambda e: self._nudge_out_event("A", -1, e))

        ttk.Label(self.tab_a_more, text="(reserved)", padding=6).pack(anchor="w")

        # PPT controls (Deck A)
        ppt_a = ttk.LabelFrame(deck_a, text="PPT Control", padding=2)
        ppt_a.pack(fill="x", pady=(4, 0))
        ttk.Button(ppt_a, text="▶ Start", command=self._play_selected_ppt_only, width=10).pack(fill="x")
        ttk.Button(ppt_a, text="⛶ 2nd Screen", command=self._ppt_fullscreen_2nd_screen, width=10).pack(fill="x", pady=(2, 0))
        ppt_nav_a = ttk.Frame(ppt_a)
        ppt_nav_a.pack(fill="x", pady=(2, 0))
        ttk.Button(ppt_nav_a, text="◀", command=ppt_prev_slide, width=5).pack(side="left", expand=True, fill="x")
        ttk.Button(ppt_nav_a, text="▶", command=ppt_next_slide, width=5).pack(side="left", expand=True, fill="x", padx=(2, 0))
        ttk.Button(ppt_a, text="⏹ End", command=ppt_end_show, width=10).pack(fill="x", pady=(2, 0))

        # B DECK (right) - full height
        deck_b = ttk.LabelFrame(decks_container, text="DECK B", padding=4)
        deck_b.grid(row=0, column=1, sticky="nsew", padx=(3, 0))

        self.tree_b = ttk.Treeview(
            deck_b,
            columns=("idx", "kind", "name", "start", "stop"),
            show="headings",
            selectmode="browse",
        )
        self.tree_b.heading("idx", text="#")
        self.tree_b.heading("kind", text="Type")
        self.tree_b.heading("name", text="File")
        self.tree_b.heading("start", text="Start")
        self.tree_b.heading("stop", text="Stop")
        self.tree_b.column("idx", width=30, stretch=False, anchor="e")
        self.tree_b.column("kind", width=50, stretch=False)
        self.tree_b.column("name", width=250, stretch=True)
        self.tree_b.column("start", width=55, stretch=False, anchor="e")
        self.tree_b.column("stop", width=55, stretch=False, anchor="e")
        self.tree_b.pack(fill="both", expand=True)
        try:
            self.tree_b.tag_configure("playing", background="#2e7d32", foreground="#ffffff")
        except Exception:
            pass
        self.tree_b.bind("<<TreeviewSelect>>", lambda _e: self._on_deck_b_select())
        self.tree_b.bind("<Double-1>", lambda _e: self._play_deck_b())

        btn_b = ttk.Frame(deck_b, padding=(0, 4, 0, 0))
        btn_b.pack(fill="x")
        for i in range(4):
            btn_b.columnconfigure(i, weight=1, uniform="cuebtn_b_main")
        btn_b.columnconfigure(4, weight=0, minsize=36)
        btn_b.columnconfigure(5, weight=0, minsize=36)
        ttk.Button(btn_b, text="+ Audio", command=lambda: self._add_cue_b("audio")).grid(row=0, column=0, sticky="ew", padx=2)
        ttk.Button(btn_b, text="+ Video", command=lambda: self._add_cue_b("video")).grid(row=0, column=1, sticky="ew", padx=2)
        ttk.Button(btn_b, text="+ PPT", command=lambda: self._add_cue_b("ppt")).grid(row=0, column=2, sticky="ew", padx=2)
        ttk.Button(btn_b, text="Remove", command=self._remove_b).grid(row=0, column=3, sticky="ew", padx=2)
        ttk.Button(btn_b, text="▲", width=2, command=lambda: self._move_b(-1)).grid(row=0, column=4, sticky="ew", padx=2)
        ttk.Button(btn_b, text="▼", width=2, command=lambda: self._move_b(1)).grid(row=0, column=5, sticky="ew", padx=2)

        # Waveform placeholder (Deck B)
        self.wave_b_frame = ttk.LabelFrame(deck_b, text=f"Waveform - {self._wave_help_text()}", padding=2)
        self.wave_b_frame.pack(fill="x", pady=(4, 0))
        self.canvas_b = tk.Canvas(self.wave_b_frame, height=60, bg="#2b2b2b", highlightthickness=0, cursor="crosshair")
        self.canvas_b.pack(fill="x")
        self.canvas_b.bind("<Button-1>", lambda e: self._waveform_click(e, "B", "IN"))
        self.canvas_b.bind("<Button-2>", lambda e: self._waveform_click(e, "B", "OUT"))
        self.canvas_b.bind("<Button-3>", lambda e: self._waveform_click(e, "B", "OUT"))

        # Playback block (Deck B) - under waveform
        now_b = ttk.Frame(deck_b, padding=(0, 4, 0, 0))
        now_b.pack(fill="x")
        self.var_now_b_time = tk.StringVar(value="—")
        now_b_top = ttk.Frame(now_b)
        now_b_top.pack(fill="x")
        now_b_top.columnconfigure(2, weight=1)
        self.vu_canvas_b = tk.Canvas(
            now_b_top,
            width=120,
            height=10,
            bg="#2b2b2b",
            highlightthickness=0,
            bd=0,
        )
        self.vu_canvas_b.grid(row=0, column=0, sticky="w", pady=(4, 0))
        self.var_vu_b_db = tk.StringVar(value="")
        self.lbl_vu_b_db = tk.Label(now_b_top, textvariable=self.var_vu_b_db, anchor="w", font=("Courier", 10), fg="#cfcfcf")
        self.lbl_vu_b_db.grid(row=0, column=1, sticky="w", padx=(8, 0), pady=(2, 0))
        self.lbl_now_b_time = tk.Label(now_b_top, textvariable=self.var_now_b_time, anchor="e", font=("Courier", 14, "bold"))
        self.lbl_now_b_time.grid(row=0, column=2, sticky="e", padx=(10, 0))
        self._now_time_default_fg_b = self.lbl_now_b_time.cget("fg")

        now_b_ctrl = ttk.Frame(now_b)
        now_b_ctrl.pack(fill="x", pady=(4, 0))
        now_b_ctrl.columnconfigure(0, weight=1, uniform="playrow_b")
        now_b_ctrl.columnconfigure(1, weight=1, uniform="playrow_b")
        now_b_ctrl.columnconfigure(2, weight=1, uniform="playrow_b")
        self.var_play_b = tk.StringVar(value="▶ PLAY")
        self.btn_play_b = self._make_transport_button(now_b_ctrl, self.var_play_b, self._play_deck_b)
        self.btn_play_b.grid(row=0, column=0, sticky="ew", padx=2)
        self.btn_stop_b = self._make_transport_button(now_b_ctrl, "⏹ STOP", self._stop_deck_b)
        self.btn_stop_b.grid(row=0, column=1, sticky="ew", padx=2)
        self.var_loop_b = tk.StringVar(value="⟲ LOOP OFF")
        self.btn_loop_b = self._make_transport_button(now_b_ctrl, self.var_loop_b, lambda: self._toggle_loop("B"))
        self.btn_loop_b.grid(row=0, column=2, sticky="ew", padx=2)

        # Tabs under playback block (Deck B)
        tabs_b_panel = tk.Frame(
            deck_b,
            bg="#2b2b2b",
            highlightthickness=1,
            highlightbackground="#3b3b3b",
            highlightcolor="#3b3b3b",
            bd=0,
            takefocus=0,
        )
        tabs_b_panel.pack(fill="x", pady=(4, 0))
        self.tabs_b = ttk.Notebook(tabs_b_panel, style="Deck.TNotebook")
        try:
            self.tabs_b.configure(takefocus=0)
        except Exception:
            pass
        self.tabs_b.pack(fill="both", expand=True)
        self.tab_b_setup = tk.Frame(self.tabs_b, bg="#2b2b2b")
        self.tab_b_inout = tk.Frame(self.tabs_b, bg="#2b2b2b")
        self.tab_b_more = tk.Frame(self.tabs_b, bg="#2b2b2b")
        self.tabs_b.add(self.tab_b_setup, text="Setup")
        self.tabs_b.add(self.tab_b_inout, text="IN/OUT")
        self.tabs_b.add(self.tab_b_more, text="More")

        # Setup tab (Deck B) - per-cue target
        setup_b = ttk.Frame(self.tab_b_setup, padding=6)
        setup_b.pack(fill="x")
        ttk.Label(setup_b, text="Target (video):").pack(side="left")
        self.var_target_b = tk.StringVar(value="window")
        self.rb_b_window = ttk.Radiobutton(
            setup_b,
            text="Window",
            value="window",
            variable=self.var_target_b,
            command=lambda: self._apply_target_setting("B"),
        )
        self.rb_b_second = ttk.Radiobutton(
            setup_b,
            text="2nd screen",
            value="second",
            variable=self.var_target_b,
            command=lambda: self._apply_target_setting("B"),
        )
        self.rb_b_window.pack(side="left", padx=(8, 0))
        self.rb_b_second.pack(side="left", padx=(6, 0))

        ttk.Separator(setup_b, orient="vertical").pack(side="left", fill="y", padx=10)
        ttk.Label(setup_b, text="Start volume:").pack(side="left")
        self.var_cue_vol_b = tk.IntVar(value=int(self.settings.startup_volume))
        self.var_cue_vol_b_label = tk.StringVar(value=str(int(self.settings.startup_volume)))
        self.scale_cue_vol_b = ttk.Scale(
            setup_b,
            from_=0.0,
            to=100.0,
            orient="horizontal",
            length=140,
            command=lambda _v: self._apply_volume_setting("B"),
        )
        self.scale_cue_vol_b.pack(side="left", padx=(6, 2), fill="x", expand=True)
        self.scale_cue_vol_b.configure(variable=self.var_cue_vol_b)
        ttk.Label(setup_b, textvariable=self.var_cue_vol_b_label, width=3).pack(side="left")

        # IN/OUT tab (Deck B)
        inout_b = ttk.Frame(self.tab_b_inout, padding=6)
        inout_b.pack(fill="x")
        mark_b = ttk.Frame(inout_b)
        mark_b.pack(fill="x")
        mark_b.columnconfigure(0, weight=1, uniform="inout_b")
        mark_b.columnconfigure(1, weight=1, uniform="inout_b")
        in_frame_b = ttk.LabelFrame(mark_b, text="IN", padding=2)
        in_frame_b.grid(row=0, column=0, sticky="ew", padx=(0, 4))
        self.var_in_b = tk.StringVar(value="0:00.000")
        in_row_b = ttk.Frame(in_frame_b)
        in_row_b.pack(fill="x")
        in_row_b.columnconfigure(0, weight=1)
        in_row_b.columnconfigure(1, weight=0)
        in_row_b.columnconfigure(2, weight=0)
        entry_in_b = ttk.Entry(in_row_b, textvariable=self.var_in_b)
        entry_in_b.grid(row=0, column=0, sticky="ew")
        ttk.Button(in_row_b, text="−", width=3, command=lambda: self._nudge_in("B", -0.05)).grid(row=0, column=1, sticky="e", padx=(6, 2))
        ttk.Button(in_row_b, text="+", width=3, command=lambda: self._nudge_in("B", 0.05)).grid(row=0, column=2, sticky="e", padx=(0, 2))
        entry_in_b.bind("<Return>", lambda _e: self._adjust_in("B"))
        entry_in_b.bind("<KP_Enter>", lambda _e: self._adjust_in("B"))
        entry_in_b.bind("<FocusOut>", lambda _e: self._adjust_in("B"))
        entry_in_b.bind("<Up>", lambda e: self._nudge_in_event("B", +1, e))
        entry_in_b.bind("<Down>", lambda e: self._nudge_in_event("B", -1, e))
        entry_in_b.bind("<MouseWheel>", lambda e: self._nudge_in_wheel("B", e))
        entry_in_b.bind("<Button-4>", lambda e: self._nudge_in_event("B", +1, e))
        entry_in_b.bind("<Button-5>", lambda e: self._nudge_in_event("B", -1, e))

        out_frame_b = ttk.LabelFrame(mark_b, text="OUT", padding=2)
        out_frame_b.grid(row=0, column=1, sticky="ew", padx=(4, 0))
        self.var_out_b = tk.StringVar(value="—")
        out_row_b = ttk.Frame(out_frame_b)
        out_row_b.pack(fill="x")
        out_row_b.columnconfigure(0, weight=1)
        out_row_b.columnconfigure(1, weight=0)
        out_row_b.columnconfigure(2, weight=0)
        entry_out_b = ttk.Entry(out_row_b, textvariable=self.var_out_b)
        entry_out_b.grid(row=0, column=0, sticky="ew")
        ttk.Button(out_row_b, text="−", width=3, command=lambda: self._nudge_out("B", -0.05)).grid(row=0, column=1, sticky="e", padx=(6, 2))
        ttk.Button(out_row_b, text="+", width=3, command=lambda: self._nudge_out("B", 0.05)).grid(row=0, column=2, sticky="e", padx=(0, 2))
        entry_out_b.bind("<Return>", lambda _e: self._adjust_out("B"))
        entry_out_b.bind("<KP_Enter>", lambda _e: self._adjust_out("B"))
        entry_out_b.bind("<FocusOut>", lambda _e: self._adjust_out("B"))
        entry_out_b.bind("<Up>", lambda e: self._nudge_out_event("B", +1, e))
        entry_out_b.bind("<Down>", lambda e: self._nudge_out_event("B", -1, e))
        entry_out_b.bind("<MouseWheel>", lambda e: self._nudge_out_wheel("B", e))
        entry_out_b.bind("<Button-4>", lambda e: self._nudge_out_event("B", +1, e))
        entry_out_b.bind("<Button-5>", lambda e: self._nudge_out_event("B", -1, e))

        ttk.Label(self.tab_b_more, text="(reserved)", padding=6).pack(anchor="w")

        # PPT controls (Deck B)
        ppt_b = ttk.LabelFrame(deck_b, text="PPT Control", padding=2)
        ppt_b.pack(fill="x", pady=(4, 0))
        ttk.Button(ppt_b, text="▶ Start", command=self._play_selected_ppt_only, width=10).pack(fill="x")
        ttk.Button(ppt_b, text="⛶ 2nd Screen", command=self._ppt_fullscreen_2nd_screen, width=10).pack(fill="x", pady=(2, 0))
        ppt_nav_b = ttk.Frame(ppt_b)
        ppt_nav_b.pack(fill="x", pady=(2, 0))
        ttk.Button(ppt_nav_b, text="◀", command=ppt_prev_slide, width=5).pack(side="left", expand=True, fill="x")
        ttk.Button(ppt_nav_b, text="▶", command=ppt_next_slide, width=5).pack(side="left", expand=True, fill="x", padx=(2, 0))
        ttk.Button(ppt_b, text="⏹ End", command=ppt_end_show, width=10).pack(fill="x", pady=(2, 0))

        # Default states for per-cue setup controls (no selection yet).
        self._sync_target_setting_controls("A", None)
        self._sync_target_setting_controls("B", None)
        self._update_transport_button_visuals()

        # Store separate cue lists
        self._cues_a: list[Cue] = []
        self._cues_b: list[Cue] = []
        self._selected_a: int = -1
        self._selected_b: int = -1

        self._update_showfile_label()
        self._update_now_playing()
        self._log("UI ready.")

        # Set cleanup protocol for window close
        self.protocol("WM_DELETE_WINDOW", self._on_closing)

    def _on_closing(self):
        """Clean up media players before closing the application"""
        try:
            # Stop both media runners
            if hasattr(self, 'audio_runner'):
                self.audio_runner.stop()
            if hasattr(self, 'video_runner'):
                self.video_runner.stop()
            if hasattr(self, "_stop_preview"):
                self._stop_preview()
            self._log("Shutting down...")
        except Exception as e:
            self._log(f"Cleanup error: {e}")
        finally:
            # Force kill any remaining ffplay processes
            try:
                subprocess.run(["pkill", "-9", "ffplay"], capture_output=True)
            except Exception:
                pass
            self.destroy()

    # ── Dual Deck Data Management ───────────────────────────────────────
    def _on_deck_a_select(self):
        sel = self.tree_a.selection()
        self._selected_a = int(sel[0]) if sel else -1
        if self._selected_a >= 0:
            self._load_cue_into_editor(self._cues_a[self._selected_a])
            # Generate waveform for selected audio/video
            cue = self._cues_a[self._selected_a]
            self._set_wave_title("A", cue)
            self._sync_target_setting_controls("A", cue)
            # Update IN/OUT spinboxes
            self.var_in_a.set(_format_timecode(cue.start_sec, with_ms=True))
            self.var_out_a.set(_format_timecode(cue.stop_at_sec, with_ms=True) if cue.stop_at_sec else "—")
            if cue.kind in ("audio", "video"):
                self._request_waveform_generate("A", cue)
                if bool(getattr(self.settings, "normalize_enabled", False)):
                    self._request_loudness_analysis(cue)
        else:
            try:
                self._set_wave_title("A", None)
                self.canvas_a.delete("all")
            except Exception:
                pass

    def _on_deck_b_select(self):
        sel = self.tree_b.selection()
        self._selected_b = int(sel[0]) if sel else -1
        if self._selected_b >= 0:
            self._load_cue_into_editor(self._cues_b[self._selected_b])
            # Generate waveform for selected audio/video
            cue = self._cues_b[self._selected_b]
            self._set_wave_title("B", cue)
            self._sync_target_setting_controls("B", cue)
            # Update IN/OUT spinboxes
            self.var_in_b.set(_format_timecode(cue.start_sec, with_ms=True))
            self.var_out_b.set(_format_timecode(cue.stop_at_sec, with_ms=True) if cue.stop_at_sec else "—")
            if cue.kind in ("audio", "video"):
                self._request_waveform_generate("B", cue)
                if bool(getattr(self.settings, "normalize_enabled", False)):
                    self._request_loudness_analysis(cue)
        else:
            try:
                self._set_wave_title("B", None)
                self.canvas_b.delete("all")
            except Exception:
                pass

    def _adjust_in(self, deck: str) -> None:
        """Fine-tune IN point from IN field"""
        try:
            if deck == "A":
                if self._selected_a < 0 or self._selected_a >= len(self._cues_a):
                    return
                cue = self._cues_a[self._selected_a]
                time_str = self.var_in_a.get()
                canvas = self.canvas_a
            else:  # deck == "B"
                if self._selected_b < 0 or self._selected_b >= len(self._cues_b):
                    return
                cue = self._cues_b[self._selected_b]
                time_str = self.var_in_b.get()
                canvas = self.canvas_b

            # Parse timecode
            time_sec = _parse_timecode(time_str)
            if time_sec is None:
                if deck == "A":
                    self.var_in_a.set(_format_timecode(cue.start_sec, with_ms=True))
                else:
                    self.var_in_b.set(_format_timecode(cue.start_sec, with_ms=True))
                return

            cue.start_sec = max(0.0, float(time_sec))
            if cue.stop_at_sec is not None and cue.stop_at_sec < cue.start_sec:
                cue.stop_at_sec = cue.start_sec

            self._update_tree_item(cue)
            if deck == "A":
                self.var_in_a.set(_format_timecode(cue.start_sec, with_ms=True))
            else:
                self.var_in_b.set(_format_timecode(cue.start_sec, with_ms=True))
            self._log(f"Deck {deck}: IN adjusted to {_format_timecode(cue.start_sec, with_ms=True)}")

            if cue.kind in ("audio", "video"):
                self._refresh_waveform_markers(cue, canvas, deck)

        except Exception as e:
            try:
                if deck == "A" and self._selected_a >= 0:
                    self.var_in_a.set(_format_timecode(self._cues_a[self._selected_a].start_sec, with_ms=True))
                elif deck == "B" and self._selected_b >= 0:
                    self.var_in_b.set(_format_timecode(self._cues_b[self._selected_b].start_sec, with_ms=True))
            except Exception:
                pass
            self._log(f"IN adjust error: {e}")

    def _adjust_out(self, deck: str) -> None:
        """Fine-tune OUT point from OUT field"""
        try:
            if deck == "A":
                if self._selected_a < 0 or self._selected_a >= len(self._cues_a):
                    return
                cue = self._cues_a[self._selected_a]
                time_str = self.var_out_a.get()
                canvas = self.canvas_a
            else:  # deck == "B"
                if self._selected_b < 0 or self._selected_b >= len(self._cues_b):
                    return
                cue = self._cues_b[self._selected_b]
                time_str = self.var_out_b.get()
                canvas = self.canvas_b

            if (time_str or "").strip() in ("", "—"):
                cue.stop_at_sec = None
                self._update_tree_item(cue)
                if deck == "A":
                    self.var_out_a.set("—")
                else:
                    self.var_out_b.set("—")
                if cue.kind in ("audio", "video"):
                    self._refresh_waveform_markers(cue, canvas, deck)
                return

            # Parse timecode
            time_sec = _parse_timecode(time_str)
            if time_sec is None:
                if deck == "A":
                    self.var_out_a.set(_format_timecode(cue.stop_at_sec, with_ms=True) if cue.stop_at_sec else "—")
                else:
                    self.var_out_b.set(_format_timecode(cue.stop_at_sec, with_ms=True) if cue.stop_at_sec else "—")
                return

            cue.stop_at_sec = max(0.0, float(time_sec))
            if cue.stop_at_sec < cue.start_sec:
                cue.start_sec = cue.stop_at_sec

            self._update_tree_item(cue)
            if deck == "A":
                self.var_out_a.set(_format_timecode(cue.stop_at_sec, with_ms=True) if cue.stop_at_sec else "—")
            else:
                self.var_out_b.set(_format_timecode(cue.stop_at_sec, with_ms=True) if cue.stop_at_sec else "—")
            self._log(f"Deck {deck}: OUT adjusted to {_format_timecode(cue.stop_at_sec, with_ms=True)}")

            if cue.kind in ("audio", "video"):
                self._refresh_waveform_markers(cue, canvas, deck)

        except Exception as e:
            try:
                if deck == "A" and self._selected_a >= 0:
                    cue = self._cues_a[self._selected_a]
                    self.var_out_a.set(_format_timecode(cue.stop_at_sec, with_ms=True) if cue.stop_at_sec else "—")
                elif deck == "B" and self._selected_b >= 0:
                    cue = self._cues_b[self._selected_b]
                    self.var_out_b.set(_format_timecode(cue.stop_at_sec, with_ms=True) if cue.stop_at_sec else "—")
            except Exception:
                pass
            self._log(f"OUT adjust error: {e}")

    def _nudge_step(self, event) -> float:
        try:
            state = int(getattr(event, "state", 0))
        except Exception:
            state = 0
        if state & 0x0001:  # Shift
            return 0.01
        if state & 0x0004:  # Control
            return 0.10
        return 0.05

    def _refresh_waveform_markers(self, cue: Cue, canvas: tk.Canvas, deck_name: str) -> None:
        try:
            images = getattr(self, "_waveform_images", None)
            has_image = bool(images and images.get(deck_name))
        except Exception:
            has_image = False
        if has_image:
            self._update_waveform_markers(cue, canvas)
            return
        self._request_waveform_generate(deck_name, cue)

    def _update_waveform_markers(self, cue: Cue, canvas: tk.Canvas) -> None:
        try:
            canvas.delete("marker")
            duration = self._duration_for_cue(cue)
            if not duration or duration <= 0:
                return
            width = canvas.winfo_width()
            height = canvas.winfo_height()
            if width < 10 or height < 10:
                width = 600
                height = 60

            if cue.start_sec:
                x = int((cue.start_sec / duration) * width)
                canvas.create_line(x, 0, x, height, fill="#00ff00", width=2, tags=("marker",))
                canvas.create_text(
                    x,
                    5,
                    text=f"IN: {_format_timecode(cue.start_sec, with_ms=True)}",
                    anchor="nw",
                    fill="#00ff00",
                    font=("Arial", 8, "bold"),
                    tags=("marker",),
                )

            if cue.stop_at_sec:
                x = int((cue.stop_at_sec / duration) * width)
                canvas.create_line(x, 0, x, height, fill="#ff0000", width=2, tags=("marker",))
                canvas.create_text(
                    x,
                    height - 5,
                    text=f"OUT: {_format_timecode(cue.stop_at_sec, with_ms=True)}",
                    anchor="sw",
                    fill="#ff0000",
                    font=("Arial", 8, "bold"),
                    tags=("marker",),
                )
        except Exception:
            return

    def _request_waveform_generate(self, deck: str, cue: Cue) -> None:
        if cue.kind not in ("audio", "video"):
            return
        canvas = self.canvas_a if deck == "A" else self.canvas_b
        try:
            width = int(canvas.winfo_width() or 0)
            height = int(canvas.winfo_height() or 0)
        except Exception:
            width, height = 0, 0
        if width < 10 or height < 10:
            width, height = 600, 60

        self._wave_req_seq[deck] = int(self._wave_req_seq.get(deck, 0)) + 1
        token = int(self._wave_req_seq[deck])
        self._wave_req_cue_id[deck] = cue.id

        def _worker():
            png_bytes: bytes | None = None
            err_text: str | None = None
            try:
                ffmpeg = _resolve_fftool("ffmpeg")
                if not ffmpeg:
                    raise RuntimeError("ffmpeg not found")
                cmd = [
                    ffmpeg,
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-i",
                    str(cue.path),
                    "-filter_complex",
                    f"showwavespic=s={width}x{height}:colors=#4a9eff",
                    "-frames:v",
                    "1",
                    "-f",
                    "image2pipe",
                    "-vcodec",
                    "png",
                    "-",
                ]
                result = subprocess.run(cmd, capture_output=True, timeout=10)
                if result.returncode == 0 and result.stdout:
                    png_bytes = bytes(result.stdout)
                else:
                    err_text = (result.stderr or b"").decode(errors="ignore") if isinstance(result.stderr, (bytes, bytearray)) else str(result.stderr)
            except Exception as e:
                err_text = str(e)

            self._ui_tasks.put(lambda: self._apply_waveform_result(deck, cue.id, token, png_bytes, err_text))

        threading.Thread(target=_worker, daemon=True).start()

    def _compute_vu_levels_from_image(self, img) -> list[float]:
        try:
            from PIL import Image
        except Exception:
            Image = None  # type: ignore[assignment]
        try:
            rgba = img.convert("RGBA")
        except Exception:
            rgba = img
        try:
            w, h = rgba.size
        except Exception:
            return []
        if w <= 0 or h <= 0:
            return []
        px = rgba.load()
        mid = h / 2.0
        half = max(1.0, h / 2.0)
        levels: list[float] = [0.0] * int(w)

        # Background is typically black; waveform is bright blue. Use a simple luminance threshold.
        for x in range(int(w)):
            max_dev = 0.0
            try:
                for y in range(int(h)):
                    r, g, b, a = px[x, y]
                    if a < 10:
                        continue
                    if (r + g + b) < 60:
                        continue
                    dev = abs(float(y) - mid)
                    if dev > max_dev:
                        max_dev = dev
            except Exception:
                max_dev = 0.0
            levels[x] = max(0.0, min(1.0, float(max_dev) / half))
        return levels

    def _downsample_levels(self, levels: list[float], target_len: int = 240) -> list[float]:
        if not levels:
            return []
        target_len = int(max(16, min(2000, target_len)))
        if len(levels) <= target_len:
            return list(levels)
        out: list[float] = []
        step = float(len(levels)) / float(target_len)
        for i in range(target_len):
            a = int(i * step)
            b = int((i + 1) * step)
            if b <= a:
                b = a + 1
            b = min(len(levels), b)
            m = 0.0
            for v in levels[a:b]:
                if v > m:
                    m = float(v)
            out.append(max(0.0, min(1.0, m)))
        return out

    def _quantize_levels(self, levels: list[float]) -> list[int]:
        return [int(max(0, min(1000, round(float(v) * 1000.0)))) for v in (levels or [])]

    def _dequantize_levels(self, q: list[int]) -> list[float]:
        out: list[float] = []
        for v in (q or []):
            try:
                out.append(max(0.0, min(1.0, float(int(v)) / 1000.0)))
            except Exception:
                continue
        return out

    def _request_vu_profile(self, cue: Cue) -> None:
        if cue.kind not in ("audio", "video"):
            return
        if cue.id in self._vu_profile_cache:
            return
        if cue.vu_profile_q:
            levels = self._dequantize_levels(cue.vu_profile_q)
            if levels:
                peak = 0.0
                try:
                    peak = max(float(v) for v in levels) if levels else 0.0
                except Exception:
                    peak = 0.0
                self._vu_profile_cache[cue.id] = (int(len(levels)), float(peak), list(levels))
                return
        if cue.id in self._vu_req_inflight:
            return
        self._vu_req_inflight.add(cue.id)

        def _worker() -> None:
            levels: list[float] = []
            err_text: str | None = None
            try:
                ffmpeg = _resolve_fftool("ffmpeg")
                if not ffmpeg:
                    raise RuntimeError("ffmpeg not found")
                cmd = [
                    ffmpeg,
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-i",
                    str(cue.path),
                    "-filter_complex",
                    "showwavespic=s=480x80:colors=#4a9eff",
                    "-frames:v",
                    "1",
                    "-f",
                    "image2pipe",
                    "-vcodec",
                    "png",
                    "-",
                ]
                result = subprocess.run(cmd, capture_output=True, timeout=10)
                if result.returncode == 0 and result.stdout:
                    try:
                        from io import BytesIO
                        from PIL import Image

                        img = Image.open(BytesIO(result.stdout))
                        levels = self._compute_vu_levels_from_image(img)
                    except Exception as e:
                        err_text = str(e)
                else:
                    err_text = (result.stderr or b"").decode(errors="ignore") if isinstance(result.stderr, (bytes, bytearray)) else str(result.stderr)
            except Exception as e:
                err_text = str(e)

            def _apply() -> None:
                try:
                    self._vu_req_inflight.discard(cue.id)
                except Exception:
                    pass
                if levels:
                    down = self._downsample_levels(levels, 240)
                    peak = 0.0
                    try:
                        peak = max(float(v) for v in down) if down else 0.0
                    except Exception:
                        peak = 0.0
                    self._vu_profile_cache[cue.id] = (int(len(down)), float(peak), list(down))
                    try:
                        cue.vu_profile_q = self._quantize_levels(down)
                    except Exception:
                        pass
                else:
                    if err_text:
                        try:
                            self._log(f"VU profile unavailable ({err_text.strip()[:120]})")
                        except Exception:
                            pass

            self._ui_tasks.put(_apply)

        threading.Thread(target=_worker, daemon=True).start()

    def _request_loudness_analysis(self, cue: Cue, *, force: bool = False, track_progress: bool = False) -> None:
        if cue.kind not in ("audio", "video"):
            return
        if not force and cue.loudness_i_lufs is not None and cue.true_peak_db is not None:
            return
        if cue.id in self._loud_req_inflight:
            return
        self._loud_req_inflight.add(cue.id)

        # Analyze full file loudness (independent of IN/OUT), then apply gain at playback.

        def _worker() -> None:
            out: dict | None = None
            err_text: str | None = None
            try:
                # Use safe analysis params; we only need input stats from the JSON.
                analysis_i = -16.0
                analysis_tp = -1.0

                try:
                    self._loudness_sem.acquire()
                except Exception:
                    pass

                ffmpeg = _resolve_fftool("ffmpeg")
                if not ffmpeg:
                    raise RuntimeError("ffmpeg not found")
                # loudnorm prints JSON at "info" level; keep output minimal but not suppressed.
                cmd = [ffmpeg, "-hide_banner", "-nostats", "-loglevel", "info"]
                cmd += [
                    "-i",
                    str(cue.path),
                    "-vn",
                    "-sn",
                    "-dn",
                    "-af",
                    f"loudnorm=I={analysis_i:.1f}:TP={analysis_tp:.1f}:LRA=11:print_format=json",
                    "-f",
                    "null",
                    "-",
                ]
                res = subprocess.run(cmd, capture_output=True, text=True, timeout=45)
                stderr = res.stderr or ""
                if res.returncode == 0 and stderr:
                    out = _extract_last_json_object(stderr)
                if out is None:
                    lines = [ln.rstrip() for ln in (stderr or "").splitlines() if ln.strip()]
                    tail = "\n".join(lines[-14:]) if lines else ""
                    if res.returncode != 0:
                        err_text = tail or f"ffmpeg exit={res.returncode}"
                    else:
                        err_text = (
                            "ffmpeg returned 0 but no loudnorm JSON was found.\n"
                            + (tail if tail else "(no stderr output)")
                        )
            except Exception as e:
                err_text = str(e)
            finally:
                try:
                    self._loudness_sem.release()
                except Exception:
                    pass

            def _apply() -> None:
                try:
                    self._loud_req_inflight.discard(cue.id)
                except Exception:
                    pass
                if out:
                    try:
                        cue.loudness_i_lufs = float(out.get("input_i"))
                    except Exception:
                        cue.loudness_i_lufs = cue.loudness_i_lufs
                    try:
                        cue.true_peak_db = float(out.get("input_tp"))
                    except Exception:
                        cue.true_peak_db = cue.true_peak_db
                else:
                    if err_text:
                        if cue.id not in self._loud_fail_once:
                            self._loud_fail_once.add(cue.id)
                            try:
                                self._log(f"Loudness analyze failed for {Path(cue.path).name}:\n{err_text}")
                            except Exception:
                                pass

                if track_progress and cue.id in self._analysis_batch_ids:
                    try:
                        self._analysis_batch_ids.discard(cue.id)
                        self._analysis_batch_done = int(self._analysis_batch_done) + 1
                        total = int(self._analysis_batch_total)
                        done = int(self._analysis_batch_done)
                        self._analysis_progress_var.set(float(done))
                        self._analysis_progress_text_var.set(f"{done}/{total}" if total > 0 else "0/0")
                        try:
                            if self._analysis_progressbar is not None and self._analysis_progressbar.winfo_exists():
                                self._analysis_progressbar.configure(maximum=max(1, total))
                        except Exception:
                            pass
                        if total > 0 and done >= total:
                            self._analysis_progress_text_var.set("Done")
                    except Exception:
                        pass

            self._ui_tasks.put(_apply)

        threading.Thread(target=_worker, daemon=True).start()

    def _apply_waveform_result(
        self,
        deck: str,
        cue_id: str,
        token: int,
        png_bytes: bytes | None,
        err_text: str | None,
    ) -> None:
        # Only apply the latest request for that deck and only if the cue is still selected.
        try:
            if int(self._wave_req_seq.get(deck, 0)) != int(token):
                return
            if self._wave_req_cue_id.get(deck) != cue_id:
                return
        except Exception:
            return

        if deck == "A":
            cue = self._cues_a[self._selected_a] if 0 <= self._selected_a < len(self._cues_a) else None
            canvas = self.canvas_a
        else:
            cue = self._cues_b[self._selected_b] if 0 <= self._selected_b < len(self._cues_b) else None
            canvas = self.canvas_b

        if cue is None or cue.id != cue_id:
            return

        try:
            canvas.delete("all")
        except Exception:
            pass

        # The canvas got cleared, so any cached playback item ids are now invalid.
        try:
            self._playback_items[deck] = None
        except Exception:
            pass

        width = max(1, int(canvas.winfo_width() or 1))
        height = max(1, int(canvas.winfo_height() or 1))
        if width < 10 or height < 10:
            width, height = 600, 60

        photo = None
        if png_bytes:
            try:
                from io import BytesIO
                from PIL import Image, ImageTk

                img = Image.open(BytesIO(png_bytes))
                try:
                    levels = self._compute_vu_levels_from_image(img)
                    if levels:
                        down = self._downsample_levels(levels, 240)
                        peak = 0.0
                        try:
                            peak = max(float(v) for v in down) if down else 0.0
                        except Exception:
                            peak = 0.0
                        self._vu_profile_cache[cue_id] = (int(len(down)), float(peak), list(down))
                        try:
                            cue.vu_profile_q = self._quantize_levels(down)
                        except Exception:
                            pass
                        self._vu_req_inflight.discard(cue_id)
                except Exception:
                    pass
                photo = ImageTk.PhotoImage(img)
            except Exception:
                photo = None

        if photo is not None:
            try:
                if not hasattr(self, "_waveform_images"):
                    self._waveform_images = {}
                self._waveform_images[deck] = photo
            except Exception:
                pass
            try:
                canvas.create_image(0, 0, anchor="nw", image=photo, tags=("waveform",))
            except Exception:
                pass
            self._update_waveform_markers(cue, canvas)
            self._update_waveform_playback_visuals()
        else:
            try:
                canvas.create_text(
                    width // 2,
                    height // 2,
                    text="Waveform preview unavailable",
                    fill="#888888",
                    font=("Arial", 10),
                )
            except Exception:
                pass
            if err_text:
                try:
                    self._log(f"Deck {deck}: Waveform preview unavailable ({err_text.strip()[:120]})")
                except Exception:
                    pass

    def _nudge_in(self, deck: str, delta_sec: float) -> None:
        try:
            if deck == "A":
                if self._selected_a < 0 or self._selected_a >= len(self._cues_a):
                    return
                cue = self._cues_a[self._selected_a]
                var_in = self.var_in_a
                var_out = self.var_out_a
                canvas = self.canvas_a
            else:
                if self._selected_b < 0 or self._selected_b >= len(self._cues_b):
                    return
                cue = self._cues_b[self._selected_b]
                var_in = self.var_in_b
                var_out = self.var_out_b
                canvas = self.canvas_b

            base = _parse_timecode(var_in.get())
            current = cue.start_sec if base is None else float(base)
            duration = self._duration_for_cue(cue)
            new_start = max(0.0, current + float(delta_sec))
            if duration is not None:
                new_start = min(new_start, float(duration))
            cue.start_sec = new_start
            if cue.stop_at_sec is not None and cue.stop_at_sec < cue.start_sec:
                cue.stop_at_sec = cue.start_sec
                var_out.set(_format_timecode(cue.stop_at_sec, with_ms=True))

            var_in.set(_format_timecode(cue.start_sec, with_ms=True))
            self._update_tree_item(cue)
            if cue.kind in ("audio", "video"):
                self._refresh_waveform_markers(cue, canvas, deck)
                self._request_cue_preview_in(cue)
        except Exception as e:
            self._log(f"IN nudge error: {e}")

    def _nudge_out(self, deck: str, delta_sec: float) -> None:
        try:
            if deck == "A":
                if self._selected_a < 0 or self._selected_a >= len(self._cues_a):
                    return
                cue = self._cues_a[self._selected_a]
                var_out = self.var_out_a
                var_in = self.var_in_a
                canvas = self.canvas_a
            else:
                if self._selected_b < 0 or self._selected_b >= len(self._cues_b):
                    return
                cue = self._cues_b[self._selected_b]
                var_out = self.var_out_b
                var_in = self.var_in_b
                canvas = self.canvas_b

            base = _parse_timecode(var_out.get())
            if base is None:
                current = cue.stop_at_sec if cue.stop_at_sec is not None else cue.start_sec
            else:
                current = float(base)
            duration = self._duration_for_cue(cue)
            new_stop = max(0.0, float(current) + float(delta_sec))
            if duration is not None:
                new_stop = min(new_stop, float(duration))
            cue.stop_at_sec = new_stop
            if cue.stop_at_sec < cue.start_sec:
                cue.start_sec = cue.stop_at_sec
                var_in.set(_format_timecode(cue.start_sec, with_ms=True))

            var_out.set(_format_timecode(cue.stop_at_sec, with_ms=True))
            self._update_tree_item(cue)
            if cue.kind in ("audio", "video"):
                self._refresh_waveform_markers(cue, canvas, deck)
                self._request_cue_preview_out(cue)
        except Exception as e:
            self._log(f"OUT nudge error: {e}")

    def _nudge_in_event(self, deck: str, direction: int, event) -> str:
        self._nudge_in(deck, float(direction) * self._nudge_step(event))
        return "break"

    def _nudge_out_event(self, deck: str, direction: int, event) -> str:
        self._nudge_out(deck, float(direction) * self._nudge_step(event))
        return "break"

    def _nudge_in_wheel(self, deck: str, event) -> str:
        delta = getattr(event, "delta", 0)
        direction = 1 if delta > 0 else -1
        self._nudge_in(deck, float(direction) * self._nudge_step(event))
        return "break"

    def _nudge_out_wheel(self, deck: str, event) -> str:
        delta = getattr(event, "delta", 0)
        direction = 1 if delta > 0 else -1
        self._nudge_out(deck, float(direction) * self._nudge_step(event))
        return "break"

    def _stop_preview(self) -> None:
        if self._preview_debounce_after_id is not None:
            try:
                self.after_cancel(self._preview_debounce_after_id)
            except Exception:
                pass
            self._preview_debounce_after_id = None
        proc = self._preview_proc
        self._preview_proc = None
        if proc is None:
            return
        try:
            proc.terminate()
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    def _preview_poll(self) -> None:
        proc = self._preview_proc
        if proc is None:
            return
        try:
            if proc.poll() is None:
                self.after(150, self._preview_poll)
                return
        except Exception:
            pass
        self._preview_proc = None

    def _start_preview(self, path: str, start_sec: float, duration_sec: float, volume_override: int | None = None) -> None:
        if duration_sec <= 0:
            return
        if self.audio_runner.is_playing() or self.video_runner.is_playing():
            return
        ffplay = _resolve_fftool("ffplay")
        if not ffplay:
            return

        self._stop_preview()
        start = max(0.0, float(start_sec))
        dur = max(0.05, float(duration_sec))
        try:
            vol = int(self.settings.startup_volume if volume_override is None else volume_override)
        except Exception:
            vol = 100
        args = [
            ffplay,
            "-hide_banner",
            "-loglevel",
            "error",
            "-autoexit",
            "-nodisp",
            "-vn",
            "-volume",
            str(_clamp_int(vol, 0, 100)),
            "-ss",
            f"{start:.3f}",
            "-t",
            f"{dur:.3f}",
            path,
        ]
        try:
            self._preview_proc = subprocess.Popen(
                args,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                text=False,
            )
            self.after(150, self._preview_poll)
        except Exception:
            self._preview_proc = None

    def _run_preview_request(self) -> None:
        self._preview_debounce_after_id = None
        req = self._preview_request
        self._preview_request = None
        if req is None:
            return
        path, start, dur, vol = req
        self._start_preview(path, start, dur, vol)

    def _request_preview(self, path: str, start_sec: float, duration_sec: float, volume_override: int | None = None) -> None:
        self._preview_request = (path, float(start_sec), float(duration_sec), volume_override)
        if self._preview_debounce_after_id is not None:
            try:
                self.after_cancel(self._preview_debounce_after_id)
            except Exception:
                pass
        self._preview_debounce_after_id = self.after(70, self._run_preview_request)

    def _request_cue_preview_in(self, cue: Cue) -> None:
        try:
            dur_total = self._duration_for_cue(cue)
            start = max(0.0, float(cue.start_sec))
            dur = 0.35
            if cue.stop_at_sec is not None:
                dur = min(dur, max(0.0, float(cue.stop_at_sec) - start))
            if dur_total is not None:
                dur = min(dur, max(0.0, float(dur_total) - start))
            if dur <= 0:
                return
            self._request_preview(cue.path, start, dur, cue.volume_percent)
        except Exception:
            return

    def _request_cue_preview_out(self, cue: Cue) -> None:
        try:
            if cue.stop_at_sec is None:
                return
            out_t = float(cue.stop_at_sec)
            pre = 0.35
            start = max(float(cue.start_sec), out_t - pre)
            dur = max(0.0, out_t - start)
            if dur <= 0:
                return
            self._request_preview(cue.path, start, dur, cue.volume_percent)
        except Exception:
            return

    def _refresh_tree_a(self):
        self.tree_a.delete(*self.tree_a.get_children())
        self._cueid_to_iid_a = {}
        for i, cue in enumerate(self._cues_a):
            iid = str(i)
            self._cueid_to_iid_a[cue.id] = iid
            self.tree_a.insert("", "end", iid=iid, values=(
                i+1,
                cue.kind,
                _shorten_middle(Path(cue.path).name, 64),
                _format_timecode(cue.start_sec),
                _format_timecode(cue.stop_at_sec) if cue.stop_at_sec else "—"
            ))
        self._update_tree_playing_highlight()

    def _refresh_tree_b(self):
        self.tree_b.delete(*self.tree_b.get_children())
        self._cueid_to_iid_b = {}
        for i, cue in enumerate(self._cues_b):
            iid = str(i)
            self._cueid_to_iid_b[cue.id] = iid
            self.tree_b.insert("", "end", iid=iid, values=(
                i+1,
                cue.kind,
                _shorten_middle(Path(cue.path).name, 64),
                _format_timecode(cue.start_sec),
                _format_timecode(cue.stop_at_sec) if cue.stop_at_sec else "—"
            ))
        self._update_tree_playing_highlight()

    def _add_cue_a(self, kind: CueKind):
        types = {
            "audio": [("Audio", "*.mp3 *.wav *.m4a *.aac *.flac"), ("All files", "*.*")],
            "video": [("Video", "*.mp4 *.mov *.mkv *.avi"), ("All files", "*.*")],
            "ppt": [("PowerPoint", "*.pptx *.ppt"), ("All files", "*.*")],
        }
        path = filedialog.askopenfilename(title=f"Add {kind} to Deck A", filetypes=types[kind])
        if not path:
            return
        cue = Cue(id=str(uuid.uuid4()), kind=kind, path=path, start_sec=0.0, stop_at_sec=None,
                  fade_at_sec=None, fade_dur_sec=5.0, fade_to_percent=100, open_on_second_screen=False)
        self._cues_a.append(cue)
        self._refresh_tree_a()
        self._log(f"Deck A: Added {kind} - {Path(path).name}")

    def _add_cue_b(self, kind: CueKind):
        types = {
            "audio": [("Audio", "*.mp3 *.wav *.m4a *.aac *.flac"), ("All files", "*.*")],
            "video": [("Video", "*.mp4 *.mov *.mkv *.avi"), ("All files", "*.*")],
            "ppt": [("PowerPoint", "*.pptx *.ppt"), ("All files", "*.*")],
        }
        path = filedialog.askopenfilename(title=f"Add {kind} to Deck B", filetypes=types[kind])
        if not path:
            return
        cue = Cue(id=str(uuid.uuid4()), kind=kind, path=path, start_sec=0.0, stop_at_sec=None,
                  fade_at_sec=None, fade_dur_sec=5.0, fade_to_percent=100, open_on_second_screen=False)
        self._cues_b.append(cue)
        self._refresh_tree_b()
        self._log(f"Deck B: Added {kind} - {Path(path).name}")

    def _remove_a(self):
        if self._selected_a >= 0 and self._selected_a < len(self._cues_a):
            del self._cues_a[self._selected_a]
            self._selected_a = -1
            self._refresh_tree_a()
            try:
                self._set_wave_title("A", None)
                self._sync_target_setting_controls("A", None)
                self.canvas_a.delete("all")
            except Exception:
                pass
            self._log("Deck A: Removed cue")

    def _remove_b(self):
        if self._selected_b >= 0 and self._selected_b < len(self._cues_b):
            del self._cues_b[self._selected_b]
            self._selected_b = -1
            self._refresh_tree_b()
            try:
                self._set_wave_title("B", None)
                self._sync_target_setting_controls("B", None)
                self.canvas_b.delete("all")
            except Exception:
                pass
            self._log("Deck B: Removed cue")

    def _move_a(self, delta: int):
        if self._selected_a < 0:
            return
        j = self._selected_a + delta
        if j < 0 or j >= len(self._cues_a):
            return
        self._cues_a[self._selected_a], self._cues_a[j] = self._cues_a[j], self._cues_a[self._selected_a]
        self._selected_a = j
        self._refresh_tree_a()
        self.tree_a.selection_set(str(j))

    def _move_b(self, delta: int):
        if self._selected_b < 0:
            return
        j = self._selected_b + delta
        if j < 0 or j >= len(self._cues_b):
            return
        self._cues_b[self._selected_b], self._cues_b[j] = self._cues_b[j], self._cues_b[self._selected_b]
        self._selected_b = j
        self._refresh_tree_b()
        self.tree_b.selection_set(str(j))

    def _play_deck_a(self):
        self._transport_play_pause("A")

    def _play_deck_b(self):
        self._transport_play_pause("B")

    def _stop_deck_a(self):
        self._transport_stop("A")

    def _stop_deck_b(self):
        self._transport_stop("B")

    def _deck_runner(self, deck: str) -> MediaRunner:
        return self.audio_runner if deck == "A" else self.video_runner

    def _selected_cue_for_deck(self, deck: str) -> Cue | None:
        if deck == "A":
            if 0 <= self._selected_a < len(self._cues_a):
                return self._cues_a[self._selected_a]
            return None
        if 0 <= self._selected_b < len(self._cues_b):
            return self._cues_b[self._selected_b]
        return None

    def _paused_state_for_deck(self, deck: str) -> tuple[str, float] | None:
        return self._paused_a if deck == "A" else self._paused_b

    def _set_paused_state_for_deck(self, deck: str, state: tuple[str, float] | None) -> None:
        if deck == "A":
            self._paused_a = state
        else:
            self._paused_b = state

    def _transport_play_pause(self, deck: str) -> None:
        runner = self._deck_runner(deck)

        # PLAY acts as PLAY/PAUSE.
        try:
            if runner.is_playing():
                cue = runner.current_cue()
                if cue is None or cue.kind not in ("audio", "video"):
                    return
                pos = runner.playback_position_sec()
                if pos is None:
                    return
                self._set_paused_state_for_deck(deck, (cue.id, float(pos)))
                self._suppress_finish[deck] = "pause"
                if deck == "A":
                    self._was_playing_a = True
                else:
                    self._was_playing_b = True
                runner.stop()
                self._log(f"Deck {deck}: Paused @ {_format_timecode(pos, with_ms=True)}")
                self._update_transport_button_visuals()
                return
        except Exception:
            pass

        cue = self._selected_cue_for_deck(deck)
        if cue is None:
            return

        paused = self._paused_state_for_deck(deck)
        resume_pos = None
        if paused is not None and paused[0] == cue.id:
            resume_pos = float(paused[1])
        else:
            self._set_paused_state_for_deck(deck, None)

        self._active_runner = runner
        try:
            if resume_pos is not None and cue.kind in ("audio", "video"):
                runner.play_at(cue, resume_pos, volume_override=cue.volume_percent)
                self._log(f"Deck {deck}: Resumed @ {_format_timecode(resume_pos, with_ms=True)}")
                self._set_paused_state_for_deck(deck, None)
            else:
                runner.play(cue)
                self._log(f"Deck {deck}: Playing {Path(cue.path).name}")
        except Exception as e:
            self._log(f"Deck {deck} play error: {e}")
            try:
                runner.stop()
            except Exception:
                pass
        finally:
            self._update_transport_button_visuals()

    def _transport_stop(self, deck: str) -> None:
        runner = self._deck_runner(deck)
        self._set_paused_state_for_deck(deck, None)
        try:
            playing = bool(runner.is_playing())
        except Exception:
            playing = False
        if playing:
            self._suppress_finish[deck] = "stop"
            if deck == "A":
                self._was_playing_a = True
            else:
                self._was_playing_b = True
        try:
            runner.stop()
            self._log(f"Deck {deck}: Stopped")
        except Exception as e:
            self._log(f"Deck {deck} stop error: {e}")
        finally:
            self._update_transport_button_visuals()

    def _toggle_loop(self, deck: str) -> None:
        if deck == "A":
            self._loop_a_enabled = not self._loop_a_enabled
            try:
                self.var_loop_a.set("⟲ LOOP ON" if self._loop_a_enabled else "⟲ LOOP OFF")
            except Exception:
                pass
            try:
                self._update_transport_button_visuals()
            except Exception:
                pass
            self._log(f"Deck A: Loop {'ON' if self._loop_a_enabled else 'OFF'}")
            return
        self._loop_b_enabled = not self._loop_b_enabled
        try:
            self.var_loop_b.set("⟲ LOOP ON" if self._loop_b_enabled else "⟲ LOOP OFF")
        except Exception:
            pass
        try:
            self._update_transport_button_visuals()
        except Exception:
            pass
        self._log(f"Deck B: Loop {'ON' if self._loop_b_enabled else 'OFF'}")

    def _loop_enabled_for_runner(self, runner: MediaRunner) -> bool:
        if runner == self.audio_runner:
            return bool(self._loop_a_enabled)
        if runner == self.video_runner:
            return bool(self._loop_b_enabled)
        return False

    def _sync_target_setting_controls(self, deck: str, cue: Cue | None) -> None:
        try:
            if deck == "A":
                var = getattr(self, "var_target_a", None)
                rb_window = getattr(self, "rb_a_window", None)
                rb_second = getattr(self, "rb_a_second", None)
                vol_var = getattr(self, "var_cue_vol_a", None)
                vol_label = getattr(self, "var_cue_vol_a_label", None)
                vol_scale = getattr(self, "scale_cue_vol_a", None)
            else:
                var = getattr(self, "var_target_b", None)
                rb_window = getattr(self, "rb_b_window", None)
                rb_second = getattr(self, "rb_b_second", None)
                vol_var = getattr(self, "var_cue_vol_b", None)
                vol_label = getattr(self, "var_cue_vol_b_label", None)
                vol_scale = getattr(self, "scale_cue_vol_b", None)

            enabled = bool(cue is not None and cue.kind == "video")
            state = "normal" if enabled else "disabled"
            if rb_window is not None:
                rb_window.configure(state=state)
            if rb_second is not None:
                rb_second.configure(state=state)

            if var is not None:
                if cue is None:
                    var.set("window")
                else:
                    var.set("second" if cue.open_on_second_screen else "window")

            vol_enabled = bool(cue is not None and cue.kind in ("audio", "video"))
            if vol_scale is not None:
                try:
                    vol_scale.state(["!disabled"] if vol_enabled else ["disabled"])
                except Exception:
                    try:
                        vol_scale.configure(state=("normal" if vol_enabled else "disabled"))
                    except Exception:
                        pass
            if vol_var is not None and vol_label is not None:
                if cue is None or not vol_enabled:
                    v = int(self.settings.startup_volume)
                else:
                    v = int(self.settings.startup_volume if cue.volume_percent is None else cue.volume_percent)
                v = _clamp_int(v, 0, 100)
                try:
                    vol_var.set(v)
                    vol_label.set(str(v))
                except Exception:
                    pass
        except Exception:
            return

    def _apply_target_setting(self, deck: str) -> None:
        try:
            cue = None
            if deck == "A" and 0 <= self._selected_a < len(self._cues_a):
                cue = self._cues_a[self._selected_a]
                var = getattr(self, "var_target_a", None)
            elif deck == "B" and 0 <= self._selected_b < len(self._cues_b):
                cue = self._cues_b[self._selected_b]
                var = getattr(self, "var_target_b", None)
            else:
                return
            if cue is None or cue.kind != "video" or var is None:
                return
            cue.open_on_second_screen = (str(var.get()) == "second")
            self._update_tree_item(cue)
        except Exception:
            return

    def _apply_volume_setting(self, deck: str) -> None:
        try:
            if deck == "A":
                if not (0 <= self._selected_a < len(self._cues_a)):
                    return
                cue = self._cues_a[self._selected_a]
                var = getattr(self, "var_cue_vol_a", None)
                label = getattr(self, "var_cue_vol_a_label", None)
            else:
                if not (0 <= self._selected_b < len(self._cues_b)):
                    return
                cue = self._cues_b[self._selected_b]
                var = getattr(self, "var_cue_vol_b", None)
                label = getattr(self, "var_cue_vol_b_label", None)

            if cue is None or cue.kind not in ("audio", "video") or var is None:
                return
            v = int(round(float(var.get())))
            v = _clamp_int(v, 0, 100)
            cue.volume_percent = v
            if label is not None:
                try:
                    label.set(str(v))
                except Exception:
                    pass
        except Exception:
            return

    # Legacy compatibility stubs
    def _selected_cue(self) -> Cue | None:
        if self._selected_a >= 0 and self._selected_a < len(self._cues_a):
            return self._cues_a[self._selected_a]
        if self._selected_b >= 0 and self._selected_b < len(self._cues_b):
            return self._cues_b[self._selected_b]
        return None

    def _refresh_tree(self) -> None:
        self._refresh_tree_a()
        self._refresh_tree_b()

    def _tree_values_for_cue(self, idx: int, cue: Cue) -> tuple[str, str, str, str, str, str, str]:
        return (
            str(int(idx)),
            cue.kind,
            cue.display_name(),
            _shorten_middle(cue.note, 38),
            _format_timecode(cue.start_sec),
            _format_timecode(cue.stop_at_sec),
            "Y" if cue.open_on_second_screen else "",
        )

    def _update_tree_item(self, cue: Cue) -> None:
        # Find which deck the cue belongs to and update the appropriate tree.
        # Important: avoid full tree refresh here because that clears Treeview
        # selection, which is disruptive while setting IN/OUT points.
        try:
            idx_a: int | None = None
            iid_a = self._cueid_to_iid_a.get(cue.id)
            if iid_a is not None:
                try:
                    idx_guess = int(iid_a)
                except Exception:
                    idx_guess = -1
                if 0 <= idx_guess < len(self._cues_a) and self._cues_a[idx_guess].id == cue.id:
                    idx_a = idx_guess
            if idx_a is None:
                idx_a = next((i for i, c in enumerate(self._cues_a) if c.id == cue.id), None)
            if idx_a is not None:
                iid = str(int(idx_a))
                stop_txt = _format_timecode(cue.stop_at_sec) if cue.stop_at_sec else "—"
                values = (
                    int(idx_a) + 1,
                    cue.kind,
                    _shorten_middle(Path(cue.path).name, 64),
                    _format_timecode(cue.start_sec),
                    stop_txt,
                )
                if self.tree_a.exists(iid):
                    self.tree_a.item(iid, values=values)
                else:
                    self._refresh_tree_a()
                return

            idx_b: int | None = None
            iid_b = self._cueid_to_iid_b.get(cue.id)
            if iid_b is not None:
                try:
                    idx_guess = int(iid_b)
                except Exception:
                    idx_guess = -1
                if 0 <= idx_guess < len(self._cues_b) and self._cues_b[idx_guess].id == cue.id:
                    idx_b = idx_guess
            if idx_b is None:
                idx_b = next((i for i, c in enumerate(self._cues_b) if c.id == cue.id), None)
            if idx_b is not None:
                iid = str(int(idx_b))
                stop_txt = _format_timecode(cue.stop_at_sec) if cue.stop_at_sec else "—"
                values = (
                    int(idx_b) + 1,
                    cue.kind,
                    _shorten_middle(Path(cue.path).name, 64),
                    _format_timecode(cue.start_sec),
                    stop_txt,
                )
                if self.tree_b.exists(iid):
                    self.tree_b.item(iid, values=values)
                else:
                    self._refresh_tree_b()
                return
        except Exception:
            self._refresh_tree()

    def _load_cue_into_editor(self, cue: Cue | None) -> None:
        # No longer used - editor panel removed in dual-deck layout
        pass

    def _load_selected_into_editor(self) -> None:
        # No longer used - editor panel removed in dual-deck layout
        pass

    def _duration_for_cue(self, cue: Cue) -> float | None:
        if cue.kind not in ("audio", "video"):
            return None
        key = cue.path
        if key in self._duration_cache:
            return self._duration_cache[key]
        dur = probe_media_duration_sec(cue.path)
        if dur is None:
            return None
        self._duration_cache[key] = dur
        return dur

    def _set_timeline(self, duration: float | None) -> None:
        self._current_duration = duration
        if duration is None:
            self.var_dur.set("Duration: —")
            self.scale.configure(state="disabled", from_=0.0, to=1.0)
            self.var_playhead.set(0.0)
            self.var_playhead_label.set("0:00")
            return
        self.var_dur.set(f"Duration: {_format_timecode(duration)}")
        self.scale.configure(state="normal", from_=0.0, to=float(duration))
        cue = self._selected_cue()
        if cue:
            self.var_playhead.set(float(max(0.0, min(cue.start_sec, duration))))
        self._update_playhead_label()

    def _update_playhead_label(self) -> None:
        try:
            self.var_playhead_label.set(_format_timecode(float(self.var_playhead.get())))
        except Exception:
            pass

    def _save_editor_to_selected(self) -> None:
        if self._loading_editor:
            return
        cue = self._selected_cue()
        if not cue:
            return

        cue.open_on_second_screen = self.var_target.get() == "second"
        cue.note = (self.var_note.get() or "").strip()
        self._update_tree_item(cue)

    # ── Actions ─────────────────────────────────────────────────────────
    def _apply_settings_from_vars(self) -> None:
        try:
            self.settings.second_screen_left = int(float(self.var_left.get().strip() or "0"))
            self.settings.second_screen_top = int(float(self.var_top.get().strip() or "0"))
            self.settings.video_fullscreen = bool(self.var_fs.get())
        except Exception:
            return

    def _on_volume_change(self) -> None:
        if not hasattr(self, "var_vol") or not hasattr(self, "var_vol_label"):
            return
        try:
            v = int(round(float(self.var_vol.get())))
        except Exception:
            return
        v = _clamp_int(v, 0, 100)
        try:
            self.var_vol_label.set(str(v))
        except Exception:
            pass
        self.settings.startup_volume = v

        if self._vol_restart_after_id is not None:
            try:
                self.after_cancel(self._vol_restart_after_id)
            except Exception:
                pass
            self._vol_restart_after_id = None

        # Live-ish volume control: restart only audio playback (no window) from current position.
        try:
            if self.audio_runner.is_playing():
                self._vol_restart_after_id = self.after(180, lambda: self._restart_audio_with_volume(v))
        except Exception:
            pass

    def _restart_audio_with_volume(self, v: int) -> None:
        self._vol_restart_after_id = None
        try:
            if not self.audio_runner.is_playing():
                return
            cue = self.audio_runner.current_cue()
            if cue is None or cue.kind != "audio":
                return
            self._inhibit_auto_advance = True
            self._was_playing = False
            self._was_playing_a = False
            self._active_runner = self.audio_runner
            try:
                self.audio_runner.restart_with_volume(int(v))  # type: ignore[attr-defined]
                self._log(f"Volume: {int(v)}%")
            finally:
                self._inhibit_auto_advance = False
        except Exception as e:
            self._log(f"Volume change failed: {e}")

    def _add_cue(self, kind: CueKind) -> None:
        types = {
            "audio": [("Audio", "*.mp3 *.wav *.m4a *.aac *.flac"), ("All files", "*.*")],
            "video": [("Video", "*.mp4 *.mov *.mkv *.avi"), ("All files", "*.*")],
            "ppt": [("PowerPoint", "*.pptx *.ppt"), ("All files", "*.*")],
        }
        path = filedialog.askopenfilename(title=f"Add {kind}", filetypes=types[kind])
        if not path:
            return

        cue = Cue(
            id=str(uuid.uuid4()),
            kind=kind,
            path=path,
            start_sec=0.0,
            stop_at_sec=None,
            fade_at_sec=None,
            fade_dur_sec=5.0,
            fade_to_percent=100,
            open_on_second_screen=False,
        )
        self._cues.append(cue)
        self._refresh_tree()
        self.tree.selection_set(cue.id)
        self.tree.see(cue.id)
        self._log(f"Added {cue.kind}: {cue.display_name()}")

    def _remove_selected(self) -> None:
        cue = self._selected_cue()
        if not cue:
            return
        self._cues = [c for c in self._cues if c.id != cue.id]
        self._refresh_tree()
        self._load_selected_into_editor()
        self._log(f"Removed: {cue.display_name()}")

    def _move_selected(self, delta: int) -> None:
        cue = self._selected_cue()
        if not cue:
            return
        idx = next((i for i, c in enumerate(self._cues) if c.id == cue.id), None)
        if idx is None:
            return
        j = idx + int(delta)
        if j < 0 or j >= len(self._cues):
            return
        self._cues[idx], self._cues[j] = self._cues[j], self._cues[idx]
        self._refresh_tree()
        self.tree.selection_set(cue.id)
        self.tree.see(cue.id)
        self._log(f"Moved: {cue.display_name()} ({'up' if delta < 0 else 'down'})")

    def _play_selected(self) -> None:
        cue = self._selected_cue()
        if not cue:
            return
        try:
            self._paused_cue_id = None
            self._paused_kind = None
            self._paused_pos_sec = None
            self._inhibit_auto_advance = True
            try:
                self.audio_runner.stop()
            except Exception:
                pass
            try:
                self.video_runner.stop()
            except Exception:
                pass
            self._inhibit_auto_advance = False

            runner = self.video_runner if cue.kind == "video" else self.audio_runner
            self._active_runner = runner
            was_playing = runner.is_playing()
            self._inhibit_auto_advance = bool(was_playing)
            runner.play(cue)
            self._inhibit_auto_advance = False
            if cue.kind == "ppt":
                self._log(f"PPT started: {cue.display_name()}")
            else:
                target = ""
                if cue.kind == "video":
                    target = " (2nd screen)" if cue.open_on_second_screen else " (window)"
                self._log(f"Playing{target}: {cue.display_name()}")
                try:
                    self._log(runner.debug_text())  # type: ignore[attr-defined]
                except Exception:
                    pass
        except Exception as e:
            self._inhibit_auto_advance = False
            messagebox.showerror("Play failed", str(e))
            self._log(f"Play failed: {e}")

    def _play_selected_ppt_only(self) -> None:
        cue = self._selected_cue()
        if not cue or cue.kind != "ppt":
            return
        try:
            self._inhibit_auto_advance = True
            try:
                self.audio_runner.stop()
            except Exception:
                pass
            try:
                self.video_runner.stop()
            except Exception:
                pass
            self._inhibit_auto_advance = False

            self._active_runner = self.audio_runner
            self.audio_runner.play(cue)
            self._log(f"PPT started: {cue.display_name()}")
        except Exception as e:
            messagebox.showerror("PPT failed", str(e))
            self._log(f"PPT failed: {e}")

    def _stop(self) -> None:
        self._inhibit_auto_advance = True
        self._paused_cue_id = None
        self._paused_kind = None
        self._paused_pos_sec = None
        try:
            self.audio_runner.stop()
        except Exception:
            pass
        try:
            self.video_runner.stop()
        except Exception:
            pass
        self._log("Stopped.")

    def _show_debug(self) -> None:
        try:
            msg = self._active_runner.debug_text()  # type: ignore[attr-defined]
        except Exception:
            msg = "No debug info."
        messagebox.showinfo("Playback debug", msg)
        self._log(msg)

    def _toggle_play_pause(self) -> None:
        runner, cue = self._current_playback_source()
        if runner is not None and cue is not None and cue.kind in ("audio", "video"):
            try:
                pos = runner.playback_position_sec()  # type: ignore[attr-defined]
            except Exception:
                pos = None
            if pos is None:
                return
            self._paused_cue_id = cue.id
            self._paused_kind = cue.kind
            self._paused_pos_sec = float(pos)
            try:
                runner.stop()
            except Exception:
                pass
            self._log(f"Paused: {cue.display_name()} @ {_format_timecode(pos)}")
            return

        if self._paused_cue_id and self._paused_kind and self._paused_pos_sec is not None:
            # Search for paused cue in both decks
            cue_obj = next((c for c in self._cues_a if c.id == self._paused_cue_id), None)
            if cue_obj is None:
                cue_obj = next((c for c in self._cues_b if c.id == self._paused_cue_id), None)
            if cue_obj is None:
                self._paused_cue_id = None
                self._paused_kind = None
                self._paused_pos_sec = None
                return
            runner2 = self.video_runner if cue_obj.kind == "video" else self.audio_runner
            self._active_runner = runner2
            try:
                runner2.play(cue_obj)
                runner2.restart_at(float(self._paused_pos_sec))  # type: ignore[attr-defined]
            except Exception:
                try:
                    runner2.stop()
                except Exception:
                    pass
                return
            self._log(f"Resumed: {cue_obj.display_name()} @ {_format_timecode(self._paused_pos_sec)}")
            self._paused_cue_id = None
            self._paused_kind = None
            self._paused_pos_sec = None
            return

        self._play_selected()

    def _select_next_after_id(self, cue_id: str) -> bool:
        # Check deck A first
        ids_a = [c.id for c in self._cues_a]
        try:
            idx = ids_a.index(cue_id)
            if idx + 1 < len(ids_a):
                self._selected_a = idx + 1
                self.tree_a.selection_set(str(self._selected_a))
                self.tree_a.see(str(self._selected_a))
                self._load_cue_into_editor(self._cues_a[self._selected_a])
                return True
            return False
        except ValueError:
            pass

        # Check deck B
        ids_b = [c.id for c in self._cues_b]
        try:
            idx = ids_b.index(cue_id)
            if idx + 1 < len(ids_b):
                self._selected_b = idx + 1
                self.tree_b.selection_set(str(self._selected_b))
                self.tree_b.see(str(self._selected_b))
                self._load_cue_into_editor(self._cues_b[self._selected_b])
                return True
            return False
        except ValueError:
            return False

    def _go_live(self) -> None:
        runner, playing = self._current_playback_source()
        if runner is not None and playing is not None and playing.kind in ("audio", "video", "ppt"):
            try:
                runner.stop()
            except Exception:
                pass
            advanced = self._select_next_after_id(playing.id)
            if not advanced:
                # End of show: just stop.
                self._log("End of cue list.")
                return
        else:
            # No selection - try to select first cue from deck A or B
            if self._selected_a < 0 and self._selected_b < 0:
                if self._cues_a:
                    self._selected_a = 0
                    self.tree_a.selection_set("0")
                    self.tree_a.see("0")
                    self._load_cue_into_editor(self._cues_a[0])
                elif self._cues_b:
                    self._selected_b = 0
                    self.tree_b.selection_set("0")
                    self.tree_b.see("0")
                    self._load_cue_into_editor(self._cues_b[0])
        self._play_selected()

    def _seek_relative(self, delta_sec: float) -> None:
        delta = float(delta_sec)
        runner, cue = self._current_playback_source()
        if runner is not None and cue is not None and cue.kind in ("audio", "video"):
            try:
                pos = runner.playback_position_sec()  # type: ignore[attr-defined]
            except Exception:
                pos = None
            if pos is None:
                return
            try:
                runner.restart_at(float(pos) + delta)  # type: ignore[attr-defined]
            except Exception:
                return
            self._log(f"Seek: {_format_timecode(float(pos) + delta)}")
            return

        if self._paused_cue_id and self._paused_pos_sec is not None:
            # Search for paused cue in both decks
            cue_obj = next((c for c in self._cues_a if c.id == self._paused_cue_id), None)
            if cue_obj is None:
                cue_obj = next((c for c in self._cues_b if c.id == self._paused_cue_id), None)
            if cue_obj is None:
                return
            new_pos = max(0.0, float(self._paused_pos_sec) + delta)
            if cue_obj.stop_at_sec is not None:
                new_pos = min(new_pos, float(cue_obj.stop_at_sec))
            self._paused_pos_sec = new_pos
            self._log(f"Paused seek: {_format_timecode(new_pos)}")
            return

    def _mark_target_and_time(self) -> tuple[Cue | None, float | None]:
        runner, playing = self._current_playback_source()
        if runner is not None and playing is not None and playing.kind in ("audio", "video"):
            try:
                t = runner.playback_position_sec()  # type: ignore[attr-defined]
            except Exception:
                t = None
            if t is not None:
                # Search for playing cue in both decks
                cue_obj = next((c for c in self._cues_a if c.id == playing.id), None)
                if cue_obj is None:
                    cue_obj = next((c for c in self._cues_b if c.id == playing.id), None)
                if cue_obj is None:
                    cue_obj = self._selected_cue()
                # No need to update tree selection - just use the cue object
                return cue_obj, float(t)

        cue = self._selected_cue()
        if cue is None:
            return None, None
        try:
            return cue, float(self.var_playhead.get())
        except Exception:
            return cue, float(cue.start_sec)

    def _mark_start(self) -> None:
        cue, t = self._mark_target_and_time()
        if cue is None or t is None:
            return
        cue.start_sec = max(0.0, float(t))
        if cue.stop_at_sec is not None and cue.stop_at_sec < cue.start_sec:
            cue.stop_at_sec = cue.start_sec
        self._update_tree_item(cue)
        if self._selected_cue() and self._selected_cue().id == cue.id:
            self._load_selected_into_editor()
        try:
            if self._current_duration is not None:
                self.var_playhead.set(float(max(0.0, min(cue.start_sec, self._current_duration))))
        except Exception:
            pass
        self._log(f"Marked START: {cue.display_name()} @ {_format_timecode(cue.start_sec)}")

    def _mark_stop(self) -> None:
        cue, t = self._mark_target_and_time()
        if cue is None or t is None:
            return
        cue.stop_at_sec = max(0.0, float(t))
        if cue.stop_at_sec < cue.start_sec:
            cue.start_sec = cue.stop_at_sec
        self._update_tree_item(cue)
        if self._selected_cue() and self._selected_cue().id == cue.id:
            self._load_selected_into_editor()
        try:
            if self._current_duration is not None:
                self.var_playhead.set(float(max(0.0, min(cue.stop_at_sec, self._current_duration))))
        except Exception:
            pass
        self._log(f"Marked STOP: {cue.display_name()} @ {_format_timecode(cue.stop_at_sec)}")

    def _poll_playback(self) -> None:
        delay_ms = 250
        try:
            self._drain_ui_tasks()
            self._update_now_playing()
            self._update_vu_meters()
            self._update_waveform_playback_visuals()
            self._update_transport_button_visuals()
            self._update_tree_playing_highlight()
            try:
                a_playing = bool(self.audio_runner.is_playing())
            except Exception:
                a_playing = False
            try:
                b_playing = bool(self.video_runner.is_playing())
            except Exception:
                b_playing = False

            if self._was_playing_a and not a_playing:
                self._handle_runner_finished("A", self.audio_runner)
            if self._was_playing_b and not b_playing:
                self._handle_runner_finished("B", self.video_runner)

            self._was_playing_a = a_playing
            self._was_playing_b = b_playing
            if a_playing or b_playing:
                delay_ms = 33
            elif self._paused_a is not None or self._paused_b is not None:
                delay_ms = 80
        finally:
            self.after(int(delay_ms), self._poll_playback)

    def _drain_ui_tasks(self, max_items: int = 10) -> None:
        for _ in range(int(max_items)):
            try:
                fn = self._ui_tasks.get_nowait()
            except Exception:
                return
            try:
                fn()
            except Exception:
                continue

    def _update_tree_playing_highlight(self) -> None:
        # Highlight the currently playing cue in each deck's file list without changing selection.
        if self._playing_iid_a is None and self._playing_iid_b is None:
            try:
                if not (self.audio_runner.is_playing() or self.video_runner.is_playing()):
                    return
            except Exception:
                pass
        new_iid_a: str | None = None
        new_iid_b: str | None = None
        try:
            if self.audio_runner.is_playing():
                cue = self.audio_runner.current_cue()
                if cue is not None:
                    iid = self._cueid_to_iid_a.get(cue.id)
                    if iid is not None and self.tree_a.exists(iid):
                        new_iid_a = iid
        except Exception:
            pass
        try:
            if self.video_runner.is_playing():
                cue = self.video_runner.current_cue()
                if cue is not None:
                    iid = self._cueid_to_iid_b.get(cue.id)
                    if iid is not None and self.tree_b.exists(iid):
                        new_iid_b = iid
        except Exception:
            pass

        if new_iid_a == self._playing_iid_a and new_iid_b == self._playing_iid_b:
            return

        if new_iid_a != self._playing_iid_a:
            try:
                if self._playing_iid_a is not None and self.tree_a.exists(self._playing_iid_a):
                    tags = [t for t in (self.tree_a.item(self._playing_iid_a).get("tags") or ()) if t != "playing"]
                    self.tree_a.item(self._playing_iid_a, tags=tuple(tags))
            except Exception:
                pass
            try:
                if new_iid_a is not None and self.tree_a.exists(new_iid_a):
                    tags = list(self.tree_a.item(new_iid_a).get("tags") or ())
                    if "playing" not in tags:
                        tags.append("playing")
                    self.tree_a.item(new_iid_a, tags=tuple(tags))
            except Exception:
                pass
            self._playing_iid_a = new_iid_a

        if new_iid_b != self._playing_iid_b:
            try:
                if self._playing_iid_b is not None and self.tree_b.exists(self._playing_iid_b):
                    tags = [t for t in (self.tree_b.item(self._playing_iid_b).get("tags") or ()) if t != "playing"]
                    self.tree_b.item(self._playing_iid_b, tags=tuple(tags))
            except Exception:
                pass
            try:
                if new_iid_b is not None and self.tree_b.exists(new_iid_b):
                    tags = list(self.tree_b.item(new_iid_b).get("tags") or ())
                    if "playing" not in tags:
                        tags.append("playing")
                    self.tree_b.item(new_iid_b, tags=tuple(tags))
            except Exception:
                pass
            self._playing_iid_b = new_iid_b

    def _handle_runner_finished(self, deck: str, runner: MediaRunner) -> None:
        # Do not advance on user stop/pause, only on natural OUT/file end.
        if deck in self._suppress_finish:
            self._suppress_finish.pop(deck, None)
            return
        if self._inhibit_auto_advance:
            self._inhibit_auto_advance = False
            return

        last_exit = getattr(runner, "last_exit_code", None)
        if last_exit not in (None, 0):
            self._log("Playback failed.")
            try:
                self._log(runner.debug_text())  # type: ignore[attr-defined]
            except Exception:
                pass
            return

        cue = None
        try:
            cue = runner.current_cue()
        except Exception:
            cue = None

        if cue and cue.kind in ("audio", "video") and self._loop_enabled_for_runner(runner):
            try:
                runner.play(cue)
                if deck == "A":
                    self._was_playing_a = True
                else:
                    self._was_playing_b = True
            except Exception:
                pass
            return

        try:
            if cue:
                self._log(f"Finished: {cue.display_name()}")
        except Exception:
            pass
        self._select_next_cue_for_deck(deck)

    def _update_transport_button_visuals(self) -> None:
        def _update_deck(deck: str, *, playing: bool, loop_enabled: bool) -> None:
            try:
                if deck == "A":
                    btn_play = getattr(self, "btn_play_a", None)
                    btn_stop = getattr(self, "btn_stop_a", None)
                    btn_loop = getattr(self, "btn_loop_a", None)
                    var_play = getattr(self, "var_play_a", None)
                else:
                    btn_play = getattr(self, "btn_play_b", None)
                    btn_stop = getattr(self, "btn_stop_b", None)
                    btn_loop = getattr(self, "btn_loop_b", None)
                    var_play = getattr(self, "var_play_b", None)

                paused = self._paused_state_for_deck(deck)
                sel = self._selected_cue_for_deck(deck)

                if playing:
                    play_text = "⏸ PAUSE"
                elif paused is not None and sel is not None and paused[0] == sel.id:
                    play_text = "▶ RESUME"
                else:
                    play_text = "▶ PLAY"

                play_bg = self._btn_play_on_bg if playing else self._btn_off_bg
                stop_bg = self._btn_stop_on_bg if playing else self._btn_off_bg
                loop_bg = self._btn_loop_on_bg if loop_enabled else self._btn_off_bg
                loop_fg = self._btn_loop_on_fg if loop_enabled else self._btn_off_fg

                state = (play_text, play_bg, stop_bg, loop_bg, loop_fg)
                if self._transport_visual_cache.get(deck) == state:
                    return
                self._transport_visual_cache[deck] = state

                if var_play is not None:
                    try:
                        if str(var_play.get()) != play_text:
                            var_play.set(play_text)
                    except Exception:
                        pass

                if btn_play is not None:
                    try:
                        btn_play.configure(bg=play_bg, fg=self._btn_off_fg)
                    except Exception:
                        pass
                if btn_stop is not None:
                    try:
                        btn_stop.configure(bg=stop_bg, fg=self._btn_off_fg)
                    except Exception:
                        pass
                if btn_loop is not None:
                    try:
                        btn_loop.configure(bg=loop_bg, fg=loop_fg)
                    except Exception:
                        pass
            except Exception:
                return

        try:
            a_playing = bool(self.audio_runner.is_playing())
        except Exception:
            a_playing = False
        _update_deck("A", playing=a_playing, loop_enabled=bool(self._loop_a_enabled))

        try:
            b_playing = bool(self.video_runner.is_playing())
        except Exception:
            b_playing = False
        _update_deck("B", playing=b_playing, loop_enabled=bool(self._loop_b_enabled))

    def _current_playback_source(self) -> tuple[object | None, Cue | None]:
        try:
            if self.video_runner.is_playing():
                return self.video_runner, self.video_runner.current_cue()
        except Exception:
            pass
        try:
            if self.audio_runner.is_playing():
                return self.audio_runner, self.audio_runner.current_cue()
        except Exception:
            pass
        return None, None

    def _update_now_playing(self) -> None:
        # Update Deck A
        self._update_deck_now_playing(
            "A",
            self.audio_runner,
            self.var_now_a_time
        )

        # Update Deck B
        self._update_deck_now_playing(
            "B",
            self.video_runner,
            self.var_now_b_time
        )

    def _ensure_vu_items(self, deck: str, canvas: tk.Canvas) -> dict[str, int]:
        items = self._vu_items.get(deck)
        if items:
            try:
                canvas.type(items["peak"])
                return items
            except Exception:
                pass

        bg = canvas.create_rectangle(0, 0, 0, 0, fill="#1f1f1f", outline="#3b3b3b", width=1, tags=("vu_bg",))
        # LED-style segments.
        seg_ids: list[int] = []
        for _ in range(24):
            seg_ids.append(canvas.create_rectangle(0, 0, 0, 0, fill="#2a2a2a", outline="", tags=("vu_seg",)))
        # Peak marker as a thin in-bar rectangle (keeps within bar height).
        peak = canvas.create_rectangle(0, 0, 0, 0, fill="#eaeaea", outline="", tags=("vu_peak",))
        items = {"bg": bg, "peak": peak, **{f"s{i}": sid for i, sid in enumerate(seg_ids)}}
        self._vu_items[deck] = items
        self._vu_visible[deck] = False
        return items

    def _set_vu_visible(self, deck: str, canvas: tk.Canvas, visible: bool) -> None:
        if bool(self._vu_visible.get(deck, False)) == bool(visible):
            return
        self._vu_visible[deck] = bool(visible)
        items = self._ensure_vu_items(deck, canvas)
        state = "normal" if visible else "hidden"
        for iid in items.values():
            try:
                canvas.itemconfigure(iid, state=state)
            except Exception:
                pass

    def _clear_vu_for_deck(self, deck: str) -> None:
        canvas = getattr(self, "vu_canvas_a", None) if deck == "A" else getattr(self, "vu_canvas_b", None)
        if canvas is None:
            return
        try:
            self._set_vu_visible(deck, canvas, False)
        except Exception:
            return
        try:
            var = getattr(self, "var_vu_a_db", None) if deck == "A" else getattr(self, "var_vu_b_db", None)
            if var is not None:
                var.set("")
            self._vu_db_cache[deck] = ""
        except Exception:
            pass

    def _find_cue_by_id_for_deck(self, deck: str, cue_id: str) -> Cue | None:
        try:
            cues = self._cues_a if deck == "A" else self._cues_b
        except Exception:
            return None
        try:
            for c in cues:
                if c.id == cue_id:
                    return c
        except Exception:
            return None
        return None

    def _update_vu_meters(self) -> None:
        self._update_vu_for_deck("A", self.audio_runner)
        self._update_vu_for_deck("B", self.video_runner)

    def _update_vu_for_deck(self, deck: str, runner: MediaRunner) -> None:
        canvas = getattr(self, "vu_canvas_a", None) if deck == "A" else getattr(self, "vu_canvas_b", None)
        if canvas is None:
            return

        cue: Cue | None = None
        pos: float | None = None
        try:
            if runner is not None and runner.is_playing():
                cue = runner.current_cue()
                pos = runner.playback_position_sec()
        except Exception:
            cue, pos = None, None

        if cue is None or pos is None:
            paused = None
            try:
                paused = self._paused_state_for_deck(deck)
            except Exception:
                paused = None
            if paused is not None:
                cue = self._find_cue_by_id_for_deck(deck, paused[0])
                pos = float(paused[1])

        if cue is None or pos is None or cue.kind not in ("audio", "video"):
            self._clear_vu_for_deck(deck)
            return

        duration = self._duration_for_cue(cue)
        if duration is None or duration <= 0:
            self._clear_vu_for_deck(deck)
            return

        prof = self._vu_profile_cache.get(cue.id)
        if prof is None or not prof[2]:
            self._request_vu_profile(cue)
            self._clear_vu_for_deck(deck)
            return

        levels = prof[2]
        peak_raw = 0.0
        try:
            peak_raw = float(prof[1] or 0.0)
        except Exception:
            peak_raw = 0.0
        if peak_raw <= 1e-6:
            try:
                peak_raw = max(float(v) for v in levels) if levels else 0.0
            except Exception:
                peak_raw = 0.0
            try:
                self._vu_profile_cache[cue.id] = (int(len(levels)), float(peak_raw), list(levels))
            except Exception:
                pass
        idx = int(max(0.0, min(1.0, float(pos) / float(duration))) * (len(levels) - 1))
        # Sample a tiny window for a livelier "VU" feel.
        a = max(0, idx - 1)
        b = min(len(levels), idx + 2)
        raw = max([float(v) for v in levels[a:b]] + [0.0])

        # Approximate POST-fader behavior by applying the same gain logic used for playback normalization.
        gain_db = 0.0
        tp_limit_db = _clamp_float(getattr(self.settings, "normalize_true_peak_db", -1.0), -9.0, 0.0, -1.0)
        try:
            if bool(getattr(self.settings, "normalize_enabled", False)) and cue.loudness_i_lufs is not None:
                target_i = float(getattr(self.settings, "normalize_target_i_lufs", -14.0))
                gain_db = float(target_i) - float(cue.loudness_i_lufs)
                if cue.true_peak_db is not None:
                    gain_db = min(gain_db, float(tp_limit_db) - float(cue.true_peak_db))
                gain_db = max(-18.0, min(18.0, gain_db))
        except Exception:
            gain_db = 0.0

        # Use a relative meter based on the cue's own peak envelope, then apply gain/TP for a post-fader feel.
        rel = float(raw) / float(max(1e-6, peak_raw))
        rel = max(0.0, min(1.0, rel))
        try:
            db_rel = 20.0 * math.log10(max(1e-6, rel))  # <= 0.0
        except Exception:
            db_rel = -80.0
        db_rel = max(-80.0, min(0.0, db_rel))

        normalize_on = bool(getattr(self.settings, "normalize_enabled", False))
        top_db = float(tp_limit_db) if normalize_on else 0.0
        # Apply gain to the relative dB, then cap to top (represents limiter/ceiling).
        dbfs = min(float(top_db), float(top_db) + float(db_rel) + float(gain_db))
        dbfs = max(-80.0, min(float(top_db), dbfs))

        # DJ-style scale: focus on the visible range.
        min_db = -24.0
        max_db = float(top_db)
        if max_db <= min_db:
            max_db = -1.0
        target = (float(dbfs) - float(min_db)) / (float(max_db) - float(min_db))
        target = max(0.0, min(1.0, float(target)))

        st = self._vu_state.get(deck)
        if st is None:
            st = {"level": 0.0, "peak": 0.0, "last_t": float(time.monotonic()), "peak_hold_until": 0.0}
            self._vu_state[deck] = st

        now = float(time.monotonic())
        dt = max(0.0, min(0.25, now - float(st.get("last_t", now))))
        st["last_t"] = now

        # Attack/decay smoothing.
        cur = float(st.get("level", 0.0))
        if target >= cur:
            cur = cur + (target - cur) * min(1.0, 18.0 * dt)
        else:
            cur = max(target, cur - (2.2 * dt))
        cur = max(0.0, min(1.0, cur))
        st["level"] = cur

        # Peak hold.
        peak = float(st.get("peak", 0.0))
        hold_until = float(st.get("peak_hold_until", 0.0))
        if cur >= peak:
            peak = cur
            hold_until = now + 0.8
        elif now > hold_until:
            peak = max(cur, peak - (1.6 * dt))
        peak = max(0.0, min(1.0, peak))
        st["peak"] = peak
        st["peak_hold_until"] = hold_until

        try:
            w = int(canvas.winfo_width() or 0)
            h = int(canvas.winfo_height() or 0)
        except Exception:
            w, h = 0, 0
        if w < 20 or h < 8:
            w, h = 120, 10

        items = self._ensure_vu_items(deck, canvas)
        self._set_vu_visible(deck, canvas, True)

        # Thin LED-style bar area.
        bar_h = max(6, min(10, h))
        y0 = int((h - bar_h) / 2)
        y1 = y0 + bar_h
        canvas.coords(items["bg"], 0, y0, w, y1)

        inner_y0 = y0 + 1
        inner_y1 = y1 - 1

        # LED segments (blue -> red gradient, with a red-zone tail).
        seg_total = 24
        seg_ids = [items.get(f"s{i}") for i in range(seg_total)]
        seg_ids = [sid for sid in seg_ids if isinstance(sid, int)]
        if seg_ids:
            seg_total = len(seg_ids)

        gap = 1
        usable_w = max(1, (w - 2) - (seg_total - 1) * gap)
        seg_w = max(1, int(usable_w / seg_total))
        # If too cramped, reduce the number of visible segments.
        while seg_total > 12 and seg_w <= 2:
            seg_total -= 4
            seg_ids = seg_ids[:seg_total]
            usable_w = max(1, (w - 2) - (seg_total - 1) * gap)
            seg_w = max(1, int(usable_w / seg_total))

        lit = int(round(max(0.0, min(1.0, cur)) * seg_total))
        # Red zone: last ~6 dB (relative to the top of scale).
        red_db = float(max_db) - 6.0
        red_zone_norm = (red_db - float(min_db)) / (float(max_db) - float(min_db))
        red_zone_start = max(0, min(seg_total - 1, int(seg_total * max(0.0, min(1.0, red_zone_norm)))))
        base_off = "#2a2a2a"
        blue = (0x4A, 0x9E, 0xFF)  # matches waveform blue
        red = (0xFF, 0x17, 0x44)

        def _mix(c1, c2, t: float) -> str:
            t = max(0.0, min(1.0, float(t)))
            r = int(c1[0] + (c2[0] - c1[0]) * t)
            g = int(c1[1] + (c2[1] - c1[1]) * t)
            b = int(c1[2] + (c2[2] - c1[2]) * t)
            return f"#{r:02x}{g:02x}{b:02x}"

        for i, sid in enumerate(seg_ids):
            x0 = 1 + i * (seg_w + gap)
            x1 = min(w - 1, x0 + seg_w)
            canvas.coords(sid, x0, inner_y0, x1, inner_y1)
            if i < lit:
                t = 0.0 if seg_total <= 1 else float(i) / float(seg_total - 1)
                if i >= red_zone_start:
                    # Force a stronger red in the last zone.
                    t = max(t, 0.85)
                canvas.itemconfigure(sid, fill=_mix(blue, red, t), state="normal")
            else:
                canvas.itemconfigure(sid, fill=base_off, state="normal")

        # Peak marker
        px = max(1, min(w - 1, 1 + int((w - 2) * float(peak))))
        px0 = max(1, px - 1)
        px1 = min(w - 1, px + 1)
        canvas.coords(items["peak"], px0, inner_y0, px1, inner_y1)
        canvas.itemconfigure(items["peak"], state="normal")

        # dB readout (approx.; derived from precomputed envelope + normalization gain/ceiling)
        db_txt = f"{float(dbfs):>5.1f} dB"
        if self._vu_db_cache.get(deck) != db_txt:
            self._vu_db_cache[deck] = db_txt
            try:
                var = getattr(self, "var_vu_a_db", None) if deck == "A" else getattr(self, "var_vu_b_db", None)
                if var is not None:
                    var.set(db_txt)
            except Exception:
                pass

        try:
            canvas.tag_raise("vu_peak")
        except Exception:
            pass
        try:
            canvas.tag_raise("vu_bg")
            canvas.tag_raise("vu_seg")
            canvas.tag_raise("vu_peak")
        except Exception:
            pass

    def _update_waveform_playback_visuals(self) -> None:
        self._update_waveform_playback_for_deck("A", self.audio_runner)
        self._update_waveform_playback_for_deck("B", self.video_runner)

    def _ensure_playback_items(self, deck: str, canvas: tk.Canvas) -> dict[str, int]:
        items = self._playback_items.get(deck)
        if items:
            try:
                # Validate at least one item still exists.
                canvas.type(items["cursor"])
                return items
            except Exception:
                pass

        seg_bg = canvas.create_rectangle(0, 0, 0, 0, fill="#555555", outline="", tags=("playback_bg",))
        played = canvas.create_rectangle(0, 0, 0, 0, fill="#00c853", outline="", tags=("playback_bg",))
        remain = canvas.create_rectangle(0, 0, 0, 0, fill="#ffab00", outline="", tags=("playback_bg",))
        cursor = canvas.create_line(0, 0, 0, 0, fill="#ffffff", width=2, tags=("playback_fg",))
        out_line = canvas.create_line(0, 0, 0, 0, fill="#ff1744", width=3, tags=("playback_fg",))
        items = {"seg_bg": seg_bg, "played": played, "remain": remain, "cursor": cursor, "out": out_line}
        self._playback_items[deck] = items
        self._playback_visible[deck] = False
        for iid in items.values():
            try:
                canvas.itemconfigure(iid, state="hidden")
            except Exception:
                pass
        return items

    def _set_playback_visibility(self, deck: str, canvas: tk.Canvas, *, visible: bool) -> None:
        items = self._ensure_playback_items(deck, canvas)
        if bool(self._playback_visible.get(deck, False)) == bool(visible):
            return
        self._playback_visible[deck] = bool(visible)
        state = "normal" if visible else "hidden"
        for iid in items.values():
            try:
                canvas.itemconfigure(iid, state=state)
            except Exception:
                pass

    def _clear_waveform_playback(self, deck: str, canvas: tk.Canvas) -> None:
        try:
            self._set_playback_visibility(deck, canvas, visible=False)
        except Exception:
            return

    def _update_waveform_playback_for_deck(self, deck: str, runner: MediaRunner) -> None:
        try:
            if deck == "A":
                if self._selected_a < 0 or self._selected_a >= len(self._cues_a):
                    self._clear_waveform_playback("A", self.canvas_a)
                    return
                selected = self._cues_a[self._selected_a]
                canvas = self.canvas_a
            else:
                if self._selected_b < 0 or self._selected_b >= len(self._cues_b):
                    self._clear_waveform_playback("B", self.canvas_b)
                    return
                selected = self._cues_b[self._selected_b]
                canvas = self.canvas_b

            if not runner.is_playing():
                paused = None
                try:
                    paused = self._paused_state_for_deck(deck)
                except Exception:
                    paused = None
                if (
                    paused is None
                    or paused[0] != selected.id
                    or selected.kind not in ("audio", "video")
                ):
                    self._clear_waveform_playback(deck, canvas)
                    return

                duration = self._duration_for_cue(selected)
                if duration is None or duration <= 0:
                    self._clear_waveform_playback(deck, canvas)
                    return

                pos = max(0.0, min(float(duration), float(paused[1])))

                seg_start = max(0.0, float(selected.start_sec or 0.0))
                seg_end = float(selected.stop_at_sec) if selected.stop_at_sec is not None else float(duration)
                seg_end = max(seg_start, min(float(duration), seg_end))

                width = max(1, int(canvas.winfo_width() or 1))
                height = max(1, int(canvas.winfo_height() or 1))
                if width < 10 or height < 10:
                    width, height = 600, 60

                x0 = int((seg_start / float(duration)) * width)
                x1 = int((seg_end / float(duration)) * width)
                x0 = max(0, min(width, x0))
                x1 = max(0, min(width, x1))
                if x1 < x0:
                    x0, x1 = x1, x0

                px = int((pos / float(duration)) * width)
                px = max(0, min(width, px))

                items = self._ensure_playback_items(deck, canvas)
                self._set_playback_visibility(deck, canvas, visible=True)

                # Segment progress bar (bottom), without obscuring the waveform.
                bar_y0 = max(0, height - 10)
                bar_y1 = max(1, height - 2)
                if x1 - x0 >= 2:
                    played_x = max(x0, min(x1, px))
                    canvas.coords(items["seg_bg"], x0, bar_y0, x1, bar_y1)
                    canvas.itemconfigure(items["seg_bg"], fill="#555555")
                    canvas.coords(items["played"], x0, bar_y0, played_x, bar_y1)
                    canvas.itemconfigure(items["played"], fill="#00c853", state=("hidden" if played_x <= x0 else "normal"))
                    canvas.coords(items["remain"], played_x, bar_y0, x1, bar_y1)
                    canvas.itemconfigure(items["remain"], fill="#777777", state=("hidden" if x1 <= played_x else "normal"))
                else:
                    canvas.itemconfigure(items["seg_bg"], state="hidden")
                    canvas.itemconfigure(items["played"], state="hidden")
                    canvas.itemconfigure(items["remain"], state="hidden")

                # Paused cursor (blink).
                blink_on = (int(time.monotonic() * 3) % 2 == 0)
                cursor_color = "#ffab00" if blink_on else "#ffffff"
                canvas.coords(items["cursor"], px, 0, px, height)
                canvas.itemconfigure(items["cursor"], fill=cursor_color, state="normal")
                canvas.itemconfigure(items["out"], state="hidden")
                try:
                    canvas.tag_raise("playback_bg")
                    canvas.tag_raise("playback_fg")
                    canvas.tag_raise("marker")
                except Exception:
                    pass
                return

            playing = runner.current_cue()
            if playing is None or playing.kind not in ("audio", "video"):
                self._clear_waveform_playback(deck, canvas)
                return

            # Only draw playback cursor if the waveform shown belongs to the playing cue.
            if selected.id != playing.id:
                self._clear_waveform_playback(deck, canvas)
                return

            pos = runner.playback_position_sec()
            if pos is None:
                self._clear_waveform_playback(deck, canvas)
                return

            duration = self._duration_for_cue(playing)
            if duration is None or duration <= 0:
                self._clear_waveform_playback(deck, canvas)
                return

            seg_start = max(0.0, float(playing.start_sec or 0.0))
            seg_end = float(playing.stop_at_sec) if playing.stop_at_sec is not None else float(duration)
            seg_end = max(seg_start, min(float(duration), seg_end))

            width = max(1, int(canvas.winfo_width() or 1))
            height = max(1, int(canvas.winfo_height() or 1))
            if width < 10 or height < 10:
                width, height = 600, 60

            x0 = int((seg_start / float(duration)) * width)
            x1 = int((seg_end / float(duration)) * width)
            x0 = max(0, min(width, x0))
            x1 = max(0, min(width, x1))
            if x1 < x0:
                x0, x1 = x1, x0

            p = max(seg_start, min(seg_end, float(pos)))
            px = int((p / float(duration)) * width)
            px = max(0, min(width, px))

            seg_len = max(0.001, seg_end - seg_start)
            seg_pos = max(0.0, min(seg_len, float(pos) - seg_start))
            frac = max(0.0, min(1.0, seg_pos / seg_len))
            blink = frac >= 0.80
            blink_on = blink and (int(time.monotonic() * 4) % 2 == 0)

            items = self._ensure_playback_items(deck, canvas)
            self._set_playback_visibility(deck, canvas, visible=True)

            # Segment progress bar (bottom), without obscuring the waveform.
            bar_y0 = max(0, height - 10)
            bar_y1 = max(1, height - 2)
            if x1 - x0 >= 2:
                played_x = max(x0, min(x1, px))
                rem_fill = "#ff1744" if blink_on else "#ffab00"
                canvas.coords(items["seg_bg"], x0, bar_y0, x1, bar_y1)
                canvas.itemconfigure(items["seg_bg"], fill="#555555")
                canvas.coords(items["played"], x0, bar_y0, played_x, bar_y1)
                canvas.itemconfigure(items["played"], fill="#00c853", state=("hidden" if played_x <= x0 else "normal"))
                canvas.coords(items["remain"], played_x, bar_y0, x1, bar_y1)
                canvas.itemconfigure(items["remain"], fill=rem_fill, state=("hidden" if x1 <= played_x else "normal"))
            else:
                canvas.itemconfigure(items["seg_bg"], state="hidden")
                canvas.itemconfigure(items["played"], state="hidden")
                canvas.itemconfigure(items["remain"], state="hidden")

            # Playback cursor.
            cursor_color = "#ffffff" if not blink_on else "#ff1744"
            canvas.coords(items["cursor"], px, 0, px, height)
            canvas.itemconfigure(items["cursor"], fill=cursor_color, state="normal")

            # Blink the OUT position in the last 20% of the marked segment.
            if blink_on and x1 > 0:
                canvas.coords(items["out"], x1, 0, x1, height)
                canvas.itemconfigure(items["out"], state="normal")
            else:
                canvas.itemconfigure(items["out"], state="hidden")
            try:
                canvas.tag_raise("playback_bg")
                canvas.tag_raise("playback_fg")
                canvas.tag_raise("marker")
            except Exception:
                pass
        except Exception:
            return

    def _update_deck_now_playing(self, deck: str, runner, var_time) -> None:
        """Update Now Playing display for a specific deck"""
        label = getattr(self, "lbl_now_a_time", None) if deck == "A" else getattr(self, "lbl_now_b_time", None)
        default_fg = getattr(self, "_now_time_default_fg_a", None) if deck == "A" else getattr(self, "_now_time_default_fg_b", None)

        def _set_time(text: str) -> None:
            if self._now_time_cache.get(deck) == text:
                return
            try:
                var_time.set(text)
            except Exception:
                return
            self._now_time_cache[deck] = text

        def _set_fg(color: str | None) -> None:
            if label is None:
                return
            try:
                label.configure(fg=(default_fg if color is None else color))
            except Exception:
                return

        def _set_fg_cached(color: str | None) -> None:
            if self._now_fg_cache.get(deck) == color:
                return
            _set_fg(color)
            self._now_fg_cache[deck] = color

        if not runner or not runner.is_playing():
            # When a media is selected, show the marked segment length (timecode).
            cue = None
            try:
                if deck == "A" and 0 <= self._selected_a < len(self._cues_a):
                    cue = self._cues_a[self._selected_a]
                elif deck == "B" and 0 <= self._selected_b < len(self._cues_b):
                    cue = self._cues_b[self._selected_b]
            except Exception:
                cue = None

            if cue is None or cue.kind not in ("audio", "video"):
                _set_time("—")
                _set_fg_cached(None)
                return

            duration = self._duration_for_cue(cue)
            if duration is None or duration <= 0:
                _set_time("—")
                _set_fg_cached(None)
                return

            seg_start = max(0.0, float(cue.start_sec or 0.0))
            seg_end = float(cue.stop_at_sec) if cue.stop_at_sec is not None else float(duration)
            seg_end = max(seg_start, min(float(duration), seg_end))
            seg_len = max(0.0, seg_end - seg_start)
            _set_time(_format_timecode(seg_len, with_ms=True))
            _set_fg_cached(None)
            return

        # Use the runner's current cue (more reliable than selection)
        cue = None
        try:
            cue = runner.current_cue()
        except Exception:
            cue = None
        if cue is None or cue.kind == "ppt":
            _set_time("—")
            _set_fg_cached(None)
            return

        pos = None
        length = None
        try:
            pos = runner.playback_position_sec()
        except Exception:
            pos = None
        try:
            length = runner.playback_length_sec()
        except Exception:
            length = None
        if length is None:
            length = self._duration_for_cue(cue)

        # Prefer cue end markers if present
        end_for_display = cue.stop_at_sec if cue.stop_at_sec is not None else length

        if pos is None or end_for_display is None:
            _set_time("…")
            _set_fg_cached(None)
            return

        # Calculate remaining time (countdown)
        seg_start = float(cue.start_sec or 0.0)
        seg_end = float(end_for_display)
        seg_len = max(0.001, seg_end - seg_start)
        seg_pos = max(0.0, min(seg_len, float(pos) - seg_start))
        frac = max(0.0, min(1.0, seg_pos / seg_len))
        remaining = max(0.0, seg_end - float(pos))

        # During playback: show only the countdown (timecode with ms).
        _set_time(f"-{_format_timecode(remaining, with_ms=True)}")

        # Blink in the last 20% of the marked segment (match waveform logic).
        blink = frac >= 0.80
        blink_on = blink and (int(time.monotonic() * 4) % 2 == 0)
        _set_fg_cached("#ff1744" if blink_on else None)

    def _select_next_cue_for_deck(self, deck: str) -> None:
        if deck == "A":
            if self._selected_a >= 0 and self._selected_a + 1 < len(self._cues_a):
                self._selected_a += 1
                self.tree_a.selection_set(str(self._selected_a))
                self.tree_a.see(str(self._selected_a))
                self._load_cue_into_editor(self._cues_a[self._selected_a])
                self._log("Ready on next cue (Deck A).")
            return
        if self._selected_b >= 0 and self._selected_b + 1 < len(self._cues_b):
            self._selected_b += 1
            self.tree_b.selection_set(str(self._selected_b))
            self.tree_b.see(str(self._selected_b))
            self._load_cue_into_editor(self._cues_b[self._selected_b])
            self._log("Ready on next cue (Deck B).")

    def _select_next_cue(self) -> None:
        # Legacy: pick deck based on the last active runner.
        if self._active_runner == self.audio_runner:
            self._select_next_cue_for_deck("A")
        elif self._active_runner == self.video_runner:
            self._select_next_cue_for_deck("B")

    # ── File IO ─────────────────────────────────────────────────────────
    def _new_show(self) -> None:
        if (self._cues_a or self._cues_b) and not messagebox.askyesno("New", "Discard current show?"):
            return
        self._show_path = None
        self._loaded_preset_path = None
        self._cues_a = []
        self._cues_b = []
        self._selected_a = -1
        self._selected_b = -1
        self._refresh_tree_a()
        self._refresh_tree_b()
        self._load_selected_into_editor()
        self._log("New show.")
        self._update_showfile_label()

    def _load_show_from_path(self, path: Path, *, set_show_path: bool) -> None:
        data = json.loads(path.read_text(encoding="utf-8"))
        self.settings = Settings.from_dict(data.get("settings", {}))
        self.audio_runner.settings = self.settings
        self.video_runner.settings = self.settings

        # Load dual deck cues
        self._cues_a = [Cue.from_dict(x) for x in data.get("cues_a", [])]
        self._cues_b = [Cue.from_dict(x) for x in data.get("cues_b", [])]

        # Legacy support - if old format, load to deck A
        if not self._cues_a and not self._cues_b and "cues" in data:
            self._cues_a = [Cue.from_dict(x) for x in data.get("cues", [])]

        self._show_path = path if set_show_path else None

        self.var_left.set(str(self.settings.second_screen_left))
        self.var_top.set(str(self.settings.second_screen_top))
        self.var_fs.set(bool(self.settings.video_fullscreen))

        self._refresh_tree_a()
        self._refresh_tree_b()
        self._load_selected_into_editor()
        self._update_showfile_label()
        try:
            where = f"show file {path.name}" if set_show_path else f"preset {path.name}"
            self._log(f"Loaded {len(self._cues_a)} cues to Deck A, {len(self._cues_b)} cues to Deck B from {where}.")
        except Exception:
            pass

    def _open_show(self) -> None:
        path = filedialog.askopenfilename(
            title="Open show",
            filetypes=[("Show JSON", "*.json"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            self._loaded_preset_path = None
            self._load_show_from_path(Path(path), set_show_path=True)
            self._log(f"Loaded: {Path(path).name}")
        except Exception as e:
            messagebox.showerror("Open failed", str(e))
            self._log(f"Open failed: {e}")

    def _save_show(self) -> None:
        if not self._show_path:
            return self._save_show_as()
        self._write_show(self._show_path)
        self._update_showfile_label()

    def _save_show_as(self) -> None:
        path = filedialog.asksaveasfilename(
            title="Save show as",
            defaultextension=".json",
            filetypes=[("Show JSON", "*.json")],
        )
        if not path:
            return
        self._show_path = Path(path)
        self._write_show(self._show_path)
        self._update_showfile_label()

    def _update_showfile_label(self) -> None:
        if getattr(self, "var_showfile", None) is None:
            return
        if self._show_path:
            self.var_showfile.set(_shorten_middle(f"Show: {self._show_path.name}", 28))
            return
        if self._loaded_preset_path:
            self.var_showfile.set(_shorten_middle(f"Preset: {self._loaded_preset_path.name}", 28))
            return
        self.var_showfile.set("Show: (unsaved)")

    def _save_preset(self) -> None:
        path = self._preset_path()
        try:
            payload = {
                "version": 2,
                "settings": self.settings.to_dict(),
                "cues_a": [c.to_dict() for c in self._cues_a],
                "cues_b": [c.to_dict() for c in self._cues_b],
            }
            path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
            self._loaded_preset_path = path
            self._update_showfile_label()
            self._log(f"Preset saved: {path.name}")
        except Exception as e:
            messagebox.showerror("Preset save failed", str(e))
            self._log(f"Preset save failed: {e}")

    def _load_preset(self) -> None:
        path = self._preset_path()
        if not path.exists():
            messagebox.showinfo("Preset", f"No preset found at:\n{path}")
            return
        try:
            self._loaded_preset_path = path
            self._load_show_from_path(path, set_show_path=False)
            self._log(f"Preset loaded: {path.name}")
        except Exception as e:
            messagebox.showerror("Preset load failed", str(e))
            self._log(f"Preset load failed: {e}")

    def _auto_load_preset(self) -> bool:
        path = self._preset_path()
        if not path.exists():
            self._update_showfile_label()
            return False
        try:
            self._loaded_preset_path = path
            self._load_show_from_path(path, set_show_path=False)
            self._log(f"Preset auto-loaded: {path.name}")
            return True
        except Exception:
            # Keep startup resilient for live use.
            self._loaded_preset_path = None
            return False
        finally:
            self._update_showfile_label()

    def _waveform_click(self, event, deck: str, mark_type: str) -> None:
        """Handle waveform canvas click to set IN/OUT points with millisecond precision"""
        try:
            # Get the cue and canvas for the selected deck
            if deck == "A":
                if self._selected_a < 0 or self._selected_a >= len(self._cues_a):
                    return
                cue = self._cues_a[self._selected_a]
                canvas = self.canvas_a
            else:  # deck == "B"
                if self._selected_b < 0 or self._selected_b >= len(self._cues_b):
                    return
                cue = self._cues_b[self._selected_b]
                canvas = self.canvas_b

            if cue.kind not in ("audio", "video"):
                return

            # IN/OUT marking is only enabled when the IN/OUT tab is active.
            inout_active = False
            try:
                if deck == "A" and hasattr(self, "tabs_a") and hasattr(self, "tab_a_inout"):
                    inout_active = (self.tabs_a.select() == str(self.tab_a_inout))
                elif deck == "B" and hasattr(self, "tabs_b") and hasattr(self, "tab_b_inout"):
                    inout_active = (self.tabs_b.select() == str(self.tab_b_inout))
            except Exception:
                inout_active = False

            # Get total duration
            duration = self._duration_for_cue(cue)
            if not duration or duration <= 0:
                return

            # Calculate time from click position with millisecond precision
            canvas_width = canvas.winfo_width()
            if canvas_width <= 0:
                return

            click_x = event.x
            time_sec = (click_x / canvas_width) * duration

            # Round to millisecond precision
            time_sec = round(time_sec, 3)

            # If not on IN/OUT tab:
            # - during playback: click seeks
            # - stopped/paused: click sets a cue/playhead position (does NOT touch IN/OUT markers)
            if not inout_active:
                runner = self.audio_runner if deck == "A" else self.video_runner
                try:
                    if runner.is_playing():
                        playing = runner.current_cue()
                        if playing is not None and playing.id == cue.id and playing.kind in ("audio", "video"):
                            # Avoid seeking past OUT (would instantly stop).
                            if cue.stop_at_sec is not None and time_sec >= float(cue.stop_at_sec):
                                time_sec = max(float(cue.start_sec or 0.0), float(cue.stop_at_sec) - 0.001)
                            time_sec = max(0.0, min(float(duration), float(time_sec)))
                            self._suppress_finish[deck] = "seek"
                            runner.play_at(cue, float(time_sec), volume_override=cue.volume_percent)
                            self._active_runner = runner
                            self._log(f"Deck {deck}: Seek -> {_format_timecode(time_sec, with_ms=True)}")
                            return
                except Exception:
                    pass
                try:
                    # Store cue/playhead position for RESUME (shows on waveform when paused/stopped).
                    self._set_paused_state_for_deck(deck, (cue.id, float(time_sec)))
                    self._update_waveform_playback_visuals()
                except Exception:
                    pass
                return

            # Set the marker
            if mark_type == "IN":
                cue.start_sec = max(0.0, time_sec)
                if cue.stop_at_sec is not None and cue.stop_at_sec < cue.start_sec:
                    cue.stop_at_sec = cue.start_sec
                if deck == "A":
                    self.var_in_a.set(_format_timecode(cue.start_sec, with_ms=True))
                else:
                    self.var_in_b.set(_format_timecode(cue.start_sec, with_ms=True))
                self._log(f"Deck {deck}: Mark IN at {_format_timecode(cue.start_sec, with_ms=True)}")
            else:  # mark_type == "OUT"
                cue.stop_at_sec = max(0.0, time_sec)
                if cue.stop_at_sec < cue.start_sec:
                    cue.start_sec = cue.stop_at_sec
                if deck == "A":
                    self.var_out_a.set(_format_timecode(cue.stop_at_sec, with_ms=True) if cue.stop_at_sec else "—")
                else:
                    self.var_out_b.set(_format_timecode(cue.stop_at_sec, with_ms=True) if cue.stop_at_sec else "—")
                self._log(f"Deck {deck}: Mark OUT at {_format_timecode(cue.stop_at_sec, with_ms=True)}")

            # Update tree display
            self._update_tree_item(cue)

            # Refresh markers (fast path if waveform already exists)
            self._refresh_waveform_markers(cue, canvas, deck)

        except Exception as e:
            self._log(f"Waveform click error: {e}")

    # (waveform generation is handled by _request_waveform_generate + _apply_waveform_result)

    def _write_show(self, path: Path) -> None:
        try:
            payload = {
                "version": 2,
                "settings": self.settings.to_dict(),
                "cues_a": [c.to_dict() for c in self._cues_a],
                "cues_b": [c.to_dict() for c in self._cues_b],
            }
            path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
            self._log(f"Saved: {path.name}")
        except Exception as e:
            messagebox.showerror("Save failed", str(e))
            self._log(f"Save failed: {e}")


def main() -> None:
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
