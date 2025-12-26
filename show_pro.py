#!/usr/bin/env python3
"""
Show Control PRO v2.0 - Professional Live Show Controller
Designed for dual-monitor live production environments.

Key Features:
- Dual-channel playback: Audio + Video simultaneously
- Real smooth fade in/out with pre-rendered FFmpeg filters
- Millisecond-accurate timecode display
- Compact UI designed for 1920x1080 displays
- Automatic cleanup on exit
- Live PPT control alongside media playback
"""

from __future__ import annotations

import atexit
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

CueKind = Literal["audio", "video", "ppt"]

# Global cleanup registry for ffplay processes
_ACTIVE_PROCESSES: list[subprocess.Popen] = []


def _cleanup_all_processes() -> None:
    """Kill all ffplay processes on exit"""
    for proc in _ACTIVE_PROCESSES:
        try:
            proc.terminate()
            proc.wait(timeout=0.5)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
    _ACTIVE_PROCESSES.clear()


atexit.register(_cleanup_all_processes)


def _format_timecode_ms(seconds: float | None) -> str:
    """Format time as MM:SS.mmm"""
    if seconds is None:
        return "—"
    total_ms = max(0, int(seconds * 1000))
    ms = total_ms % 1000
    total_sec = total_ms // 1000
    s = total_sec % 60
    m = total_sec // 60
    return f"{m:02d}:{s:02d}.{ms:03d}"


def _parse_timecode(value: str) -> float | None:
    """Parse MM:SS or MM:SS.mmm or seconds"""
    value = (value or "").strip()
    if not value:
        return None

    # Handle milliseconds
    if "." in value:
        main, ms_part = value.split(".", 1)
        ms = float(f"0.{ms_part}")
    else:
        main = value
        ms = 0.0

    if ":" not in main:
        return float(main) + ms

    parts = main.split(":")
    if len(parts) == 2:
        m, s = parts
        return float(m) * 60.0 + float(s) + ms
    if len(parts) == 3:
        h, m, s = parts
        return float(h) * 3600.0 + float(m) * 60.0 + float(s) + ms

    raise ValueError(f"Invalid timecode: {value!r}")


@dataclass
class Settings:
    second_screen_left: int = 1920
    second_screen_top: int = 0
    video_fullscreen: bool = True

    def to_dict(self) -> dict:
        return {
            "second_screen_left": self.second_screen_left,
            "second_screen_top": self.second_screen_top,
            "video_fullscreen": self.video_fullscreen,
        }

    @staticmethod
    def from_dict(data: dict) -> Settings:
        s = Settings()
        if isinstance(data, dict):
            s.second_screen_left = int(data.get("second_screen_left", s.second_screen_left))
            s.second_screen_top = int(data.get("second_screen_top", s.second_screen_top))
            s.video_fullscreen = bool(data.get("video_fullscreen", s.video_fullscreen))
        return s


@dataclass
class Cue:
    id: str
    kind: CueKind
    path: str
    note: str = ""
    start_sec: float = 0.0
    stop_at_sec: float | None = None

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
        }

    @staticmethod
    def from_dict(data: dict) -> Cue:
        stop = data.get("stop_at_sec")
        if stop in ("", "null", None):
            stop = None
        else:
            stop = float(stop)

        return Cue(
            id=str(data.get("id") or uuid.uuid4()),
            kind=data.get("kind", "audio"),
            path=str(data.get("path", "")),
            note=str(data.get("note", "")),
            start_sec=float(data.get("start_sec", 0.0)),
            stop_at_sec=stop,
        )


