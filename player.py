#!/usr/bin/env python3

from __future__ import annotations

import json
import datetime
import math
import os
import platform
import queue
import random
import re
import shutil
import socket
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
try:
    from screeninfo import get_monitors as _screeninfo_get_monitors  # type: ignore[reportMissingImports]
except Exception:
    _screeninfo_get_monitors = None


@dataclass(frozen=True)
class MonitorInfo:
    x: int
    y: int
    width: int
    height: int
    name: str = ""
    is_primary: bool = False


def _macos_coregraphics_monitors() -> list[MonitorInfo]:
    """Best-effort monitor enumeration on macOS without PyObjC/screeninfo (works in PyInstaller builds)."""
    if platform.system() != "Darwin":
        return []
    try:
        import ctypes
        import ctypes.util
    except Exception:
        return []

    lib = ctypes.util.find_library("CoreGraphics") or "/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics"
    try:
        coregraphics = ctypes.cdll.LoadLibrary(lib)
    except Exception:
        return []

    CGDirectDisplayID = ctypes.c_uint32
    UInt32 = ctypes.c_uint32

    class CGPoint(ctypes.Structure):
        _fields_ = [("x", ctypes.c_double), ("y", ctypes.c_double)]

    class CGSize(ctypes.Structure):
        _fields_ = [("width", ctypes.c_double), ("height", ctypes.c_double)]

    class CGRect(ctypes.Structure):
        _fields_ = [("origin", CGPoint), ("size", CGSize)]

    try:
        coregraphics.CGGetActiveDisplayList.argtypes = [UInt32, ctypes.POINTER(CGDirectDisplayID), ctypes.POINTER(UInt32)]
        coregraphics.CGGetActiveDisplayList.restype = ctypes.c_int32
        coregraphics.CGMainDisplayID.argtypes = []
        coregraphics.CGMainDisplayID.restype = CGDirectDisplayID
        coregraphics.CGDisplayBounds.argtypes = [CGDirectDisplayID]
        coregraphics.CGDisplayBounds.restype = CGRect
        # Note: we intentionally use CGDisplayBounds().size for width/height (points),
        # because it matches the coordinate system of the origin and is the correct unit for window geometry.
    except Exception:
        return []

    max_displays = 16
    active = (CGDirectDisplayID * max_displays)()
    count = UInt32(0)
    try:
        err = int(coregraphics.CGGetActiveDisplayList(UInt32(max_displays), active, ctypes.byref(count)))
    except Exception:
        return []
    if err != 0 or int(count.value) <= 0:
        return []

    try:
        main_id = int(coregraphics.CGMainDisplayID())
    except Exception:
        main_id = -1

    out: list[MonitorInfo] = []
    display_count = int(count.value)
    for idx in range(display_count):
        try:
            did = int(active[idx])
        except Exception:
            continue
        try:
            bounds = coregraphics.CGDisplayBounds(CGDirectDisplayID(did))
            left = int(round(float(bounds.origin.x)))
            top = int(round(float(bounds.origin.y)))
        except Exception:
            continue
        try:
            width = int(round(float(bounds.size.width)))
            height = int(round(float(bounds.size.height)))
        except Exception:
            continue
        is_primary = bool(did == main_id)
        name = f"Display {idx + 1}" + (" (main)" if is_primary else "")
        out.append(MonitorInfo(x=left, y=top, width=width, height=height, name=name, is_primary=is_primary))

    # Stabilize ordering: primary first, then others.
    try:
        primary = next((m for m in out if m.is_primary), None)
    except Exception:
        primary = None
    if primary is None:
        return out
    others = [m for m in out if m is not primary]
    return [primary, *others]


def get_monitors():  # type: ignore
    """Cross-platform monitor enumeration with a macOS CoreGraphics fallback."""
    # On macOS, prefer CoreGraphics: screeninfo (AppKit) can be incomplete/flaky in some extended setups
    # and in PyInstaller builds.
    if platform.system() == "Darwin":
        try:
            mons = _macos_coregraphics_monitors()
            if mons:
                return mons
        except Exception:
            pass
    if _screeninfo_get_monitors is not None:
        try:
            mons = _screeninfo_get_monitors()
            if mons:
                return mons
        except Exception:
            pass
    if platform.system() == "Darwin":
        try:
            return _macos_coregraphics_monitors()
        except Exception:
            return []
    return []


def _monitor_area(mon) -> int:
    try:
        w = int(getattr(mon, "width", 0))
        h = int(getattr(mon, "height", 0))
        return max(0, w) * max(0, h)
    except Exception:
        return 0


def _pick_primary_monitor(monitors: list) -> object | None:
    if not monitors:
        return None
    try:
        for mon in monitors:
            if bool(getattr(mon, "is_primary", False)):
                return mon
    except Exception:
        pass
    return monitors[0]


def _pick_output_monitor(monitors: list) -> object | None:
    """Pick a likely 'projector' monitor when multiple non-primary displays exist."""
    if not monitors:
        return None
    primary = _pick_primary_monitor(monitors)
    candidates = [m for m in monitors if m is not primary]
    if not candidates:
        return primary
    # Prefer a projector-like display:
    # - aspect ratio near 16:9 (common for projectors/TVs; avoids 4:3 iPads)
    # - then larger area
    # - then closer to origin
    def _aspect_score(mon) -> float:
        try:
            w = float(int(getattr(mon, "width", 0)))
            h = float(int(getattr(mon, "height", 0)))
            if w <= 0 or h <= 0:
                return 10.0
            ratio = w / h
            # Penalize portrait-ish displays a bit.
            portrait_penalty = 1.5 if ratio < 1.05 else 0.0
            return abs(ratio - (16.0 / 9.0)) + portrait_penalty
        except Exception:
            return 10.0

    try:
        return min(
            candidates,
            key=lambda m: (
                _aspect_score(m),
                -_monitor_area(m),
                abs(int(getattr(m, "x", 0))) + abs(int(getattr(m, "y", 0))),
            ),
        )
    except Exception:
        return candidates[0]


def _find_monitor_by_origin(monitors: list, left: int, top: int) -> object | None:
    try:
        want_left = int(left)
        want_top = int(top)
    except Exception:
        want_left, want_top = 0, 0
    for mon in monitors or []:
        try:
            if int(getattr(mon, "x", 0)) == want_left and int(getattr(mon, "y", 0)) == want_top:
                return mon
        except Exception:
            continue
    return None


def _monitor_index(monitors: list, target: object) -> int | None:
    """Return index of `target` in `monitors` by matching geometry."""
    if not monitors or target is None:
        return None
    try:
        tx = int(getattr(target, "x", 0))
        ty = int(getattr(target, "y", 0))
        tw = int(getattr(target, "width", 0))
        th = int(getattr(target, "height", 0))
    except Exception:
        return None
    for i, mon in enumerate(monitors):
        try:
            if (
                int(getattr(mon, "x", 0)) == tx
                and int(getattr(mon, "y", 0)) == ty
                and int(getattr(mon, "width", 0)) == tw
                and int(getattr(mon, "height", 0)) == th
            ):
                return int(i)
        except Exception:
            continue
    return None


def _monitor_contains_point(mon: object, px: int, py: int) -> bool:
    try:
        x = int(getattr(mon, "x", 0))
        y = int(getattr(mon, "y", 0))
        w = int(getattr(mon, "width", 0))
        h = int(getattr(mon, "height", 0))
    except Exception:
        return False
    if w <= 0 or h <= 0:
        return False
    return (x <= int(px) < (x + w)) and (y <= int(py) < (y + h))


def _pick_output_monitor_excluding(monitors: list, exclude: object | None) -> object | None:
    if not monitors:
        return None
    if exclude is None:
        return _pick_output_monitor(monitors)
    candidates = [m for m in monitors if m is not exclude]
    if not candidates:
        return _pick_output_monitor(monitors)
    # If possible, avoid the primary display too (in case `exclude` isn't primary).
    try:
        primary = _pick_primary_monitor(monitors)
    except Exception:
        primary = None
    try:
        externals = [m for m in candidates if not bool(getattr(m, "is_primary", False))]
    except Exception:
        externals = []
    if externals:
        candidates = externals
    elif primary is not None and len(candidates) > 1:
        try:
            non_primary = [m for m in candidates if m is not primary]
            if non_primary:
                candidates = non_primary
        except Exception:
            pass
    # Reuse the projector-like scoring by temporarily treating `candidates` as the full set.
    return _pick_output_monitor(candidates) or candidates[0]

# Try to import TkinterDnD2 for drag & drop support
try:
    from tkinterdnd2 import DND_FILES, TkinterDnD  # type: ignore[reportMissingImports]
    HAS_DND = True
except ImportError:
    HAS_DND = False
    TkinterDnD = tk.Tk  # Fallback to regular Tk

CueKind = Literal["audio", "video", "ppt", "image"]

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


def _mpv_bin_dir() -> Path:
    return _user_data_dir() / "tools" / "mpv" / "bin"


def _ytdlp_bin_dir() -> Path:
    return _user_data_dir() / "tools" / "ytdlp" / "bin"


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


def _resolve_mpv() -> str | None:
    tool = "mpv"
    # Prefer bundled mpv in frozen apps (PyInstaller --add-binary).
    try:
        if platform.system() == "Windows":
            bundled = _resource_path("tools", "mpv", "mpv.exe")
        else:
            bundled = _resource_path("tools", "mpv", "mpv")
        if bundled.exists():
            return str(bundled)
    except Exception:
        pass

    # Prefer user-installed/downloaded mpv in our tool dir.
    try:
        p = _mpv_bin_dir() / (_tool_exe_name("mpv"))
        if p.exists():
            return str(p)
    except Exception:
        pass

    try:
        found = shutil.which(tool)
        if found:
            return str(found)
    except Exception:
        pass
    if platform.system() == "Darwin":
        for base in (Path("/opt/homebrew/bin"), Path("/usr/local/bin")):
            try:
                p = base / tool
                if p.exists():
                    return str(p)
            except Exception:
                continue
    return None


def _resolve_ytdlp() -> str | None:
    tool = _tool_exe_name("yt-dlp")
    sysname = platform.system()

    # Prefer bundled yt-dlp in frozen apps (PyInstaller --add-binary).
    try:
        if sysname == "Windows":
            bundled = _resource_path("tools", "ytdlp", "yt-dlp.exe")
        else:
            bundled = _resource_path("tools", "ytdlp", "yt-dlp")
        if bundled.exists() and _is_probably_executable_binary(bundled):
            return str(bundled)
    except Exception:
        pass

    # Prefer user-installed/downloaded yt-dlp in our tool dir.
    try:
        p = _ytdlp_bin_dir() / tool
        if p.exists() and _is_probably_executable_binary(p):
            return str(p)
    except Exception:
        pass

    try:
        found = shutil.which(tool)
        if found:
            try:
                fp = Path(found)
                # On macOS, prefer a real binary (Mach-O). Python scripts can bind to an old system python.
                if sysname == "Darwin":
                    return str(found) if _is_probably_executable_binary(fp) else None
                return str(found)
            except Exception:
                return str(found)
    except Exception:
        pass
    return None


def _pick_playback_backend(settings: Settings) -> tuple[str, str]:
    """Return (backend, executable_path). Backend is 'mpv' or 'ffplay'."""
    pref = ""
    try:
        pref = str(getattr(settings, "playback_engine", "auto") or "").strip().lower()
    except Exception:
        pref = "auto"

    if pref == "mpv":
        mpv = _resolve_mpv()
        if not mpv:
            raise RuntimeError("mpv not found. Install mpv (recommended) or switch to ffplay.")
        return ("mpv", mpv)

    if pref == "ffplay":
        ffplay = _resolve_fftool("ffplay")
        if not ffplay:
            raise RuntimeError("ffplay not found (install FFmpeg) or switch to mpv.")
        return ("ffplay", ffplay)

    # auto
    mpv = _resolve_mpv()
    if mpv:
        return ("mpv", mpv)
    ffplay = _resolve_fftool("ffplay")
    if ffplay:
        return ("ffplay", ffplay)
    raise RuntimeError("No playback engine found. Install mpv or FFmpeg (ffplay).")


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
    tools_needed: tuple[str, ...] = ("ffmpeg", "ffplay", "ffprobe"),
    *,
    on_status: Callable[[str], None] | None = None,
    on_progress: Callable[[int, int, int, int], None] | None = None,
    cancel_event: threading.Event | None = None,
) -> None:
    sysname = platform.system()
    if sysname not in ("Windows", "Darwin"):
        raise RuntimeError("Auto-install is supported on Windows and macOS only.")

    tools = tuple(str(t).strip().lower() for t in (tools_needed or ()))
    if not tools:
        return
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


def _install_ytdlp_binary(
    *,
    on_status: Callable[[str], None] | None = None,
    on_progress: Callable[[int, int], None] | None = None,
    cancel_event: threading.Event | None = None,
) -> None:
    """Install yt-dlp into the app tool directory (standalone binary)."""
    bin_dir = _ytdlp_bin_dir()
    bin_dir.mkdir(parents=True, exist_ok=True)
    exe_name = _tool_exe_name("yt-dlp")
    dest = bin_dir / exe_name

    try:
        if dest.exists() and _is_probably_executable_binary(dest):
            return
    except Exception:
        pass

    def _status(msg: str) -> None:
        if on_status is None:
            return
        try:
            on_status(msg)
        except Exception:
            pass

    sysname = platform.system()
    # Prefer native (PyInstaller) binaries to avoid relying on a system python version.
    if sysname == "Windows":
        urls = ["https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe"]
    elif sysname == "Darwin":
        urls = ["https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp_macos"]
    else:
        urls = ["https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp"]

    with tempfile.TemporaryDirectory(prefix="sp_show_ctrl_ytdlp_") as tmp:
        tmp_dir = Path(tmp)
        errors: list[str] = []
        for attempt, url in enumerate(urls, start=1):
            tmp_path = tmp_dir / f"{exe_name}_{attempt}"
            try:
                _status("Downloading yt-dlp…")
                _download_url_to_file(url, tmp_path, on_progress=on_progress, cancel_event=cancel_event)
                _status("Installing yt-dlp…")
                try:
                    shutil.copy2(tmp_path, dest)
                except Exception:
                    shutil.copy(tmp_path, dest)
                _ensure_executable(dest)
                # Best-effort: remove quarantine on macOS to allow running downloaded binaries.
                if sysname == "Darwin":
                    try:
                        subprocess.run(
                            ["xattr", "-dr", "com.apple.quarantine", str(dest)],
                            check=False,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                        )
                    except Exception:
                        pass
                if not _is_probably_executable_binary(dest):
                    raise RuntimeError(f"Downloaded yt-dlp is not a native executable [url={url}]")
                _status("yt-dlp ready.")
                return
            except Exception as e:
                errors.append(str(e))
                continue
        raise RuntimeError("\n".join(errors[-3:]) if errors else "yt-dlp install failed")


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


