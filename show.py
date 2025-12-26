#!/usr/bin/env python3
"""
Show Control PRO – Professional live show controller for schools
(macOS/Windows/Linux)

Features:
- Audio/Video/PowerPoint cue list management
- Real-time fade in/out control with FFmpeg audio filters
- Start/stop time markers for precise playback
- Live volume control with smooth transitions
- Second screen support for video playback
- PowerPoint integration with slide control (macOS)
- Keyboard shortcuts for live operation
- Professional UI with color-coded cues

Playback: ffplay (FFmpeg) subprocess - no pip dependencies required.
"""

from __future__ import annotations

import json
import platform
import shutil
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

CueKind = Literal["audio", "video", "ppt"]


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


def _format_timecode(seconds: float | None) -> str:
    if seconds is None:
        return ""
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
        self._current_fade_volume: float = 1.0  # 0.0 to 1.0 for live fade control

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
        args = self._build_ffplay_args(ffplay, cue, duration_limit=duration_limit)
        self._proc = self._spawn_ffplay(args)
        self._playing_cue = cue
        self._started_at_monotonic = time.monotonic()
        self._playing_seek_sec = float(cue.start_sec)

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

    def fade_to(self, target_volume_percent: int) -> None:
        """Live fade to target volume with smooth transition"""
        pos = self.playback_position_sec()
        if pos is None or self._playing_cue is None:
            return

        cue = self._playing_cue
        if cue.kind not in ("audio", "video"):
            return

        ffplay = shutil.which("ffplay")
        if not ffplay:
            return

        # Calculate fade filter
        target = _clamp_int(target_volume_percent, 0, 100) / 100.0
        self._current_fade_volume = target

        # Build afade filter: fade from current volume to target
        # We use volume filter for immediate effect
        audio_filter = f"volume={target:.2f}"

        # Restart with new filter
        duration_limit = None
        if cue.stop_at_sec is not None and pos < cue.stop_at_sec:
            duration_limit = float(cue.stop_at_sec) - float(pos)

        self.stop()
        args = self._build_ffplay_args(
            ffplay,
            cue,
            seek_override=float(pos),
            audio_filter=audio_filter,
            duration_limit=duration_limit,
        )
        self._proc = self._spawn_ffplay(args)
        self._playing_cue = cue
        self._started_at_monotonic = time.monotonic()
        self._playing_seek_sec = float(pos)

    def get_fade_volume(self) -> float:
        """Get current fade volume (0.0 to 1.0)"""
        return self._current_fade_volume


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
        self.title("🎬 Show Control PRO – Live Production Controller")
        self.minsize(1100, 600)

        self.settings = Settings()
        self.audio_runner = MediaRunner(self.settings)
        self.video_runner = MediaRunner(self.settings)
        self._active_runner = self.audio_runner

        self._show_path: Path | None = None
        self._loaded_preset_path: Path | None = None
        self._cues: list[Cue] = []
        self._loading_editor = False
        self._duration_cache: dict[str, float] = {}
        self._current_duration: float | None = None
        self._was_playing = False
        self._inhibit_auto_advance = False
        self._log_max_lines = 800
        self._vol_restart_after_id: str | None = None
        self._paused_cue_id: str | None = None
        self._paused_kind: CueKind | None = None
        self._paused_pos_sec: float | None = None
        self._fade_slider_updating = False

        self._build_ui()
        self._setup_keyboard_shortcuts()
        loaded = self._auto_load_preset()
        if not loaded:
            self._refresh_tree()
            self._load_selected_into_editor()
        self.after(0, self._bring_to_front)
        self._poll_playback()

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

        try:
            if hasattr(self, "status"):
                self.status.set(msg)
        except Exception:
            pass

        ts = time.strftime("%H:%M:%S")
        line = f"[{ts}] {msg}\n"
        txt = getattr(self, "log_text", None)
        if txt is None:
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
        if txt is None:
            return
        try:
            content = txt.get("1.0", "end-1c")
        except Exception:
            return
        try:
            self.clipboard_clear()
            self.clipboard_append(content)
            self._log("Log copied to clipboard.")
        except Exception:
            pass

    def _clear_log(self) -> None:
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
        root = ttk.Frame(self, padding=10)
        root.pack(fill="both", expand=True)

        main_pane = ttk.Panedwindow(root, orient="vertical")
        main_pane.pack(fill="both", expand=True)

        content = ttk.Frame(main_pane)
        log_container = ttk.Frame(main_pane)
        main_pane.add(content, weight=4)
        main_pane.add(log_container, weight=1)

        # File / preset toolbar (user-visible)
        filebar = ttk.Frame(content)
        filebar.pack(fill="x", pady=(0, 8))

        ttk.Button(filebar, text="New", command=self._new_show).pack(side="left")
        ttk.Button(filebar, text="Open…", command=self._open_show).pack(side="left", padx=(6, 0))
        ttk.Button(filebar, text="Save", command=self._save_show).pack(side="left", padx=(12, 0))
        ttk.Button(filebar, text="Save As…", command=self._save_show_as).pack(side="left", padx=(6, 0))

        ttk.Separator(filebar, orient="vertical").pack(side="left", fill="y", padx=12)
        ttk.Button(filebar, text="Save preset", command=self._save_preset).pack(side="left")
        ttk.Button(filebar, text="Load preset", command=self._load_preset).pack(side="left", padx=(6, 0))

        self.var_showfile = tk.StringVar(value="")
        ttk.Label(filebar, textvariable=self.var_showfile).pack(side="right")

        top = ttk.Frame(content)
        top.pack(fill="both", expand=True)

        # Cue list
        left = ttk.Frame(top)
        left.pack(side="left", fill="both", expand=True)

        self.tree = ttk.Treeview(
            left,
            columns=("idx", "kind", "name", "note", "start", "stop", "screen"),
            show="headings",
            selectmode="browse",
        )
        self.tree.heading("idx", text="#")
        self.tree.heading("kind", text="Type")
        self.tree.heading("name", text="File")
        self.tree.heading("note", text="Note")
        self.tree.heading("start", text="Start")
        self.tree.heading("stop", text="Stop")
        self.tree.heading("screen", text="2nd?")
        self.tree.column("idx", width=42, stretch=False, anchor="e")
        self.tree.column("kind", width=70, stretch=False)
        self.tree.column("name", width=280, stretch=True)
        self.tree.column("note", width=220, stretch=True)
        self.tree.column("start", width=70, stretch=False, anchor="e")
        self.tree.column("stop", width=70, stretch=False, anchor="e")
        self.tree.column("screen", width=55, stretch=False, anchor="center")
        self.tree.pack(fill="both", expand=True)

        # Configure color-coded tags for different cue types
        self.tree.tag_configure("audio", background="#e3f2fd")  # Light blue
        self.tree.tag_configure("video", background="#e8f5e9")  # Light green
        self.tree.tag_configure("ppt", background="#fff3e0")    # Light orange

        self.tree.bind("<<TreeviewSelect>>", lambda _e: self._load_selected_into_editor())
        self.tree.bind("<Double-1>", lambda _e: self._play_selected())

        btn_row = ttk.Frame(left, padding=(0, 8, 0, 0))
        btn_row.pack(fill="x")
        ttk.Button(btn_row, text="+ Audio", command=lambda: self._add_cue("audio")).pack(
            side="left"
        )
        ttk.Button(btn_row, text="+ Video", command=lambda: self._add_cue("video")).pack(
            side="left", padx=(6, 0)
        )
        ttk.Button(btn_row, text="+ PPT", command=lambda: self._add_cue("ppt")).pack(
            side="left", padx=(6, 0)
        )
        ttk.Button(btn_row, text="Remove", command=self._remove_selected).pack(
            side="left", padx=(14, 0)
        )
        ttk.Button(btn_row, text="Up", command=lambda: self._move_selected(-1)).pack(
            side="left", padx=(6, 0)
        )
        ttk.Button(btn_row, text="Down", command=lambda: self._move_selected(1)).pack(
            side="left", padx=(6, 0)
        )

        # Editor + controls
        right = ttk.Frame(top, padding=(12, 0, 0, 0), width=420)
        right.pack(side="left", fill="y")
        right.pack_propagate(False)

        cue_box = ttk.LabelFrame(right, text="📋 Selected Cue", padding=10)
        cue_box.pack(fill="x")

        self.var_kind = tk.StringVar()
        self.var_path = tk.StringVar()
        self.var_path_display = tk.StringVar()
        self.var_start = tk.StringVar()
        self.var_stop = tk.StringVar()
        self.var_note = tk.StringVar()
        self.var_target = tk.StringVar(value="window")

        self._add_row(cue_box, 0, "Type", ttk.Label(cue_box, textvariable=self.var_kind))
        file_row = ttk.Frame(cue_box)
        ttk.Label(
            file_row,
            textvariable=self.var_path_display,
            width=34,
            anchor="w",
        ).pack(side="left", fill="x", expand=True)
        ttk.Button(file_row, text="Copy", width=6, command=self._copy_selected_path).pack(
            side="left", padx=(8, 0)
        )
        self._add_row(cue_box, 1, "File", file_row)
        self._add_row(cue_box, 2, "Start", ttk.Label(cue_box, textvariable=self.var_start))
        self._add_row(cue_box, 3, "Stop", ttk.Label(cue_box, textvariable=self.var_stop))
        note_row = ttk.Entry(cue_box, textvariable=self.var_note, width=34)
        self._add_row(cue_box, 4, "Note", note_row)
        target_row = ttk.Frame(cue_box)
        ttk.Radiobutton(target_row, text="Window", value="window", variable=self.var_target).pack(
            side="left"
        )
        ttk.Radiobutton(
            target_row,
            text="2nd screen",
            value="second",
            variable=self.var_target,
        ).pack(side="left", padx=(10, 0))
        self._add_row(cue_box, 5, "Target", target_row)

        self.var_target.trace_add("write", lambda *_: self._save_editor_to_selected())
        self.var_note.trace_add("write", lambda *_: self._save_editor_to_selected())

        timeline_box = ttk.LabelFrame(right, text="⏱️ Timeline & Markers", padding=10)
        timeline_box.pack(fill="x", pady=(10, 0))
        self.var_dur = tk.StringVar(value="Duration: —")
        info_row = ttk.Frame(timeline_box)
        info_row.pack(fill="x")
        ttk.Label(info_row, textvariable=self.var_dur).pack(side="left")
        self.var_playhead_label = tk.StringVar(value="0:00")
        ttk.Label(info_row, textvariable=self.var_playhead_label).pack(side="right")

        self.var_playhead = tk.DoubleVar(value=0.0)
        self.scale = ttk.Scale(
            timeline_box,
            from_=0.0,
            to=1.0,
            orient="horizontal",
            variable=self.var_playhead,
        )
        self.scale.pack(fill="x", pady=(6, 0))

        self.var_playhead.trace_add("write", lambda *_: self._update_playhead_label())

        mark_row = ttk.Frame(timeline_box)
        mark_row.pack(fill="x", pady=(8, 0))
        ttk.Button(mark_row, text="⏵ Mark Start", command=self._mark_start).pack(
            side="left", expand=True, fill="x"
        )
        ttk.Button(mark_row, text="⏹ Mark Stop", command=self._mark_stop).pack(
            side="left", expand=True, fill="x", padx=(6, 0)
        )

        now_box = ttk.LabelFrame(right, text="▶️ Now Playing", padding=6)
        now_box.pack(fill="x", pady=(10, 0))
        self.var_now_title = tk.StringVar(value="—")
        self.var_now_time = tk.StringVar(value="—")
        self.var_now_line = tk.StringVar(value="—")
        ttk.Label(now_box, textvariable=self.var_now_line, anchor="w", width=42).pack(anchor="w")
        self.var_now_progress = tk.IntVar(value=0)
        self.now_progress = ttk.Progressbar(
            now_box,
            orient="horizontal",
            mode="determinate",
            maximum=1000,
            variable=self.var_now_progress,
        )
        self.now_progress.pack(fill="x", pady=(6, 0))

        play_box = ttk.LabelFrame(right, text="🎮 Transport & Live Control", padding=6)
        play_box.pack(fill="x", pady=(10, 0))

        transport_row = ttk.Frame(play_box)
        transport_row.pack(fill="x")
        ttk.Button(transport_row, text="⏪", width=3, command=lambda: self._seek_relative(-5.0)).pack(
            side="left"
        )
        ttk.Button(transport_row, text="⏯", width=3, command=self._toggle_play_pause).pack(
            side="left", padx=(6, 0)
        )
        ttk.Button(transport_row, text="⏩", width=3, command=lambda: self._seek_relative(5.0)).pack(
            side="left", padx=(6, 0)
        )
        ttk.Button(transport_row, text="⏹", width=3, command=self._stop).pack(side="left", padx=(10, 0))

        # Big GO LIVE button
        go_live_btn = ttk.Button(transport_row, text="🔴 GO LIVE!", command=self._go_live)
        go_live_btn.pack(side="right")

        # Live Fade Control
        fade_frame = ttk.LabelFrame(play_box, text="🎚️ Live Fade", padding=6)
        fade_frame.pack(fill="x", pady=(8, 0))

        fade_btn_row = ttk.Frame(fade_frame)
        fade_btn_row.pack(fill="x")
        ttk.Button(fade_btn_row, text="Fade OUT (0%)", command=lambda: self._quick_fade(0)).pack(
            side="left", expand=True, fill="x"
        )
        ttk.Button(fade_btn_row, text="Fade IN (100%)", command=lambda: self._quick_fade(100)).pack(
            side="left", expand=True, fill="x", padx=(6, 0)
        )

        # Fade slider
        self.var_fade = tk.DoubleVar(value=100.0)
        self.var_fade_label = tk.StringVar(value="100%")

        fade_slider_row = ttk.Frame(fade_frame)
        fade_slider_row.pack(fill="x", pady=(6, 0))
        ttk.Label(fade_slider_row, text="Volume:").pack(side="left")
        ttk.Scale(
            fade_slider_row,
            from_=0.0,
            to=100.0,
            orient="horizontal",
            variable=self.var_fade,
            command=lambda _v: self._on_fade_slider_change(),
        ).pack(side="left", fill="x", expand=True, padx=(6, 0))
        ttk.Label(fade_slider_row, textvariable=self.var_fade_label, width=5).pack(side="left", padx=(6, 0))

        self.var_fade.trace_add("write", lambda *_: self._update_fade_label())

        ppt_box = ttk.LabelFrame(right, text="📊 PowerPoint Control", padding=10)
        ppt_box.pack(fill="x", pady=(10, 0))
        ttk.Button(ppt_box, text="▶ Start PPT Cue", command=self._play_selected_ppt_only).pack(fill="x")
        row = ttk.Frame(ppt_box)
        row.pack(fill="x", pady=(6, 0))
        ttk.Button(row, text="◀ Prev", command=ppt_prev_slide).pack(side="left", expand=True, fill="x")
        ttk.Button(row, text="Next ▶", command=ppt_next_slide).pack(side="left", expand=True, fill="x", padx=(6, 0))
        ttk.Button(ppt_box, text="⏹ End Show", command=ppt_end_show).pack(fill="x", pady=(6, 0))

        settings_box = ttk.LabelFrame(right, text="⚙️ Settings", padding=10)
        settings_box.pack(fill="x", pady=(10, 0))

        self.var_left = tk.StringVar(value=str(self.settings.second_screen_left))
        self.var_top = tk.StringVar(value=str(self.settings.second_screen_top))
        self.var_fs = tk.BooleanVar(value=self.settings.video_fullscreen)
        self.var_vol = tk.DoubleVar(value=float(self.settings.startup_volume))
        self.var_vol_label = tk.StringVar(value=str(int(self.settings.startup_volume)))

        self._add_row(settings_box, 0, "2nd left", ttk.Entry(settings_box, textvariable=self.var_left, width=12))
        self._add_row(settings_box, 1, "2nd top", ttk.Entry(settings_box, textvariable=self.var_top, width=12))
        self._add_row(settings_box, 2, "Video fullscreen", ttk.Checkbutton(settings_box, variable=self.var_fs))
        vol_row = ttk.Frame(settings_box)
        ttk.Scale(
            vol_row,
            from_=0.0,
            to=100.0,
            orient="horizontal",
            variable=self.var_vol,
            command=lambda _v: self._on_volume_change(),
        ).pack(side="left", fill="x", expand=True)
        ttk.Label(vol_row, textvariable=self.var_vol_label, width=4).pack(side="left", padx=(8, 0))
        self._add_row(settings_box, 3, "Volume (0-100)", vol_row)

        for var in (self.var_left, self.var_top, self.var_fs):
            var.trace_add("write", lambda *_: self._apply_settings_from_vars())

        self.status = tk.StringVar(value="Ready.")
        ttk.Label(content, textvariable=self.status, padding=(0, 10, 0, 0)).pack(anchor="w")
        self._update_showfile_label()
        self._update_now_playing()

        log_box = ttk.LabelFrame(log_container, text="📝 Status & Debug Log", padding=8)
        log_box.pack(fill="both", expand=True)
        self.log_text = ScrolledText(log_box, height=4, wrap="none")
        self.log_text.pack(fill="both", expand=True)
        try:
            self.log_text.configure(state="disabled")
        except Exception:
            pass
        log_btns = ttk.Frame(log_box)
        log_btns.pack(fill="x", pady=(6, 0))
        ttk.Button(log_btns, text="Copy", command=self._copy_log).pack(side="left")
        ttk.Button(log_btns, text="Clear", command=self._clear_log).pack(side="left", padx=(6, 0))

        self._log("UI ready.")

        def _set_default_sash() -> None:
            try:
                h = int(self.winfo_height() or 0)
                if h > 360:
                    main_pane.sashpos(0, int(h * 0.86))
            except Exception:
                pass

        self.after(150, _set_default_sash)

    def _add_row(self, parent: ttk.Frame, row: int, label: str, widget: tk.Widget) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=2)
        widget.grid(row=row, column=1, sticky="w", pady=2, padx=(10, 0))
        parent.grid_columnconfigure(1, weight=1)

    def _setup_keyboard_shortcuts(self) -> None:
        """Setup keyboard shortcuts for live operation"""
        # Space: Play/Pause
        self.bind("<space>", lambda e: self._toggle_play_pause())
        # Escape: Emergency Stop
        self.bind("<Escape>", lambda e: self._stop())
        # N or Right Arrow: Next/Go Live
        self.bind("n", lambda e: self._go_live())
        self.bind("<Right>", lambda e: self._go_live())
        # F: Fade Out
        self.bind("f", lambda e: self._quick_fade(0))
        # U: Fade Up/In
        self.bind("u", lambda e: self._quick_fade(100))
        # Left/Right brackets: Seek
        self.bind("[", lambda e: self._seek_relative(-5.0))
        self.bind("]", lambda e: self._seek_relative(5.0))
        # M: Mark start
        self.bind("m", lambda e: self._mark_start())
        # Period: Mark stop
        self.bind(".", lambda e: self._mark_stop())

        self._log("⌨️  Shortcuts: Space=Play/Pause, Esc=Stop, N=Next, F=FadeOut, U=FadeIn")

    def _quick_fade(self, target_percent: int) -> None:
        """Quick fade to target volume"""
        runner, cue = self._current_playback_source()
        if runner is None or cue is None or cue.kind not in ("audio", "video"):
            self._log("No audio/video playing to fade.")
            return

        try:
            runner.fade_to(int(target_percent))
            self.var_fade.set(float(target_percent))
            self._log(f"Fade to {int(target_percent)}%")
        except Exception as e:
            self._log(f"Fade failed: {e}")

    def _on_fade_slider_change(self) -> None:
        """Handle fade slider movement"""
        if self._fade_slider_updating:
            return

        try:
            target = int(round(float(self.var_fade.get())))
        except Exception:
            return

        runner, cue = self._current_playback_source()
        if runner is None or cue is None or cue.kind not in ("audio", "video"):
            return

        try:
            self._fade_slider_updating = True
            runner.fade_to(target)
        except Exception as e:
            self._log(f"Fade error: {e}")
        finally:
            self._fade_slider_updating = False

    def _update_fade_label(self) -> None:
        """Update fade percentage label"""
        try:
            val = int(round(float(self.var_fade.get())))
            self.var_fade_label.set(f"{val}%")
        except Exception:
            pass

    # ── Data / selection ────────────────────────────────────────────────
    def _selected_cue(self) -> Cue | None:
        sel = self.tree.selection()
        if not sel:
            return None
        cue_id = sel[0]
        for c in self._cues:
            if c.id == cue_id:
                return c
        return None

    def _refresh_tree(self) -> None:
        self.tree.delete(*self.tree.get_children())
        for i, cue in enumerate(self._cues, start=1):
            self.tree.insert(
                "",
                "end",
                iid=cue.id,
                values=self._tree_values_for_cue(i, cue),
                tags=(cue.kind,)  # Apply color tag based on cue type
            )

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
        try:
            idx = next((i for i, c in enumerate(self._cues, start=1) if c.id == cue.id), 0)
            self.tree.item(cue.id, values=self._tree_values_for_cue(idx, cue))
        except Exception:
            self._refresh_tree()

    def _load_selected_into_editor(self) -> None:
        cue = self._selected_cue()
        self._loading_editor = True
        try:
            if not cue:
                self.var_kind.set("")
                self.var_path.set("")
                self.var_path_display.set("")
                self.var_start.set("")
                self.var_stop.set("")
                self.var_note.set("")
                self.var_target.set("window")
                self._set_timeline(None)
                return
            self.var_kind.set(cue.kind)
            self.var_path.set(cue.path)
            short = _shorten_middle(str(cue.path), 52)
            self.var_path_display.set(short)
            self.var_start.set(_format_timecode(cue.start_sec))
            self.var_stop.set(_format_timecode(cue.stop_at_sec))
            self.var_note.set(cue.note or "")
            self.var_target.set("second" if cue.open_on_second_screen else "window")
            self._set_timeline(self._duration_for_cue(cue))
        finally:
            self._loading_editor = False

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
            cue_obj = next((c for c in self._cues if c.id == self._paused_cue_id), None)
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
        ids = [c.id for c in self._cues]
        try:
            idx = ids.index(cue_id)
        except ValueError:
            return False
        if idx + 1 >= len(ids):
            return False
        next_id = ids[idx + 1]
        try:
            self.tree.selection_set(next_id)
            self.tree.see(next_id)
        except Exception:
            return False
        return True

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
            if not self.tree.selection() and self._cues:
                try:
                    self.tree.selection_set(self._cues[0].id)
                    self.tree.see(self._cues[0].id)
                except Exception:
                    pass
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
            cue_obj = next((c for c in self._cues if c.id == self._paused_cue_id), None)
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
                cue_obj = next((c for c in self._cues if c.id == playing.id), None)
                if cue_obj is None:
                    cue_obj = self._selected_cue()
                if cue_obj is not None:
                    try:
                        sel = self.tree.selection()
                        if not sel or sel[0] != cue_obj.id:
                            self.tree.selection_set(cue_obj.id)
                            self.tree.see(cue_obj.id)
                    except Exception:
                        pass
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
        try:
            self._update_now_playing()
            is_playing = self._active_runner.is_playing()
            if self._was_playing and not is_playing:
                last_exit = getattr(self._active_runner, "last_exit_code", None)
                if last_exit not in (None, 0):
                    self._log("Playback failed.")
                    try:
                        self._log(self._active_runner.debug_text())  # type: ignore[attr-defined]
                    except Exception:
                        pass
                    self._inhibit_auto_advance = False
                    self._was_playing = is_playing
                    return
                if self._inhibit_auto_advance:
                    self._inhibit_auto_advance = False
                else:
                    try:
                        cue = self._active_runner.current_cue()
                        if cue:
                            self._log(f"Finished: {cue.display_name()}")
                    except Exception:
                        pass
                    self._select_next_cue()
            self._was_playing = is_playing
        finally:
            self.after(250, self._poll_playback)

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
        if getattr(self, "var_now_title", None) is None:
            return

        runner, cue = self._current_playback_source()
        if runner is None or cue is None or cue.kind == "ppt":
            self.var_now_title.set("—")
            self.var_now_time.set("—")
            self.var_now_line.set("—")
            self.var_now_progress.set(0)
            return

        pos = None
        length = None
        try:
            pos = runner.playback_position_sec()  # type: ignore[attr-defined]
        except Exception:
            pos = None
        try:
            length = runner.playback_length_sec()  # type: ignore[attr-defined]
        except Exception:
            length = None
        if length is None:
            length = self._duration_for_cue(cue)

        # Prefer cue end markers if present.
        end_for_display = cue.stop_at_sec if cue.stop_at_sec is not None else length

        title = f"{cue.kind}: {cue.display_name()}"
        self.var_now_title.set(title)

        if pos is None:
            self.var_now_time.set("…")
            self.var_now_line.set(_shorten_middle(title, 60))
            self.var_now_progress.set(0)
            return

        tail = ""
        if cue.start_sec:
            tail = f" (start {_format_timecode(cue.start_sec)})"
        if end_for_display is not None:
            self.var_now_time.set(f"{_format_timecode(pos)} / {_format_timecode(end_for_display)}{tail}")
        else:
            self.var_now_time.set(f"{_format_timecode(pos)}{tail}")
        self.var_now_line.set(_shorten_middle(f"{title}  {self.var_now_time.get()}", 72))

        seg_start = float(cue.start_sec or 0.0)
        seg_end = (
            float(end_for_display)
            if end_for_display is not None and float(end_for_display) > seg_start
            else None
        )
        if seg_end is None:
            self.var_now_progress.set(0)
            return

        seg_len = max(0.001, seg_end - seg_start)
        seg_pos = max(0.0, min(seg_len, float(pos) - seg_start))
        frac = max(0.0, min(1.0, seg_pos / seg_len))
        self.var_now_progress.set(int(round(frac * 1000)))

    def _select_next_cue(self) -> None:
        sel = self.tree.selection()
        if not sel:
            return
        current = sel[0]
        ids = [c.id for c in self._cues]
        try:
            idx = ids.index(current)
        except ValueError:
            return
        if idx + 1 >= len(ids):
            return
        next_id = ids[idx + 1]
        self.tree.selection_set(next_id)
        self.tree.see(next_id)
        self._log("Ready on next cue.")

    # ── File IO ─────────────────────────────────────────────────────────
    def _new_show(self) -> None:
        if self._cues and not messagebox.askyesno("New", "Discard current show?"):
            return
        self._show_path = None
        self._loaded_preset_path = None
        self._cues = []
        self._refresh_tree()
        self._load_selected_into_editor()
        self._log("New show.")
        self._update_showfile_label()

    def _load_show_from_path(self, path: Path, *, set_show_path: bool) -> None:
        data = json.loads(path.read_text(encoding="utf-8"))
        self.settings = Settings.from_dict(data.get("settings", {}))
        self.audio_runner.settings = self.settings
        self.video_runner.settings = self.settings
        self._cues = [Cue.from_dict(x) for x in data.get("cues", [])]
        self._show_path = path if set_show_path else None

        self.var_left.set(str(self.settings.second_screen_left))
        self.var_top.set(str(self.settings.second_screen_top))
        self.var_fs.set(bool(self.settings.video_fullscreen))
        try:
            self.var_vol.set(float(self.settings.startup_volume))
            self.var_vol_label.set(str(int(self.settings.startup_volume)))
        except Exception:
            pass

        self._refresh_tree()
        self._load_selected_into_editor()
        self._update_showfile_label()
        try:
            where = f"show file {path.name}" if set_show_path else f"preset {path.name}"
            self._log(f"Loaded {len(self._cues)} cues from {where}.")
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
            self.var_showfile.set(f"Show: {self._show_path.name}")
            return
        if self._loaded_preset_path:
            self.var_showfile.set(f"Preset: {self._loaded_preset_path.name}")
            return
        self.var_showfile.set("Show: (unsaved)")

    def _save_preset(self) -> None:
        path = self._preset_path()
        try:
            payload = {
                "version": 1,
                "settings": self.settings.to_dict(),
                "cues": [c.to_dict() for c in self._cues],
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

    def _write_show(self, path: Path) -> None:
        try:
            payload = {
                "version": 1,
                "settings": self.settings.to_dict(),
                "cues": [c.to_dict() for c in self._cues],
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
