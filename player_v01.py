#!/usr/bin/env python3
"""
Show control – gyors, telepítésmentes MVP (macOS/Windows/Linux).

Fókusz: cue lista audio/video/PPT elemekkel:
- fájlok betöltése
- sorrendezés (Up/Down)
- cue play (start offset)
- automatikus halkítás adott időpontnál (fade to %)
- PPT megnyitás + léptetés (macOS: PowerPoint + osascript)

Lejátszás: `ffplay` (FFmpeg) subprocess-szel, így nincs pip-es dependency.
"""

from __future__ import annotations

import json
import datetime
import platform
import queue
import shutil
import subprocess
import threading
import time
import uuid
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

CueKind = Literal["audio", "video", "ppt"]

APP_NAME = "S.P. Show Control"
APP_VERSION = "v01"


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


def _shell_quote(s: str) -> str:
    if s == "":
        return "''"
    safe = all(ch.isalnum() or ch in "._-/:=+" for ch in s)
    if safe:
        return s
    return "'" + s.replace("'", "'\"'\"'") + "'"


@dataclass
class Settings:
    second_screen_left: int = 1920
    second_screen_top: int = 0
    video_fullscreen: bool = True
    startup_volume: int = 100

    def to_dict(self) -> dict:
        return {
            "second_screen_left": self.second_screen_left,
            "second_screen_top": self.second_screen_top,
            "video_fullscreen": self.video_fullscreen,
            "startup_volume": self.startup_volume,
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
            ppt_open_and_start(cue.path)
            return

        ffplay = shutil.which("ffplay")
        if not ffplay:
            raise RuntimeError("ffplay not found (install ffmpeg).")

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
            ppt_open_and_start(cue.path)
            return

        ffplay = shutil.which("ffplay")
        if not ffplay:
            raise RuntimeError("ffplay not found (install ffmpeg).")

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

        if audio_filter:
            args += ["-af", audio_filter]

        args.append(cue.path)
        return args

    def restart_at(self, position_sec: float, *, volume_override: int | None = None) -> None:
        cue = self._playing_cue
        if cue is None or cue.kind == "ppt":
            return
        ffplay = shutil.which("ffplay")
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


def ppt_open_and_start(ppt_path: str) -> None:
    path = str(Path(ppt_path).expanduser().resolve())
    system = platform.system()
    if system == "Darwin":
        script = r'''
on run argv
  set pptPath to item 1 of argv
  tell application "Microsoft PowerPoint"
    activate
    open POSIX file pptPath
    delay 0.2
    try
      start slide show of active presentation
    on error
      -- fallback: sometimes the object model fails; rely on UI.
      tell application "System Events"
        key code 49 -- space
      end tell
    end try
  end tell
end run
'''
        res = _osascript(script, [path])
        if res.returncode != 0:
            # Fallback: just open it (PowerPoint should handle).
            subprocess.run(["open", path])
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


def probe_media_duration_sec(path: str, timeout_sec: float = 3.0) -> float | None:
    ffprobe = shutil.which("ffprobe")
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
        loaded = self._auto_load_preset()
        if not loaded:
            self._refresh_tree()
            self._load_selected_into_editor()
        self.after(0, self._bring_to_front)
        self._poll_playback()

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
        return Path.cwd() / "show_preset.json"

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
        tab_log = tk.Frame(nb, bg="#2b2b2b")
        tab_about = tk.Frame(nb, bg="#2b2b2b")
        nb.add(tab_display, text="Display")
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
            Path.cwd() / "assets" / "logo.png",
            Path.cwd() / "assets" / "logo.gif",
            Path.cwd() / "logo.png",
            Path.cwd() / "logo.gif",
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
            style.layout("Deck.TNotebook", [("Notebook.client", {"sticky": "nswe"})])
            style.configure("Deck.TNotebook", borderwidth=0, relief="flat", padding=0, background="#2b2b2b")
            style.configure("Deck.TNotebook.Tab", padding=(12, 6))
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
        self.lbl_now_a_time = tk.Label(now_a, textvariable=self.var_now_a_time, anchor="e", font=("Courier", 14, "bold"))
        self.lbl_now_a_time.pack(fill="x")
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
        self.lbl_now_b_time = tk.Label(now_b, textvariable=self.var_now_b_time, anchor="e", font=("Courier", 14, "bold"))
        self.lbl_now_b_time.pack(fill="x")
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
                cmd = [
                    "ffmpeg",
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
        ffplay = shutil.which("ffplay")
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