def _detect_media_type(file_path: str) -> CueKind:
    """Automatically detect media type based on file extension."""
    ext = Path(file_path).suffix.lower()

    # Image extensions
    image_exts = {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.tiff', '.tif', '.webp', '.svg'}
    if ext in image_exts:
        return "image"

    # Audio extensions
    audio_exts = {'.mp3', '.wav', '.m4a', '.aac', '.flac', '.ogg', '.wma', '.aiff', '.ape'}
    if ext in audio_exts:
        return "audio"

    # Video extensions
    video_exts = {'.mp4', '.mov', '.mkv', '.avi', '.wmv', '.flv', '.webm', '.m4v', '.mpg', '.mpeg'}
    if ext in video_exts:
        return "video"

    # Presentation extensions
    ppt_exts = {'.ppt', '.pptx'}
    if ext in ppt_exts:
        return "ppt"

    # Default to video for unknown types
    return "video"


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


def _hex_to_rgb(hex_color: str) -> tuple[int, int, int] | None:
    try:
        s = str(hex_color or "").strip()
        if s.startswith("#"):
            s = s[1:]
        if len(s) != 6:
            return None
        r = int(s[0:2], 16)
        g = int(s[2:4], 16)
        b = int(s[4:6], 16)
        return r, g, b
    except Exception:
        return None


def _contrast_text_color(bg_hex: str) -> str:
    rgb = _hex_to_rgb(bg_hex)
    if rgb is None:
        return "#ffffff"
    r, g, b = rgb
    lum = (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255.0
    return "#111111" if lum > 0.62 else "#ffffff"


_SCENE_COLOR_PALETTE: list[str] = [
    "#4a90e2",  # blue
    "#26a69a",  # teal
    "#66bb6a",  # green
    "#ffa726",  # orange
    "#ef5350",  # red
    "#ab47bc",  # purple
    "#8d6e63",  # brown
    "#78909c",  # blue grey
]


def _random_scene_color(existing: list[str] | None = None) -> str:
    existing_set = set((existing or []))
    candidates = [c for c in _SCENE_COLOR_PALETTE if c not in existing_set] or list(_SCENE_COLOR_PALETTE)
    return str(random.choice(candidates))


_VIDEO_MODE_LABELS: dict[str, str] = {
    "output": "Output (2nd screen)",
    "preview": "Preview (this screen)",
    "audio_only": "Audio only (keep visual)",
}
_VIDEO_MODE_FROM_LABEL: dict[str, str] = {v: k for k, v in _VIDEO_MODE_LABELS.items()}


def _video_mode_to_label(mode: str | None) -> str:
    m = str(mode or "").strip().lower()
    return _VIDEO_MODE_LABELS.get(m, _VIDEO_MODE_LABELS["output"])


def _video_mode_from_label(label: str | None) -> str:
    s = str(label or "").strip()
    return _VIDEO_MODE_FROM_LABEL.get(s, "output")


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
    video_fullscreen: bool = False
    macos_native_fullscreen: bool = False
    # Runtime-only flag: toggled by the "Presentation" button. Not persisted.
    presentation_active: bool = False
    playback_engine: str = "auto"  # auto | mpv | ffplay
    mpv_persistent_output: bool = True
    mpv_offer_shown: bool = False
    startup_volume: int = 100
    downloads_dir: str = ""
    normalize_enabled: bool = False
    normalize_target_i_lufs: float = -14.0
    normalize_true_peak_db: float = -1.0

    def to_dict(self) -> dict:
        return {
            "second_screen_left": self.second_screen_left,
            "second_screen_top": self.second_screen_top,
            "video_fullscreen": self.video_fullscreen,
            "macos_native_fullscreen": self.macos_native_fullscreen,
            "playback_engine": self.playback_engine,
            "mpv_persistent_output": self.mpv_persistent_output,
            "mpv_offer_shown": self.mpv_offer_shown,
            "startup_volume": self.startup_volume,
            "downloads_dir": self.downloads_dir,
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
        s.macos_native_fullscreen = bool(data.get("macos_native_fullscreen", s.macos_native_fullscreen))
        try:
            engine = str(data.get("playback_engine", s.playback_engine) or "").strip().lower()
        except Exception:
            engine = str(s.playback_engine)
        if engine not in ("auto", "mpv", "ffplay"):
            engine = "auto"
        s.playback_engine = engine
        s.mpv_persistent_output = bool(data.get("mpv_persistent_output", s.mpv_persistent_output))
        s.mpv_offer_shown = bool(data.get("mpv_offer_shown", s.mpv_offer_shown))
        s.startup_volume = int(data.get("startup_volume", s.startup_volume))
        try:
            s.downloads_dir = str(data.get("downloads_dir", s.downloads_dir) or "")
        except Exception:
            s.downloads_dir = str(s.downloads_dir or "")
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
    video_mode: str = "output"  # output | preview | audio_only (video only)
    volume_percent: int | None = None
    vu_profile_q: list[int] | None = None  # 0..1000, downsampled envelope
    loudness_i_lufs: float | None = None
    true_peak_db: float | None = None
    auto_play: bool = False  # Auto-play / include in playlist

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
            "video_mode": self.video_mode,
            "volume_percent": self.volume_percent,
            "vu_profile_q": self.vu_profile_q,
            "loudness_i_lufs": self.loudness_i_lufs,
            "true_peak_db": self.true_peak_db,
            "auto_play": self.auto_play,
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
        kind = data.get("kind", "audio")
        vm = data.get("video_mode", None)
        if vm not in ("output", "preview", "audio_only"):
            vm = None
        open_on_second = bool(data.get("open_on_second_screen", True))
        if vm is None:
            vm = "output" if open_on_second else "preview"
        # Keep legacy flag consistent with the new mode for video cues.
        if kind == "video":
            open_on_second = bool(vm == "output")

        return Cue(
            id=str(data.get("id") or uuid.uuid4()),
            kind=kind,
            path=str(data.get("path", "")),
            note=str(data.get("note", "")),
            start_sec=float(data.get("start_sec", 0.0)),
            stop_at_sec=stop_val,
            fade_at_sec=fade_val,
            fade_dur_sec=float(data.get("fade_dur_sec", 5.0)),
            fade_to_percent=int(data.get("fade_to_percent", 100)),
            open_on_second_screen=open_on_second,
            video_mode=str(vm),
            volume_percent=(None if data.get("volume_percent", None) in (None, "", "null") else int(data.get("volume_percent"))),
            vu_profile_q=vu_profile_q,
            loudness_i_lufs=(None if loud_i is None else float(loud_i)),
            true_peak_db=(None if tp is None else float(tp)),
            auto_play=bool(data.get("auto_play", False)),
        )


@dataclass
class Scene:
    id: str
    name: str
    color: str = "#4a90e2"  # Default blue
    cue_ids_a: list[str] | None = None  # Cue IDs belonging to this scene (Deck A)
    cue_ids_b: list[str] | None = None  # Cue IDs belonging to this scene (Deck B)
    notes: str = ""
    auto_advance: bool = False  # Auto-advance to next scene when all cues finish

    def __post_init__(self):
        if self.cue_ids_a is None:
            self.cue_ids_a = []
        if self.cue_ids_b is None:
            self.cue_ids_b = []

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "color": self.color,
            "cue_ids_a": self.cue_ids_a,
            "cue_ids_b": self.cue_ids_b,
            "notes": self.notes,
            "auto_advance": self.auto_advance,
        }

    @staticmethod
    def from_dict(data: dict) -> "Scene":
        return Scene(
            id=str(data.get("id") or uuid.uuid4()),
            name=str(data.get("name", "Untitled Scene")),
            color=str(data.get("color", "#4a90e2")),
            cue_ids_a=list(data.get("cue_ids_a", [])),
            cue_ids_b=list(data.get("cue_ids_b", [])),
            notes=str(data.get("notes", "")),
            auto_advance=bool(data.get("auto_advance", False)),
        )


class MpvIpcSession:
    def __init__(
        self,
        mpv_exe: str,
        *,
        name: str,
        second_screen_left: int,
        second_screen_top: int,
        fullscreen: bool,
    ):
        self.mpv_exe = str(mpv_exe)
        self.name = str(name or "output")
        self.second_screen_left = int(second_screen_left)
        self.second_screen_top = int(second_screen_top)
        self.fullscreen = bool(fullscreen)
        self.native_fullscreen = False

        self._proc: subprocess.Popen | None = None
        self._sock: socket.socket | None = None
        self._pipe = None  # Windows named pipe file handle

        self._lock = threading.Lock()
        self._pending: dict[int, "queue.Queue[dict]"] = {}
        self._next_id: int = 1
        self._playing: bool = False
        self._end_info: tuple[str | None, str | None] | None = None

        self._reader_thread: threading.Thread | None = None
        self.owner: str | None = None

        if platform.system() == "Windows":
            self.ipc_server = r"\\.\pipe\sp_show_control_mpv_" + self.name
        else:
            self.ipc_server = str(_user_data_dir() / f"mpv_ipc_{self.name}.sock")

    def is_alive(self) -> bool:
        proc = self._proc
        return bool(proc is not None and proc.poll() is None)

    def is_playing(self) -> bool:
        return bool(self._playing) and self.is_alive()

    def consume_end_info(self) -> tuple[str | None, str | None] | None:
        info = self._end_info
        self._end_info = None
        return info

    def start(self) -> None:
        if self.is_alive():
            return
        self.shutdown()

        # Remove stale unix socket file.
        if platform.system() != "Windows":
            try:
                p = Path(self.ipc_server)
                if p.exists():
                    p.unlink()
            except Exception:
                pass

        # Start windowed: user can drag the output window to the desired display, then hit "Presentation".
        geometry = "960x540+80+80"

        args = [
            self.mpv_exe,
            "--no-terminal",
            "--hwdec=auto-safe",
            "--idle=yes",
            "--force-window=yes",
            "--keep-open=yes",
            "--no-auto-window-resize",
            "--no-keepaspect-window",
            "--no-osc",
            "--no-input-default-bindings",
            "--osd-level=0",
            "--border=yes",
            "--msg-level=all=no",
            "--ontop=no",
            "--image-display-duration=inf",
            f"--title=SP Show Control Output ({self.name})",
            f"--input-ipc-server={self.ipc_server}",
            f"--geometry={geometry}",
        ]
        if platform.system() == "Darwin":
            # Avoid weird clamping when using full-display geometry on macOS.
            args.append("--macos-geometry-calculation=whole")
            # Default to pseudo-fullscreen on macOS (avoid Spaces).
            args.append("--native-fs=no")

        self._proc = subprocess.Popen(
            args,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=False,
            bufsize=0,
        )
        self._connect_ipc()

    def apply_window_placement(self) -> None:
        """Best-effort: apply windowed vs presentation fullscreen without moving the window."""
        want_fullscreen = bool(self.fullscreen)

        is_macos = bool(platform.system() == "Darwin")
        try:
            if is_macos:
                use_native = bool(want_fullscreen) and bool(getattr(self, "native_fullscreen", False))
                self.set_property_strict("native-fs", bool(use_native), timeout=0.9, retries=3)
        except Exception:
            pass
        try:
            self.set_property_strict("auto-window-resize", False, timeout=0.9, retries=3)
        except Exception:
            pass
        try:
            self.set_property_strict("keepaspect-window", False, timeout=0.9, retries=3)
        except Exception:
            pass

        if want_fullscreen:
            try:
                self.set_property_strict("fs-screen", "current", timeout=1.0, retries=3)
            except Exception:
                pass
            try:
                self.set_property_strict("border", False, timeout=0.9, retries=3)
            except Exception:
                pass
            try:
                self.set_property_strict("ontop", True, timeout=0.9, retries=3)
            except Exception:
                pass
            try:
                self.set_property_strict("fullscreen", True, timeout=1.4, retries=8)
            except Exception:
                pass
            return

        # Windowed mode
        try:
            self.set_property_strict("fullscreen", False, timeout=1.2, retries=6)
        except Exception:
            pass
        try:
            self.set_property_strict("border", True, timeout=0.9, retries=3)
        except Exception:
            pass
        try:
            self.set_property_strict("ontop", False, timeout=0.9, retries=3)
        except Exception:
            pass

    def _connect_ipc(self) -> None:
        deadline = time.monotonic() + 5.0

        if platform.system() == "Windows":
            import io

            while time.monotonic() < deadline:
                try:
                    f = open(self.ipc_server, "r+b", buffering=0)
                    self._pipe = f
                    self._pipe_text = io.TextIOWrapper(f, encoding="utf-8", errors="ignore", newline="\n")  # type: ignore[attr-defined]
                    break
                except Exception:
                    time.sleep(0.05)
            else:
                raise RuntimeError("mpv IPC pipe did not become available")

            self._reader_thread = threading.Thread(target=self._reader_loop_pipe, daemon=True)
            self._reader_thread.start()
            return

        while time.monotonic() < deadline:
            try:
                if Path(self.ipc_server).exists():
                    break
            except Exception:
                pass
            time.sleep(0.05)
        else:
            raise RuntimeError("mpv IPC socket did not become available")

        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            sock.connect(self.ipc_server)
        except Exception:
            sock.close()
            raise
        self._sock = sock
        self._sock_file = sock.makefile("r", encoding="utf-8", errors="ignore", newline="\n")  # type: ignore[attr-defined]
        self._reader_thread = threading.Thread(target=self._reader_loop_socket, daemon=True)
        self._reader_thread.start()

    def shutdown(self) -> None:
        proc = self._proc
        self._proc = None
        self._playing = False
        self._end_info = None

        try:
            if self._sock is not None:
                try:
                    self._sock.close()
                except Exception:
                    pass
        finally:
            self._sock = None
        try:
            if self._pipe is not None:
                try:
                    self._pipe.close()
                except Exception:
                    pass
        finally:
            self._pipe = None

        if proc is None:
            return
        try:
            proc.terminate()
            proc.wait(timeout=1.0)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    def _next_request_id(self) -> int:
        with self._lock:
            rid = int(self._next_id)
            self._next_id += 1
            return rid

    def _send_json(self, payload: dict) -> None:
        line = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
        if platform.system() == "Windows":
            if self._pipe is None:
                raise RuntimeError("mpv IPC pipe not connected")
            self._pipe.write(line)
            return
        if self._sock is None:
            raise RuntimeError("mpv IPC socket not connected")
        self._sock.sendall(line)

    def command(self, cmd: list[object], *, timeout: float = 0.6) -> dict:
        rid = self._next_request_id()
        q: "queue.Queue[dict]" = queue.Queue(maxsize=1)
        with self._lock:
            self._pending[rid] = q
        self._send_json({"command": cmd, "request_id": rid})
        try:
            return q.get(timeout=float(timeout))
        except Exception:
            raise TimeoutError("mpv IPC request timed out")
        finally:
            with self._lock:
                self._pending.pop(rid, None)

    @staticmethod
    def _is_success(resp: object) -> bool:
        try:
            return bool(isinstance(resp, dict) and resp.get("error") == "success")
        except Exception:
            return False

    @staticmethod
    def _parse_geometry(value: object) -> tuple[int, int, int, int] | None:
        """Parse mpv geometry strings like '960x540+80+80'."""
        try:
            s = str(value or "").strip()
        except Exception:
            return None
        m = re.match(r"^([0-9]+)x([0-9]+)([+-][0-9]+)([+-][0-9]+)$", s)
        if not m:
            return None
        try:
            w = int(m.group(1))
            h = int(m.group(2))
            x = int(m.group(3))
            y = int(m.group(4))
            return (w, h, x, y)
        except Exception:
            return None

    def set_property(self, name: str, value: object) -> bool:
        try:
            resp = self.command(["set_property", str(name), value], timeout=0.6)
        except Exception:
            return False
        return self._is_success(resp)

    def set_property_strict(self, name: str, value: object, *, timeout: float = 1.2, retries: int = 4) -> bool:
        """Set an mpv property with retries (useful for critical properties like mute/volume)."""
        for _ in range(max(1, int(retries))):
            try:
                resp = self.command(["set_property", str(name), value], timeout=float(timeout))
                if not self._is_success(resp):
                    raise RuntimeError(f"mpv set_property failed: {name}")
                return True
            except Exception:
                time.sleep(0.05)
                continue
        return False

    def get_property(self, name: str) -> object | None:
        try:
            resp = self.command(["get_property", str(name)], timeout=0.4)
            if isinstance(resp, dict) and resp.get("error") == "success":
                return resp.get("data")
        except Exception:
            return None
        return None

    def stop(self) -> None:
        try:
            self.command(["stop"], timeout=0.4)
        except Exception:
            pass
        self._playing = False
        self.owner = None

    def loadfile(
        self,
        path: str,
        *,
        start: float = 0.0,
        end: float | None = None,
        volume: int | None = None,
        af_lavfi: str | None = None,
    ) -> None:
        opts: list[str] = []
        # Prefer `end=` (absolute timestamp) for OUT segments; `length=` is not supported reliably in mpv IPC.
        if end is not None and float(end) > 0:
            end_v = float(end)
            if start and float(start) > 0:
                end_v = max(float(start) + 0.001, float(end_v))
            opts.append(f"end={end_v:.3f}")
        try:
            resp = self.command(["loadfile", str(path), "replace", *opts], timeout=1.5)
        except Exception:
            resp = {}
        # If mpv rejected options, retry without them so playback still starts.
        try:
            if isinstance(resp, dict) and resp.get("error") not in (None, "success"):
                self.command(["loadfile", str(path), "replace"], timeout=1.5)
        except Exception:
            pass
        self._playing = True

        # Enforce IN (start) via an explicit seek; `start=` loadfile options are not reliable across mpv builds.
        try:
            start_v = float(start or 0.0)
        except Exception:
            start_v = 0.0
        if start_v > 0.0:
            deadline = time.monotonic() + 0.8
            while time.monotonic() < deadline:
                try:
                    self.command(["seek", float(start_v), "absolute", "exact"], timeout=0.6)
                except Exception:
                    time.sleep(0.05)
                    continue
                # Verify we actually moved (best-effort).
                try:
                    pos = self.get_property("time-pos")
                    if pos is not None and float(pos) >= float(start_v) - 0.05:
                        break
                except Exception:
                    break
                time.sleep(0.05)
        if volume is not None:
            self.set_property("volume", _clamp_int(int(volume), 0, 100))
        if af_lavfi:
            # Best-effort; mpv accepts filter chains as strings.
            self.set_property("af", str(af_lavfi))

    def _reader_loop_socket(self) -> None:
        f = getattr(self, "_sock_file", None)
        if f is None:
            return
        try:
            for line in f:
                self._handle_ipc_line(line)
        except Exception:
            pass
        finally:
            self._playing = False

    def _reader_loop_pipe(self) -> None:
        f = getattr(self, "_pipe_text", None)
        if f is None:
            return
        try:
            while True:
                line = f.readline()
                if not line:
                    break
                self._handle_ipc_line(line)
        except Exception:
            pass
        finally:
            self._playing = False

    def _handle_ipc_line(self, line: str) -> None:
        try:
            msg = json.loads(str(line or "").strip())
        except Exception:
            return
        if not isinstance(msg, dict):
            return
        rid = msg.get("request_id")
        if isinstance(rid, int):
            with self._lock:
                q = self._pending.get(int(rid))
            if q is not None:
                try:
                    q.put_nowait(msg)
                except Exception:
                    pass
            return
        ev = msg.get("event")
        if ev == "file-loaded":
            self._playing = True
            return
        if ev == "end-file":
            self._playing = False
            try:
                reason = msg.get("reason")
                err = msg.get("error")
                self._end_info = (None if reason is None else str(reason), None if err is None else str(err))
            except Exception:
                self._end_info = (None, None)
            return


_SHARED_MPV_OUTPUT: MpvIpcSession | None = None
_SHARED_MPV_OUTPUT_LOCK = threading.Lock()


def _get_shared_mpv_output(settings: Settings) -> MpvIpcSession | None:
    """Return a singleton mpv output session (persistent window), starting it if needed."""
    if not bool(getattr(settings, "mpv_persistent_output", True)):
        return None
    mpv = _resolve_mpv()
    if mpv is None:
        return None
    with _SHARED_MPV_OUTPUT_LOCK:
        global _SHARED_MPV_OUTPUT
        sess = _SHARED_MPV_OUTPUT
        if sess is None:
            sess = MpvIpcSession(
                mpv,
                name="OUTPUT",
                second_screen_left=int(getattr(settings, "second_screen_left", 0)),
                second_screen_top=int(getattr(settings, "second_screen_top", 0)),
                fullscreen=bool(getattr(settings, "presentation_active", False)),
            )
            _SHARED_MPV_OUTPUT = sess
        try:
            sess.mpv_exe = mpv
        except Exception:
            pass
        try:
            sess.second_screen_left = int(getattr(settings, "second_screen_left", 0))
            sess.second_screen_top = int(getattr(settings, "second_screen_top", 0))
            sess.fullscreen = bool(getattr(settings, "presentation_active", False))
        except Exception:
            pass
        if not sess.is_alive():
            try:
                sess.start()
            except Exception:
                _SHARED_MPV_OUTPUT = None
                return None
        return sess


def _shutdown_shared_mpv_output() -> None:
    with _SHARED_MPV_OUTPUT_LOCK:
        global _SHARED_MPV_OUTPUT
        sess = _SHARED_MPV_OUTPUT
        _SHARED_MPV_OUTPUT = None
    if sess is None:
        return
    try:
        sess.shutdown()
    except Exception:
        pass


class OutputRunner:
    """Single visual output surface driven by mpv IPC (persistent window)."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self._sess: MpvIpcSession | None = None
        self.owner_deck: str | None = None  # "A" or "B"
        self._playing_cue: Cue | None = None
        self._paused: bool = False
        self._stop_at_sec: float | None = None
        self.last_exit_code: int | None = None
        self.last_end_reason: str | None = None

    def ensure_window(self) -> bool:
        sess = _get_shared_mpv_output(self.settings)
        if sess is None:
            self._sess = None
            return False
        self._sess = sess
        return True

    def is_playing(self) -> bool:
        sess = self._sess
        if sess is None:
            try:
                sess = _get_shared_mpv_output(self.settings)
            except Exception:
                sess = None
            self._sess = sess
        if sess is None:
            return False
        # Manual OUT enforcement (safety net if mpv options are ignored).
        try:
            if self._stop_at_sec is not None and self._playing_cue is not None and self._playing_cue.kind == "video":
                pos = sess.get_property("time-pos")
                if pos is not None and float(pos) >= float(self._stop_at_sec) - 0.001:
                    try:
                        sess.stop()
                    except Exception:
                        pass
        except Exception:
            pass
        playing = bool(sess.is_playing())
        # Fallback end detection: some mpv builds (or some files) may not emit end-file reliably via IPC.
        # For videos only, treat EOF as "not playing" so the app can auto-advance playlists.
        if playing and self._playing_cue is not None and self._playing_cue.kind == "video":
            try:
                eof = sess.get_property("eof-reached")
                if eof is True:
                    try:
                        setattr(sess, "_playing", False)
                    except Exception:
                        pass
                    playing = False
                    self.last_exit_code = 0
                    self.last_end_reason = "eof"
                    self._paused = False
                    self._stop_at_sec = None
            except Exception:
                pass
            if playing:
                try:
                    pos = sess.get_property("time-pos")
                    dur = sess.get_property("duration")
                    if pos is not None and dur is not None:
                        pos_f = float(pos)
                        dur_f = float(dur)
                        # Some files report duration late; only use it when sane.
                        if dur_f > 0.5 and pos_f >= (dur_f - 0.05):
                            try:
                                setattr(sess, "_playing", False)
                            except Exception:
                                pass
                            playing = False
                            self.last_exit_code = 0
                            self.last_end_reason = "eof"
                            self._paused = False
                            self._stop_at_sec = None
                except Exception:
                    pass
        if not playing:
            info = sess.consume_end_info()
            if info is not None:
                self.last_exit_code = 0
                try:
                    self.last_end_reason = info[0]
                except Exception:
                    self.last_end_reason = None
                self._paused = False
            self._stop_at_sec = None
        return playing

    def current_cue(self) -> Cue | None:
        return self._playing_cue

    def playback_position_sec(self) -> float | None:
        cue = self._playing_cue
        if cue is None or cue.kind != "video":
            return None
        sess = self._sess
        if sess is None:
            return None
        v = sess.get_property("time-pos")
        try:
            if v is None:
                return None
            return max(0.0, float(v))
        except Exception:
            return None

    def _build_normalize_af(self, cue: Cue) -> str | None:
        try:
            if not bool(getattr(self.settings, "normalize_enabled", False)):
                return None
            if cue.loudness_i_lufs is None:
                return None
            target_i = float(getattr(self.settings, "normalize_target_i_lufs", -14.0))
            tp_limit = float(getattr(self.settings, "normalize_true_peak_db", -1.0))
            gain_db = float(target_i) - float(cue.loudness_i_lufs)
            if cue.true_peak_db is not None:
                gain_db = min(gain_db, float(tp_limit) - float(cue.true_peak_db))
            gain_db = max(-18.0, min(18.0, gain_db))
            filters: list[str] = []
            if abs(gain_db) >= 0.05:
                filters.append(f"volume={gain_db:.2f}dB")
            if gain_db > 0.25:
                filters.append("alimiter=limit=0.97")
            if not filters:
                return None
            return "lavfi=[" + ",".join(filters) + "]"
        except Exception:
            return None

    def stop(self) -> None:
        sess = self._sess
        if sess is None:
            return
        try:
            sess.stop()
        except Exception:
            pass
        self.owner_deck = None
        self._paused = False
        self._stop_at_sec = None
        # Keep _playing_cue so the app can handle natural finish vs stop if needed.

    def pause(self) -> None:
        sess = self._sess
        if sess is None:
            return
        try:
            sess.set_property("pause", True)
            self._paused = True
        except Exception:
            pass

    def resume(self) -> None:
        sess = self._sess
        if sess is None:
            return
        try:
            sess.set_property("pause", False)
            self._paused = False
        except Exception:
            pass

    def is_paused(self) -> bool:
        return bool(self._paused)

    def seek_to(self, position_sec: float) -> None:
        sess = self._sess
        if sess is None:
            return
        try:
            sess.set_property("time-pos", float(max(0.0, position_sec)))
        except Exception:
            pass

    def play_for_deck(self, deck: str, cue: Cue, *, volume_override: int | None = None) -> None:
        self.play_at_for_deck(deck, cue, float(cue.start_sec), volume_override=volume_override)

    def play_at_for_deck(self, deck: str, cue: Cue, position_sec: float, *, volume_override: int | None = None) -> None:
        if cue.kind not in ("video", "image"):
            return
        if not self.ensure_window():
            raise RuntimeError("mpv output window not available")
        sess = self._sess
        if sess is None:
            raise RuntimeError("mpv output window not available")

        # Ensure the output window is visible (it may have been minimized for external apps like PPT).
        try:
            sess.set_property("window-minimized", False)
        except Exception:
            pass
        pres = bool(getattr(self.settings, "presentation_active", False))
        try:
            sess.set_property("ontop", bool(pres))
        except Exception:
            pass
        if pres:
            try:
                sess.fullscreen = True
            except Exception:
                pass
            try:
                sess.apply_window_placement()
            except Exception:
                pass

        self.owner_deck = str(deck)
        try:
            sess.owner = str(deck)
        except Exception:
            pass

        if cue.kind == "image":
            try:
                try:
                    sess.set_property_strict("mute", True)
                except Exception:
                    sess.set_property("mute", True)
            except Exception:
                pass
            self._stop_at_sec = None
            sess.loadfile(cue.path, start=0.0, end=None, volume=0, af_lavfi=None)
            self._playing_cue = cue
            self._paused = False
            self.last_exit_code = None
            return

        # VIDEO
        is_visuals = bool(str(deck) == "B")
        try:
            # VISUALS (deck B) videos are muted by default so they can play over Deck A music.
            try:
                sess.set_property_strict("mute", bool(is_visuals))
            except Exception:
                sess.set_property("mute", bool(is_visuals))
        except Exception:
            pass
        pos = max(0.0, float(position_sec))
        end_at = None
        if cue.stop_at_sec is not None and float(cue.stop_at_sec) > pos:
            end_at = float(cue.stop_at_sec)
        if is_visuals:
            vol = 0
        else:
            vol = self.settings.startup_volume if volume_override is None else int(volume_override)
        try:
            if cue.volume_percent is not None:
                vol = int(cue.volume_percent) if volume_override is None else int(volume_override)
        except Exception:
            pass
        af = self._build_normalize_af(cue)
        self._stop_at_sec = float(end_at) if end_at is not None else None
        sess.loadfile(cue.path, start=float(pos), end=end_at, volume=_clamp_int(int(vol), 0, 100), af_lavfi=af)
        try:
            sess.set_property("pause", False)
        except Exception:
            pass
        # Re-apply critical audio properties after load (some mpv builds can race during replace).
        try:
            sess.set_property_strict("mute", bool(is_visuals))
        except Exception:
            pass
        try:
            if not is_visuals:
                sess.set_property_strict("volume", _clamp_int(int(vol), 0, 100))
        except Exception:
            pass
        self._playing_cue = cue
        self._paused = False
        self.last_exit_code = None

    # Back-compat method names used by the App.
    def play(self, cue: Cue) -> None:
        self.play_for_deck("B", cue)

    def play_at(self, cue: Cue, position_sec: float, *, volume_override: int | None = None) -> None:
        self.play_at_for_deck("B", cue, float(position_sec), volume_override=volume_override)

    def restart_at(self, position_sec: float, *, volume_override: int | None = None) -> None:
        cue = self._playing_cue
        if cue is None:
            return
        self.play_at_for_deck(self.owner_deck or "B", cue, float(position_sec), volume_override=volume_override)


class MediaRunner:
    def __init__(self, settings: Settings, *, name: str = ""):
        self.settings = settings
        self.name = str(name or "")
        self._proc: subprocess.Popen | None = None
        self._playing_cue: Cue | None = None
        self._started_at_monotonic: float | None = None
        self._playing_seek_sec: float | None = None
        self._backend: str | None = None
        self.last_args: list[str] | None = None
        self.last_exit_code: int | None = None
        self.last_stderr_tail: list[str] = []

    def shutdown(self) -> None:
        self.stop()

    def is_playing(self) -> bool:
        # Image cues are considered "playing" if they have a current cue set
        if self._playing_cue and self._playing_cue.kind == "image":
            return ImageWindow._instance is not None
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
        backend = self._backend
        self._proc = None
        self._playing_cue = None
        self._started_at_monotonic = None
        self._playing_seek_sec = None
        self._backend = None
        if not proc:
            return

        # Check if process is already dead
        if proc.poll() is not None:
            # Process already exited, nothing to do
            return

        # Try graceful quit first for ffplay via stdin.
        if backend == "ffplay":
            try:
                if proc.stdin and not proc.stdin.closed:
                    proc.stdin.write("q")
                    proc.stdin.flush()
                    proc.stdin.close()
                    proc.wait(timeout=0.5)
                    return
            except (BrokenPipeError, OSError):
                pass
            except Exception:
                pass

        # If graceful quit failed, try terminate
        try:
            proc.terminate()
            proc.wait(timeout=1.0)
        except Exception:
            # Last resort: kill
            try:
                proc.kill()
            except Exception:
                pass

    def _spawn_player(self, backend: str, args: list[str]) -> subprocess.Popen:
        self.last_args = args
        self.last_exit_code = None
        self.last_stderr_tail = []

        stdin = subprocess.DEVNULL
        text = True
        if backend == "ffplay":
            stdin = subprocess.PIPE

        proc = subprocess.Popen(
            args,
            stdin=stdin,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=text,
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
            return "No player command yet."
        backend = self._backend or "unknown"
        msg = f"Backend: {backend}\n\nCommand:\n" + " ".join(_shell_quote(a) for a in args)
        if rc is not None:
            msg += f"\n\nExit code: {rc}"
        if tail:
            msg += "\n\nstderr (tail):\n" + "\n".join(tail[-30:])
        return msg

    def play(self, cue: Cue) -> None:
        if cue.kind == "ppt":
            # Close any image window when starting PPT
            ImageWindow.close_current()
            self.stop()
            ppt_open_and_start(
                cue.path,
                on_second_screen=bool(getattr(cue, "open_on_second_screen", False)),
                second_screen_left=int(getattr(self.settings, "second_screen_left", 0)),
                second_screen_top=int(getattr(self.settings, "second_screen_top", 0)),
            )
            return

        if cue.kind == "image":
            ImageWindow.show_image(
                cue.path,
                second_screen_left=int(getattr(self.settings, "second_screen_left", 0)),
                second_screen_top=int(getattr(self.settings, "second_screen_top", 0)),
            )
            self._playing_cue = cue
            self._started_at_monotonic = time.monotonic()
            self._playing_seek_sec = 0.0
            return

        # For video playback, close any image window
        if cue.kind == "video":
            ImageWindow.close_current()

        backend, exe = _pick_playback_backend(self.settings)

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
        if backend == "mpv":
            args = self._build_mpv_args(exe, cue, duration_limit=duration_limit, volume_override=vol_override)
        else:
            args = self._build_ffplay_args(exe, cue, duration_limit=duration_limit, volume_override=vol_override)
        self._backend = backend
        self._proc = self._spawn_player(backend, args)
        self._playing_cue = cue
        self._started_at_monotonic = time.monotonic()
        self._playing_seek_sec = float(cue.start_sec)

    def play_at(self, cue: Cue, position_sec: float, *, volume_override: int | None = None) -> None:
        if cue.kind == "ppt":
            # Close any image window when starting PPT
            ImageWindow.close_current()
            self.stop()
            ppt_open_and_start(
                cue.path,
                on_second_screen=bool(getattr(cue, "open_on_second_screen", False)),
                second_screen_left=int(getattr(self.settings, "second_screen_left", 0)),
                second_screen_top=int(getattr(self.settings, "second_screen_top", 0)),
            )
            return

        if cue.kind == "image":
            ImageWindow.show_image(
                cue.path,
                second_screen_left=int(getattr(self.settings, "second_screen_left", 0)),
                second_screen_top=int(getattr(self.settings, "second_screen_top", 0)),
            )
            self._playing_cue = cue
            self._started_at_monotonic = time.monotonic()
            self._playing_seek_sec = 0.0
            return

        # For video playback, close any image window
        if cue.kind == "video":
            ImageWindow.close_current()

        backend, exe = _pick_playback_backend(self.settings)

        pos = max(0.0, float(position_sec))
        if cue.stop_at_sec is not None and pos >= float(cue.stop_at_sec):
            self.stop()
            return

        duration_limit = None
        if cue.stop_at_sec is not None and float(cue.stop_at_sec) > pos:
            duration_limit = float(cue.stop_at_sec) - pos

        self.stop()
        if backend == "mpv":
            args = self._build_mpv_args(
                exe,
                cue,
                seek_override=float(pos),
                duration_limit=duration_limit,
                volume_override=volume_override,
            )
        else:
            args = self._build_ffplay_args(
                exe,
                cue,
                seek_override=float(pos),
                duration_limit=duration_limit,
                volume_override=volume_override,
            )
        self._backend = backend
        self._proc = self._spawn_player(backend, args)
        self._playing_cue = cue
        self._started_at_monotonic = time.monotonic()
        self._playing_seek_sec = float(pos)

    def playback_position_sec(self) -> float | None:
        if not self.is_playing():
            return None
        cue = self._playing_cue
        if not cue or cue.kind == "ppt" or cue.kind == "image":
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

        audio_only = bool(cue.kind == "video" and getattr(cue, "video_mode", "output") == "audio_only")

        if cue.kind == "audio":
            args += ["-nodisp"]

        if cue.kind == "video":
            if audio_only:
                # Treat the file like an audio-only source (keep visuals on the output surface).
                args += ["-nodisp"]
            else:
                has_second = False
                try:
                    has_second = len(get_monitors()) >= 2
                except Exception:
                    has_second = False
                if cue.open_on_second_screen:
                    # Use screeninfo to get second monitor coordinates
                    try:
                        monitors = list(get_monitors() or [])
                        cfg_left = int(getattr(self.settings, "second_screen_left", 0))
                        cfg_top = int(getattr(self.settings, "second_screen_top", 0))
                        second = _find_monitor_by_origin(monitors, cfg_left, cfg_top)
                        if second is None and len(monitors) >= 2:
                            second = _pick_output_monitor(monitors) or monitors[1]
                        if second is not None:
                            args += [
                                "-left",
                                str(second.x),
                                "-top",
                                str(second.y),
                                "-x",
                                str(second.width),
                                "-y",
                                str(second.height),
                            ]
                        else:
                            # No monitor info detected: fall back to configured origin with a small window.
                            args += [
                                "-left",
                                str(cfg_left),
                                "-top",
                                str(cfg_top),
                                "-x",
                                "960",
                                "-y",
                                "540",
                            ]
                    except Exception:
                        # Fallback to a small window on error
                        args += [
                            "-left",
                            str(int(getattr(self.settings, "second_screen_left", 0))),
                            "-top",
                            str(int(getattr(self.settings, "second_screen_top", 0))),
                            "-x",
                            "960",
                            "-y",
                            "540",
                        ]
                else:
                    args += ["-left", "80", "-top", "80", "-x", "960", "-y", "540"]
                if cue.open_on_second_screen and has_second and self.settings.video_fullscreen:
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

    def _build_mpv_args(
        self,
        mpv: str,
        cue: Cue,
        *,
        seek_override: float | None = None,
        audio_filter: str | None = None,
        duration_limit: float | None = None,
        volume_override: int | None = None,
    ) -> list[str]:
        vol = _clamp_int(self.settings.startup_volume if volume_override is None else volume_override, 0, 100)
        args: list[str] = [
            mpv,
            "--no-terminal",
            "--hwdec=auto-safe",
            "--keep-open=no",
            "--force-window=no",
            "--msg-level=all=no",
            f"--volume={vol}",
        ]

        seek = cue.start_sec if seek_override is None else float(seek_override)
        if seek > 0:
            args.append(f"--start={seek:.3f}")
        if duration_limit is not None and duration_limit > 0:
            args.append(f"--length={float(duration_limit):.3f}")

        audio_only = bool(cue.kind == "video" and getattr(cue, "video_mode", "output") == "audio_only")

        if cue.kind == "audio" or audio_only:
            args.append("--no-video")

        if cue.kind == "video":
            if audio_only:
                # No window/geometry for audio-only.
                pass
            else:
                has_second = False
                try:
                    has_second = len(get_monitors()) >= 2
                except Exception:
                    has_second = False
                left = 80
                top = 80
                width = 960
                height = 540
                if cue.open_on_second_screen:
                    try:
                        monitors = list(get_monitors() or [])
                        cfg_left = int(getattr(self.settings, "second_screen_left", 0))
                        cfg_top = int(getattr(self.settings, "second_screen_top", 0))
                        second = _find_monitor_by_origin(monitors, cfg_left, cfg_top)
                        if second is None and len(monitors) >= 2:
                            second = _pick_output_monitor(monitors) or monitors[1]
                        if second is not None:
                            left, top, width, height = int(second.x), int(second.y), int(second.width), int(second.height)
                        else:
                            # No monitor info detected: use the configured origin with a small window.
                            left, top, width, height = cfg_left, cfg_top, 960, 540
                    except Exception:
                        left, top, width, height = int(getattr(self.settings, "second_screen_left", 0)), int(getattr(self.settings, "second_screen_top", 0)), 960, 540
                args.append(f"--geometry={width}x{height}+{left}+{top}")
                if cue.open_on_second_screen and has_second and bool(getattr(self.settings, "video_fullscreen", False)):
                    args.append("--fs")
                args.append("--ontop")

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
            args.append("--af=lavfi=[" + ",".join(filters) + "]")

        args.append(cue.path)
        return args

    def restart_at(self, position_sec: float, *, volume_override: int | None = None) -> None:
        cue = self._playing_cue
        if cue is None or cue.kind == "ppt":
            return
        backend, exe = _pick_playback_backend(self.settings)
        pos = max(0.0, float(position_sec))
        if cue.stop_at_sec is not None and pos >= float(cue.stop_at_sec):
            self.stop()
            # Restore black background when stopping
            if hasattr(self, '_restore_black_background'):
                self.after(200, self._restore_black_background)
            return
        duration_limit = None
        if cue.stop_at_sec is not None and cue.stop_at_sec > pos:
            duration_limit = float(cue.stop_at_sec) - float(pos)

        self.stop()
        # Restore black background before starting new video
        if hasattr(self, '_restore_black_background'):
            self.after(200, self._restore_black_background)
        if backend == "mpv":
            args = self._build_mpv_args(
                exe,
                cue,
                seek_override=float(pos),
                duration_limit=duration_limit,
                volume_override=volume_override,
            )
        else:
            args = self._build_ffplay_args(
                exe,
                cue,
                seek_override=float(pos),
                duration_limit=duration_limit,
                volume_override=volume_override,
            )
        self._backend = backend
        self._proc = self._spawn_player(backend, args)
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
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=2.0)
    except Exception:
        # Avoid freezing the UI if AppleScript hangs; callers treat non-zero as best-effort failure.
        return subprocess.CompletedProcess(cmd, returncode=1, stdout="", stderr="osascript timeout or error")


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
        # NOTE: Do not move/resize PowerPoint windows here. PowerPoint is best at choosing the external display
        # and keeping the editor/presenter UI on the primary display. Window shuffling caused chaos on some setups.
        last_err: Exception | None = None
        for _ in range(5):
            try:
                time.sleep(0.7)
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
tell application "System Events"
  tell process "Microsoft PowerPoint"
    set frontmost to true
  end tell
  delay 0.05
  key code 124 -- right arrow
end tell
'''
    _osascript(script)


def ppt_prev_slide() -> None:
    if platform.system() != "Darwin":
        return
    script = r'''
tell application "System Events"
  tell process "Microsoft PowerPoint"
    set frontmost to true
  end tell
  delay 0.05
  key code 123 -- left arrow
end tell
'''
    _osascript(script)


def ppt_end_show() -> None:
    if platform.system() != "Darwin":
        return
    script = r'''
tell application "System Events"
  tell process "Microsoft PowerPoint"
    set frontmost to true
  end tell
  delay 0.05
  key code 53 -- esc
end tell
'''
    _osascript(script)


def ppt_hide_window() -> None:
    """Minimize PowerPoint after ending a show so it doesn't linger on the projector."""
    if platform.system() != "Darwin":
        return
    script = r'''
tell application "System Events"
  tell process "Microsoft PowerPoint"
    set frontmost to true
    delay 0.1
    try
      keystroke "m" using {command down} -- minimize
    end try
  end tell
end tell
'''
    _osascript(script)


def ppt_is_slideshow_active() -> bool:
    """Best-effort detection whether PowerPoint is currently in Slide Show mode (macOS)."""
    if platform.system() != "Darwin":
        return False
    # Match common slideshow window name fragments (varies by language/version).
    needles = ("slide show", "slideshow", "diavet", "present", "presentation")
    script = r'''
tell application "System Events"
  if not (exists process "Microsoft PowerPoint") then return "0"
  tell process "Microsoft PowerPoint"
    set namesList to {}
    try
      set namesList to name of windows
    end try
    repeat with n in namesList
      set t to (n as text)
      set lt to (t as lowercase)
      if lt contains "slide show" then return "1"
      if lt contains "slideshow" then return "1"
      if lt contains "diavet" then return "1"
      if lt contains "present" then return "1"
      if lt contains "presentation" then return "1"
    end repeat
  end tell
end tell
return "0"
'''
    try:
        res = _osascript(script)
        out = (res.stdout or "").strip()
        return bool(out == "1")
    except Exception:
        return False

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


class ImageWindow:
    """Fullscreen image display window for second screen."""
    _instance: "ImageWindow | None" = None

    def __init__(self, image_path: str, second_screen_left: int = 0, second_screen_top: int = 0):
        self.window = tk.Toplevel()
        self.window.title("Image Display")
        self.image_path = image_path

        # Set window to fullscreen on second screen
        self.window.attributes('-fullscreen', True)
        self.window.geometry(f"+{second_screen_left}+{second_screen_top}")
        self.window.configure(bg='black')

        # Remove window decorations
        self.window.overrideredirect(True)

        # Load and display image
        try:
            from PIL import Image, ImageTk  # type: ignore[reportMissingImports]
        except Exception:
            Image = None  # type: ignore[assignment]
            ImageTk = None  # type: ignore[assignment]

        if Image is None or ImageTk is None:
            # Fallback if PIL is not available - show error message
            error_label = tk.Label(
                self.window,
                text="PIL (Pillow) library not installed.\nCannot display images.",
                fg="white",
                bg="black",
                font=("Arial", 24),
            )
            error_label.pack(expand=True)
            return

        try:
            img = Image.open(image_path)

            # Get screen dimensions
            screen_width = self.window.winfo_screenwidth()
            screen_height = self.window.winfo_screenheight()

            # Resize image to fit screen while maintaining aspect ratio
            img_ratio = img.width / img.height
            screen_ratio = screen_width / screen_height

            if img_ratio > screen_ratio:
                # Image is wider than screen
                new_width = screen_width
                new_height = int(screen_width / img_ratio)
            else:
                # Image is taller than screen
                new_height = screen_height
                new_width = int(screen_height * img_ratio)

            img = img.resize((new_width, new_height), Image.Resampling.LANCZOS)
            self.photo = ImageTk.PhotoImage(img)

            # Create label to display image
            self.label = tk.Label(self.window, image=self.photo, bg='black')
            self.label.pack(expand=True)

            # Bind escape key to close
            self.window.bind('<Escape>', lambda e: self.close())

        except Exception as e:
            # Show error if image cannot be loaded
            error_label = tk.Label(
                self.window,
                text=f"Error loading image:\n{str(e)}",
                fg='white',
                bg='black',
                font=('Arial', 24)
            )
            error_label.pack(expand=True)

    def close(self):
        """Close the image window."""
        if self.window:
            self.window.destroy()
            self.window = None
        if ImageWindow._instance == self:
            ImageWindow._instance = None

    @classmethod
    def show_image(cls, image_path: str, second_screen_left: int = 0, second_screen_top: int = 0):
        """Show an image in fullscreen on second screen. Closes any existing image window."""
        if cls._instance:
            cls._instance.close()
        cls._instance = cls(image_path, second_screen_left, second_screen_top)
        return cls._instance

    @classmethod
    def close_current(cls):
        """Close the current image window if one exists."""
        if cls._instance:
            cls._instance.close()
            cls._instance = None


class BlackScreenWindow:
    """Fullscreen black window for second screen - stays until media is sent."""
    _instance: "BlackScreenWindow | None" = None

    def __init__(self, second_screen_left: int = 0, second_screen_top: int = 0):
        self.window = tk.Toplevel()
        self.window.title("Output Screen")

        # Set window to fullscreen on second screen
        self.window.attributes('-fullscreen', True)
        self.window.geometry(f"+{second_screen_left}+{second_screen_top}")
        self.window.configure(bg='black')

        # Remove window decorations
        self.window.overrideredirect(True)

        # Add subtle text to indicate screen is ready
        self.label = tk.Label(
            self.window,
            text="● OUTPUT READY ●",
            fg='#333333',
            bg='black',
            font=('Helvetica', 14)
        )
        self.label.pack(expand=True)

        # Bind escape key to close
        self.window.bind('<Escape>', lambda e: self.close())

    def close(self):
        """Close the black screen window."""
        if self.window:
            self.window.destroy()
            self.window = None
        if BlackScreenWindow._instance == self:
            BlackScreenWindow._instance = None

    @classmethod
    def show(cls, second_screen_left: int = 0, second_screen_top: int = 0):
        """Show black screen on second screen. Closes any existing instance."""
        if cls._instance:
            cls._instance.close()
        cls._instance = cls(second_screen_left, second_screen_top)
        return cls._instance

    @classmethod
    def close_current(cls):
        """Close the current black screen window if one exists."""
        if cls._instance:
            cls._instance.close()
            cls._instance = None


class App(TkinterDnD.Tk if HAS_DND else tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("S.P. Show Control")
        # Pick a size that always fits on the current screen (avoid opening partially off-screen).
        try:
            sw = int(self.winfo_screenwidth())
            sh = int(self.winfo_screenheight())
        except Exception:
            sw, sh = 1280, 720
        # Default target is comfortably below 1920x1080 for laptop use.
        w = int(max(980, min(1200, sw - 120)))
        h = int(max(620, min(820, sh - 140)))
        x = int(max(0, (sw - w) // 2))
        y = int(max(0, (sh - h) // 2))
        self.geometry(f"{w}x{h}+{x}+{y}")
        self.minsize(980, 620)
        self._app_icon_image: tk.PhotoImage | None = None
        try:
            icon_path = _resource_path("assets", "logo.png")
            if icon_path.exists():
                self._app_icon_image = tk.PhotoImage(file=str(icon_path))
                self.iconphoto(True, self._app_icon_image)
        except Exception:
            self._app_icon_image = None

        self.settings = Settings()
        try:
            self._load_persistent_settings()
        except Exception:
            pass
        self.audio_runner = MediaRunner(self.settings, name="A")
        self.title("S.P. Show Control")
        self.video_runner = OutputRunner(self.settings)
        self._active_runner = self.audio_runner

        self._show_path: Path | None = None
        self._loaded_preset_path: Path | None = None
        self._cues: list[Cue] = []  # Legacy - now using _cues_a and _cues_b
        self._loading_editor = False
        self._duration_cache: dict[str, float] = {}
        self._current_duration: float | None = None
        self._was_playing = False
        self._was_playing_a = False
        # Tracks the persistent mpv output window playback state (video/image).
        self._was_playing_b = False
        self._last_output_owner: str | None = None
        self._last_output_cue_id: str | None = None
        self._last_visual_cue_id: str | None = None
        self._ppt_running: bool = False
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
        self._ytdlp_thread: threading.Thread | None = None
        self._ytdlp_cancel_event: threading.Event | None = None
        self._ytdlp_proc: subprocess.Popen | None = None
        self._ytdlp_spinner_after_id: str | None = None
        self._ytdlp_spinner_phase: int = 0
        self._ytdlp_spinner_running: bool = False
        self._ytdlp_status_base: str = ""
        self._tree_resize_after: dict[str, str | None] = {"A": None, "B": None}
        self._resume_visuals_state: dict[str, object] | None = None
        self._paused_cue_id: str | None = None
        self._paused_kind: CueKind | None = None
        self._paused_pos_sec: float | None = None
        self._paused_a: tuple[str, float] | None = None
        self._paused_b: tuple[str, float] | None = None
        self._suppress_finish: dict[str, str] = {}
        self._last_seek_time: float = 0.0
        self._last_seek_deck: str = ""
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
        self._ppt_keep_on_top: bool = False
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
        self._disp_apply_after_id: str | None = None
        self._wave_req_seq: dict[str, int] = {"A": 0, "B": 0}
        self._wave_req_cue_id: dict[str, str | None] = {"A": None, "B": None}
        self._playback_items: dict[str, dict[str, int] | None] = {"A": None, "B": None}
        self._playback_visible: dict[str, bool] = {"A": False, "B": False}

        # Global display settings (2nd screen placement + fullscreen)
        self._suppress_display_var_trace = False
        self.var_left = tk.StringVar(value=str(self.settings.second_screen_left))
        self.var_top = tk.StringVar(value=str(self.settings.second_screen_top))
        for var in (self.var_left, self.var_top):
            var.trace_add("write", self._on_display_var_changed)

        self._build_ui()
        try:
            self._sync_presentation_button()
        except Exception:
            pass
        self.after(0, self._bring_to_front)
        self._poll_playback()
        self.after(0, self._startup_sequence)

    def _controller_monitor(self, monitors: list) -> object | None:
        """Best-effort: find the monitor that contains the controller (this) window."""
        if not monitors:
            return None
        try:
            self.update_idletasks()
        except Exception:
            pass
        try:
            x = int(self.winfo_rootx())
            y = int(self.winfo_rooty())
            w = int(self.winfo_width())
            h = int(self.winfo_height())
        except Exception:
            x, y, w, h = 0, 0, 0, 0
        cx = int(x + max(0, w) // 2)
        cy = int(y + max(0, h) // 2)
        for m in monitors:
            try:
                if _monitor_contains_point(m, cx, cy):
                    return m
            except Exception:
                continue
        return _pick_primary_monitor(monitors)

    def _on_display_var_changed(self, *_a) -> None:
        self._apply_settings_from_vars()

    def _set_display_vars(self, left: int, top: int, *, apply: bool) -> None:
        """Update display vars without triggering intermediate trace callbacks."""
        self._suppress_display_var_trace = True
        try:
            try:
                self.var_left.set(str(int(left)))
            except Exception:
                pass
            try:
                self.var_top.set(str(int(top)))
            except Exception:
                pass
        finally:
            self._suppress_display_var_trace = False
        if apply:
            self._apply_settings_from_vars()

    def _auto_pick_output_display(self) -> object | None:
        """Pick an output display that is NOT the controller window's display."""
        try:
            monitors = list(get_monitors() or [])
        except Exception:
            monitors = []
        if len(monitors) < 2:
            return None
        controller = self._controller_monitor(monitors)
        return _pick_output_monitor_excluding(monitors, controller)

    def _log_mpv_output_state(self, label: str = "mpv output") -> None:
        try:
            sess = _get_shared_mpv_output(self.settings)
        except Exception:
            sess = None
        if sess is None:
            return
        try:
            geo = sess.get_property("geometry")
        except Exception:
            geo = None
        try:
            fs = sess.get_property("fullscreen")
        except Exception:
            fs = None
        try:
            border = sess.get_property("border")
        except Exception:
            border = None
        try:
            ontop = sess.get_property("ontop")
        except Exception:
            ontop = None
        try:
            awr = sess.get_property("auto-window-resize")
        except Exception:
            awr = None
        try:
            kaw = sess.get_property("keepaspect-window")
        except Exception:
            kaw = None
        try:
            wm = sess.get_property("window-maximized")
        except Exception:
            wm = None
        mode = "presentation" if bool(getattr(self.settings, "presentation_active", False)) else "windowed"
        try:
            self._log(
                f"{label}: geometry={geo} fullscreen={fs} border={border} ontop={ontop} "
                f"auto-window-resize={awr} keepaspect-window={kaw} window-maximized={wm} mode={mode}"
            )
        except Exception:
            pass

    def _has_second_screen(self) -> bool:
        try:
            monitors = get_monitors()
            return bool(len(monitors) >= 2)
        except Exception:
            return False

    def _startup_sequence(self) -> None:
        def _after_deps() -> None:
            try:
                self._init_output_surface()
            except Exception:
                pass
            loaded = self._auto_load_preset()
            if not loaded:
                self._refresh_tree()
                self._load_selected_into_editor()

        self._ensure_ffmpeg_tools_async(_after_deps)

    def _init_output_surface(self) -> None:
        """Prepare the 2nd screen output surface.

        If mpv is available and enabled, keep a persistent mpv window open on the 2nd screen.
        Otherwise fall back to the Tk black background window.
        """
        started = False
        try:
            started = bool(self.video_runner.ensure_window())
        except Exception:
            started = False
        if started:
            self._log("mpv output window ready. Drag it to the projector/external display, then press PRESENTATION.")
            return
        try:
            self.after(200, self._start_black_background)
        except Exception:
            pass
        try:
            self._last_video_playing = False
            self._monitor_video_playback()
        except Exception:
            pass

    def _sync_presentation_button(self) -> None:
        v = getattr(self, "var_presentation_btn", None)
        if v is None:
            return
        try:
            if bool(getattr(self.settings, "presentation_active", False)):
                v.set("⏹ EXIT PRESENTATION")
            else:
                v.set("🎥 PRESENTATION")
        except Exception:
            pass

    def _toggle_presentation(self) -> None:
        try:
            if bool(getattr(self.settings, "presentation_active", False)):
                self._exit_presentation_mode()
            else:
                self._enter_presentation_mode()
        finally:
            try:
                self._sync_presentation_button()
            except Exception:
                pass

    def _force_mpv_fullscreen(self) -> None:
        """Force mpv output fullscreen on the currently selected display (manual workflow)."""
        try:
            self.settings.presentation_active = True
        except Exception:
            pass
        try:
            self._sync_presentation_button()
        except Exception:
            pass
        try:
            sess = _get_shared_mpv_output(self.settings)
        except Exception:
            sess = None
        if sess is None:
            try:
                messagebox.showerror("mpv", "mpv output window not available.", parent=self)
            except Exception:
                pass
            self._log("mpv fullscr: mpv output window not available.")
            return
        try:
            sess.fullscreen = True
        except Exception:
            pass
        try:
            # "Real" fullscreen on macOS (non-draggable). This can create a Space.
            sess.native_fullscreen = True
        except Exception:
            pass
        try:
            sess.apply_window_placement()
        except Exception:
            pass
        self._log("mpv fullscr: forced fullscreen (native).")
        try:
            self._log_mpv_output_state()
        except Exception:
            pass

    def _enter_presentation_mode(self) -> None:
        try:
            sess = _get_shared_mpv_output(self.settings)
        except Exception:
            sess = None
        if sess is None:
            try:
                messagebox.showerror("Presentation", "mpv output window not available.", parent=self)
            except Exception:
                pass
            self._log("PRESENTATION: mpv output window not available.")
            return

        # Remember current window origin (user dragged it to the desired display).
        try:
            geo = sess.get_property("geometry")
            parsed = MpvIpcSession._parse_geometry(geo)
            if parsed is not None:
                _w, _h, x, y = parsed
                self.settings.second_screen_left = int(x)
                self.settings.second_screen_top = int(y)
                try:
                    self._save_persistent_settings()
                except Exception:
                    pass
                try:
                    self._set_display_vars(int(x), int(y), apply=False)
                except Exception:
                    pass
        except Exception:
            pass

        try:
            self.settings.presentation_active = True
        except Exception:
            pass
        try:
            sess.fullscreen = True
        except Exception:
            pass
        try:
            # Presentation button stays in pseudo-fullscreen by default (still allows drag if needed).
            sess.native_fullscreen = False
        except Exception:
            pass
        try:
            sess.apply_window_placement()
        except Exception:
            pass
        self._log("PRESENTATION: ON (fullscreen).")
        try:
            self._log_mpv_output_state()
        except Exception:
            pass

    def _exit_presentation_mode(self) -> None:
        try:
            sess = _get_shared_mpv_output(self.settings)
        except Exception:
            sess = None
        if sess is None:
            try:
                self.settings.presentation_active = False
            except Exception:
                pass
            self._log("PRESENTATION: OFF (mpv output window not available).")
            return
        try:
            self.settings.presentation_active = False
        except Exception:
            pass
        try:
            sess.fullscreen = False
        except Exception:
            pass
        try:
            sess.apply_window_placement()
        except Exception:
            pass
        self._log("PRESENTATION: OFF (windowed).")
        try:
            self._log_mpv_output_state()
        except Exception:
            pass

    def _wave_help_text(self) -> str:
        # Keep this short-ish to avoid affecting layout.
        return "Click to seek (during playback)"

    def _start_black_background(self):
        """Start persistent black background on second screen using Tkinter Toplevel"""
        try:
            # Destroy any existing background window
            if hasattr(self, '_background_window') and self._background_window:
                try:
                    self._background_window.destroy()
                except Exception:
                    pass

            # Get monitors info
            monitors = list(get_monitors() or [])

            # Create a Toplevel window for black background
            self._background_window = tk.Toplevel(self)
            self._background_window.title("SP Show Control - Output")

            # Configure black background
            self._background_window.configure(bg='black')

            # Set fullscreen on second monitor using the working method
            if len(monitors) >= 2:
                cfg_left = int(getattr(self.settings, "second_screen_left", 0))
                cfg_top = int(getattr(self.settings, "second_screen_top", 0))
                second = _find_monitor_by_origin(monitors, cfg_left, cfg_top)
                # Prefer the largest non-primary display (projector) when unset/unknown.
                if second is None:
                    second = _pick_output_monitor(monitors) or monitors[1]
                # First set full geometry, then fullscreen (this is the working method!)
                self._background_window.geometry(f"{second.width}x{second.height}+{second.x}+{second.y}")
                self._background_window.attributes('-fullscreen', True)
                self._log(f"Black background window created on second screen: {second.name} ({second.width}x{second.height}+{second.x}+{second.y})")
            else:
                # Fallback to old method if only one monitor
                left = int(getattr(self.settings, "second_screen_left", 0))
                top = int(getattr(self.settings, "second_screen_top", 0))
                self._background_window.geometry(f"+{left}+{top}")
                self._background_window.attributes('-fullscreen', True)
                self._log(f"Black background window created on second screen at {left}, {top} (fallback mode)")

            # Remove window decorations
            self._background_window.overrideredirect(True)

            # Keep the window in the background (below other windows)
            self._background_window.attributes('-topmost', False)
            self._background_window.lower()

            # Add a subtle label
            label = tk.Label(
                self._background_window,
                text="● OUTPUT READY ●",
                fg='#222222',
                bg='black',
                font=('Helvetica', 16)
            )
            label.place(relx=0.5, rely=0.5, anchor='center')

            # Bind escape to close (for testing)
            self._background_window.bind('<Escape>', lambda e: self._background_window.destroy())

            # Periodically ensure the window stays in background
            def keep_in_background():
                if self._background_window and self._background_window.winfo_exists():
                    try:
                        self._background_window.lower()
                        self._background_window.after(500, keep_in_background)
                    except Exception:
                        pass
            self._background_window.after(500, keep_in_background)

        except Exception as e:
            self._log(f"Failed to start black background: {e}")

    def _monitor_video_playback(self):
        """Monitor video playback and restore black background when video ends"""
        try:
            # Check if video is currently playing
            video_playing = self.video_runner.is_playing() if hasattr(self, 'video_runner') else False

            # If video just stopped, ensure black background is visible
            if self._last_video_playing and not video_playing:
                self._restore_black_background()

            self._last_video_playing = video_playing

        except Exception as e:
            pass  # Silently ignore errors in monitoring

        # Schedule next check
        self.after(500, self._monitor_video_playback)

    def _restore_black_background(self):
        """Restore black background window visibility"""
        if hasattr(self, '_background_window') and self._background_window:
            try:
                # Ensure the window still exists and is visible
                if self._background_window.winfo_exists():
                    self._background_window.deiconify()
                    self._background_window.attributes('-fullscreen', True)
                    self._background_window.lift()
                    # Small delay then lower to background
                    self.after(100, lambda: self._background_window.lower() if self._background_window and self._background_window.winfo_exists() else None)
                    self._log("Black background restored after video playback")
            except Exception as e:
                self._log(f"Failed to restore black background: {e}")

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
            ppt_start_slideshow()
            self._log("PPT: started slideshow.")
        except Exception as e:
            try:
                messagebox.showerror("PPT", str(e), parent=self)
            except Exception:
                pass
            try:
                self._log(f"PPT: 2nd screen failed ({e})")
            except Exception:
                pass

    def _ppt_end(self) -> None:
        try:
            ppt_end_show()
            try:
                ppt_hide_window()
            except Exception:
                pass
        finally:
            self._ppt_running = False
            try:
                self._ppt_keep_on_top = False
                if platform.system() == "Darwin":
                    self.attributes("-topmost", False)
            except Exception:
                pass
            try:
                self.after(250, self._restore_visuals_after_ppt)
            except Exception:
                pass

    def _restore_visuals_after_ppt(self) -> None:
        # Bring mpv output back and restore whatever VISUALS was showing before PPT.
        try:
            self.video_runner.ensure_window()  # type: ignore[attr-defined]
            sess = getattr(self.video_runner, "_sess", None)
            if sess is not None:
                try:
                    sess.set_property("window-minimized", False)
                except Exception:
                    pass
                try:
                    sess.set_property("ontop", True)
                except Exception:
                    pass
                try:
                    sess.apply_window_placement()
                except Exception:
                    pass
        except Exception:
            pass
        try:
            self._resume_visuals_if_any()
        except Exception:
            pass
        try:
            self._restore_last_visual_if_any()
        except Exception:
            pass

    def _ppt_prev_ui(self) -> None:
        try:
            ppt_prev_slide()
        finally:
            try:
                self.after(80, self._bring_to_front)
            except Exception:
                pass
            try:
                self.after(450, self._ppt_post_nav_check)
            except Exception:
                pass

    def _ppt_next_ui(self) -> None:
        try:
            ppt_next_slide()
        finally:
            try:
                self.after(80, self._bring_to_front)
            except Exception:
                pass
            try:
                self.after(450, self._ppt_post_nav_check)
            except Exception:
                pass

    def _ppt_post_nav_check(self) -> None:
        # If the slideshow ended (e.g. next on last slide), restore mpv output automatically.
        try:
            if not bool(getattr(self, "_ppt_running", False)):
                return
        except Exception:
            return
        try:
            if ppt_is_slideshow_active():
                return
        except Exception:
            return
        try:
            self._ppt_running = False
        except Exception:
            pass
        try:
            self._ppt_keep_on_top = False
            if platform.system() == "Darwin":
                self.attributes("-topmost", False)
        except Exception:
            pass
        try:
            self._restore_visuals_after_ppt()
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

    # Visual PREVIEW has been removed (it did not mirror the 2nd screen output reliably).

    def _preset_path(self) -> Path:
        if _is_frozen():
            try:
                d = _user_data_dir()
                d.mkdir(parents=True, exist_ok=True)
                return d / "show_preset.json"
            except Exception:
                return Path.home() / "show_preset.json"
        return Path.cwd() / "show_preset.json"

    def _ensure_dependencies_async(self, on_ready: Callable[[], None]) -> None:
        # Decide playback backend; mpv is preferred in "auto". If missing, offer install help once.
        try:
            pref = str(getattr(self.settings, "playback_engine", "auto") or "").strip().lower()
        except Exception:
            pref = "auto"

        try:
            mpv_missing = _resolve_mpv() is None
        except Exception:
            mpv_missing = True

        try:
            offer_shown = bool(getattr(self.settings, "mpv_offer_shown", False))
        except Exception:
            offer_shown = True

        if mpv_missing and (pref in ("auto", "mpv")) and (not offer_shown):
            try:
                self.settings.mpv_offer_shown = True
                self._save_persistent_settings()
            except Exception:
                pass
            try:
                wants = messagebox.askyesno(
                    "mpv recommended",
                    "For the best and smoothest video output, mpv is recommended.\n\n"
                    "mpv is not installed on this machine.\n\n"
                    "Do you want to open the mpv install help now?",
                    parent=self,
                )
            except Exception:
                wants = False
            if wants:
                try:
                    self._install_mpv_prompt()
                except Exception:
                    pass

        if pref == "mpv" and _resolve_mpv() is None:
            # Auto-fallback to ffplay so the app remains usable without mpv installed.
            try:
                self.settings.playback_engine = "ffplay"
            except Exception:
                pass
            pref = "ffplay"
        try:
            backend, _exe = _pick_playback_backend(self.settings)
        except Exception:
            backend = "ffplay"

        required: list[str] = ["ffmpeg", "ffprobe"]
        if backend == "ffplay":
            required.append("ffplay")

        missing = [t for t in required if _resolve_fftool(t) is None]
        if not missing:
            try:
                on_ready()
            except Exception:
                return
            return

        win = tk.Toplevel(self)
        win.title("Installing dependencies")
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
            text="Installing required tools for playback, waveform and analysis…",
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
                _install_ffmpeg_tools(tuple(required), on_status=_ui_status, on_progress=_ui_progress, cancel_event=cancel_event)
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

    # Backward-compatible name used by startup sequence.
    def _ensure_ffmpeg_tools_async(self, on_ready: Callable[[], None]) -> None:
        self._ensure_dependencies_async(on_ready)

    def _apply_downloads_dir_setting(self) -> None:
        try:
            var = getattr(self, "var_download_dir", None)
            if var is None:
                return
            p = str(var.get() or "").strip()
        except Exception:
            return
        try:
            self.settings.downloads_dir = p
        except Exception:
            pass

    def _browse_download_dir(self) -> None:
        try:
            initial = str(getattr(self.settings, "downloads_dir", "") or "").strip()
            if not initial:
                initial = str(Path.home() / "Downloads")
        except Exception:
            initial = ""
        try:
            chosen = filedialog.askdirectory(title="Select download folder", initialdir=initial)
        except Exception:
            chosen = ""
        if chosen:
            try:
                self.var_download_dir.set(str(chosen))
            except Exception:
                pass

    def _start_ytdlp_spinner(self) -> None:
        try:
            if self._ytdlp_spinner_running:
                return
        except Exception:
            pass
        try:
            self._ytdlp_spinner_running = True
            self._ytdlp_spinner_phase = 0
        except Exception:
            return
        self._tick_ytdlp_spinner()

    def _stop_ytdlp_spinner(self) -> None:
        try:
            self._ytdlp_spinner_running = False
        except Exception:
            pass
        try:
            after_id = getattr(self, "_ytdlp_spinner_after_id", None)
            if after_id:
                self.after_cancel(after_id)
        except Exception:
            pass
        try:
            self._ytdlp_spinner_after_id = None
        except Exception:
            pass

    def _tick_ytdlp_spinner(self) -> None:
        try:
            if not self._ytdlp_spinner_running:
                return
        except Exception:
            return
        frames = ["|", "/", "-", "\\"]
        try:
            base = str(getattr(self, "_ytdlp_status_base", "") or "").strip()
        except Exception:
            base = ""
        if not base:
            base = "Downloading..."
        try:
            phase = int(getattr(self, "_ytdlp_spinner_phase", 0))
        except Exception:
            phase = 0
        frame = frames[phase % len(frames)]
        try:
            self._ytdlp_spinner_phase = phase + 1
        except Exception:
            pass
        try:
            var = getattr(self, "var_ytdlp_status", None)
            if var is not None:
                var.set(f"{base} {frame}")
        except Exception:
            pass
        try:
            self._ytdlp_spinner_after_id = self.after(120, self._tick_ytdlp_spinner)
        except Exception:
            self._ytdlp_spinner_after_id = None

    def _cancel_ytdlp_download(self) -> None:
        try:
            if self._ytdlp_cancel_event is not None:
                self._ytdlp_cancel_event.set()
        except Exception:
            pass
        try:
            proc = self._ytdlp_proc
            if proc is not None and proc.poll() is None:
                proc.terminate()
        except Exception:
            pass

    def _start_ytdlp_download(self) -> None:
        if self._ytdlp_thread is not None and self._ytdlp_thread.is_alive():
            return
        url = ""
        try:
            url = str(getattr(self, "var_ytdlp_url", tk.StringVar()).get() or "").strip()
        except Exception:
            url = ""
        if not url:
            try:
                messagebox.showwarning("Download", "Please paste a YouTube URL.")
            except Exception:
                pass
            return
        try:
            mode = str(getattr(self, "var_ytdlp_mode", tk.StringVar(value="av")).get() or "av").strip().lower()
        except Exception:
            mode = "av"
        if mode not in ("audio", "video", "av"):
            mode = "av"

        # Resolve output folder (default to ~/Downloads).
        try:
            out_dir_s = str(getattr(self, "var_download_dir", tk.StringVar()).get() or "").strip()
        except Exception:
            out_dir_s = ""
        if not out_dir_s:
            try:
                out_dir_s = str(getattr(self.settings, "downloads_dir", "") or "").strip()
            except Exception:
                out_dir_s = ""
        if not out_dir_s:
            out_dir_s = str(Path.home() / "Downloads")
            try:
                self.var_download_dir.set(out_dir_s)
            except Exception:
                pass
        out_dir = Path(out_dir_s).expanduser()
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            try:
                messagebox.showerror("Download", f"Cannot create folder:\n{out_dir}\n\n{e}")
            except Exception:
                pass
            return

        def _ui(
            running: bool,
            status: str,
        ) -> None:
            def _apply() -> None:
                try:
                    if hasattr(self, "btn_ytdlp_download"):
                        self.btn_ytdlp_download.configure(state=("disabled" if running else "normal"))
                    if hasattr(self, "btn_ytdlp_cancel"):
                        self.btn_ytdlp_cancel.configure(state=("normal" if running else "disabled"))
                    try:
                        self._ytdlp_status_base = str(status or "")
                    except Exception:
                        pass
                    if running:
                        try:
                            self._start_ytdlp_spinner()
                        except Exception:
                            pass
                    else:
                        try:
                            self._stop_ytdlp_spinner()
                        except Exception:
                            pass
                        try:
                            self._ytdlp_status_base = ""
                        except Exception:
                            pass
                        try:
                            if hasattr(self, "var_ytdlp_status"):
                                self.var_ytdlp_status.set(str(status or ""))
                        except Exception:
                            pass
                except Exception:
                    pass

            try:
                self._ui_tasks.put(_apply)
            except Exception:
                pass

        self._ytdlp_cancel_event = threading.Event()
        _ui(True, "Downloading...")

        def _worker() -> None:
            final_path: str | None = None
            try:
                ytdlp = _resolve_ytdlp()
                if not ytdlp:
                    # Install yt-dlp on demand.
                    def _p(done: int, total: int) -> None:
                        _ui(True, "Downloading...")

                    _install_ytdlp_binary(on_status=lambda _s: _ui(True, "Downloading..."), on_progress=_p, cancel_event=self._ytdlp_cancel_event)
                    ytdlp = _resolve_ytdlp()
                if not ytdlp:
                    raise RuntimeError("yt-dlp not available")

                tmpl = "%(title).150B [%(id)s].%(ext)s"
                args = [
                    str(ytdlp),
                    "--progress",
                    "--newline",
                    "--no-playlist",
                    "--windows-filenames",
                    "--paths",
                    str(out_dir),
                    "-o",
                    tmpl,
                    "--print",
                    "after_move:filepath",
                ]
                if mode == "audio":
                    args += ["-f", "ba/best"]
                elif mode == "video":
                    args += ["-f", "bv*"]
                else:
                    args += ["-f", "bv*+ba/b", "--merge-output-format", "mp4"]
                    ffmpeg = _resolve_fftool("ffmpeg")
                    if not ffmpeg:
                        raise RuntimeError("FFmpeg is required to merge A/V downloads. Please install FFmpeg tools first.")
                    args += ["--ffmpeg-location", str(Path(ffmpeg).parent)]
                args.append(url)

                _ui(True, "Downloading...")
                env = None
                try:
                    env = os.environ.copy()
                except Exception:
                    env = None
                # In packaged apps on macOS, HTTPS verification can fail inside subprocesses unless we
                # explicitly point to a CA bundle. Use certifi when available.
                try:
                    import certifi  # type: ignore

                    cafile = str(certifi.where() or "")
                    if cafile and env is not None:
                        env["SSL_CERT_FILE"] = cafile
                        env["REQUESTS_CA_BUNDLE"] = cafile
                except Exception:
                    pass
                proc = subprocess.Popen(
                    args,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                    env=env,
                )
                self._ytdlp_proc = proc

                tail: list[str] = []

                out = proc.stdout
                if out is not None:
                    for raw in out:
                        if self._ytdlp_cancel_event is not None and self._ytdlp_cancel_event.is_set():
                            break
                        line = str(raw or "").strip()
                        if not line:
                            continue
                        tail.append(line)
                        if len(tail) > 40:
                            tail = tail[-40:]

                        # yt-dlp --print after_move:filepath prints the final path as a plain line.
                        if ("/" in line or "\\\\" in line) and (len(line) < 1024) and (Path(line).suffix):
                            final_path = line

                try:
                    rc = proc.wait(timeout=2.0 if (self._ytdlp_cancel_event and self._ytdlp_cancel_event.is_set()) else None)
                except Exception:
                    rc = proc.poll()

                if self._ytdlp_cancel_event is not None and self._ytdlp_cancel_event.is_set():
                    try:
                        if proc.poll() is None:
                            proc.terminate()
                    except Exception:
                        pass
                    _ui(False, "Canceled.")
                    return

                if rc not in (0, None):
                    msg_tail = "\n".join(tail[-20:]) if tail else ""
                    raise RuntimeError(f"yt-dlp failed (exit={rc})" + (f"\n\nLast output:\n{msg_tail}" if msg_tail else ""))
                _ui(False, "DONE")
            except Exception as e:
                err = str(e or "").strip()
                if err:
                    err = err.splitlines()[0].strip()
                    if len(err) > 160:
                        err = err[:160].rstrip() + "…"
                _ui(False, f"FAILED: {err}" if err else "FAILED")
            finally:
                self._ytdlp_proc = None
                self._ytdlp_cancel_event = None

        self._ytdlp_thread = threading.Thread(target=_worker, daemon=True)
        self._ytdlp_thread.start()

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
                keep = bool(getattr(self, "_ppt_keep_on_top", False)) or bool(getattr(self, "_ppt_running", False))
                self.attributes("-topmost", True)
                self.update_idletasks()
                if not keep:
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
        ttk.Button(disp, text="🔍 Detect Screens", command=self._detect_screens).grid(row=0, column=4, sticky="w", padx=(14, 0))
        disp.columnconfigure(5, weight=1)

        # Playback engine selector (mpv recommended). Auto chooses mpv if available, else ffplay.
        var_engine = tk.StringVar(value=str(getattr(self.settings, "playback_engine", "auto") or "auto"))

        def _apply_engine(*_a) -> None:
            try:
                v = str(var_engine.get() or "").strip().lower()
            except Exception:
                v = "auto"
            if v not in ("auto", "mpv", "ffplay"):
                v = "auto"
            try:
                self.settings.playback_engine = v
            except Exception:
                pass

        var_engine.trace_add("write", _apply_engine)
        ttk.Label(disp, text="Playback engine:").grid(row=1, column=0, sticky="w", pady=(10, 0))
        cb = ttk.Combobox(disp, textvariable=var_engine, values=("auto", "mpv", "ffplay"), state="readonly", width=10)
        cb.grid(row=1, column=1, sticky="w", padx=(8, 0), pady=(10, 0))
        try:
            cb.configure(takefocus=0)
        except Exception:
            pass
        ttk.Button(disp, text="Install mpv…", command=self._install_mpv_prompt).grid(
            row=1, column=2, sticky="w", padx=(14, 0), pady=(10, 0)
        )

        var_mpv_persist = tk.BooleanVar(value=bool(getattr(self.settings, "mpv_persistent_output", True)))

        def _apply_mpv_persist(*_a) -> None:
            try:
                self.settings.mpv_persistent_output = bool(var_mpv_persist.get())
            except Exception:
                return
            # Best-effort: if enabled, start the persistent output window now.
            if bool(getattr(self.settings, "mpv_persistent_output", True)):
                try:
                    self.video_runner.ensure_window()
                except Exception:
                    pass

        var_mpv_persist.trace_add("write", _apply_mpv_persist)
        ttk.Checkbutton(
            disp,
            text="Keep mpv output window open (2nd screen)",
            variable=var_mpv_persist,
        ).grid(row=2, column=0, columnspan=4, sticky="w", pady=(8, 0))

        ttk.Separator(tab_display, orient="horizontal").pack(fill="x", pady=(6, 0))
        ttk.Label(
            tab_display,
            text="Applies to video playback target on the second screen. Use 'Detect Screens' to auto-detect iPad or external displays.",
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
        root = ttk.Frame(self, padding=6)
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
        self.var_presentation_btn = tk.StringVar(value="🎥 PRESENTATION")
        ttk.Button(filebar, text="⚙", width=3, command=self._open_global_setup).pack(side="right", padx=(6, 0))
        ttk.Button(filebar, text="⛶ mpv native fs", command=self._force_mpv_fullscreen).pack(side="right", padx=(6, 0))
        ttk.Button(filebar, textvariable=self.var_presentation_btn, command=self._toggle_presentation).pack(side="right")
        self.lbl_showfile = ttk.Label(filebar, textvariable=self.var_showfile, anchor="e", width=28)
        self.lbl_showfile.pack(side="right")

        # MAIN LAYOUT: 3 columns - Scenes | Deck A | Deck B
        decks_container = ttk.Frame(root)
        decks_container.pack(fill="both", expand=True, pady=(0, 6))
        decks_container.columnconfigure(0, weight=0, minsize=220)  # Scenes column (fixed width)
        decks_container.columnconfigure(1, weight=1, uniform="decks")  # Deck A
        decks_container.columnconfigure(2, weight=1, uniform="decks")  # Deck B
        decks_container.rowconfigure(0, weight=1)

        # SCENE PANEL (left column)
        scene_panel = ttk.LabelFrame(decks_container, text="SCENES", padding=4)
        scene_panel.grid(row=0, column=0, sticky="nsew", padx=(0, 3))

        # Scene listbox
        scene_list_frame = ttk.Frame(scene_panel)
        scene_list_frame.pack(fill="both", expand=True)

        scene_scrollbar = ttk.Scrollbar(scene_list_frame)
        scene_scrollbar.pack(side="right", fill="y")

        self.scene_listbox = tk.Listbox(
            scene_list_frame,
            yscrollcommand=scene_scrollbar.set,
            selectmode="browse",
            font=("Helvetica", 11),
            bg="#2b2b2b",
            fg="#ffffff",
            selectbackground="#4a90e2",
            selectforeground="#ffffff",
            relief="flat",
            highlightthickness=0,
            # Critical: keep the selection even when focus moves to other widgets
            # (otherwise <<ListboxSelect>> can fire with empty selection and clear the decks).
            exportselection=False,
        )
        self.scene_listbox.pack(side="left", fill="both", expand=True)
        scene_scrollbar.config(command=self.scene_listbox.yview)
        self.scene_listbox.bind("<<ListboxSelect>>", lambda _e: self._on_scene_select())
        self.scene_listbox.bind("<Double-1>", lambda _e: self._activate_scene())

        # Scene control buttons
        scene_btn = ttk.Frame(scene_panel, padding=(0, 4, 0, 0))
        scene_btn.pack(fill="x")
        ttk.Button(scene_btn, text="+ Scene", command=self._add_scene).pack(fill="x", pady=1)
        ttk.Button(scene_btn, text="Edit", command=self._edit_scene).pack(fill="x", pady=1)
        ttk.Button(scene_btn, text="Remove", command=self._remove_scene).pack(fill="x", pady=1)
        ttk.Button(scene_btn, text="▲", command=lambda: self._move_scene(-1)).pack(fill="x", pady=1)
        ttk.Button(scene_btn, text="▼", command=lambda: self._move_scene(1)).pack(fill="x", pady=1)

        # Scene navigation buttons (big ones)
        scene_nav = ttk.Frame(scene_panel, padding=(0, 6, 0, 0))
        scene_nav.pack(fill="x")
        ttk.Button(scene_nav, text="◀ PREV", command=self._prev_scene).pack(fill="x", pady=2)
        ttk.Button(scene_nav, text="▶ NEXT", command=self._next_scene).pack(fill="x", pady=2)

        # MEDIA (middle column): audio/video
        deck_a = ttk.LabelFrame(decks_container, text="MEDIA", padding=4)
        deck_a.grid(row=0, column=1, sticky="nsew", padx=(0, 3))

        self.tree_a = ttk.Treeview(
            deck_a,
            columns=("checkbox", "auto", "idx", "kind", "name", "duration"),
            show="headings",
            selectmode="browse",
        )
        self.tree_a.heading("checkbox", text="☐")
        self.tree_a.heading("auto", text="▶")
        self.tree_a.heading("idx", text="#")
        self.tree_a.heading("kind", text="Type")
        self.tree_a.heading("name", text="File")
        self.tree_a.heading("duration", text="Duration")
        self.tree_a.column("checkbox", width=0, minwidth=0, stretch=False, anchor="center")  # Hidden by default
        self.tree_a.column("auto", width=30, minwidth=30, stretch=False, anchor="center")
        self.tree_a.column("idx", width=30, minwidth=30, stretch=False, anchor="e")
        self.tree_a.column("kind", width=55, minwidth=55, stretch=False)
        self.tree_a.column("name", width=380, minwidth=120, stretch=True)
        self.tree_a.column("duration", width=86, minwidth=78, stretch=False, anchor="e")
        self.tree_a.pack(fill="both", expand=True)
        try:
            self.tree_a.tag_configure("playing", background="#2e7d32", foreground="#ffffff")
        except Exception:
            pass
        self.tree_a.bind("<<TreeviewSelect>>", lambda _e: self._on_deck_a_select())
        self.tree_a.bind("<Double-1>", lambda _e: self._play_deck_a())
        self.tree_a.bind("<Button-1>", self._on_tree_a_click)  # checkbox/auto toggles
        self.tree_a.bind("<Configure>", lambda _e: self._schedule_tree_column_layout("A"))
        # Enable drag & drop if TkinterDnD2 is available
        if HAS_DND:
            self.tree_a.drop_target_register(DND_FILES)
            self.tree_a.dnd_bind('<<Drop>>', lambda e: self._on_drop_deck_a(e.data))

        # Total duration summary for Deck A
        self.var_total_duration_a = tk.StringVar(value="Total: 00:00:00")
        ttk.Label(deck_a, textvariable=self.var_total_duration_a, font=("Courier", 10, "bold"), anchor="e").pack(fill="x", padx=2, pady=(2, 0))

        btn_a = ttk.Frame(deck_a, padding=(0, 4, 0, 0))
        btn_a.pack(fill="x")
        for i in range(2):
            btn_a.columnconfigure(i, weight=1, uniform="cuebtn_a_main")
        btn_a.columnconfigure(2, weight=0, minsize=36)
        btn_a.columnconfigure(3, weight=0, minsize=36)
        ttk.Button(btn_a, text="+ ADD MEDIA", command=self._add_media_a).grid(row=0, column=0, sticky="ew", padx=2)
        ttk.Button(btn_a, text="Remove", command=self._remove_a).grid(row=0, column=1, sticky="ew", padx=2)
        ttk.Button(btn_a, text="▲", width=2, command=lambda: self._move_a(-1)).grid(row=0, column=2, sticky="ew", padx=2)
        ttk.Button(btn_a, text="▼", width=2, command=lambda: self._move_a(1)).grid(row=0, column=3, sticky="ew", padx=2)

        # Waveform placeholder (Deck A)
        self.wave_a_frame = ttk.LabelFrame(deck_a, text=f"Waveform - {self._wave_help_text()}", padding=2)
        self.wave_a_frame.pack(fill="x", pady=(4, 0))
        self.canvas_a = tk.Canvas(self.wave_a_frame, height=60, bg="#2b2b2b", highlightthickness=0, cursor="crosshair")
        self.canvas_a.pack(fill="x")
        self.canvas_a.bind("<Button-1>", lambda e: self._waveform_click(e, "A", "IN"))
        self.canvas_a.bind("<Button-2>", lambda e: self._waveform_click(e, "A", "OUT"))
        self.canvas_a.bind("<Button-3>", lambda e: self._waveform_click(e, "A", "OUT"))

        # Playback block (Deck A) - under preview
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
        setup_a_vol_row = ttk.Frame(setup_a)
        setup_a_vol_row.pack(fill="x")
        ttk.Label(setup_a_vol_row, text="Start volume:").pack(side="left")
        self.var_cue_vol_a = tk.IntVar(value=int(self.settings.startup_volume))
        self.var_cue_vol_a_label = tk.StringVar(value=str(int(self.settings.startup_volume)))
        self.scale_cue_vol_a = ttk.Scale(
            setup_a_vol_row,
            from_=0.0,
            to=100.0,
            orient="horizontal",
            length=140,
            command=lambda _v: self._apply_volume_setting("A"),
        )
        self.scale_cue_vol_a.pack(side="left", padx=(6, 2), fill="x", expand=True)
        self.scale_cue_vol_a.configure(variable=self.var_cue_vol_a)
        ttk.Label(setup_a_vol_row, textvariable=self.var_cue_vol_a_label, width=3).pack(side="left")

        setup_a_mode_row = ttk.Frame(setup_a)
        setup_a_mode_row.pack(fill="x", pady=(6, 0))
        ttk.Label(setup_a_mode_row, text="Video mode:").pack(side="left")
        self.var_video_mode_a = tk.StringVar(value=_video_mode_to_label("output"))
        self.cb_video_mode_a = ttk.Combobox(
            setup_a_mode_row,
            textvariable=self.var_video_mode_a,
            values=tuple(_VIDEO_MODE_LABELS.values()),
            state="readonly",
            width=22,
        )
        try:
            self.cb_video_mode_a.configure(takefocus=0)
        except Exception:
            pass
        self.cb_video_mode_a.pack(side="left", padx=(8, 0))
        self.var_video_mode_a.trace_add("write", lambda *_: self._apply_target_setting("A"))

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

        # Auto-play checkbox for this cue (Deck A)
        self.var_autoplay_a = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            self.tab_a_more,
            text="Auto-play this cue when selected as next",
            variable=self.var_autoplay_a,
            command=lambda: self._on_cue_autoplay_changed("A")
        ).pack(anchor="w", padx=10, pady=10)

        # VISUALS (right column): images/PPT
        deck_b = ttk.LabelFrame(decks_container, text="VISUALS", padding=4)
        deck_b.grid(row=0, column=2, sticky="nsew", padx=(3, 0))

        self.tree_b = ttk.Treeview(
            deck_b,
            columns=("checkbox", "auto", "idx", "kind", "name", "duration"),
            show="headings",
            selectmode="browse",
        )
        self.tree_b.heading("checkbox", text="☐")
        self.tree_b.heading("auto", text="▶")
        self.tree_b.heading("idx", text="#")
        self.tree_b.heading("kind", text="Type")
        self.tree_b.heading("name", text="File")
        self.tree_b.heading("duration", text="Duration")
        self.tree_b.column("checkbox", width=0, minwidth=0, stretch=False, anchor="center")  # Hidden by default
        self.tree_b.column("auto", width=30, minwidth=30, stretch=False, anchor="center")
        self.tree_b.column("idx", width=30, minwidth=30, stretch=False, anchor="e")
        self.tree_b.column("kind", width=55, minwidth=55, stretch=False)
        self.tree_b.column("name", width=380, minwidth=120, stretch=True)
        self.tree_b.column("duration", width=86, minwidth=78, stretch=False, anchor="e")
        self.tree_b.pack(fill="both", expand=True)
        try:
            self.tree_b.tag_configure("playing", background="#2e7d32", foreground="#ffffff")
        except Exception:
            pass
        self.tree_b.bind("<<TreeviewSelect>>", lambda _e: self._on_deck_b_select())
        self.tree_b.bind("<Double-1>", lambda _e: self._play_deck_b())
        self.tree_b.bind("<Button-1>", self._on_tree_b_click)  # checkbox/auto toggles
        self.tree_b.bind("<Configure>", lambda _e: self._schedule_tree_column_layout("B"))
        # Enable drag & drop if TkinterDnD2 is available
        if HAS_DND:
            self.tree_b.drop_target_register(DND_FILES)
            self.tree_b.dnd_bind('<<Drop>>', lambda e: self._on_drop_deck_b(e.data))

        # Total duration summary for Deck B
        self.var_total_duration_b = tk.StringVar(value="Total: 00:00:00")
        ttk.Label(deck_b, textvariable=self.var_total_duration_b, font=("Courier", 10, "bold"), anchor="e").pack(fill="x", padx=2, pady=(2, 0))

        btn_b = ttk.Frame(deck_b, padding=(0, 4, 0, 0))
        btn_b.pack(fill="x")
        for i in range(2):
            btn_b.columnconfigure(i, weight=1, uniform="cuebtn_b_main")
        btn_b.columnconfigure(2, weight=0, minsize=36)
        btn_b.columnconfigure(3, weight=0, minsize=36)
        ttk.Button(btn_b, text="+ ADD MEDIA", command=self._add_media_b).grid(row=0, column=0, sticky="ew", padx=2)
        ttk.Button(btn_b, text="Remove", command=self._remove_b).grid(row=0, column=1, sticky="ew", padx=2)
        ttk.Button(btn_b, text="▲", width=2, command=lambda: self._move_b(-1)).grid(row=0, column=2, sticky="ew", padx=2)
        ttk.Button(btn_b, text="▼", width=2, command=lambda: self._move_b(1)).grid(row=0, column=3, sticky="ew", padx=2)

        # VISUALS controls: no waveform + no IN/OUT editor on this side.
        visuals_ctrl = ttk.Frame(deck_b, padding=(0, 4, 0, 0))
        visuals_ctrl.pack(fill="x", pady=(4, 0))
        visuals_ctrl.columnconfigure(0, weight=1, uniform="visuals_ctrl")
        visuals_ctrl.columnconfigure(1, weight=1, uniform="visuals_ctrl")
        visuals_ctrl.columnconfigure(2, weight=1, uniform="visuals_ctrl")
        self.var_play_b = tk.StringVar(value="▶ SHOW")
        self.btn_play_b = self._make_transport_button(visuals_ctrl, self.var_play_b, self._play_deck_b)
        self.btn_play_b.grid(row=0, column=0, sticky="ew", padx=2)
        self.btn_stop_b = self._make_transport_button(visuals_ctrl, "⏹ CLEAR", self._stop_deck_b)
        self.btn_stop_b.grid(row=0, column=1, sticky="ew", padx=2)
        self.var_loop_b = tk.StringVar(value="⟲ LOOP OFF")
        self.btn_loop_b = self._make_transport_button(visuals_ctrl, self.var_loop_b, lambda: self._toggle_loop("B"))
        self.btn_loop_b.grid(row=0, column=2, sticky="ew", padx=2)

        # Tabs under VISUALS controls (Deck B)
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
        self.tab_b_playlist = tk.Frame(self.tabs_b, bg="#2b2b2b")
        self.tab_b_ppt = tk.Frame(self.tabs_b, bg="#2b2b2b")
        self.tab_b_download = tk.Frame(self.tabs_b, bg="#2b2b2b")
        self.tabs_b.add(self.tab_b_setup, text="Setup")
        self.tabs_b.add(self.tab_b_playlist, text="Playlist")
        self.tabs_b.add(self.tab_b_ppt, text="PPT")
        self.tabs_b.add(self.tab_b_download, text="Download")

        # Setup tab (Deck B) - per-cue options
        setup_b = ttk.Frame(self.tab_b_setup, padding=6)
        setup_b.pack(fill="x")
        self.var_autoplay_b = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            setup_b,
            text="Include this cue in auto-play playlist",
            variable=self.var_autoplay_b,
            command=lambda: self._on_cue_autoplay_changed("B"),
        ).pack(anchor="w")
        ttk.Label(
            setup_b,
            text="Visual videos are muted by default, so they can play over Deck A music.",
            padding=(0, 6, 0, 0),
        ).pack(anchor="w")

        # Playlist tab (Deck B) - multi-item auto-play behavior
        playlist_b = ttk.Frame(self.tab_b_playlist, padding=6)
        playlist_b.pack(fill="x")
        self.var_visuals_playlist_info = tk.StringVar(value="Auto-play videos: 0")
        ttk.Label(playlist_b, textvariable=self.var_visuals_playlist_info).pack(anchor="w")
        playlist_btns = ttk.Frame(playlist_b)
        playlist_btns.pack(fill="x", pady=(6, 0))
        ttk.Button(playlist_btns, text="▶ Start playlist", command=self._play_visuals_autoplay).pack(side="left")
        ttk.Button(playlist_btns, text="Clear auto-play flags", command=self._visuals_clear_autoplay_flags).pack(side="left", padx=(8, 0))
        ttk.Label(
            playlist_b,
            text="When LOOP is ON, the playlist wraps back to the first auto-play video.",
            padding=(0, 8, 0, 0),
        ).pack(anchor="w")

        # PPT tab (Deck B)
        ppt_tab = ttk.Frame(self.tab_b_ppt, padding=6)
        ppt_tab.pack(fill="x")
        ttk.Button(ppt_tab, text="▶ Start", command=self._play_selected_ppt_b, width=10).pack(fill="x")
        ttk.Button(ppt_tab, text="⛶ 2nd Screen", command=self._ppt_fullscreen_2nd_screen, width=10).pack(fill="x", pady=(2, 0))
        ppt_nav_b = ttk.Frame(ppt_tab)
        ppt_nav_b.pack(fill="x", pady=(6, 0))
        ttk.Button(ppt_nav_b, text="◀", command=self._ppt_prev_ui, width=5).pack(side="left", expand=True, fill="x")
        ttk.Button(ppt_nav_b, text="▶", command=self._ppt_next_ui, width=5).pack(side="left", expand=True, fill="x", padx=(2, 0))
        ttk.Button(ppt_tab, text="⏹ End", command=self._ppt_end, width=10).pack(fill="x", pady=(6, 0))

        # Download tab (Deck B) - yt-dlp integration
        dl_b = ttk.Frame(self.tab_b_download, padding=6)
        dl_b.pack(fill="x")

        self.var_download_dir = tk.StringVar(value=str(getattr(self.settings, "downloads_dir", "") or ""))
        dl_path_row = ttk.Frame(dl_b)
        dl_path_row.pack(fill="x")
        ttk.Label(dl_path_row, text="Save to:").pack(side="left")
        ttk.Entry(dl_path_row, textvariable=self.var_download_dir).pack(side="left", fill="x", expand=True, padx=(8, 6))
        ttk.Button(dl_path_row, text="Browse…", command=self._browse_download_dir).pack(side="left")
        self.var_download_dir.trace_add("write", lambda *_: self._apply_downloads_dir_setting())

        dl_url_row = ttk.Frame(dl_b)
        dl_url_row.pack(fill="x", pady=(8, 0))
        ttk.Label(dl_url_row, text="YouTube URL:").pack(side="left")
        self.var_ytdlp_url = tk.StringVar(value="")
        self.entry_ytdlp_url = ttk.Entry(dl_url_row, textvariable=self.var_ytdlp_url)
        self.entry_ytdlp_url.pack(side="left", fill="x", expand=True, padx=(8, 6))
        ttk.Button(dl_url_row, text="Clear", command=lambda: self.var_ytdlp_url.set(""), width=7).pack(side="left")

        dl_mode_row = ttk.Frame(dl_b)
        dl_mode_row.pack(fill="x", pady=(8, 0))
        self.var_ytdlp_mode = tk.StringVar(value="av")
        ttk.Label(dl_mode_row, text="Mode:").pack(side="left")
        ttk.Radiobutton(dl_mode_row, text="Best audio", value="audio", variable=self.var_ytdlp_mode).pack(side="left", padx=(8, 0))
        ttk.Radiobutton(dl_mode_row, text="Best video", value="video", variable=self.var_ytdlp_mode).pack(side="left", padx=(8, 0))
        ttk.Radiobutton(dl_mode_row, text="Best A/V", value="av", variable=self.var_ytdlp_mode).pack(side="left", padx=(8, 0))

        dl_btn_row = ttk.Frame(dl_b)
        dl_btn_row.pack(fill="x", pady=(8, 0))
        self.btn_ytdlp_download = ttk.Button(dl_btn_row, text="Download", command=self._start_ytdlp_download)
        self.btn_ytdlp_download.pack(side="left")
        self.btn_ytdlp_cancel = ttk.Button(dl_btn_row, text="Cancel", command=self._cancel_ytdlp_download, state="disabled")
        self.btn_ytdlp_cancel.pack(side="left", padx=(8, 0))

        self.var_ytdlp_status = tk.StringVar(value="")
        ttk.Label(dl_b, textvariable=self.var_ytdlp_status, padding=(0, 8, 0, 0)).pack(anchor="w")

        # Default states for per-cue setup controls (no selection yet).
        self._sync_target_setting_controls("A", None)
        self._sync_target_setting_controls("B", None)
        self._update_transport_button_visuals()

        # Store separate cue lists
        self._cues_a: list[Cue] = []
        self._cues_b: list[Cue] = []
        self._selected_a: int = -1
        self._selected_b: int = -1

        # Checkbox states for remove functionality
        self._checkbox_mode_a: bool = False  # Whether checkbox mode is active
        self._checkbox_mode_b: bool = False
        self._checked_cues_a: set[int] = set()  # Indices of checked cues
        self._checked_cues_b: set[int] = set()

        # Scene management
        self._scenes: list[Scene] = []
        self._selected_scene_idx: int = -1
        self._all_cues_a: list[Cue] = []  # Master list of all cues for deck A
        self._all_cues_b: list[Cue] = []  # Master list of all cues for deck B

        self._update_showfile_label()
        self._update_now_playing()
        self._log("UI ready.")

        # Auto-select first scene if available
        if self._scenes:
            self._selected_scene_idx = 0
            self.scene_listbox.selection_set(0)
            self._activate_scene()

        # Output surface (2nd screen) is initialized after dependencies are ready.
        self._background_window = None
        self._last_video_playing = False

        # Set cleanup protocol for window close
        self.protocol("WM_DELETE_WINDOW", self._on_closing)

    def _on_closing(self):
        """Clean up media players before closing the application"""
        try:
            # Stop both media runners
            if hasattr(self, 'audio_runner'):
                try:
                    self.audio_runner.shutdown()  # type: ignore[attr-defined]
                except Exception:
                    self.audio_runner.stop()
            if hasattr(self, 'video_runner'):
                try:
                    self.video_runner.shutdown()  # type: ignore[attr-defined]
                except Exception:
                    self.video_runner.stop()
            if hasattr(self, "_stop_preview"):
                self._stop_preview()
            # Close background black screen window
            if hasattr(self, '_background_window') and self._background_window:
                try:
                    self._background_window.destroy()
                    self._log("Black background window closed")
                except Exception:
                    pass
            # Close image windows
            ImageWindow.close_current()
            self._log("Shutting down...")
        except Exception as e:
            self._log(f"Cleanup error: {e}")
        finally:
            try:
                _shutdown_shared_mpv_output()
            except Exception:
                pass
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
            # Update auto-play checkbox
            self.var_autoplay_a.set(cue.auto_play)
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
            cue = self._cues_b[self._selected_b]
            try:
                self._load_cue_into_editor(cue)
            except Exception:
                pass
            try:
                var = getattr(self, "var_autoplay_b", None)
                if var is not None:
                    var.set(bool(getattr(cue, "auto_play", False)))
            except Exception:
                pass
            try:
                self._update_visuals_playlist_info()
            except Exception:
                pass
        else:
            return

    def _adjust_in(self, deck: str) -> None:
        """Fine-tune IN point from IN field"""
        if deck != "A":
            return
        try:
            if deck == "A":
                if self._selected_a < 0 or self._selected_a >= len(self._cues_a):
                    return
                cue = self._cues_a[self._selected_a]
                time_str = self.var_in_a.get()
                canvas = self.canvas_a

            # Parse timecode
            time_sec = _parse_timecode(time_str)
            if time_sec is None:
                if deck == "A":
                    self.var_in_a.set(_format_timecode(cue.start_sec, with_ms=True))
                return

            cue.start_sec = max(0.0, float(time_sec))
            if cue.stop_at_sec is not None and cue.stop_at_sec < cue.start_sec:
                cue.stop_at_sec = cue.start_sec

            self._update_tree_item(cue)
            if deck == "A":
                self.var_in_a.set(_format_timecode(cue.start_sec, with_ms=True))
            self._log(f"Deck {deck}: IN adjusted to {_format_timecode(cue.start_sec, with_ms=True)}")

            if cue.kind in ("audio", "video"):
                self._refresh_waveform_markers(cue, canvas, deck)
                self._request_cue_preview_in(cue)

        except Exception as e:
            try:
                if deck == "A" and self._selected_a >= 0:
                    self.var_in_a.set(_format_timecode(self._cues_a[self._selected_a].start_sec, with_ms=True))
            except Exception:
                pass
            self._log(f"IN adjust error: {e}")

    def _adjust_out(self, deck: str) -> None:
        """Fine-tune OUT point from OUT field"""
        if deck != "A":
            return
        try:
            if deck == "A":
                if self._selected_a < 0 or self._selected_a >= len(self._cues_a):
                    return
                cue = self._cues_a[self._selected_a]
                time_str = self.var_out_a.get()
                canvas = self.canvas_a

            if (time_str or "").strip() in ("", "—"):
                cue.stop_at_sec = None
                self._update_tree_item(cue)
                if deck == "A":
                    self.var_out_a.set("—")
                if cue.kind in ("audio", "video"):
                    self._refresh_waveform_markers(cue, canvas, deck)
                return

            # Parse timecode
            time_sec = _parse_timecode(time_str)
            if time_sec is None:
                if deck == "A":
                    self.var_out_a.set(_format_timecode(cue.stop_at_sec, with_ms=True) if cue.stop_at_sec else "—")
                return

            cue.stop_at_sec = max(0.0, float(time_sec))
            if cue.stop_at_sec < cue.start_sec:
                cue.start_sec = cue.stop_at_sec

            self._update_tree_item(cue)
            if deck == "A":
                self.var_out_a.set(_format_timecode(cue.stop_at_sec, with_ms=True) if cue.stop_at_sec else "—")
            self._log(f"Deck {deck}: OUT adjusted to {_format_timecode(cue.stop_at_sec, with_ms=True)}")

            if cue.kind in ("audio", "video"):
                self._refresh_waveform_markers(cue, canvas, deck)
                self._request_cue_preview_out(cue)

        except Exception as e:
            try:
                if deck == "A" and self._selected_a >= 0:
                    cue = self._cues_a[self._selected_a]
                    self.var_out_a.set(_format_timecode(cue.stop_at_sec, with_ms=True) if cue.stop_at_sec else "—")
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
            from PIL import Image  # type: ignore[reportMissingImports]
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
                        from PIL import Image  # type: ignore[reportMissingImports]

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
                # Long files can take a long time to scan. For responsiveness, analyze only an initial segment.
                analyze_limit_sec: float | None = None
                try:
                    dur = probe_media_duration_sec(str(cue.path), timeout_sec=2.5)
                except Exception:
                    dur = None
                try:
                    if dur is not None and float(dur) > 600.0:
                        analyze_limit_sec = 120.0
                except Exception:
                    analyze_limit_sec = None
                # loudnorm prints JSON at "info" level; keep output minimal but not suppressed.
                cmd = [ffmpeg, "-hide_banner", "-nostats", "-loglevel", "info"]
                cmd += [
                    "-i",
                    str(cue.path),
                ]
                if analyze_limit_sec is not None:
                    cmd += ["-t", f"{float(analyze_limit_sec):.3f}"]
                cmd += [
                    "-vn",
                    "-sn",
                    "-dn",
                    "-af",
                    f"loudnorm=I={analysis_i:.1f}:TP={analysis_tp:.1f}:LRA=11:print_format=json",
                    "-f",
                    "null",
                    "-",
                ]
                timeout_sec = 90.0 if analyze_limit_sec is not None else 180.0
                res = subprocess.run(cmd, capture_output=True, text=True, timeout=float(timeout_sec))
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
                try:
                    from PIL import Image, ImageTk  # type: ignore[reportMissingImports]
                except Exception:
                    Image = None  # type: ignore[assignment]
                    ImageTk = None  # type: ignore[assignment]

                if Image is None or ImageTk is None:
                    raise RuntimeError("Pillow (PIL) not installed")

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
        if deck != "A":
            return
        try:
            if deck == "A":
                if self._selected_a < 0 or self._selected_a >= len(self._cues_a):
                    return
                cue = self._cues_a[self._selected_a]
                var_in = self.var_in_a
                var_out = self.var_out_a
                canvas = self.canvas_a

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
        if deck != "A":
            return
        try:
            if deck == "A":
                if self._selected_a < 0 or self._selected_a >= len(self._cues_a):
                    return
                cue = self._cues_a[self._selected_a]
                var_out = self.var_out_a
                var_in = self.var_in_a
                canvas = self.canvas_a

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
        # Don't start preview if the user is currently playing audio or video.
        # But do allow preview while a still IMAGE is shown on the output surface.
        try:
            if self.audio_runner.is_playing():
                return
        except Exception:
            pass
        try:
            if self.video_runner.is_playing():
                playing = self.video_runner.current_cue()
                if playing is not None and playing.kind == "video":
                    return
        except Exception:
            pass
        try:
            backend, exe = _pick_playback_backend(self.settings)
        except Exception:
            backend, exe = ("ffplay", _resolve_fftool("ffplay") or "")
        if not exe:
            return

        self._stop_preview()
        start = max(0.0, float(start_sec))
        dur = max(0.05, float(duration_sec))
        try:
            vol = int(self.settings.startup_volume if volume_override is None else volume_override)
        except Exception:
            vol = 100
        if backend == "mpv":
            args = [
                exe,
                "--no-terminal",
                "--keep-open=no",
                "--msg-level=all=no",
                "--no-video",
                f"--volume={_clamp_int(vol, 0, 100)}",
                f"--start={start:.3f}",
                f"--length={dur:.3f}",
                path,
            ]
        else:
            args = [
                exe,
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
        total_duration = 0.0

        for i, cue in enumerate(self._cues_a):
            iid = str(i)
            self._cueid_to_iid_a[cue.id] = iid
            # Show checkbox only if in checkbox mode
            checkbox_mark = ""
            if self._checkbox_mode_a:
                checkbox_mark = "☑" if i in self._checked_cues_a else "☐"

            # Calculate duration (stop - start, or full duration if no markers)
            full_duration = self._duration_for_cue(cue)
            if full_duration is not None:
                if cue.stop_at_sec:
                    duration = cue.stop_at_sec - cue.start_sec
                else:
                    duration = full_duration - cue.start_sec
                duration_str = _format_timecode(duration)
                total_duration += duration
            else:
                duration_str = "—"

            self.tree_a.insert("", "end", iid=iid, values=(
                checkbox_mark,
                "▶" if cue.auto_play else "",
                i+1,
                cue.kind,
                _shorten_middle(Path(cue.path).name, 64),
                duration_str
            ))

        # Update total duration display
        self.var_total_duration_a.set(f"Total: {_format_timecode(total_duration)}")
        self._update_tree_playing_highlight()

    def _refresh_tree_b(self):
        self.tree_b.delete(*self.tree_b.get_children())
        self._cueid_to_iid_b = {}
        total_duration = 0.0

        for i, cue in enumerate(self._cues_b):
            iid = str(i)
            self._cueid_to_iid_b[cue.id] = iid
            # Show checkbox only if in checkbox mode
            checkbox_mark = ""
            if self._checkbox_mode_b:
                checkbox_mark = "☑" if i in self._checked_cues_b else "☐"

            # Calculate duration (stop - start, or full duration if no markers)
            full_duration = self._duration_for_cue(cue)
            if full_duration is not None:
                if cue.stop_at_sec:
                    duration = cue.stop_at_sec - cue.start_sec
                else:
                    duration = full_duration - cue.start_sec
                duration_str = _format_timecode(duration)
                total_duration += duration
            else:
                duration_str = "—"

            self.tree_b.insert("", "end", iid=iid, values=(
                checkbox_mark,
                "▶" if cue.auto_play else "",
                i+1,
                cue.kind,
                _shorten_middle(Path(cue.path).name, 64),
                duration_str
            ))

        # Update total duration display
        self.var_total_duration_b.set(f"Total: {_format_timecode(total_duration)}")
        self._update_tree_playing_highlight()

    def _route_kind_to_deck(self, kind: str) -> str:
        # MEDIA (A): audio/video; VISUALS (B): image/ppt.
        return "A" if kind in ("audio", "video") else "B"

    def _add_one_file_visuals(self, file_path: Path, scene: Scene) -> None:
        """Add a file to VISUALS (Deck B) only. Allows image/ppt/video."""
        kind = _detect_media_type(str(file_path))
        if kind not in ("image", "ppt", "video"):
            self._log(f"Deck B: Skipped unsupported visuals type: {file_path.name}")
            return
        has_second = bool(self._has_second_screen())
        cue = Cue(
            id=str(uuid.uuid4()),
            kind=kind,
            path=str(file_path),
            start_sec=0.0,
            stop_at_sec=None,
            fade_at_sec=None,
            fade_dur_sec=5.0,
            fade_to_percent=100,
            open_on_second_screen=bool(has_second),
            video_mode="output",
        )
        self._cues_b.append(cue)
        self._all_cues_b.append(cue)
        if not scene.cue_ids_b:
            scene.cue_ids_b = []
        scene.cue_ids_b.append(cue.id)
        self._log(f"Deck B: Added {kind} to scene '{scene.name}' - {file_path.name}")

    def _add_paths_visuals(self, paths: list[str], scene: Scene) -> None:
        visuals_extensions = {
            ".jpg",
            ".jpeg",
            ".png",
            ".gif",
            ".bmp",
            ".tiff",
            ".webp",
            ".ppt",
            ".pptx",
            ".mp4",
            ".mov",
            ".mkv",
            ".avi",
            ".wmv",
            ".flv",
            ".webm",
        }
        for p in (paths or []):
            try:
                path_obj = Path(p)
            except Exception:
                continue
            if path_obj.is_dir():
                try:
                    files = sorted([f for f in path_obj.iterdir() if f.is_file() and f.suffix.lower() in visuals_extensions])
                except Exception:
                    continue
                for fp in files:
                    self._add_one_file_visuals(fp, scene)
                continue
            if path_obj.is_file():
                if path_obj.suffix.lower() in visuals_extensions:
                    self._add_one_file_visuals(path_obj, scene)

    def _add_one_file_routed(self, file_path: Path, scene: Scene) -> None:
        kind = _detect_media_type(str(file_path))
        deck = self._route_kind_to_deck(str(kind))
        has_second = bool(self._has_second_screen())
        default_second_screen = bool(kind in ("video", "image", "ppt") and has_second)
        video_mode = "output"
        if kind == "video":
            # If there's no 2nd screen, default videos to local preview.
            video_mode = "output" if has_second else "preview"
        cue = Cue(
            id=str(uuid.uuid4()),
            kind=kind,
            path=str(file_path),
            start_sec=0.0,
            stop_at_sec=None,
            fade_at_sec=None,
            fade_dur_sec=5.0,
            fade_to_percent=100,
            open_on_second_screen=bool(default_second_screen if kind != "video" else (video_mode == "output")),
            video_mode=str(video_mode),
        )

        if deck == "A":
            self._cues_a.append(cue)
            self._all_cues_a.append(cue)
            if not scene.cue_ids_a:
                scene.cue_ids_a = []
            scene.cue_ids_a.append(cue.id)
            self._log(f"Deck A: Added {kind} to scene '{scene.name}' - {file_path.name}")
            return

        self._cues_b.append(cue)
        self._all_cues_b.append(cue)
        if not scene.cue_ids_b:
            scene.cue_ids_b = []
        scene.cue_ids_b.append(cue.id)
        self._log(f"Deck B: Added {kind} to scene '{scene.name}' - {file_path.name}")

    def _add_paths_routed(self, paths: list[str], scene: Scene) -> None:
        media_extensions = {
            ".mp3",
            ".wav",
            ".m4a",
            ".aac",
            ".flac",
            ".ogg",
            ".wma",
            ".mp4",
            ".mov",
            ".mkv",
            ".avi",
            ".wmv",
            ".flv",
            ".webm",
            ".jpg",
            ".jpeg",
            ".png",
            ".gif",
            ".bmp",
            ".tiff",
            ".webp",
            ".ppt",
            ".pptx",
        }

        for p in (paths or []):
            try:
                path_obj = Path(p)
            except Exception:
                continue
            if path_obj.is_dir():
                try:
                    files = sorted(
                        [f for f in path_obj.iterdir() if f.is_file() and f.suffix.lower() in media_extensions]
                    )
                except Exception:
                    continue
                for fp in files:
                    self._add_one_file_routed(fp, scene)
                continue
            if path_obj.is_file():
                self._add_one_file_routed(path_obj, scene)

    def _add_media_a(self):
        """Add media (auto-routed) while a scene is active."""
        # Check if a scene is selected
        if self._selected_scene_idx < 0 or self._selected_scene_idx >= len(self._scenes):
            messagebox.showwarning("No Scene Selected", "Please select a scene first before adding media.")
            return

        scene = self._scenes[self._selected_scene_idx]

        filetypes = [
            ("All Media", "*.mp3 *.wav *.m4a *.aac *.flac *.mp4 *.mov *.mkv *.avi *.pptx *.ppt *.jpg *.jpeg *.png *.gif *.bmp"),
            ("Audio", "*.mp3 *.wav *.m4a *.aac *.flac *.ogg *.wma"),
            ("Video", "*.mp4 *.mov *.mkv *.avi *.wmv *.flv *.webm"),
            ("Images", "*.jpg *.jpeg *.png *.gif *.bmp *.tiff *.webp"),
            ("Presentations", "*.pptx *.ppt"),
            ("All files", "*.*")
        ]
        paths = filedialog.askopenfilenames(title="Add Media", filetypes=filetypes)
        if not paths:
            return
        self._add_paths_routed(list(paths), scene)
        self._refresh_tree_a()
        self._refresh_tree_b()
        self._refresh_scene_list()  # Update scene cue counts

    def _add_media_b(self):
        """Add VISUALS (image/video/ppt) to Deck B while a scene is active."""
        # Check if a scene is selected
        if self._selected_scene_idx < 0 or self._selected_scene_idx >= len(self._scenes):
            messagebox.showwarning("No Scene Selected", "Please select a scene first before adding media.")
            return

        scene = self._scenes[self._selected_scene_idx]

        filetypes = [
            ("Visuals", "*.mp4 *.mov *.mkv *.avi *.wmv *.flv *.webm *.pptx *.ppt *.jpg *.jpeg *.png *.gif *.bmp *.tiff *.webp"),
            ("Video", "*.mp4 *.mov *.mkv *.avi *.wmv *.flv *.webm"),
            ("Images", "*.jpg *.jpeg *.png *.gif *.bmp *.tiff *.webp"),
            ("Presentations", "*.pptx *.ppt"),
            ("All files", "*.*")
        ]
        paths = filedialog.askopenfilenames(title="Add Visuals (Deck B)", filetypes=filetypes)
        if not paths:
            return
        self._add_paths_visuals(list(paths), scene)
        self._refresh_tree_a()
        self._refresh_tree_b()
        self._refresh_scene_list()  # Update scene cue counts

    def _on_drop_deck_a(self, event_data: str):
        """Handle drag & drop files/folders (auto-routed). Requires active scene."""
        # Check if a scene is selected
        if self._selected_scene_idx < 0 or self._selected_scene_idx >= len(self._scenes):
            messagebox.showwarning("No Scene Selected", "Please select a scene first before adding media.")
            return

        scene = self._scenes[self._selected_scene_idx]
        paths = self._parse_drop_data(event_data)
        self._add_paths_routed(paths, scene)
        self._refresh_tree_a()
        self._refresh_tree_b()
        self._refresh_scene_list()

    def _on_drop_deck_b(self, event_data: str):
        """Handle drag & drop files/folders into VISUALS (Deck B) only."""
        # Check if a scene is selected
        if self._selected_scene_idx < 0 or self._selected_scene_idx >= len(self._scenes):
            messagebox.showwarning("No Scene Selected", "Please select a scene first before adding media.")
            return

        scene = self._scenes[self._selected_scene_idx]
        paths = self._parse_drop_data(event_data)
        self._add_paths_visuals(paths, scene)
        self._refresh_tree_a()
        self._refresh_tree_b()
        self._refresh_scene_list()

    def _parse_drop_data(self, data: str) -> list[str]:
        """Parse drag & drop data to extract file paths."""
        # TkinterDnD2 returns paths in various formats depending on platform
        # On macOS: "{/path/to/file1} {/path/to/file2}"
        # On Windows: "{C:/path/to/file1} {C:/path/to/file2}"
        paths = []
        current = ""
        in_braces = False
        for char in data:
            if char == "{":
                in_braces = True
                current = ""
            elif char == "}":
                in_braces = False
                if current.strip():
                    paths.append(current.strip())
                current = ""
            elif in_braces:
                current += char
            elif char == " " and not in_braces:
                if current.strip():
                    paths.append(current.strip())
                current = ""
            else:
                current += char
        if current.strip():
            paths.append(current.strip())
        return paths

    def _add_folder_to_deck_a(self, folder: Path):
        """Add all supported media files from a folder (auto-routed)."""
        # Must have active scene
        if self._selected_scene_idx < 0 or self._selected_scene_idx >= len(self._scenes):
            return

        scene = self._scenes[self._selected_scene_idx]
        self._add_paths_routed([str(folder)], scene)

    def _add_folder_to_deck_b(self, folder: Path):
        """Add all supported media files from a folder (auto-routed)."""
        # Must have active scene
        if self._selected_scene_idx < 0 or self._selected_scene_idx >= len(self._scenes):
            return

        scene = self._scenes[self._selected_scene_idx]
        self._add_paths_routed([str(folder)], scene)

    def _remove_a(self):
        """Toggle checkbox mode OR delete checked items."""
        if not self._checkbox_mode_a:
            # Enter checkbox mode
            self._checkbox_mode_a = True
            self._checked_cues_a.clear()
            self.tree_a.column("checkbox", width=30)  # Show checkbox column
            try:
                self._schedule_tree_column_layout("A")
            except Exception:
                pass
            self._refresh_tree_a()
            self._log("Deck A: Checkbox mode activated - click checkboxes to select cues for removal")
        else:
            # Delete checked cues
            if not self._checked_cues_a:
                # Exit checkbox mode if nothing selected
                self._checkbox_mode_a = False
                self.tree_a.column("checkbox", width=0)  # Hide checkbox column
                try:
                    self._schedule_tree_column_layout("A")
                except Exception:
                    pass
                self._refresh_tree_a()
                self._log("Deck A: Checkbox mode canceled")
                return

            # Get active scene
            if self._selected_scene_idx >= 0 and self._selected_scene_idx < len(self._scenes):
                scene = self._scenes[self._selected_scene_idx]
            else:
                scene = None

            # Sort indices in reverse to delete from end to start
            to_remove = sorted(self._checked_cues_a, reverse=True)
            count = len(to_remove)

            for idx in to_remove:
                if idx < len(self._cues_a):
                    removed_cue = self._cues_a[idx]
                    del self._cues_a[idx]
                    # Remove from master list
                    if removed_cue in self._all_cues_a:
                        self._all_cues_a.remove(removed_cue)
                    # Remove from scene if exists
                    if scene and scene.cue_ids_a and removed_cue.id in scene.cue_ids_a:
                        scene.cue_ids_a.remove(removed_cue.id)

            # Exit checkbox mode
            self._checkbox_mode_a = False
            self._checked_cues_a.clear()
            self._selected_a = -1
            self.tree_a.column("checkbox", width=0)  # Hide checkbox column
            try:
                self._schedule_tree_column_layout("A")
            except Exception:
                pass
            self._refresh_tree_a()
            self._refresh_scene_list()
            try:
                self._set_wave_title("A", None)
                self._sync_target_setting_controls("A", None)
                self.canvas_a.delete("all")
            except Exception:
                pass
            self._log(f"Deck A: Removed {count} cue(s)")

    def _remove_b(self):
        """Toggle checkbox mode OR delete checked items."""
        if not self._checkbox_mode_b:
            # Enter checkbox mode
            self._checkbox_mode_b = True
            self._checked_cues_b.clear()
            self.tree_b.column("checkbox", width=30)  # Show checkbox column
            try:
                self._schedule_tree_column_layout("B")
            except Exception:
                pass
            self._refresh_tree_b()
            self._log("Deck B: Checkbox mode activated - click checkboxes to select cues for removal")
        else:
            # Delete checked cues
            if not self._checked_cues_b:
                # Exit checkbox mode if nothing selected
                self._checkbox_mode_b = False
                self.tree_b.column("checkbox", width=0)  # Hide checkbox column
                try:
                    self._schedule_tree_column_layout("B")
                except Exception:
                    pass
                self._refresh_tree_b()
                self._log("Deck B: Checkbox mode canceled")
                return

            # Get active scene
            if self._selected_scene_idx >= 0 and self._selected_scene_idx < len(self._scenes):
                scene = self._scenes[self._selected_scene_idx]
            else:
                scene = None

            # Sort indices in reverse to delete from end to start
            to_remove = sorted(self._checked_cues_b, reverse=True)
            count = len(to_remove)

            for idx in to_remove:
                if idx < len(self._cues_b):
                    removed_cue = self._cues_b[idx]
                    del self._cues_b[idx]
                    # Remove from master list
                    if removed_cue in self._all_cues_b:
                        self._all_cues_b.remove(removed_cue)
                    # Remove from scene if exists
                    if scene and scene.cue_ids_b and removed_cue.id in scene.cue_ids_b:
                        scene.cue_ids_b.remove(removed_cue.id)

            # Exit checkbox mode
            self._checkbox_mode_b = False
            self._checked_cues_b.clear()
            self._selected_b = -1
            self.tree_b.column("checkbox", width=0)  # Hide checkbox column
            try:
                self._schedule_tree_column_layout("B")
            except Exception:
                pass
            self._refresh_tree_b()
            self._refresh_scene_list()
            try:
                self._set_wave_title("B", None)
                self._sync_target_setting_controls("B", None)
                self.canvas_b.delete("all")
            except Exception:
                pass
            self._log(f"Deck B: Removed {count} cue(s)")

    def _move_a(self, delta: int):
        if self._selected_a < 0:
            return
        j = self._selected_a + delta
        if j < 0 or j >= len(self._cues_a):
            return
        # Persist ordering in the active scene.
        try:
            if 0 <= self._selected_scene_idx < len(self._scenes):
                scene = self._scenes[self._selected_scene_idx]
                if scene.cue_ids_a:
                    a_id = str(self._cues_a[self._selected_a].id)
                    b_id = str(self._cues_a[j].id)
                    try:
                        ia = scene.cue_ids_a.index(a_id)
                        ib = scene.cue_ids_a.index(b_id)
                        scene.cue_ids_a[ia], scene.cue_ids_a[ib] = scene.cue_ids_a[ib], scene.cue_ids_a[ia]
                    except Exception:
                        pass
        except Exception:
            pass
        self._cues_a[self._selected_a], self._cues_a[j] = self._cues_a[j], self._cues_a[self._selected_a]
        self._selected_a = j
        self._refresh_tree_a()
        self.tree_a.selection_set(str(j))
        try:
            self._refresh_scene_list()
        except Exception:
            pass

    def _move_b(self, delta: int):
        if self._selected_b < 0:
            return
        j = self._selected_b + delta
        if j < 0 or j >= len(self._cues_b):
            return
        # Persist ordering in the active scene.
        try:
            if 0 <= self._selected_scene_idx < len(self._scenes):
                scene = self._scenes[self._selected_scene_idx]
                if scene.cue_ids_b:
                    a_id = str(self._cues_b[self._selected_b].id)
                    b_id = str(self._cues_b[j].id)
                    try:
                        ia = scene.cue_ids_b.index(a_id)
                        ib = scene.cue_ids_b.index(b_id)
                        scene.cue_ids_b[ia], scene.cue_ids_b[ib] = scene.cue_ids_b[ib], scene.cue_ids_b[ia]
                    except Exception:
                        pass
        except Exception:
            pass
        self._cues_b[self._selected_b], self._cues_b[j] = self._cues_b[j], self._cues_b[self._selected_b]
        self._selected_b = j
        self._refresh_tree_b()
        self.tree_b.selection_set(str(j))
        try:
            self._refresh_scene_list()
        except Exception:
            pass

    def _on_tree_a_click(self, event):
        """Handle click on tree A - toggle checkbox/auto columns."""
        # Identify which item and column was clicked
        try:
            region = self.tree_a.identify_region(event.x, event.y)
        except Exception:
            return
        if region != "cell":
            return

        item = self.tree_a.identify_row(event.y)
        column = self.tree_a.identify_column(event.x)

        # Auto-play toggle column (column #2; headings are: checkbox, auto, idx, kind, name, duration)
        if column == "#2" and item:
            try:
                idx = int(item)
                if 0 <= idx < len(self._cues_a):
                    cue = self._cues_a[idx]
                    cue.auto_play = not bool(getattr(cue, "auto_play", False))
                    self._update_tree_item(cue)
                    self._log(f"Deck A: Auto-play {'enabled' if cue.auto_play else 'disabled'} for '{cue.display_name()}'")
            except Exception:
                pass
            return "break"

        if not self._checkbox_mode_a:
            return  # Normal mode, let default behavior handle it

        # Check if checkbox column was clicked (column #0)
        if column == "#1":  # First column is #1 in Tkinter Treeview
            try:
                idx = int(item)
                if idx in self._checked_cues_a:
                    self._checked_cues_a.remove(idx)
                else:
                    self._checked_cues_a.add(idx)
                self._refresh_tree_a()
            except (ValueError, IndexError):
                pass

    def _on_tree_b_click(self, event):
        """Handle click on tree B - toggle checkbox/auto columns."""
        # Identify which item and column was clicked
        try:
            region = self.tree_b.identify_region(event.x, event.y)
        except Exception:
            return
        if region != "cell":
            return

        item = self.tree_b.identify_row(event.y)
        column = self.tree_b.identify_column(event.x)

        # Auto-play toggle column (column #2; headings are: checkbox, auto, idx, kind, name, duration)
        if column == "#2" and item:
            try:
                idx = int(item)
                if 0 <= idx < len(self._cues_b):
                    cue = self._cues_b[idx]
                    cue.auto_play = not bool(getattr(cue, "auto_play", False))
                    self._update_tree_item(cue)
                    try:
                        self._update_visuals_playlist_info()
                    except Exception:
                        pass
                    self._log(f"Deck B: Auto-play {'enabled' if cue.auto_play else 'disabled'} for '{cue.display_name()}'")
            except Exception:
                pass
            return "break"

        if not self._checkbox_mode_b:
            return  # Normal mode, let default behavior handle it

        # Check if checkbox column was clicked (column #0)
        if column == "#1":  # First column is #1 in Tkinter Treeview
            try:
                idx = int(item)
                if idx in self._checked_cues_b:
                    self._checked_cues_b.remove(idx)
                else:
                    self._checked_cues_b.add(idx)
                self._refresh_tree_b()
            except (ValueError, IndexError):
                pass

    def _play_deck_a(self):
        self._transport_play_pause("A")

    def _play_deck_b(self):
        self._transport_play_pause("B")

    def _stop_deck_a(self):
        self._transport_stop("A")

    def _stop_deck_b(self):
        self._transport_stop("B")

    def _deck_runner(self, deck: str):
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

    def _restore_last_visual_if_any(self) -> None:
        if bool(getattr(self, "_ppt_running", False)):
            return
        cue_id = getattr(self, "_last_visual_cue_id", None)
        if not cue_id:
            return
        cue = None
        try:
            cue = next((c for c in (self._all_cues_b or []) if c.id == cue_id), None)
        except Exception:
            cue = None
        if cue is None:
            try:
                cue = next((c for c in (self._cues_b or []) if c.id == cue_id), None)
            except Exception:
                cue = None
        if cue is None or cue.kind != "image":
            return
        try:
            self.video_runner.play_for_deck("B", cue)  # type: ignore[attr-defined]
            self._active_runner = self.video_runner
        except Exception:
            return

    def _capture_visuals_resume_state(self) -> None:
        """Remember currently running VISUALS so we can restore after Deck A video ends."""
        if bool(getattr(self, "_ppt_running", False)):
            return
        try:
            if not (self.video_runner.is_playing() and getattr(self.video_runner, "owner_deck", None) == "B"):
                return
        except Exception:
            return
        try:
            cue = self.video_runner.current_cue()
        except Exception:
            cue = None
        if cue is None or cue.kind not in ("image", "video"):
            return
        playlist = False
        try:
            playlist = bool(cue.kind == "video" and bool(getattr(cue, "auto_play", False)) and len(self._visuals_autoplay_indices()) >= 1)
        except Exception:
            playlist = False
        self._resume_visuals_state = {
            "cue_id": str(cue.id),
            "kind": str(cue.kind),
            "playlist": bool(playlist),
        }

    def _resume_visuals_if_any(self) -> None:
        """Restore VISUALS after Deck A video ends/stops (image or video/playlist)."""
        if bool(getattr(self, "_ppt_running", False)):
            return
        st = getattr(self, "_resume_visuals_state", None)
        if not st:
            return
        self._resume_visuals_state = None
        cue_id = str(st.get("cue_id") or "")
        kind = str(st.get("kind") or "")
        playlist = bool(st.get("playlist"))
        if not cue_id:
            return
        cue: Cue | None = None
        try:
            cue = next((c for c in (self._cues_b or []) if c.id == cue_id), None)
        except Exception:
            cue = None
        if cue is None:
            try:
                cue = next((c for c in (self._all_cues_b or []) if c.id == cue_id), None)
            except Exception:
                cue = None
        if cue is None:
            return
        if cue.kind == "image":
            try:
                self._last_visual_cue_id = str(cue.id)
            except Exception:
                pass
            try:
                self.video_runner.play_for_deck("B", cue)  # type: ignore[attr-defined]
                self._active_runner = self.video_runner
                self._log(f"Deck B: Restored image {Path(cue.path).name}")
            except Exception:
                pass
            return
        if cue.kind != "video":
            return

        # Resume visuals video/playlist (muted) and keep loop behavior on Deck B.
        start_idx: int | None = None
        try:
            idx = next((i for i, c in enumerate(self._cues_b or []) if c.id == cue.id), None)
            start_idx = int(idx) if idx is not None else None
        except Exception:
            start_idx = None

        if playlist and len(self._visuals_autoplay_indices()) >= 1:
            # Resume from the currently playing cue if it's still in the autoplay set, otherwise from the first.
            ids = self._visuals_autoplay_indices()
            if start_idx is None or start_idx not in ids:
                start_idx = int(ids[0])
            self._play_visuals_by_index(int(start_idx), log_action="Deck B: Restored visuals playlist")
            return

        if start_idx is not None:
            self._play_visuals_by_index(int(start_idx), log_action="Deck B: Restored visual video")
            return

    def _transport_play_pause(self, deck: str) -> None:
        cue = self._selected_cue_for_deck(deck)
        if cue is None:
            return

        # Deck B is visuals only (image/PPT).
        if deck == "B":
            if cue.kind == "ppt":
                try:
                    # Remember VISUALS state before handing the 2nd screen to PowerPoint.
                    try:
                        self._capture_visuals_resume_state()
                    except Exception:
                        pass
                    self._ppt_running = True
                    # Best-effort: get mpv output out of the way so PPT can appear on the 2nd display.
                    try:
                        self.video_runner.ensure_window()  # type: ignore[attr-defined]
                        sess = getattr(self.video_runner, "_sess", None)
                        if sess is not None:
                            # Avoid VISUALS auto-advancing while PPT is active.
                            self._suppress_finish["B"] = "ppt"
                            try:
                                sess.set_property("ontop", False)
                            except Exception:
                                pass
                            try:
                                # Clear the current visual so PowerPoint can take over the projector cleanly.
                                sess.stop()
                            except Exception:
                                pass
                    except Exception:
                        pass
                    ppt_open_and_start(
                        cue.path,
                        on_second_screen=bool(getattr(cue, "open_on_second_screen", True) and self._has_second_screen()),
                        second_screen_left=int(getattr(self.settings, "second_screen_left", 0)),
                        second_screen_top=int(getattr(self.settings, "second_screen_top", 0)),
                    )
                    self._log(f"Deck B: PPT started: {cue.display_name()}")
                    try:
                        self._ppt_keep_on_top = True
                        if platform.system() == "Darwin":
                            self.attributes("-topmost", True)
                    except Exception:
                        pass
                    try:
                        self.after(250, self._bring_to_front)
                    except Exception:
                        pass
                except Exception as e:
                    self._log(f"PPT failed: {e}")
                return
            if cue.kind not in ("image", "video"):
                return
            try:
                self._ppt_running = False
                if cue.kind == "image":
                    self._last_visual_cue_id = str(cue.id)
                    self.video_runner.play_for_deck("B", cue)  # type: ignore[attr-defined]
                    self._active_runner = self.video_runner
                    self._log(f"Deck B: Showing image {Path(cue.path).name}")
                    # Ensure videos are not looped when switching back to images.
                    try:
                        sess = getattr(self.video_runner, "_sess", None)
                        if sess is not None:
                            sess.set_property("loop-file", "no")
                    except Exception:
                        pass
                else:
                    # Visual video clip (muted) over Deck A music.
                    self.video_runner.play_for_deck("B", cue)  # type: ignore[attr-defined]
                    self._active_runner = self.video_runner
                    self._log(f"Deck B: Playing visual video {Path(cue.path).name}")
                    # Apply mpv-side looping when looping a single file.
                    # If multiple auto-play videos are configured, keep mpv loop off and let the app drive playlist looping.
                    try:
                        sess = getattr(self.video_runner, "_sess", None)
                        if sess is not None:
                            playlist_mode = bool(getattr(cue, "auto_play", False) and len(self._visuals_autoplay_indices()) >= 2)
                            loop_file = bool(self._loop_b_enabled and not playlist_mode)
                            sess.set_property("loop-file", ("inf" if loop_file else "no"))
                    except Exception:
                        pass
                try:
                    self._last_output_owner = "B"
                    self._last_output_cue_id = str(cue.id)
                except Exception:
                    pass
            except Exception as e:
                self._log(f"Deck B play error: {e}")
            finally:
                self._update_transport_button_visuals()
            return

        # Deck A: audio/video media player.
        if cue.kind == "audio":
            runner = self.audio_runner
            # PLAY acts as PLAY/PAUSE for audio by stopping and resuming from time-pos.
            try:
                if runner.is_playing():
                    playing = runner.current_cue()
                    if playing is None or playing.kind != "audio":
                        return
                    pos = runner.playback_position_sec()
                    if pos is None:
                        return
                    self._set_paused_state_for_deck("A", (playing.id, float(pos)))
                    self._suppress_finish["A"] = "pause"
                    self._was_playing_a = True
                    runner.stop()
                    self._log(f"Deck A: Paused @ {_format_timecode(pos, with_ms=True)}")
                    self._update_transport_button_visuals()
                    return
            except Exception:
                pass

            paused = self._paused_state_for_deck("A")
            resume_pos = float(paused[1]) if paused is not None and paused[0] == cue.id else None
            if resume_pos is None:
                self._set_paused_state_for_deck("A", None)

            # If a Deck A video is currently playing on output, stop it before starting audio.
            try:
                if getattr(self.video_runner, "owner_deck", None) == "A" and self.video_runner.is_playing():
                    self._suppress_finish["A"] = "stop"
                    self._was_playing_b = True
                    self.video_runner.stop()
                    self._restore_last_visual_if_any()
            except Exception:
                pass

            self._active_runner = runner
            self._suppress_finish.pop("A", None)
            try:
                if resume_pos is not None:
                    runner.play_at(cue, resume_pos, volume_override=cue.volume_percent)
                    self._log(f"Deck A: Resumed @ {_format_timecode(resume_pos, with_ms=True)}")
                    self._set_paused_state_for_deck("A", None)
                else:
                    runner.play(cue)
                    self._log(f"Deck A: Playing {Path(cue.path).name}")
            except Exception as e:
                self._log(f"Deck A play error: {e}")
                try:
                    runner.stop()
                except Exception:
                    pass
            finally:
                self._update_transport_button_visuals()
            return

        if cue.kind != "video":
            return

        mode = str(getattr(cue, "video_mode", None) or ("output" if cue.open_on_second_screen else "preview")).strip().lower()
        if mode not in ("output", "preview", "audio_only"):
            mode = "output"
        cue.video_mode = mode
        cue.open_on_second_screen = bool(mode == "output")

        # Video PREVIEW (local window) or AUDIO-ONLY (no video): use the media runner.
        if mode in ("preview", "audio_only"):
            runner = self.audio_runner
            # Toggle pause/resume when the same preview is already playing.
            try:
                if runner.is_playing():
                    playing = runner.current_cue()
                    if playing is not None and playing.id == cue.id and playing.kind == "video":
                        pos = runner.playback_position_sec()
                        if pos is None:
                            return
                        self._set_paused_state_for_deck("A", (playing.id, float(pos)))
                        self._suppress_finish["A"] = "pause"
                        self._was_playing_a = True
                        runner.stop()
                        self._log(f"Deck A: Paused @ {_format_timecode(pos, with_ms=True)}")
                        self._update_transport_button_visuals()
                        return
            except Exception:
                pass

            paused = self._paused_state_for_deck("A")
            resume_pos = float(paused[1]) if paused is not None and paused[0] == cue.id else None
            if resume_pos is None:
                self._set_paused_state_for_deck("A", None)

            # Stop output only if Deck A currently owns a video on it.
            try:
                if getattr(self.video_runner, "owner_deck", None) == "A" and self.video_runner.is_playing():
                    self._suppress_finish["A"] = "stop"
                    self._was_playing_b = True
                    self.video_runner.stop()
                    self._restore_last_visual_if_any()
            except Exception:
                pass

            self._active_runner = runner
            self._suppress_finish.pop("A", None)
            try:
                if resume_pos is not None:
                    runner.play_at(cue, resume_pos, volume_override=cue.volume_percent)
                    self._log(f"Deck A: Resumed @ {_format_timecode(resume_pos, with_ms=True)}")
                    self._set_paused_state_for_deck("A", None)
                else:
                    runner.play(cue)
                    if mode == "audio_only":
                        self._log(f"Deck A: Playing audio-only (from video) {Path(cue.path).name}")
                    else:
                        self._log(f"Deck A: Previewing video {Path(cue.path).name}")
            except Exception as e:
                self._log(f"Deck A play error: {e}")
                try:
                    runner.stop()
                except Exception:
                    pass
            finally:
                self._update_transport_button_visuals()
            return

        # Video OUTPUT: plays on the persistent output window (audio+video), does not stop a Deck B image unless replaced.
        out = self.video_runner
        # If VISUALS is currently driving the output, remember it so we can restore after this Deck A video.
        try:
            if getattr(out, "owner_deck", None) == "B" and out.is_playing():
                self._capture_visuals_resume_state()
                # If mpv briefly drops playing state during replace, suppress any accidental Deck B auto-advance.
                self._suppress_finish["B"] = "override"
        except Exception:
            pass
        # Toggle pause/resume when the same video is already playing.
        try:
            if getattr(out, "owner_deck", None) == "A" and out.is_playing():
                playing = out.current_cue()
                if playing is not None and playing.id == cue.id and playing.kind == "video":
                    if bool(getattr(out, "is_paused", lambda: False)()):
                        out.resume()  # type: ignore[attr-defined]
                        self._log("Deck A: Resumed (video)")
                        self._set_paused_state_for_deck("A", None)
                    else:
                        pos = out.playback_position_sec()
                        if pos is not None:
                            self._set_paused_state_for_deck("A", (cue.id, float(pos)))
                        out.pause()  # type: ignore[attr-defined]
                        self._log("Deck A: Paused (video)")
                    self._update_transport_button_visuals()
                    return
        except Exception:
            pass

        paused = self._paused_state_for_deck("A")
        resume_pos = float(paused[1]) if paused is not None and paused[0] == cue.id else None
        if resume_pos is None:
            self._set_paused_state_for_deck("A", None)

        # Stop Deck A media runner if it is currently playing (video contains audio).
        try:
            if self.audio_runner.is_playing():
                self._suppress_finish["A"] = "stop"
                self._was_playing_a = True
                self.audio_runner.stop()
        except Exception:
            pass

        self._active_runner = out
        self._suppress_finish.pop("A", None)
        try:
            if resume_pos is not None:
                out.play_at_for_deck("A", cue, resume_pos, volume_override=cue.volume_percent)  # type: ignore[attr-defined]
                self._log(f"Deck A: Resumed video @ {_format_timecode(resume_pos, with_ms=True)}")
                self._set_paused_state_for_deck("A", None)
            else:
                out.play_for_deck("A", cue, volume_override=cue.volume_percent)  # type: ignore[attr-defined]
                self._log(f"Deck A: Playing video {Path(cue.path).name}")
        except Exception as e:
            self._log(f"Deck A play error: {e}")
            try:
                out.stop()
            except Exception:
                pass
        finally:
            self._update_transport_button_visuals()

    def _transport_stop(self, deck: str) -> None:
        self._set_paused_state_for_deck(deck, None)

        if deck == "A":
            # Stop audio if playing
            try:
                if self.audio_runner.is_playing():
                    self._suppress_finish["A"] = "stop"
                    self._was_playing_a = True
                self.audio_runner.stop()
            except Exception:
                pass
            # Stop video output only if Deck A owns it
            try:
                if getattr(self.video_runner, "owner_deck", None) == "A":
                    self._suppress_finish["A"] = "stop"
                    self._was_playing_b = True
                    self.video_runner.stop()
                    self._resume_visuals_if_any()
                    self._restore_last_visual_if_any()
            except Exception:
                pass
            self._log("Deck A: Stopped")
            self._update_transport_button_visuals()
            return

        # Deck B
        try:
            if bool(getattr(self, "_ppt_running", False)):
                try:
                    ppt_end_show()
                except Exception:
                    pass
                try:
                    ppt_hide_window()
                except Exception:
                    pass
                self._ppt_running = False
                try:
                    self.after(250, self._restore_visuals_after_ppt)
                except Exception:
                    pass
                self._log("Deck B: PPT ended")
                self._update_transport_button_visuals()
                return
            if getattr(self.video_runner, "owner_deck", None) == "B":
                self._suppress_finish["B"] = "stop"
                self._was_playing_b = True
                self.video_runner.stop()
                # Clear loop in mpv as well (best-effort).
                try:
                    sess = getattr(self.video_runner, "_sess", None)
                    if sess is not None:
                        sess.set_property("loop-file", "no")
                except Exception:
                    pass
                # User explicitly cleared visuals; don't auto-restore the last image.
                self._last_visual_cue_id = None
        except Exception:
            pass
        self._log("Deck B: Stopped")
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
        # If a VISUALS video is currently playing, apply loop immediately via mpv property.
        try:
            if getattr(self.video_runner, "owner_deck", None) == "B":
                playing = self.video_runner.current_cue()
                if playing is not None and getattr(playing, "kind", None) == "video":
                    sess = getattr(self.video_runner, "_sess", None)
                    if sess is not None:
                        # If multiple auto-play videos are configured, keep mpv loop off and let the app drive playlist looping.
                        playlist_mode = bool(getattr(playing, "auto_play", False) and len(self._visuals_autoplay_indices()) >= 2)
                        loop_file = bool(self._loop_b_enabled and not playlist_mode)
                        sess.set_property("loop-file", ("inf" if loop_file else "no"))
        except Exception:
            pass
        try:
            self._update_transport_button_visuals()
        except Exception:
            pass
        self._log(f"Deck B: Loop {'ON' if self._loop_b_enabled else 'OFF'}")

    def _loop_enabled_for_deck(self, deck: str) -> bool:
        return bool(self._loop_a_enabled) if deck == "A" else bool(self._loop_b_enabled)

    def _visuals_autoplay_indices(self) -> list[int]:
        """Return indices (in scene order) of VISUALS cues that are part of the auto-play playlist."""
        try:
            return [i for i, c in enumerate(self._cues_b or []) if bool(getattr(c, "auto_play", False)) and getattr(c, "kind", None) == "video"]
        except Exception:
            return []

    def _visuals_next_autoplay_index(self, from_cue_id: str, *, wrap: bool) -> int | None:
        ids = self._visuals_autoplay_indices()
        if not ids:
            return None
        current_idx = None
        try:
            current_idx = next((i for i, c in enumerate(self._cues_b or []) if c.id == str(from_cue_id)), None)
        except Exception:
            current_idx = None
        if current_idx is None or int(current_idx) not in ids:
            return None
        try:
            pos = ids.index(int(current_idx))
        except Exception:
            return None
        if pos + 1 < len(ids):
            return int(ids[pos + 1])
        if wrap:
            return int(ids[0])
        return None

    def _play_visuals_by_index(self, idx: int, *, log_action: str | None = None) -> None:
        try:
            idx_i = int(idx)
        except Exception:
            return
        if not (0 <= idx_i < len(self._cues_b or [])):
            return
        try:
            self._selected_b = idx_i
            self.tree_b.selection_set(str(idx_i))
            self.tree_b.see(str(idx_i))
        except Exception:
            pass
        try:
            self._on_deck_b_select()
        except Exception:
            pass
        try:
            if log_action:
                self._log(str(log_action))
        except Exception:
            pass
        try:
            self._transport_play_pause("B")
        except Exception:
            pass

    def _update_visuals_playlist_info(self) -> None:
        var = getattr(self, "var_visuals_playlist_info", None)
        if var is None:
            return
        try:
            n = len(self._visuals_autoplay_indices())
        except Exception:
            n = 0
        try:
            var.set(f"Auto-play videos: {n}")
        except Exception:
            pass

    def _visuals_clear_autoplay_flags(self) -> None:
        try:
            changed = 0
            for c in (self._cues_b or []):
                if bool(getattr(c, "auto_play", False)):
                    c.auto_play = False
                    changed += 1
            if changed:
                try:
                    self._refresh_tree_b()
                except Exception:
                    pass
            self._update_visuals_playlist_info()
            self._log(f"Deck B: Cleared auto-play flags ({changed} item(s))")
        except Exception:
            return

    def _play_visuals_autoplay(self) -> None:
        ids = self._visuals_autoplay_indices()
        if not ids:
            try:
                messagebox.showinfo("VISUALS playlist", "No auto-play videos are selected.")
            except Exception:
                pass
            return
        start_idx: int | None = None
        try:
            if 0 <= self._selected_b < len(self._cues_b):
                c = self._cues_b[self._selected_b]
                if c.kind == "video" and bool(getattr(c, "auto_play", False)):
                    start_idx = int(self._selected_b)
        except Exception:
            start_idx = None
        if start_idx is None:
            start_idx = int(ids[0])
        self._play_visuals_by_index(start_idx, log_action=f"Deck B: Starting playlist ({len(ids)} item(s))")

    def _schedule_tree_column_layout(self, deck: str) -> None:
        """Throttle tree column layout during window resize."""
        if deck not in ("A", "B"):
            return
        after_id = self._tree_resize_after.get(deck)
        if after_id:
            try:
                self.after_cancel(after_id)
            except Exception:
                pass
        try:
            self._tree_resize_after[deck] = self.after(60, lambda: self._apply_tree_column_layout(deck))
        except Exception:
            self._tree_resize_after[deck] = None

    def _apply_tree_column_layout(self, deck: str) -> None:
        """Keep Duration visible by dynamically sizing the File column to remaining width."""
        tree = self.tree_a if deck == "A" else self.tree_b
        try:
            total = int(tree.winfo_width())
        except Exception:
            return
        if total <= 50:
            return
        checkbox_on = bool(self._checkbox_mode_a) if deck == "A" else bool(self._checkbox_mode_b)
        w_checkbox = 30 if checkbox_on else 0
        w_auto = 30
        w_idx = 30
        w_kind = 55
        w_duration = 86
        pad = 18
        fixed = int(w_checkbox + w_auto + w_idx + w_kind + w_duration + pad)
        w_name = max(120, int(total - fixed))
        try:
            tree.column("checkbox", width=w_checkbox, minwidth=(w_checkbox if checkbox_on else 0), stretch=False, anchor="center")
            tree.column("auto", width=w_auto, minwidth=w_auto, stretch=False, anchor="center")
            tree.column("idx", width=w_idx, minwidth=w_idx, stretch=False, anchor="e")
            tree.column("kind", width=w_kind, minwidth=w_kind, stretch=False)
            tree.column("duration", width=w_duration, minwidth=78, stretch=False, anchor="e")
            tree.column("name", width=w_name, minwidth=120, stretch=True)
        except Exception:
            return

    def _sync_target_setting_controls(self, deck: str, cue: Cue | None) -> None:
        try:
            if deck == "A":
                var = getattr(self, "var_video_mode_a", None)
                cb_mode = getattr(self, "cb_video_mode_a", None)
                vol_var = getattr(self, "var_cue_vol_a", None)
                vol_label = getattr(self, "var_cue_vol_a_label", None)
                vol_scale = getattr(self, "scale_cue_vol_a", None)
            else:
                var = getattr(self, "var_video_mode_b", None)
                cb_mode = getattr(self, "cb_video_mode_b", None)
                vol_var = getattr(self, "var_cue_vol_b", None)
                vol_label = getattr(self, "var_cue_vol_b_label", None)
                vol_scale = getattr(self, "scale_cue_vol_b", None)

            enabled = bool(cue is not None and cue.kind == "video")
            if cb_mode is not None:
                try:
                    cb_mode.configure(state=("readonly" if enabled else "disabled"))
                except Exception:
                    try:
                        cb_mode.state(["!disabled"] if enabled else ["disabled"])
                    except Exception:
                        pass

            if var is not None:
                if cue is None or cue.kind != "video":
                    var.set(_video_mode_to_label("output"))
                else:
                    var.set(_video_mode_to_label(getattr(cue, "video_mode", "output")))

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
                var = getattr(self, "var_video_mode_a", None)
            elif deck == "B" and 0 <= self._selected_b < len(self._cues_b):
                cue = self._cues_b[self._selected_b]
                var = getattr(self, "var_video_mode_b", None)
            else:
                return
            if cue is None or cue.kind != "video" or var is None:
                return
            mode = _video_mode_from_label(str(var.get()))
            cue.video_mode = mode
            cue.open_on_second_screen = bool(mode == "output")
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
                checkbox_mark = ""
                try:
                    if self._checkbox_mode_a:
                        checkbox_mark = "☑" if int(idx_a) in self._checked_cues_a else "☐"
                except Exception:
                    checkbox_mark = ""
                full_duration = self._duration_for_cue(cue)
                if full_duration is not None:
                    if cue.stop_at_sec:
                        duration = float(cue.stop_at_sec) - float(cue.start_sec)
                    else:
                        duration = float(full_duration) - float(cue.start_sec)
                    duration_str = _format_timecode(duration)
                else:
                    duration_str = "—"
                values = (
                    checkbox_mark,
                    "▶" if cue.auto_play else "",
                    int(idx_a) + 1,
                    cue.kind,
                    _shorten_middle(Path(cue.path).name, 64),
                    duration_str,
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
                checkbox_mark = ""
                try:
                    if self._checkbox_mode_b:
                        checkbox_mark = "☑" if int(idx_b) in self._checked_cues_b else "☐"
                except Exception:
                    checkbox_mark = ""
                full_duration = self._duration_for_cue(cue)
                if full_duration is not None:
                    if cue.stop_at_sec:
                        duration = float(cue.stop_at_sec) - float(cue.start_sec)
                    else:
                        duration = float(full_duration) - float(cue.start_sec)
                    duration_str = _format_timecode(duration)
                else:
                    duration_str = "—"
                values = (
                    checkbox_mark,
                    "▶" if cue.auto_play else "",
                    int(idx_b) + 1,
                    cue.kind,
                    _shorten_middle(Path(cue.path).name, 64),
                    duration_str,
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
    def _detect_screens(self) -> None:
        """Auto-detect second screen position (works with iPad extended display on macOS)"""
        try:
            monitors = list(get_monitors() or [])
            if len(monitors) < 2:
                self._log("Only one display detected")
                self._detect_screens_fallback()
                return
            try:
                info = []
                for i, m in enumerate(monitors):
                    info.append(
                        f"Display {i}: {getattr(m,'name','')} {int(getattr(m,'width',0))}x{int(getattr(m,'height',0))}+{int(getattr(m,'x',0))}+{int(getattr(m,'y',0))}"
                        + (" (primary)" if bool(getattr(m, "is_primary", False)) else "")
                    )
                self._log("Detected displays: " + " | ".join(info))
            except Exception:
                pass

            controller = self._controller_monitor(monitors)
            second = _pick_output_monitor_excluding(monitors, controller) or monitors[1]
            left = int(getattr(second, "x", 0))
            top = int(getattr(second, "y", 0))
            width = int(getattr(second, "width", 0))
            height = int(getattr(second, "height", 0))
            self._set_display_vars(left, top, apply=True)
            self._log(f"Detected output display at: {left}, {top} (size: {width}x{height}) - settings updated")
            try:
                self._log_mpv_output_state()
            except Exception:
                pass
            return
        except Exception as e:
            self._log(f"Screen detection error: {e}")
            self._detect_screens_fallback()

    def _detect_screens_fallback(self) -> None:
        """Fallback screen detection using Tkinter"""
        try:
            # Get primary screen width
            primary_width = self.winfo_screenwidth()

            # Set second screen to the right of primary
            self._set_display_vars(primary_width, 0, apply=True)
            self._log(f"Second screen position set to: {primary_width}, 0 (standard layout) - settings updated")
        except Exception as e:
            self._log(f"Fallback detection failed: {e}")

    def _apply_settings_from_vars(self) -> None:
        if bool(getattr(self, "_suppress_display_var_trace", False)):
            return
        try:
            self.settings.second_screen_left = int(float(self.var_left.get().strip() or "0"))
            self.settings.second_screen_top = int(float(self.var_top.get().strip() or "0"))
        except Exception:
            return
        try:
            self._save_persistent_settings()
        except Exception:
            pass
        # Manual workflow: do not move/resize mpv output from settings edits.

    def _apply_display_settings_to_output(self) -> None:
        self._disp_apply_after_id = None
        # Kept for back-compat (older UI traces). Intentionally does nothing.
        return

    def _persistent_settings_path(self) -> Path:
        return _user_data_dir() / "settings.json"

    def _load_persistent_settings(self) -> None:
        """Load user-level settings (independent from presets)."""
        path = self._persistent_settings_path()
        try:
            if not path.exists():
                return
        except Exception:
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return
        loaded = Settings.from_dict(data if isinstance(data, dict) else {})
        # Keep the current Settings instance (runners hold references to it).
        try:
            for k, v in loaded.to_dict().items():
                try:
                    setattr(self.settings, k, v)
                except Exception:
                    continue
        except Exception:
            try:
                self.settings = loaded
            except Exception:
                pass

    def _save_persistent_settings(self) -> None:
        """Persist user-level settings to disk."""
        path = self._persistent_settings_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
        except Exception:
            return
        try:
            path.write_text(json.dumps(self.settings.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception:
            return

    def _install_mpv_prompt(self) -> None:
        existing = _resolve_mpv()
        if existing:
            try:
                messagebox.showinfo("mpv", f"mpv already available:\n\n{existing}", parent=self)
            except Exception:
                pass
            return

        install_url = "https://mpv.io/installation/"
        sysname = platform.system()

        if sysname == "Darwin":
            brew = shutil.which("brew")
            if not brew:
                for p in (Path("/opt/homebrew/bin/brew"), Path("/usr/local/bin/brew")):
                    try:
                        if p.exists():
                            brew = str(p)
                            break
                    except Exception:
                        continue

            if brew:
                try:
                    ok = messagebox.askyesno(
                        "Install mpv (Homebrew)",
                        "mpv is recommended for the smoothest output.\n\n"
                        "Homebrew detected.\n\n"
                        "Install mpv now via Homebrew? (brew install mpv)",
                        parent=self,
                    )
                except Exception:
                    ok = False
                if ok:
                    win = tk.Toplevel(self)
                    win.title("Installing mpv…")
                    try:
                        win.configure(bg="#2b2b2b")
                    except Exception:
                        pass
                    win.resizable(False, False)
                    try:
                        win.transient(self)
                    except Exception:
                        pass

                    status_var = tk.StringVar(value="Running: brew install mpv …")
                    body = tk.Frame(win, bg="#2b2b2b", padx=14, pady=12)
                    body.pack(fill="both", expand=True)
                    tk.Label(body, textvariable=status_var, bg="#2b2b2b", fg="#e8e8e8").pack(anchor="w")
                    pb = ttk.Progressbar(body, mode="indeterminate")
                    pb.pack(fill="x", expand=True, pady=(10, 0))
                    pb.start(10)

                    def _finish(success: bool, details: str) -> None:
                        def _apply() -> None:
                            try:
                                pb.stop()
                            except Exception:
                                pass
                            try:
                                win.destroy()
                            except Exception:
                                pass
                            if success and _resolve_mpv():
                                try:
                                    messagebox.showinfo("mpv", "mpv installed successfully.", parent=self)
                                except Exception:
                                    pass
                            else:
                                try:
                                    messagebox.showwarning(
                                        "mpv install",
                                        "mpv could not be installed automatically.\n\n"
                                        "I will open the install page with options.\n\n"
                                        f"Details:\n{details}",
                                        parent=self,
                                    )
                                except Exception:
                                    pass
                                try:
                                    webbrowser.open(install_url)
                                except Exception:
                                    pass

                        self._ui_tasks.put(_apply)

                    def _worker() -> None:
                        try:
                            proc = subprocess.run([str(brew), "install", "mpv"], capture_output=True, text=True, check=False)
                            out = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
                            if proc.returncode == 0:
                                _finish(True, out.strip()[-1200:])
                            else:
                                _finish(False, out.strip()[-1200:] or f"brew exit={proc.returncode}")
                        except Exception as e:
                            _finish(False, str(e))

                    threading.Thread(target=_worker, daemon=True).start()
                    return

            # No Homebrew: open instructions page.
            try:
                messagebox.showinfo(
                    "Install mpv",
                    "mpv is not installed. I will open the installation page.\n\n"
                    "Tip (macOS): the easiest is Homebrew: https://brew.sh/ then `brew install mpv`.",
                    parent=self,
                )
            except Exception:
                pass
            try:
                webbrowser.open(install_url)
            except Exception:
                pass
            return

        # Windows/Linux: open instructions page (package managers / downloads).
        try:
            messagebox.showinfo(
                "Install mpv",
                "mpv is not installed. I will open the installation page.\n\n"
                "Tip (Windows): if you have a package manager, mpv installs easily (e.g. Scoop/winget/choco).",
                parent=self,
            )
        except Exception:
            pass
        try:
            webbrowser.open(install_url)
        except Exception:
            pass

    def _on_cue_autoplay_changed(self, deck: str) -> None:
        """Called when cue auto-play checkbox is toggled"""
        try:
            cue = self._selected_cue_for_deck(deck)
            if not cue:
                return

            if deck == "A":
                cue.auto_play = bool(self.var_autoplay_a.get())
            else:
                var = getattr(self, "var_autoplay_b", None)
                if var is None:
                    return
                cue.auto_play = bool(var.get())

            status = "enabled" if cue.auto_play else "disabled"
            try:
                self._update_tree_item(cue)
            except Exception:
                pass
            try:
                if deck == "B":
                    self._update_visuals_playlist_info()
            except Exception:
                pass
            self._log(f"Deck {deck}: Auto-play {status} for '{cue.display_name()}'")
        except Exception as e:
            self._log(f"Auto-play toggle error: {e}")

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
            try:
                self._capture_visuals_resume_state()
            except Exception:
                pass
            self._ppt_running = True
            # Best-effort: get mpv output out of the way so PPT can appear on the 2nd display.
            try:
                self.video_runner.ensure_window()  # type: ignore[attr-defined]
                sess = getattr(self.video_runner, "_sess", None)
                if sess is not None:
                    try:
                        self._suppress_finish["B"] = "ppt"
                    except Exception:
                        pass
                    try:
                        sess.set_property("ontop", False)
                    except Exception:
                        pass
                    try:
                        sess.stop()
                    except Exception:
                        pass
            except Exception:
                pass

            ppt_open_and_start(
                cue.path,
                on_second_screen=bool(getattr(cue, "open_on_second_screen", True) and self._has_second_screen()),
                second_screen_left=int(getattr(self.settings, "second_screen_left", 0)),
                second_screen_top=int(getattr(self.settings, "second_screen_top", 0)),
            )
            self._log(f"PPT started: {cue.display_name()}")
            try:
                self._ppt_keep_on_top = True
                if platform.system() == "Darwin":
                    self.attributes("-topmost", True)
            except Exception:
                pass
            try:
                self.after(250, self._bring_to_front)
            except Exception:
                pass
        except Exception as e:
            messagebox.showerror("PPT failed", str(e))
            self._log(f"PPT failed: {e}")

    def _play_selected_ppt_b(self) -> None:
        cue = self._selected_cue_for_deck("B")
        if cue is None or cue.kind != "ppt":
            return
        self._transport_play_pause("B")

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
        self._inhibit_auto_advance = False  # Reset inhibit flag after stop
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
            # MEDIA (A) playback is either audio_runner (audio) OR video_runner (when owner=A and cue=video).
            try:
                a_audio_playing = bool(self.audio_runner.is_playing())
            except Exception:
                a_audio_playing = False

            out_playing = False
            out_owner: str | None = None
            out_cue: Cue | None = None
            out_paused = False
            try:
                out_playing = bool(self.video_runner.is_playing())
                out_owner = getattr(self.video_runner, "owner_deck", None)
                out_cue = self.video_runner.current_cue()
                out_paused = bool(getattr(self.video_runner, "is_paused", lambda: False)())
            except Exception:
                out_playing = False
                out_owner, out_cue, out_paused = None, None, False

            if out_playing and out_owner:
                self._last_output_owner = str(out_owner)
                try:
                    self._last_output_cue_id = str(out_cue.id) if out_cue is not None else None
                except Exception:
                    self._last_output_cue_id = None

            if self._was_playing_a and not a_audio_playing:
                self._handle_runner_finished("A", self.audio_runner)

            # Output finish should advance the owning deck (typically A for videos).
            if self._was_playing_b and not out_playing:
                deck = self._last_output_owner
                if deck in ("A", "B"):
                    self._handle_runner_finished(str(deck), self.video_runner)

            self._was_playing_a = a_audio_playing
            self._was_playing_b = out_playing

            # Only run at high FPS when something time-based is playing (audio/video). Static images don't need it.
            out_is_video = bool(out_cue is not None and out_cue.kind == "video")
            if a_audio_playing or (out_playing and out_is_video and not out_paused):
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
            if self.video_runner.is_playing():
                cue = self.video_runner.current_cue()
                owner = getattr(self.video_runner, "owner_deck", None)
                if cue is not None and owner == "A":
                    iid = self._cueid_to_iid_a.get(cue.id)
                    if iid is not None and self.tree_a.exists(iid):
                        new_iid_a = iid
                if cue is not None and owner == "B":
                    iid = self._cueid_to_iid_b.get(cue.id)
                    if iid is not None and self.tree_b.exists(iid):
                        new_iid_b = iid
        except Exception:
            pass
        try:
            if new_iid_a is None and self.audio_runner.is_playing():
                cue = self.audio_runner.current_cue()
                if cue is not None:
                    iid = self._cueid_to_iid_a.get(cue.id)
                    if iid is not None and self.tree_a.exists(iid):
                        new_iid_a = iid
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

    def _handle_runner_finished(self, deck: str, runner) -> None:
        # Do not advance on user stop/pause, only on natural OUT/file end.
        if deck in self._suppress_finish:
            reason = self._suppress_finish.pop(deck, None)
            # For "seek", only suppress if it happened very recently (< 1 second ago)
            # This allows the seek-induced finish to be suppressed, but not the natural finish later
            if reason == "seek":
                # Check if this is the immediate finish from the old process, or the natural finish later
                # If we're here within 1 second of the seek, suppress it. Otherwise, allow auto-advance.
                if hasattr(self, '_last_seek_time') and hasattr(self, '_last_seek_deck'):
                    elapsed = time.monotonic() - getattr(self, '_last_seek_time', 0)
                    if self._last_seek_deck == deck and elapsed < 1.0:
                        self._log(f"DEBUG: Deck {deck} finish suppressed (seek within {elapsed:.3f}s)")
                        return
                    else:
                        # This is the natural finish after seek, allow auto-advance
                        self._log(f"DEBUG: Deck {deck} natural finish after seek ({elapsed:.3f}s ago), allowing auto-advance")
                else:
                    # Fallback: suppress if no timestamp info
                    self._log(f"DEBUG: Deck {deck} finish suppressed (seek - no timestamp)")
                    return
            else:
                self._log(f"DEBUG: Deck {deck} finish suppressed ({reason})")
                return
        if self._inhibit_auto_advance:
            self._inhibit_auto_advance = False
            self._log(f"DEBUG: Deck {deck} auto-advance inhibited")
            return

        last_exit = getattr(runner, "last_exit_code", None)
        if last_exit not in (None, 0):
            self._log(f"Playback failed (exit code: {last_exit}).")
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

        # CRITICAL: Images and PPT stay visible until manually stopped - NO auto-advance!
        if cue and cue.kind in ("image", "ppt"):
            self._log(f"Deck {deck}: {cue.kind.upper()} displayed - stays visible until manually stopped")
            return

        playlist_mode = bool(
            deck == "B"
            and cue is not None
            and cue.kind == "video"
            and bool(getattr(cue, "auto_play", False))
            and len(self._visuals_autoplay_indices()) >= 2
        )
        if cue and cue.kind in ("audio", "video") and self._loop_enabled_for_deck(deck) and (not playlist_mode):
            self._log(f"DEBUG: Deck {deck} looping cue: {cue.display_name()}")
            try:
                # OutputRunner must be re-triggered with the correct owner deck.
                if runner == self.video_runner and cue.kind == "video":
                    self.video_runner.play_for_deck(deck, cue, volume_override=cue.volume_percent)  # type: ignore[attr-defined]
                    self._was_playing_b = True
                else:
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

        # Deck B: auto-play playlist (visual videos).
        # Auto-play is opt-in per cue; LOOP controls wrap-around.
        if deck == "B" and cue is not None and cue.kind == "video" and bool(getattr(cue, "auto_play", False)):
            next_idx = self._visuals_next_autoplay_index(str(cue.id), wrap=bool(self._loop_b_enabled))
            if next_idx is not None:
                try:
                    next_cue = self._cues_b[int(next_idx)]
                    self._play_visuals_by_index(int(next_idx), log_action=f"Auto-playing next visual (Deck B): {next_cue.display_name()}")
                except Exception:
                    self._play_visuals_by_index(int(next_idx), log_action="Auto-playing next visual (Deck B)")
                return
            # End of playlist (LOOP OFF) -> restore last still image if any and stop.
            try:
                self._restore_last_visual_if_any()
            except Exception:
                pass
            self._log("Deck B: Playlist finished")
            return

        # After a Deck A VIDEO ends, restore the last chosen VISUAL (image) so audio-only cues can play "on top".
        try:
            if deck == "A" and cue is not None and cue.kind == "video":
                # Prefer restoring an active VISUALS loop/playlist if it was running, otherwise fall back to last still image.
                self._resume_visuals_if_any()
                self._restore_last_visual_if_any()
        except Exception:
            pass
        # After a VISUALS video ends, restore the last chosen image.
        try:
            if deck == "B" and cue is not None and cue.kind == "video":
                self._restore_last_visual_if_any()
        except Exception:
            pass

        self._log(f"DEBUG: Deck {deck} calling _select_next_cue_for_deck")
        try:
            from_id = str(cue.id) if cue is not None else None
        except Exception:
            from_id = None
        self._select_next_cue_for_deck(deck, from_cue_id=from_id)

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

                if deck == "B":
                    play_text = "🖼 SHOWING" if playing else "▶ SHOW"
                else:
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

        a_audio_playing = False
        try:
            a_audio_playing = bool(self.audio_runner.is_playing())
        except Exception:
            a_audio_playing = False

        out_playing = False
        out_owner: str | None = None
        out_cue: Cue | None = None
        out_paused = False
        try:
            out_playing = bool(self.video_runner.is_playing())
            out_owner = getattr(self.video_runner, "owner_deck", None)
            out_cue = self.video_runner.current_cue()
            out_paused = bool(getattr(self.video_runner, "is_paused", lambda: False)())
        except Exception:
            out_playing = False
            out_owner, out_cue, out_paused = None, None, False

        a_video_playing = bool(out_playing and out_owner == "A" and out_cue is not None and out_cue.kind == "video" and not out_paused)
        b_visual_active = bool(out_playing and out_owner == "B")

        _update_deck("A", playing=bool(a_audio_playing or a_video_playing), loop_enabled=bool(self._loop_a_enabled))
        _update_deck("B", playing=b_visual_active, loop_enabled=bool(self._loop_b_enabled))

    def _current_playback_source(self) -> tuple[object | None, Cue | None]:
        try:
            if self.audio_runner.is_playing():
                return self.audio_runner, self.audio_runner.current_cue()
        except Exception:
            pass
        try:
            if self.video_runner.is_playing() and getattr(self.video_runner, "owner_deck", None) == "A":
                cue = self.video_runner.current_cue()
                if cue is not None and cue.kind == "video":
                    return self.video_runner, cue
        except Exception:
            pass
        return None, None

    def _update_now_playing(self) -> None:
        # Update Deck A: reflect whichever runner is currently driving MEDIA.
        runner_a = self.audio_runner
        try:
            if self.video_runner.is_playing() and getattr(self.video_runner, "owner_deck", None) == "A":
                cue = self.video_runner.current_cue()
                if cue is not None and cue.kind == "video":
                    runner_a = self.video_runner
        except Exception:
            runner_a = self.audio_runner
        self._update_deck_now_playing(
            "A",
            runner_a,
            self.var_now_a_time
        )

        # Update Deck B
        var_b = getattr(self, "var_now_b_time", None)
        if var_b is None:
            return
        runner_b = None
        try:
            if self.video_runner.is_playing() and getattr(self.video_runner, "owner_deck", None) == "B":
                runner_b = self.video_runner
        except Exception:
            runner_b = None
        self._update_deck_now_playing(
            "B",
            runner_b,
            var_b
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
        runner_a = self.audio_runner
        try:
            if self.video_runner.is_playing() and getattr(self.video_runner, "owner_deck", None) == "A":
                cue = self.video_runner.current_cue()
                if cue is not None and cue.kind == "video":
                    runner_a = self.video_runner
        except Exception:
            runner_a = self.audio_runner
        self._update_vu_for_deck("A", runner_a)
        runner_b = None
        try:
            if self.video_runner.is_playing() and getattr(self.video_runner, "owner_deck", None) == "B":
                runner_b = self.video_runner
        except Exception:
            runner_b = None
        self._update_vu_for_deck("B", runner_b)

    def _update_vu_for_deck(self, deck: str, runner) -> None:
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
        runner_a = self.audio_runner
        try:
            if getattr(self.video_runner, "owner_deck", None) == "A":
                cue = self.video_runner.current_cue()
                if cue is not None and cue.kind == "video":
                    runner_a = self.video_runner
        except Exception:
            runner_a = self.audio_runner
        self._update_waveform_playback_for_deck("A", runner_a)
        runner_b = None
        try:
            if self.video_runner.is_playing() and getattr(self.video_runner, "owner_deck", None) == "B":
                runner_b = self.video_runner
        except Exception:
            runner_b = None
        self._update_waveform_playback_for_deck("B", runner_b)

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

    def _update_waveform_playback_for_deck(self, deck: str, runner) -> None:
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

            paused_by_runner = False
            is_playing = False
            try:
                if runner is not None:
                    paused_by_runner = bool(getattr(runner, "is_paused", lambda: False)())
                    is_playing = bool(runner.is_playing())
            except Exception:
                paused_by_runner = False
                is_playing = False

            if (not is_playing) or paused_by_runner:
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

            if runner is None:
                self._clear_waveform_playback(deck, canvas)
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
        if cue is None or cue.kind in ("ppt", "image"):
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

    def _select_next_cue_for_deck(self, deck: str, *, from_cue_id: str | None = None) -> None:
        """Select (and optionally auto-play) the next cue based on its auto_play property"""
        idx_from: int | None = None
        if from_cue_id:
            try:
                cues = self._cues_a if deck == "A" else self._cues_b
                idx = next((i for i, c in enumerate(cues) if c.id == str(from_cue_id)), None)
                if idx is not None:
                    idx_from = int(idx)
                    if deck == "A":
                        self._selected_a = int(idx)
                    else:
                        self._selected_b = int(idx)
            except Exception:
                pass
        if deck == "A":
            if self._selected_a >= 0 and self._selected_a + 1 < len(self._cues_a):
                self._selected_a += 1
                next_cue = self._cues_a[self._selected_a]
                self.tree_a.selection_set(str(self._selected_a))
                self.tree_a.see(str(self._selected_a))
                self._load_cue_into_editor(next_cue)
                # Check if THIS cue has auto-play enabled
                if next_cue.auto_play:
                    # Auto-play the next cue
                    self._log(f"Auto-playing next cue (Deck A): {next_cue.display_name()}")
                    try:
                        self._play_deck_a()
                    except Exception as e:
                        self._log(f"Auto-play failed: {e}")
                else:
                    self._log(f"Ready on next cue (Deck A): {next_cue.display_name()}")
                return

            # End of Deck A list: if the active scene is configured for auto-advance, move to next scene.
            try:
                if idx_from is not None and idx_from == (len(self._cues_a) - 1) and (0 <= self._selected_scene_idx < len(self._scenes)):
                    scene = self._scenes[self._selected_scene_idx]
                    if bool(getattr(scene, "auto_advance", False)) and (self._selected_scene_idx + 1 < len(self._scenes)):
                        self._log(f"Scene auto-advance: {scene.name} -> next scene")
                        self._next_scene()
            except Exception:
                pass
            return

        # Deck B
        if self._selected_b >= 0 and self._selected_b + 1 < len(self._cues_b):
            self._selected_b += 1
            next_cue = self._cues_b[self._selected_b]
            self.tree_b.selection_set(str(self._selected_b))
            self.tree_b.see(str(self._selected_b))
            self._load_cue_into_editor(next_cue)
            # Check if THIS cue has auto-play enabled
            if next_cue.auto_play:
                # Auto-play the next cue
                self._log(f"Auto-playing next cue (Deck B): {next_cue.display_name()}")
                try:
                    self._play_deck_b()
                except Exception as e:
                    self._log(f"Auto-play failed: {e}")
            else:
                self._log(f"Ready on next cue (Deck B): {next_cue.display_name()}")

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
        # Persistent settings are user-level and must survive preset/show loads.
        try:
            self._load_persistent_settings()
        except Exception:
            pass
        self.audio_runner.settings = self.settings
        self.video_runner.settings = self.settings

        # Load dual deck cues
        self._cues_a = [Cue.from_dict(x) for x in data.get("cues_a", [])]
        self._cues_b = [Cue.from_dict(x) for x in data.get("cues_b", [])]

        # Legacy support - if old format, load to deck A
        if not self._cues_a and not self._cues_b and "cues" in data:
            self._cues_a = [Cue.from_dict(x) for x in data.get("cues", [])]

        # Load scenes (backward compatible - defaults to empty list if not present)
        self._scenes = [Scene.from_dict(x) for x in data.get("scenes", [])]
        self._selected_scene_idx = -1

        # Preserve deck assignment from the saved preset (cues_a vs cues_b).
        # VISUALS can contain video clips too (muted), so we do NOT re-route by kind.
        self._all_cues_a = list(self._cues_a or [])
        self._all_cues_b = list(self._cues_b or [])

        id_to_cue: dict[str, Cue] = {}
        deck_of: dict[str, str] = {}
        for c in (self._all_cues_a or []):
            cid = str(getattr(c, "id", ""))
            if cid:
                id_to_cue[cid] = c
                deck_of[cid] = "A"
        for c in (self._all_cues_b or []):
            cid = str(getattr(c, "id", ""))
            if cid and cid not in deck_of:
                id_to_cue[cid] = c
                deck_of[cid] = "B"

        def _move_cue_to(cid: str, deck: str) -> None:
            try:
                cid_s = str(cid)
                want = "A" if str(deck) == "A" else "B"
                cur = deck_of.get(cid_s)
                if cur == want:
                    return
                cue_obj = id_to_cue.get(cid_s)
                if cue_obj is None:
                    return
                if cur == "A":
                    try:
                        self._all_cues_a.remove(cue_obj)
                    except Exception:
                        pass
                elif cur == "B":
                    try:
                        self._all_cues_b.remove(cue_obj)
                    except Exception:
                        pass
                if want == "A":
                    self._all_cues_a.append(cue_obj)
                else:
                    self._all_cues_b.append(cue_obj)
                deck_of[cid_s] = want
            except Exception:
                return

        if self._scenes:
            for s in self._scenes:
                try:
                    ids_a = [str(x) for x in (s.cue_ids_a or [])]
                except Exception:
                    ids_a = []
                try:
                    ids_b = [str(x) for x in (s.cue_ids_b or [])]
                except Exception:
                    ids_b = []

                # Deduplicate while preserving order.
                seen: set[str] = set()
                a_out: list[str] = []
                for cid in ids_a:
                    if not cid or cid in seen:
                        continue
                    seen.add(cid)
                    a_out.append(cid)
                b_out: list[str] = []
                for cid in ids_b:
                    if not cid or cid in seen:
                        continue
                    seen.add(cid)
                    b_out.append(cid)

                # Ensure master lists match the scene assignment (scene is authoritative).
                for cid in a_out:
                    if cid in id_to_cue:
                        _move_cue_to(cid, "A")
                for cid in b_out:
                    if cid in id_to_cue:
                        _move_cue_to(cid, "B")

                # Drop missing references.
                s.cue_ids_a = [cid for cid in a_out if deck_of.get(cid) == "A"]
                s.cue_ids_b = [cid for cid in b_out if deck_of.get(cid) == "B"]
            # Drop cues that are not referenced by any scene.
            try:
                self._prune_orphan_cues()
            except Exception:
                pass
        else:
            # If a legacy preset has cues but no scenes, create a default scene so media is always scene-bound.
            if self._all_cues_a or self._all_cues_b:
                s = Scene(
                    id=str(uuid.uuid4()),
                    name="Imported",
                    color="#4a90e2",
                    cue_ids_a=[str(c.id) for c in (self._all_cues_a or [])],
                    cue_ids_b=[str(c.id) for c in (self._all_cues_b or [])],
                    notes="",
                    auto_advance=False,
                )
                self._scenes = [s]
                self._selected_scene_idx = 0

        # Scenes are mandatory: if no active scene, show nothing.
        if self._selected_scene_idx < 0 or self._selected_scene_idx >= len(self._scenes):
            self._cues_a = []
            self._cues_b = []
        else:
            self._activate_scene()

        self._show_path = path if set_show_path else None

        # Sync UI with persistent settings without moving/reapplying output placement.
        self._set_display_vars(self.settings.second_screen_left, self.settings.second_screen_top, apply=False)

        self._refresh_tree_a()
        self._refresh_tree_b()
        self._refresh_scene_list()
        try:
            if self._scenes:
                if self._selected_scene_idx < 0:
                    self._selected_scene_idx = 0
                self.scene_listbox.selection_clear(0, tk.END)
                self.scene_listbox.selection_set(self._selected_scene_idx)
                self.scene_listbox.see(self._selected_scene_idx)
                self._activate_scene()
        except Exception:
            pass
        self._load_selected_into_editor()
        self._update_showfile_label()
        try:
            where = f"show file {path.name}" if set_show_path else f"preset {path.name}"
            scene_info = f", {len(self._scenes)} scenes" if self._scenes else ""
            self._log(f"Loaded {len(self._cues_a)} cues to Deck A, {len(self._cues_b)} cues to Deck B{scene_info} from {where}.")
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
            # Enforce "scene owns media": don't persist orphan cues.
            try:
                self._prune_orphan_cues()
            except Exception:
                pass
            payload = {
                "version": 3,  # Bumped to 3 for scene support
                "settings": self.settings.to_dict(),
                # IMPORTANT: Save ALL cues from master lists, not filtered scene view
                "cues_a": [c.to_dict() for c in self._all_cues_a],
                "cues_b": [c.to_dict() for c in self._all_cues_b],
                "scenes": [s.to_dict() for s in self._scenes],
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
                try:
                    # During playback, waveform click seeks (but does not touch IN/OUT markers).
                    if deck == "A":
                        # If Deck A video is currently playing on the output window, seek via IPC (no restart).
                        out = self.video_runner
                        if (
                            out.is_playing()
                            and getattr(out, "owner_deck", None) == "A"
                            and (playing := out.current_cue()) is not None
                            and playing.id == cue.id
                            and playing.kind == "video"
                        ):
                            if cue.stop_at_sec is not None and time_sec >= float(cue.stop_at_sec):
                                time_sec = max(float(cue.start_sec or 0.0), float(cue.stop_at_sec) - 0.001)
                            time_sec = max(0.0, min(float(duration), float(time_sec)))
                            try:
                                out.seek_to(float(time_sec))  # type: ignore[attr-defined]
                            except Exception:
                                pass
                            self._active_runner = out
                            self._log(f"Deck A: Seek -> {_format_timecode(time_sec, with_ms=True)}")
                            return

                        runner = self.audio_runner
                        if runner.is_playing():
                            playing = runner.current_cue()
                            if playing is not None and playing.id == cue.id and playing.kind == "audio":
                                if cue.stop_at_sec is not None and time_sec >= float(cue.stop_at_sec):
                                    time_sec = max(float(cue.start_sec or 0.0), float(cue.stop_at_sec) - 0.001)
                                time_sec = max(0.0, min(float(duration), float(time_sec)))
                                self._suppress_finish[deck] = "seek"
                                self._last_seek_time = time.monotonic()
                                self._last_seek_deck = deck
                                runner.play_at(cue, float(time_sec), volume_override=cue.volume_percent)
                                self._active_runner = runner
                                self._log(f"Deck A: Seek -> {_format_timecode(time_sec, with_ms=True)}")
                                return
                            if playing is not None and playing.id == cue.id and playing.kind == "video":
                                if cue.stop_at_sec is not None and time_sec >= float(cue.stop_at_sec):
                                    time_sec = max(float(cue.start_sec or 0.0), float(cue.stop_at_sec) - 0.001)
                                time_sec = max(0.0, min(float(duration), float(time_sec)))
                                self._suppress_finish[deck] = "seek"
                                self._last_seek_time = time.monotonic()
                                self._last_seek_deck = deck
                                runner.play_at(cue, float(time_sec), volume_override=cue.volume_percent)
                                self._active_runner = runner
                                self._log(f"Deck A: Seek -> {_format_timecode(time_sec, with_ms=True)}")
                                return

                    # Deck B: do not seek (VISUALS are image/PPT).
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
                    var_in_b = getattr(self, "var_in_b", None)
                    if var_in_b is not None:
                        var_in_b.set(_format_timecode(cue.start_sec, with_ms=True))
                self._log(f"Deck {deck}: Mark IN at {_format_timecode(cue.start_sec, with_ms=True)}")
                try:
                    if deck == "A" and cue.kind in ("audio", "video"):
                        self._request_cue_preview_in(cue)
                except Exception:
                    pass
            else:  # mark_type == "OUT"
                cue.stop_at_sec = max(0.0, time_sec)
                if cue.stop_at_sec < cue.start_sec:
                    cue.start_sec = cue.stop_at_sec
                if deck == "A":
                    self.var_out_a.set(_format_timecode(cue.stop_at_sec, with_ms=True) if cue.stop_at_sec else "—")
                else:
                    var_out_b = getattr(self, "var_out_b", None)
                    if var_out_b is not None:
                        var_out_b.set(_format_timecode(cue.stop_at_sec, with_ms=True) if cue.stop_at_sec else "—")
                self._log(f"Deck {deck}: Mark OUT at {_format_timecode(cue.stop_at_sec, with_ms=True)}")
                try:
                    if deck == "A" and cue.kind in ("audio", "video"):
                        self._request_cue_preview_out(cue)
                except Exception:
                    pass

            # Update tree display
            self._update_tree_item(cue)

            # Refresh markers (fast path if waveform already exists)
            self._refresh_waveform_markers(cue, canvas, deck)

        except Exception as e:
            self._log(f"Waveform click error: {e}")

    # (waveform generation is handled by _request_waveform_generate + _apply_waveform_result)

    # ── Scene Management ────────────────────────────────────────────────

    def _refresh_scene_list(self):
        """Refresh the scene listbox display"""
        self.scene_listbox.delete(0, tk.END)

        # Add scenes (no "ALL CUES" - scenes are mandatory)
        for i, scene in enumerate(self._scenes):
            # Format: "1. Scene Name (3+2)" - shows cue counts
            cue_count_a = len(scene.cue_ids_a) if scene.cue_ids_a else 0
            cue_count_b = len(scene.cue_ids_b) if scene.cue_ids_b else 0
            display_text = f"{i+1}. {scene.name} ({cue_count_a}+{cue_count_b})"
            self.scene_listbox.insert(tk.END, display_text)

            # Always show the scene color (inactive: colored text; active: colored background).
            try:
                if i == self._selected_scene_idx:
                    self.scene_listbox.itemconfig(i, bg=scene.color, fg=_contrast_text_color(scene.color))
                else:
                    # Keep the dark panel background and color-code the text.
                    self.scene_listbox.itemconfig(i, bg="#2b2b2b", fg=scene.color)
            except Exception:
                try:
                    self.scene_listbox.itemconfig(i, bg="#2b2b2b", fg="#ffffff")
                except Exception:
                    pass

    def _on_scene_select(self):
        """Handle scene selection"""
        sel = self.scene_listbox.curselection()
        if not sel:
            # Avoid clearing the entire UI on transient selection loss
            # (can happen when focus moves to other widgets on some platforms).
            if 0 <= self._selected_scene_idx < len(self._scenes):
                try:
                    self.scene_listbox.selection_clear(0, tk.END)
                    self.scene_listbox.selection_set(self._selected_scene_idx)
                    self.scene_listbox.see(self._selected_scene_idx)
                except Exception:
                    pass
                return
            self._selected_scene_idx = -1
            self._activate_scene()
            return
        # Direct mapping (no offset needed anymore)
        self._selected_scene_idx = sel[0]
        self._activate_scene()

    def _activate_scene(self):
        """Load the selected scene's cues into both decks"""
        # Must have a valid scene selected
        if self._selected_scene_idx < 0 or self._selected_scene_idx >= len(self._scenes):
            # Clear cue lists if no valid scene
            self._cues_a = []
            self._cues_b = []
            self._refresh_tree_a()
            self._refresh_tree_b()
            return

        scene = self._scenes[self._selected_scene_idx]

        # Keep the scene's explicit ordering (cue_ids_* lists).
        id_to_a: dict[str, Cue] = {str(c.id): c for c in (self._all_cues_a or [])}
        id_to_b: dict[str, Cue] = {str(c.id): c for c in (self._all_cues_b or [])}
        ids_a = [str(x) for x in (scene.cue_ids_a or [])]
        ids_b = [str(x) for x in (scene.cue_ids_b or [])]
        self._cues_a = [id_to_a[cid] for cid in ids_a if cid in id_to_a]
        self._cues_b = [id_to_b[cid] for cid in ids_b if cid in id_to_b]

        self._refresh_tree_a()
        self._refresh_tree_b()
        try:
            self._update_visuals_playlist_info()
        except Exception:
            pass

        # Auto-select first cue in Deck A if available
        if self._cues_a:
            self._selected_a = 0
            self.tree_a.selection_set("0")
            self._on_deck_a_select()
        else:
            self._selected_a = -1

        self._log(f"Activated scene: {scene.name}")

    def _add_scene(self):
        """Add a new scene"""
        existing = []
        try:
            existing = [str(s.color) for s in (self._scenes or []) if getattr(s, "color", None)]
        except Exception:
            existing = []
        scene = Scene(
            id=str(uuid.uuid4()),
            name="New Scene",
            color=_random_scene_color(existing),
            cue_ids_a=[],
            cue_ids_b=[],
            notes="",
            auto_advance=False,
        )
        self._scenes.append(scene)
        self._refresh_scene_list()
        self._selected_scene_idx = len(self._scenes) - 1
        self.scene_listbox.selection_clear(0, tk.END)
        # Direct selection (no offset)
        self.scene_listbox.selection_set(self._selected_scene_idx)
        self.scene_listbox.see(self._selected_scene_idx)
        self._activate_scene()  # Automatically activate the new scene
        self._log(f"Added scene: {scene.name}")

    def _edit_scene(self):
        """Open dialog to edit the selected scene"""
        if self._selected_scene_idx < 0 or self._selected_scene_idx >= len(self._scenes):
            messagebox.showwarning("Edit Scene", "Please select a scene first.")
            return

        scene = self._scenes[self._selected_scene_idx]
        self._open_scene_editor_dialog(scene)

    def _remove_scene(self):
        """Remove the selected scene"""
        if self._selected_scene_idx < 0 or self._selected_scene_idx >= len(self._scenes):
            messagebox.showwarning("Remove Scene", "Please select a scene first.")
            return

        scene = self._scenes[self._selected_scene_idx]
        if not messagebox.askyesno("Remove Scene", f"Remove scene '{scene.name}'?"):
            return

        del self._scenes[self._selected_scene_idx]
        self._selected_scene_idx = -1
        self._refresh_scene_list()
        self._log(f"Removed scene: {scene.name}")

    def _move_scene(self, direction: int):
        """Move the selected scene up (-1) or down (+1)"""
        if self._selected_scene_idx < 0 or self._selected_scene_idx >= len(self._scenes):
            return

        new_idx = self._selected_scene_idx + direction
        if new_idx < 0 or new_idx >= len(self._scenes):
            return

        # Swap scenes
        self._scenes[self._selected_scene_idx], self._scenes[new_idx] = \
            self._scenes[new_idx], self._scenes[self._selected_scene_idx]

        self._selected_scene_idx = new_idx
        self._refresh_scene_list()
        self.scene_listbox.selection_clear(0, tk.END)
        self.scene_listbox.selection_set(self._selected_scene_idx)
        self.scene_listbox.see(self._selected_scene_idx)
        self._activate_scene()

    def _prev_scene(self):
        """Navigate to the previous scene"""
        if not self._scenes:
            self._selected_scene_idx = -1
            self._activate_scene()
            return

        if self._selected_scene_idx <= 0:
            self._selected_scene_idx = 0
        else:
            self._selected_scene_idx -= 1

        self.scene_listbox.selection_clear(0, tk.END)
        self.scene_listbox.selection_set(self._selected_scene_idx)
        self.scene_listbox.see(self._selected_scene_idx)
        self._activate_scene()

    def _next_scene(self):
        """Navigate to the next scene"""
        if not self._scenes:
            return

        if self._selected_scene_idx < 0:
            self._selected_scene_idx = 0
        elif self._selected_scene_idx >= len(self._scenes) - 1:
            self._selected_scene_idx = len(self._scenes) - 1
        else:
            self._selected_scene_idx += 1

        self.scene_listbox.selection_clear(0, tk.END)
        self.scene_listbox.selection_set(self._selected_scene_idx)
        self.scene_listbox.see(self._selected_scene_idx)
        self._activate_scene()

    def _prune_orphan_cues(self) -> None:
        """Keep only cues that are referenced by at least one scene."""
        used_a: set[str] = set()
        used_b: set[str] = set()
        for s in (self._scenes or []):
            try:
                used_a.update([str(x) for x in (s.cue_ids_a or [])])
            except Exception:
                pass
            try:
                used_b.update([str(x) for x in (s.cue_ids_b or [])])
            except Exception:
                pass

        try:
            self._all_cues_a = [c for c in (self._all_cues_a or []) if str(c.id) in used_a]
        except Exception:
            pass
        try:
            self._all_cues_b = [c for c in (self._all_cues_b or []) if str(c.id) in used_b]
        except Exception:
            pass

        existing_a = {str(c.id) for c in (self._all_cues_a or [])}
        existing_b = {str(c.id) for c in (self._all_cues_b or [])}
        for s in (self._scenes or []):
            try:
                s.cue_ids_a = [str(x) for x in (s.cue_ids_a or []) if str(x) in existing_a]
            except Exception:
                pass
            try:
                s.cue_ids_b = [str(x) for x in (s.cue_ids_b or []) if str(x) in existing_b]
            except Exception:
                pass

    def _open_scene_editor_dialog(self, scene: Scene):
        """Open a dialog to edit scene properties"""
        dialog = tk.Toplevel(self)
        dialog.title(f"Edit Scene: {scene.name}")
        dialog.geometry("500x450")
        dialog.transient(self)
        dialog.grab_set()

        # Scene name
        ttk.Label(dialog, text="Scene Name:").pack(anchor="w", padx=10, pady=(10, 2))
        name_var = tk.StringVar(value=scene.name)
        name_entry = ttk.Entry(dialog, textvariable=name_var, width=50)
        name_entry.pack(fill="x", padx=10, pady=(0, 10))

        # Scene color
        ttk.Label(dialog, text="Scene Color:").pack(anchor="w", padx=10, pady=(5, 2))
        color_frame = ttk.Frame(dialog)
        color_frame.pack(fill="x", padx=10, pady=(0, 10))

        color_var = tk.StringVar(value=scene.color)
        color_entry = ttk.Entry(color_frame, textvariable=color_var, width=15)
        color_entry.pack(side="left")

        def pick_color():
            from tkinter import colorchooser
            color = colorchooser.askcolor(initialcolor=color_var.get())
            if color[1]:
                color_var.set(color[1])

        ttk.Button(color_frame, text="Pick Color", command=pick_color).pack(side="left", padx=(5, 0))

        # Auto-advance
        auto_advance_var = tk.BooleanVar(value=scene.auto_advance)
        ttk.Checkbutton(dialog, text="Auto-advance to next scene when finished", variable=auto_advance_var).pack(anchor="w", padx=10, pady=5)

        # Notes
        ttk.Label(dialog, text="Notes:").pack(anchor="w", padx=10, pady=(10, 2))
        notes_text = ScrolledText(dialog, height=10, width=50)
        notes_text.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        notes_text.insert("1.0", scene.notes)

        # Info about cue assignment
        info_label = ttk.Label(dialog, text="ℹ️ Media is automatically assigned to this scene when you add it.",
                               foreground="gray", font=("Helvetica", 9, "italic"))
        info_label.pack(anchor="w", padx=10, pady=(0, 10))

        # Buttons
        btn_frame = ttk.Frame(dialog)
        btn_frame.pack(fill="x", padx=10, pady=10)

        def save_scene():
            scene.name = name_var.get().strip() or "Untitled Scene"
            col = color_var.get().strip() or "#4a90e2"
            if _hex_to_rgb(col) is None:
                col = "#4a90e2"
            scene.color = col
            scene.auto_advance = auto_advance_var.get()
            scene.notes = notes_text.get("1.0", "end-1c")
            self._refresh_scene_list()
            dialog.destroy()
            self._log(f"Updated scene: {scene.name}")

        ttk.Button(btn_frame, text="Save", command=save_scene).pack(side="right", padx=2)
        ttk.Button(btn_frame, text="Cancel", command=dialog.destroy).pack(side="right", padx=2)

    # ────────────────────────────────────────────────────────────────────

    def _write_show(self, path: Path) -> None:
        try:
            # Enforce "scene owns media": don't persist orphan cues.
            try:
                self._prune_orphan_cues()
            except Exception:
                pass
            payload = {
                "version": 3,  # Bumped to 3 for scene support
                "settings": self.settings.to_dict(),
                # IMPORTANT: Save ALL cues from master lists, not filtered scene view
                "cues_a": [c.to_dict() for c in self._all_cues_a],
                "cues_b": [c.to_dict() for c in self._all_cues_b],
                "scenes": [s.to_dict() for s in self._scenes],
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