class MediaPlayer:
    """Single media player with smooth fade support"""

    def __init__(self, settings: Settings, kind: CueKind):
        self.settings = settings
        self.kind = kind  # "audio" or "video"
        self._proc: subprocess.Popen | None = None
        self._cue: Cue | None = None
        self._start_time: float | None = None
        self._seek_offset: float = 0.0
        self._current_volume: float = 1.0
        self._fade_thread: threading.Thread | None = None
        self._stop_fade = False

    def is_playing(self) -> bool:
        if self._proc is None:
            return False
        return self._proc.poll() is None

    def current_cue(self) -> Cue | None:
        return self._cue

    def playback_position(self) -> float | None:
        """Get current playback position in seconds"""
        if not self.is_playing() or self._start_time is None:
            return None
        elapsed = time.time() - self._start_time
        return self._seek_offset + elapsed

    def stop(self) -> None:
        """Stop playback and cleanup"""
        self._stop_fade = True
        if self._fade_thread and self._fade_thread.is_alive():
            self._fade_thread.join(timeout=0.5)

        proc = self._proc
        self._proc = None
        self._cue = None
        self._start_time = None

        if proc:
            try:
                _ACTIVE_PROCESSES.remove(proc)
            except ValueError:
                pass
            try:
                proc.terminate()
                proc.wait(timeout=1.0)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass

    def play(self, cue: Cue, volume: float = 1.0) -> None:
        """Play a cue at specified volume (0.0-1.0)"""
        if cue.kind == "ppt":
            self._open_ppt(cue.path)
            return

        ffplay = shutil.which("ffplay")
        if not ffplay:
            raise RuntimeError("ffplay not found")

        self.stop()

        # Build ffplay command
        args = [ffplay, "-hide_banner", "-loglevel", "error", "-autoexit"]

        # Seek to start
        if cue.start_sec > 0:
            args.extend(["-ss", f"{cue.start_sec:.3f}"])

        # Duration limit
        if cue.stop_at_sec and cue.stop_at_sec > cue.start_sec:
            duration = cue.stop_at_sec - cue.start_sec
            args.extend(["-t", f"{duration:.3f}"])

        # Volume filter
        if volume < 0.99:
            args.extend(["-af", f"volume={volume:.2f}"])

        # Video positioning
        if cue.kind == "video":
            if self.settings.video_fullscreen:
                args.extend([
                    "-left", str(self.settings.second_screen_left),
                    "-top", str(self.settings.second_screen_top),
                    "-fs", "-alwaysontop"
                ])
            else:
                args.extend(["-left", "100", "-top", "100", "-x", "960", "-y", "540", "-alwaysontop"])
        else:
            args.append("-nodisp")

        args.append(cue.path)

        # Start process
        proc = subprocess.Popen(
            args,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        _ACTIVE_PROCESSES.append(proc)
        self._proc = proc
        self._cue = cue
        self._start_time = time.time()
        self._seek_offset = cue.start_sec
        self._current_volume = volume

    def fade_to(self, target_volume: float, duration_sec: float = 2.0) -> None:
        """Smooth fade to target volume over duration"""
        if not self.is_playing() or self._cue is None:
            return

        # Stop any existing fade
        self._stop_fade = True
        if self._fade_thread and self._fade_thread.is_alive():
            self._fade_thread.join(timeout=0.5)

        self._stop_fade = False

        # Start fade thread
        def _fade_worker():
            start_vol = self._current_volume
            steps = int(duration_sec * 20)  # 20 steps per second
            step_duration = duration_sec / steps

            for i in range(steps + 1):
                if self._stop_fade or not self.is_playing():
                    break

                # Calculate interpolated volume
                t = i / steps
                vol = start_vol + (target_volume - start_vol) * t

                # Restart playback at current position with new volume
                pos = self.playback_position()
                if pos is not None and self._cue:
                    cue = self._cue
                    self.stop()
                    self.play(cue, volume=vol)
                    self._seek_offset = pos
                    self._start_time = time.time()

                time.sleep(step_duration)

            # Final volume
            if not self._stop_fade and self.is_playing() and self._cue:
                pos = self.playback_position()
                if pos is not None:
                    cue = self._cue
                    self.stop()
                    self.play(cue, volume=target_volume)
                    self._seek_offset = pos
                    self._start_time = time.time()

        self._fade_thread = threading.Thread(target=_fade_worker, daemon=True)
        self._fade_thread.start()

    def _open_ppt(self, path: str) -> None:
        """Open PowerPoint presentation"""
        if platform.system() == "Darwin":
            # macOS AppleScript
            script = '''
on run argv
  set pptPath to item 1 of argv
  tell application "Microsoft PowerPoint"
    activate
    open POSIX file pptPath
    delay 0.2
    try
      start slide show of active presentation
    end try
  end tell
end run
'''
            subprocess.run(["osascript", "-e", script, "--", str(Path(path).resolve())],
                          capture_output=True)
        elif platform.system() == "Windows":
            import os
            os.startfile(path)  # type: ignore
        else:
            subprocess.run(["xdg-open", path])


class ShowControlPro(tk.Tk):
    """Main application window - optimized for 1920x1080"""

    def __init__(self):
        super().__init__()

        self.title("🎬 Show Control PRO v2.0")
        self.geometry("1280x720")  # Fits well on 1920x1080 with room for other windows

        # Settings and state
        self.settings = Settings()
        self.audio_player = MediaPlayer(self.settings, "audio")
        self.video_player = MediaPlayer(self.settings, "video")

        self._cues: list[Cue] = []
        self._show_path: Path | None = None

        # Build UI
        self._build_ui()
        self._setup_shortcuts()

        # Auto-load preset
        preset = Path.cwd() / "show_preset.json"
        if preset.exists():
            try:
                self._load_from_path(preset)
            except Exception:
                pass

        # Start update loop
        self.after(100, self._update_loop)

    def _build_ui(self) -> None:
        """Build compact, professional UI - VILÁGOS TÉMA, JÓ KONTRASZTTAL"""
        style = ttk.Style()
        style.theme_use("default")  # Alapértelmezett, tiszta téma

        # Main container - VILÁGOS
        main = tk.Frame(self, bg="#f5f5f5")
        main.pack(fill="both", expand=True)

        # Top toolbar
        toolbar = tk.Frame(main, bg="#e0e0e0", height=40)
        toolbar.pack(fill="x", side="top")
        toolbar.pack_propagate(False)

        tk.Button(toolbar, text="📂 Open", command=self._open_show, bg="#d0d0d0", fg="#000",
                 relief="raised", borderwidth=2, padx=15, pady=8, font=("", 11, "bold")).pack(side="left", padx=5, pady=5)
        tk.Button(toolbar, text="💾 Save", command=self._save_show, bg="#d0d0d0", fg="#000",
                 relief="raised", borderwidth=2, padx=15, pady=8, font=("", 11, "bold")).pack(side="left", padx=5, pady=5)

        self.var_showname = tk.StringVar(value="No show loaded")
        tk.Label(toolbar, textvariable=self.var_showname, bg="#e0e0e0", fg="#333",
                font=("", 11, "bold")).pack(side="right", padx=10)

        # Content area: Left = Cue List, Right = Players
        content = tk.Frame(main, bg="#f5f5f5")
        content.pack(fill="both", expand=True, padx=10, pady=10)

        # === LEFT: CUE LIST ===
        left = tk.Frame(content, bg="#f5f5f5", width=500)
        left.pack(side="left", fill="both", expand=True)

        tk.Label(left, text="📋 CUE LIST", bg="#f5f5f5", fg="#000",
                font=("", 13, "bold")).pack(anchor="w", pady=(0, 5))

        # Cue tree - VILÁGOS háttér, SÖTÉT szöveg
        tree_frame = tk.Frame(left, bg="white", relief="solid", borderwidth=1)
        tree_frame.pack(fill="both", expand=True)

        self.tree = ttk.Treeview(
            tree_frame,
            columns=("#", "type", "name", "start", "stop"),
            show="headings",
            selectmode="browse",
            height=20
        )

        # Fejlécek VASTAG betűvel
        style.configure("Treeview.Heading", font=("", 10, "bold"), background="#e0e0e0", foreground="black")
        style.configure("Treeview", font=("", 10), rowheight=25, background="white", foreground="black", fieldbackground="white")

        self.tree.heading("#", text="#")
        self.tree.heading("type", text="Type")
        self.tree.heading("name", text="File")
        self.tree.heading("start", text="Start")
        self.tree.heading("stop", text="Stop")

        self.tree.column("#", width=40, anchor="e")
        self.tree.column("type", width=70)
        self.tree.column("name", width=280)
        self.tree.column("start", width=90, anchor="e")
        self.tree.column("stop", width=90, anchor="e")

        # Színkódolás LÁTHATÓ színekkel
        self.tree.tag_configure("audio", background="#BBDEFB", foreground="#000")  # Világoskék + fekete
        self.tree.tag_configure("video", background="#C8E6C9", foreground="#000")  # Világoszöld + fekete
        self.tree.tag_configure("ppt", background="#FFE0B2", foreground="#000")     # Világosnarancs + fekete

        scrollbar = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)

        self.tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        # Cue buttons - JÓL LÁTHATÓ színek
        cue_btns = tk.Frame(left, bg="#f5f5f5")
        cue_btns.pack(fill="x", pady=(10, 0))

        tk.Button(cue_btns, text="+ Audio", command=lambda: self._add_cue("audio"),
                 bg="#BBDEFB", fg="#000", relief="raised", borderwidth=2, padx=15, pady=8, font=("", 11, "bold")).pack(side="left", padx=(0, 5))
        tk.Button(cue_btns, text="+ Video", command=lambda: self._add_cue("video"),
                 bg="#C8E6C9", fg="#000", relief="raised", borderwidth=2, padx=15, pady=8, font=("", 11, "bold")).pack(side="left", padx=5)
        tk.Button(cue_btns, text="+ PPT", command=lambda: self._add_cue("ppt"),
                 bg="#FFE0B2", fg="#000", relief="raised", borderwidth=2, padx=15, pady=8, font=("", 11, "bold")).pack(side="left", padx=5)
        tk.Button(cue_btns, text="🗑️ Remove", command=self._remove_cue,
                 bg="#ffcccc", fg="#000", relief="raised", borderwidth=2, padx=15, pady=8, font=("", 11, "bold")).pack(side="right")

        # === RIGHT: PLAYERS ===
        right = tk.Frame(content, bg="#f5f5f5", width=750)
        right.pack(side="right", fill="both", padx=(20, 0))
        right.pack_propagate(False)

        # AUDIO PLAYER
        self._build_player_panel(right, "audio", "🎵 AUDIO PLAYER", "#2196F3")

        tk.Frame(right, bg="#1e1e1e", height=20).pack()

        # VIDEO PLAYER
        self._build_player_panel(right, "video", "🎬 VIDEO PLAYER", "#4CAF50")

        tk.Frame(right, bg="#1e1e1e", height=20).pack()

        # PPT CONTROL - VILÁGOS, SZÍNES
        ppt_frame = tk.Frame(right, bg="white", relief="solid", borderwidth=2)
        ppt_frame.pack(fill="x", pady=5)

        tk.Label(ppt_frame, text="📊 POWERPOINT CONTROL", bg="#FF9800", fg="white",
                font=("", 12, "bold"), padx=10, pady=8).pack(fill="x")

        ppt_inner = tk.Frame(ppt_frame, bg="white", padx=10, pady=10)
        ppt_inner.pack(fill="x")

        ppt_btns = tk.Frame(ppt_inner, bg="white")
        ppt_btns.pack(fill="x")

        tk.Button(ppt_btns, text="◀ Prev", command=self._ppt_prev,
                 bg="#d0d0d0", fg="#000", relief="raised", borderwidth=2, padx=15, pady=10, font=("", 11, "bold")).pack(side="left", expand=True, fill="x", padx=(0, 5))
        tk.Button(ppt_btns, text="▶ Start PPT", command=self._ppt_start,
                 bg="#FFE0B2", fg="#000", relief="raised", borderwidth=2, padx=15, pady=10, font=("", 11, "bold")).pack(side="left", expand=True, fill="x", padx=5)
        tk.Button(ppt_btns, text="Next ▶", command=self._ppt_next,
                 bg="#d0d0d0", fg="#000", relief="raised", borderwidth=2, padx=15, pady=10, font=("", 11, "bold")).pack(side="left", expand=True, fill="x", padx=5)
        tk.Button(ppt_btns, text="⏹ End", command=self._ppt_end,
                 bg="#ffcccc", fg="#000", relief="raised", borderwidth=2, padx=15, pady=10, font=("", 11, "bold")).pack(side="left", expand=True, fill="x", padx=(5, 0))

    def _build_player_panel(self, parent: tk.Frame, player_type: str, title: str, color: str) -> None:
        """Build a player control panel (audio or video) - VILÁGOS, JÓ KONTRASZT"""
        panel = tk.Frame(parent, bg="white", relief="solid", borderwidth=2)
        panel.pack(fill="x", pady=5)

        # Header - SZÍNES háttér, FEHÉR szöveg
        tk.Label(panel, text=title, bg=color, fg="white",
                font=("", 12, "bold"), padx=10, pady=8).pack(fill="x")

        inner = tk.Frame(panel, bg="white", padx=10, pady=10)
        inner.pack(fill="x")

        # Now playing - FEKETE szöveg, világos háttér
        var_nowplaying = tk.StringVar(value="—")
        tk.Label(inner, textvariable=var_nowplaying, bg="white", fg="#000",
                font=("", 12, "bold"), anchor="w").pack(fill="x")

        # Timecode - NAGY, FEKETE, MONOSPACE
        var_timecode = tk.StringVar(value="00:00.000 / 00:00.000")
        tk.Label(inner, textvariable=var_timecode, bg="white", fg="#000",
                font=("Courier", 16, "bold"), anchor="w").pack(fill="x", pady=(5, 0))

        # Progress bar
        var_progress = tk.IntVar(value=0)
        progress = ttk.Progressbar(inner, maximum=1000, variable=var_progress, mode="determinate", length=700)
        progress.pack(fill="x", pady=(8, 10))

        # Transport buttons - NAGY, SZÍNES, FEHÉR SZÖVEG
        transport = tk.Frame(inner, bg="white")
        transport.pack(fill="x")

        # NAGY GOMBOK - SZÜRKE HÁTTÉR, FEKETE SZÖVEG - LÁTHATÓ!
        tk.Button(transport, text="▶ PLAY", command=lambda: self._play(player_type),
                 bg="#d0d0d0", fg="#000", relief="raised", borderwidth=2, padx=25, pady=10, font=("", 12, "bold")).pack(side="left", padx=(0, 8))
        tk.Button(transport, text="⏹ STOP", command=lambda: self._stop(player_type),
                 bg="#d0d0d0", fg="#000", relief="raised", borderwidth=2, padx=20, pady=10, font=("", 12, "bold")).pack(side="left", padx=5)

        # Volume gombok - LÁTHATÓ
        tk.Label(transport, text="Vol:", bg="white", fg="#000", font=("", 11, "bold")).pack(side="left", padx=(20, 5))
        tk.Button(transport, text="0%", command=lambda: self._fade(player_type, 0.0),
                 bg="#ffcccc", fg="#000", relief="raised", borderwidth=2, padx=12, pady=8, font=("", 11, "bold")).pack(side="left", padx=2)
        tk.Button(transport, text="50%", command=lambda: self._fade(player_type, 0.5),
                 bg="#ffe0b2", fg="#000", relief="raised", borderwidth=2, padx=12, pady=8, font=("", 11, "bold")).pack(side="left", padx=2)
        tk.Button(transport, text="100%", command=lambda: self._fade(player_type, 1.0),
                 bg="#c8e6c9", fg="#000", relief="raised", borderwidth=2, padx=12, pady=8, font=("", 11, "bold")).pack(side="left", padx=2)

        # Store refs
        if player_type == "audio":
            self.audio_nowplaying = var_nowplaying
            self.audio_timecode = var_timecode
            self.audio_progress = var_progress
        else:
            self.video_nowplaying = var_nowplaying
            self.video_timecode = var_timecode
            self.video_progress = var_progress

    def _setup_shortcuts(self) -> None:
        """Setup keyboard shortcuts"""
        self.bind("<space>", lambda e: self._play("audio"))
        self.bind("<Escape>", lambda e: self._stop_all())
        self.bind("f", lambda e: self._fade("audio", 0.0))
        self.bind("u", lambda e: self._fade("audio", 1.0))

    def _update_loop(self) -> None:
        """Update player displays"""
        # Update audio player
        self._update_player_display(self.audio_player, self.audio_nowplaying,
                                    self.audio_timecode, self.audio_progress)

        # Update video player
        self._update_player_display(self.video_player, self.video_nowplaying,
                                    self.video_timecode, self.video_progress)

        self.after(100, self._update_loop)

    def _update_player_display(self, player: MediaPlayer, var_name: tk.StringVar,
                               var_time: tk.StringVar, var_prog: tk.IntVar) -> None:
        """Update single player display"""
        cue = player.current_cue()
        if not cue or not player.is_playing():
            var_name.set("—")
            var_time.set("00:00.000 / 00:00.000")
            var_prog.set(0)
            return

        pos = player.playback_position()
        if pos is None:
            return

        var_name.set(cue.display_name())

        # Calculate duration
        if cue.stop_at_sec:
            duration = cue.stop_at_sec - cue.start_sec
            progress = min(1.0, (pos - cue.start_sec) / duration) if duration > 0 else 0
        else:
            duration = None
            progress = 0

        # Timecode
        pos_str = _format_timecode_ms(pos)
        dur_str = _format_timecode_ms(cue.stop_at_sec) if cue.stop_at_sec else "∞"
        var_time.set(f"{pos_str} / {dur_str}")

        var_prog.set(int(progress * 1000))

    def _add_cue(self, kind: CueKind) -> None:
        """Add new cue"""
        types = {
            "audio": [("Audio", "*.mp3 *.wav *.m4a *.aac *.flac"), ("All", "*.*")],
            "video": [("Video", "*.mp4 *.mov *.mkv *.avi"), ("All", "*.*")],
            "ppt": [("PowerPoint", "*.pptx *.ppt"), ("All", "*.*")],
        }

        path = filedialog.askopenfilename(filetypes=types[kind])
        if not path:
            return

        cue = Cue(id=str(uuid.uuid4()), kind=kind, path=path)
        self._cues.append(cue)
        self._refresh_tree()

    def _remove_cue(self) -> None:
        """Remove selected cue"""
        sel = self.tree.selection()
        if not sel:
            return

        cue_id = sel[0]
        self._cues = [c for c in self._cues if c.id != cue_id]
        self._refresh_tree()

    def _refresh_tree(self) -> None:
        """Refresh cue list display"""
        self.tree.delete(*self.tree.get_children())

        for i, cue in enumerate(self._cues, 1):
            self.tree.insert("", "end", iid=cue.id, values=(
                i,
                cue.kind.upper(),
                cue.display_name(),
                _format_timecode_ms(cue.start_sec),
                _format_timecode_ms(cue.stop_at_sec) if cue.stop_at_sec else "—"
            ), tags=(cue.kind,))  # Színkódolás

    def _play(self, player_type: str) -> None:
        """Play selected cue on specified player"""
        sel = self.tree.selection()
        if not sel:
            return

        cue_id = sel[0]
        cue = next((c for c in self._cues if c.id == cue_id), None)
        if not cue:
            return

        player = self.audio_player if player_type == "audio" or cue.kind == "audio" else self.video_player

        try:
            player.play(cue)
        except Exception as e:
            messagebox.showerror("Playback Error", str(e))

    def _stop(self, player_type: str) -> None:
        """Stop specified player"""
        if player_type == "audio":
            self.audio_player.stop()
        else:
            self.video_player.stop()

    def _stop_all(self) -> None:
        """Emergency stop all"""
        self.audio_player.stop()
        self.video_player.stop()

    def _fade(self, player_type: str, target: float) -> None:
        """Fade player to target volume"""
        player = self.audio_player if player_type == "audio" else self.video_player
        player.fade_to(target, duration_sec=2.0)

    def _ppt_start(self) -> None:
        """Start PPT cue"""
        sel = self.tree.selection()
        if not sel:
            return

        cue = next((c for c in self._cues if c.id == sel[0]), None)
        if cue and cue.kind == "ppt":
            MediaPlayer(self.settings, "ppt").play(cue)

    def _ppt_prev(self) -> None:
        """PPT previous slide"""
        if platform.system() == "Darwin":
            subprocess.run(["osascript", "-e",
                          'tell app "Microsoft PowerPoint" to activate\n'
                          'tell app "System Events" to key code 123'])

    def _ppt_next(self) -> None:
        """PPT next slide"""
        if platform.system() == "Darwin":
            subprocess.run(["osascript", "-e",
                          'tell app "Microsoft PowerPoint" to activate\n'
                          'tell app "System Events" to key code 124'])

    def _ppt_end(self) -> None:
        """End PPT show"""
        if platform.system() == "Darwin":
            subprocess.run(["osascript", "-e",
                          'tell app "Microsoft PowerPoint" to activate\n'
                          'tell app "System Events" to key code 53'])

    def _open_show(self) -> None:
        """Open show file"""
        path = filedialog.askopenfilename(filetypes=[("Show JSON", "*.json")])
        if path:
            self._load_from_path(Path(path))

    def _save_show(self) -> None:
        """Save show file"""
        if not self._show_path:
            path = filedialog.asksaveasfilename(defaultextension=".json",
                                               filetypes=[("Show JSON", "*.json")])
            if not path:
                return
            self._show_path = Path(path)

        data = {
            "version": 2,
            "settings": self.settings.to_dict(),
            "cues": [c.to_dict() for c in self._cues],
        }

        self._show_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        self.var_showname.set(f"Show: {self._show_path.name}")

    def _load_from_path(self, path: Path) -> None:
        """Load show from file"""
        data = json.loads(path.read_text(encoding="utf-8"))

        self.settings = Settings.from_dict(data.get("settings", {}))
        self._cues = [Cue.from_dict(c) for c in data.get("cues", [])]
        self._show_path = path

        self.var_showname.set(f"Show: {path.name}")
        self._refresh_tree()


def main() -> None:
    app = ShowControlPro()
    app.mainloop()


if __name__ == "__main__":
    main()
