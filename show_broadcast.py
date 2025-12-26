#!/usr/bin/env python3
"""
Broadcast-Grade Show Controller
Professional live show automation (Windows/macOS)
Based on Mairlist/RadioDJ/SAM Broadcaster design patterns
"""

from __future__ import annotations

import atexit
import json
import os
import platform
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Optional

import tkinter as tk
from tkinter import filedialog, messagebox, ttk, font as tkfont

# =====================================================================
# GLOBALS
# =====================================================================

CueKind = Literal["audio", "video", "ppt"]
_ALL_PROCESSES = []


def _cleanup_all():
    for proc in _ALL_PROCESSES:
        try:
            proc.terminate()
            proc.wait(timeout=1)
        except:
            pass


atexit.register(_cleanup_all)

# =====================================================================
# COLORS - Dark Theme like RadioDJ/SAM Broadcaster
# =====================================================================

class Theme:
    # Main colors
    BG_DARK = "#1e1e1e"          # Main background
    BG_PANEL = "#2d2d2d"         # Panel background
    BG_CONTROL = "#3a3a3a"       # Control background

    # Text colors
    TEXT_WHITE = "#ffffff"       # Primary text
    TEXT_GRAY = "#b0b0b0"        # Secondary text
    TEXT_DIM = "#808080"         # Disabled text

    # Accent colors
    ACCENT_BLUE = "#00aaff"      # Primary accent (teal/blue)
    ACCENT_GREEN = "#00cc66"     # Success/play
    ACCENT_RED = "#ff3333"       # Stop/danger
    ACCENT_ORANGE = "#ff9933"    # Warning/markers

    # Player deck colors
    DECK_A = "#2a4d6e"           # Audio deck
    DECK_B = "#2a6e4d"           # Video deck

    # Cue list colors
    CUE_AUDIO = "#1a3a52"        # Dark blue
    CUE_VIDEO = "#1a5238"        # Dark green
    CUE_PPT = "#523a1a"          # Dark orange

    # Button states
    BTN_NORMAL = "#4a4a4a"
    BTN_HOVER = "#5a5a5a"
    BTN_ACTIVE = "#3a3a3a"


# =====================================================================
# UTILITY FUNCTIONS
# =====================================================================

def _format_tc(seconds: float | None) -> str:
    """Format MM:SS.mmm"""
    if seconds is None:
        return "00:00.000"
    ms = max(0, int(seconds * 1000))
    s = (ms // 1000) % 60
    m = ms // 60000
    msec = ms % 1000
    return f"{m:02d}:{s:02d}.{msec:03d}"


def _parse_tc(value: str) -> float | None:
    """Parse timecode"""
    value = (value or "").strip()
    if not value:
        return None
    if ":" not in value:
        return float(value)
    parts = value.split(":")
    if len(parts) == 2:
        return float(parts[0]) * 60.0 + float(parts[1])
    return None


def _get_duration(path: str) -> float:
    """Get media duration"""
    try:
        cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration",
               "-of", "default=noprint_wrappers=1:nokey=1", path]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        return float(result.stdout.strip())
    except:
        return 0.0


def _shorten(text: str, max_len: int = 35) -> str:
    """Shorten text"""
    if len(text) <= max_len:
        return text
    return text[:max_len-3] + "..."


# =====================================================================
# DATA MODELS
# =====================================================================

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
    def from_dict(d: dict) -> Settings:
        return Settings(
            second_screen_left=d.get("second_screen_left", 1920),
            second_screen_top=d.get("second_screen_top", 0),
            video_fullscreen=d.get("video_fullscreen", True),
            startup_volume=d.get("startup_volume", 100),
        )


@dataclass
class Cue:
    id: str
    kind: CueKind
    path: str
    note: str = ""
    start_sec: float = 0.0
    stop_at_sec: Optional[float] = None
    open_on_second_screen: bool = False

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind,
            "path": self.path,
            "note": self.note,
            "start_sec": self.start_sec,
            "stop_at_sec": self.stop_at_sec,
            "open_on_second_screen": self.open_on_second_screen,
        }

    @staticmethod
    def from_dict(d: dict) -> Cue:
        return Cue(
            id=d.get("id", str(uuid.uuid4())),
            kind=d["kind"],
            path=d["path"],
            note=d.get("note", ""),
            start_sec=d.get("start_sec", 0.0),
            stop_at_sec=d.get("stop_at_sec"),
            open_on_second_screen=d.get("open_on_second_screen", False),
        )


# =====================================================================
# MEDIA PLAYER
# =====================================================================

class MediaPlayer:
    """Media player with FFplay backend"""

    def __init__(self, kind: str, settings: Settings):
        self.kind = kind
        self.settings = settings
        self.current_cue: Optional[Cue] = None
        self.process: Optional[subprocess.Popen] = None
        self.start_time: float = 0.0
        self.is_playing: bool = False
        self.volume: int = 100
        self.duration: float = 0.0

    def play(self, cue: Cue):
        """Play cue"""
        self.stop()
        self.current_cue = cue
        self.duration = _get_duration(cue.path)

        if cue.kind == "ppt":
            self._open_ppt(cue.path)
            return

        cmd = ["ffplay", "-nodisp", "-autoexit"]

        if cue.kind == "video":
            if cue.open_on_second_screen:
                cmd.extend(["-left", str(self.settings.second_screen_left)])
                cmd.extend(["-top", str(self.settings.second_screen_top)])
                if self.settings.video_fullscreen:
                    cmd.append("-fs")
            else:
                cmd.remove("-nodisp")

        if cue.start_sec > 0:
            cmd.extend(["-ss", str(cue.start_sec)])

        if cue.stop_at_sec and cue.stop_at_sec > cue.start_sec:
            cmd.extend(["-t", str(cue.stop_at_sec - cue.start_sec)])

        cmd.extend(["-af", f"volume={self.volume/100.0}"])
        cmd.append(cue.path)

        try:
            self.process = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            _ALL_PROCESSES.append(self.process)
            self.is_playing = True
            self.start_time = time.time()
        except Exception as e:
            print(f"Play error: {e}")

    def stop(self):
        """Stop playback"""
        if self.process:
            try:
                self.process.terminate()
                self.process.wait(timeout=1)
            except:
                pass
            if self.process in _ALL_PROCESSES:
                _ALL_PROCESSES.remove(self.process)
            self.process = None
        self.is_playing = False
        self.current_cue = None

    def set_volume(self, percent: int):
        """Set volume"""
        self.volume = max(0, min(100, percent))
        if self.is_playing and self.current_cue:
            elapsed = time.time() - self.start_time
            cue = self.current_cue
            cue.start_sec += elapsed
            self.play(cue)

    def get_elapsed(self) -> float:
        if not self.is_playing:
            return 0.0
        return time.time() - self.start_time

    def get_remaining(self) -> float:
        if not self.is_playing or not self.current_cue:
            return 0.0
        elapsed = self.get_elapsed()
        if self.current_cue.stop_at_sec:
            total = self.current_cue.stop_at_sec - self.current_cue.start_sec
        else:
            total = self.duration - self.current_cue.start_sec
        return max(0, total - elapsed)

    def _open_ppt(self, path: str):
        if platform.system() == "Darwin":
            script = f'tell application "Microsoft PowerPoint" to activate\ntell application "Microsoft PowerPoint" to open POSIX file "{path}"'
            subprocess.Popen(["osascript", "-e", script])
        else:
            os.startfile(path)


# =====================================================================
# CUSTOM WIDGETS
# =====================================================================

class DarkButton(tk.Button):
    """Dark themed button"""

    def __init__(self, parent, text="", command=None, accent=False, **kwargs):
        bg = kwargs.pop("bg", Theme.ACCENT_BLUE if accent else Theme.BTN_NORMAL)
        fg = kwargs.pop("fg", Theme.TEXT_WHITE)
        padx = kwargs.pop("padx", 20)
        pady = kwargs.pop("pady", 10)
        font_val = kwargs.pop("font", ("Roboto", 11, "bold"))

        super().__init__(
            parent,
            text=text,
            command=command,
            bg=bg,
            fg=fg,
            activebackground=Theme.BTN_HOVER,
            activeforeground=Theme.TEXT_WHITE,
            relief="flat",
            borderwidth=0,
            padx=padx,
            pady=pady,
            font=font_val,
            cursor="hand2",
            **kwargs
        )
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.default_bg = bg

    def _on_enter(self, e):
        self.config(bg=Theme.BTN_HOVER)

    def _on_leave(self, e):
        self.config(bg=self.default_bg)


class PlayerDeck(tk.Frame):
    """Professional player deck widget"""

    def __init__(self, parent, title: str, deck_color: str, player_type: str, controller):
        super().__init__(parent, bg=Theme.BG_PANEL, relief="solid", borderwidth=1)
        self.player_type = player_type
        self.controller = controller

        # Header
        header = tk.Frame(self, bg=deck_color, height=40)
        header.pack(fill="x")
        header.pack_propagate(False)

        tk.Label(header, text=title, bg=deck_color, fg=Theme.TEXT_WHITE,
                font=("Rajdhani", 16, "bold")).pack(side="left", padx=15, pady=8)

        # Content
        content = tk.Frame(self, bg=Theme.BG_PANEL)
        content.pack(fill="both", expand=True, padx=15, pady=15)

        # Now playing
        self.var_now = tk.StringVar(value="—")
        tk.Label(content, textvariable=self.var_now, bg=Theme.BG_PANEL, fg=Theme.TEXT_WHITE,
                font=("Roboto", 12, "bold"), anchor="w").pack(fill="x")

        # Timecode
        self.var_tc = tk.StringVar(value="00:00.000 / 00:00.000")
        tk.Label(content, textvariable=self.var_tc, bg=Theme.BG_PANEL, fg=Theme.ACCENT_BLUE,
                font=("Courier New", 18, "bold"), anchor="w").pack(fill="x", pady=(5, 0))

        # Progress bar
        self.var_progress = tk.IntVar(value=0)
        progress_frame = tk.Frame(content, bg=Theme.BG_DARK, height=8)
        progress_frame.pack(fill="x", pady=(10, 0))
        progress_frame.pack_propagate(False)

        self.progress_fill = tk.Frame(progress_frame, bg=Theme.ACCENT_BLUE, width=0)
        self.progress_fill.pack(side="left", fill="y")

        # Transport
        transport = tk.Frame(content, bg=Theme.BG_PANEL)
        transport.pack(fill="x", pady=(15, 0))

        DarkButton(transport, text="▶ PLAY", accent=True,
                  command=lambda: controller._play(player_type)).pack(side="left", padx=(0, 8))

        DarkButton(transport, text="⏹ STOP", bg=Theme.ACCENT_RED, fg=Theme.TEXT_WHITE,
                  command=lambda: controller._stop(player_type)).pack(side="left", padx=5)

        # Volume
        vol_frame = tk.Frame(content, bg=Theme.BG_PANEL)
        vol_frame.pack(fill="x", pady=(10, 0))

        tk.Label(vol_frame, text="VOLUME:", bg=Theme.BG_PANEL, fg=Theme.TEXT_GRAY,
                font=("Roboto", 10, "bold")).pack(side="left", padx=(0, 10))

        DarkButton(vol_frame, text="0%", command=lambda: controller._set_volume(player_type, 0),
                  padx=15, pady=6).pack(side="left", padx=2)

        DarkButton(vol_frame, text="50%", command=lambda: controller._set_volume(player_type, 50),
                  padx=15, pady=6).pack(side="left", padx=2)

        DarkButton(vol_frame, text="100%", command=lambda: controller._set_volume(player_type, 100),
                  padx=15, pady=6).pack(side="left", padx=2)

        # Markers
        marker_frame = tk.Frame(content, bg=Theme.BG_PANEL)
        marker_frame.pack(fill="x", pady=(10, 0))

        tk.Label(marker_frame, text="CUE POINTS:", bg=Theme.BG_PANEL, fg=Theme.TEXT_GRAY,
                font=("Roboto", 10, "bold")).pack(side="left", padx=(0, 10))

        DarkButton(marker_frame, text="⏵ IN", bg=Theme.ACCENT_ORANGE, fg=Theme.TEXT_WHITE,
                  command=controller._mark_start, padx=20, pady=6).pack(side="left", padx=2)

        DarkButton(marker_frame, text="⏹ OUT", bg=Theme.ACCENT_ORANGE, fg=Theme.TEXT_WHITE,
                  command=controller._mark_stop, padx=20, pady=6).pack(side="left", padx=2)

    def update_display(self, now_text: str, elapsed: float, remaining: float, progress_pct: float):
        """Update deck display"""
        self.var_now.set(now_text)
        self.var_tc.set(f"{_format_tc(elapsed)} / {_format_tc(remaining)}")

        # Update progress bar
        width = int(self.progress_fill.master.winfo_width() * progress_pct)
        self.progress_fill.config(width=max(0, width))


# =====================================================================
# MAIN APPLICATION
# =====================================================================

class BroadcastController(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Broadcast Controller PRO")
        self.geometry("1600x900")
        self.configure(bg=Theme.BG_DARK)

        # Data
        self.settings = Settings()
        self.cues: list[Cue] = []
        self.selected_index: int = -1
        self.preset_path = Path("show_preset.json")

        # Players
        self.audio_player = MediaPlayer("audio", self.settings)
        self.video_player = MediaPlayer("video", self.settings)

        # UI
        self._build_ui()
        self._load_preset()
        self._start_update_loop()

        # Keyboard
        self.bind("<space>", lambda e: self._on_play_selected())
        self.bind("<Escape>", lambda e: self._emergency_stop())
        self.bind("n", lambda e: self._go_live())
        self.bind("<Right>", lambda e: self._go_live())
        self.bind("m", lambda e: self._mark_start())
        self.bind(".", lambda e: self._mark_stop())

        # Foreground
        self.lift()
        self.attributes('-topmost', True)
        self.after_idle(self.attributes, '-topmost', False)

    def _build_ui(self):
        """Build broadcast-grade UI"""

        # ===== TOP TOOLBAR =====
        toolbar = tk.Frame(self, bg=Theme.BG_CONTROL, height=50)
        toolbar.pack(side="top", fill="x")
        toolbar.pack_propagate(False)

        tk.Label(toolbar, text="BROADCAST CONTROLLER PRO", bg=Theme.BG_CONTROL,
                fg=Theme.ACCENT_BLUE, font=("Rajdhani", 18, "bold")).pack(side="left", padx=20)

        DarkButton(toolbar, text="📂 OPEN", command=self._open_show,
                  padx=15, pady=8).pack(side="right", padx=5)

        DarkButton(toolbar, text="💾 SAVE", command=self._save_show,
                  padx=15, pady=8).pack(side="right", padx=5)

        DarkButton(toolbar, text="📌 SAVE PRESET", command=self._save_preset, accent=True,
                  padx=15, pady=8).pack(side="right", padx=5)

        DarkButton(toolbar, text="⚙️ SETTINGS", command=self._open_settings,
                  padx=15, pady=8).pack(side="right", padx=5)

        # ===== MAIN CONTENT =====
        main = tk.Frame(self, bg=Theme.BG_DARK)
        main.pack(side="top", fill="both", expand=True, padx=10, pady=10)

        # LEFT: CUE LIST (40% width)
        left = tk.Frame(main, bg=Theme.BG_DARK, width=640)
        left.pack(side="left", fill="both", expand=False, padx=(0, 10))
        left.pack_propagate(False)

        self._build_cue_list(left)

        # RIGHT: PLAYERS (60% width)
        right = tk.Frame(main, bg=Theme.BG_DARK)
        right.pack(side="right", fill="both", expand=True)

        self._build_players(right)

    def _build_cue_list(self, parent):
        """Build cue list panel"""

        # Header
        header = tk.Frame(parent, bg=Theme.BG_CONTROL, height=40)
        header.pack(fill="x")
        header.pack_propagate(False)

        tk.Label(header, text="📋 CUE LIST", bg=Theme.BG_CONTROL, fg=Theme.TEXT_WHITE,
                font=("Rajdhani", 14, "bold")).pack(side="left", padx=15, pady=8)

        # Listbox (styled like broadcast software)
        list_frame = tk.Frame(parent, bg=Theme.BG_PANEL)
        list_frame.pack(fill="both", expand=True, pady=(10, 0))

        self.listbox = tk.Listbox(
            list_frame,
            bg=Theme.BG_PANEL,
            fg=Theme.TEXT_WHITE,
            selectbackground=Theme.ACCENT_BLUE,
            selectforeground=Theme.TEXT_WHITE,
            font=("Roboto", 11),
            relief="flat",
            borderwidth=0,
            highlightthickness=0,
            activestyle="none"
        )
        self.listbox.pack(side="left", fill="both", expand=True, padx=1, pady=1)

        scrollbar = tk.Scrollbar(list_frame, orient="vertical", command=self.listbox.yview)
        self.listbox.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")

        self.listbox.bind("<<ListboxSelect>>", self._on_cue_selected)
        self.listbox.bind("<Double-1>", lambda e: self._on_play_selected())

        # Controls
        ctrl = tk.Frame(parent, bg=Theme.BG_DARK)
        ctrl.pack(fill="x", pady=(10, 0))

        DarkButton(ctrl, text="+ AUDIO", bg=Theme.CUE_AUDIO, fg=Theme.TEXT_WHITE,
                  command=lambda: self._add_cue("audio"), padx=15, pady=8).pack(side="left", padx=2)

        DarkButton(ctrl, text="+ VIDEO", bg=Theme.CUE_VIDEO, fg=Theme.TEXT_WHITE,
                  command=lambda: self._add_cue("video"), padx=15, pady=8).pack(side="left", padx=2)

        DarkButton(ctrl, text="+ PPT", bg=Theme.CUE_PPT, fg=Theme.TEXT_WHITE,
                  command=lambda: self._add_cue("ppt"), padx=15, pady=8).pack(side="left", padx=2)

        DarkButton(ctrl, text="🗑 REMOVE", bg=Theme.ACCENT_RED, fg=Theme.TEXT_WHITE,
                  command=self._remove_cue, padx=15, pady=8).pack(side="right", padx=2)

        DarkButton(ctrl, text="▼", command=self._move_cue_down,
                  padx=12, pady=8).pack(side="right", padx=2)

        DarkButton(ctrl, text="▲", command=self._move_cue_up,
                  padx=12, pady=8).pack(side="right", padx=2)

    def _build_players(self, parent):
        """Build player decks"""

        # Audio deck
        self.audio_deck = PlayerDeck(parent, "🎵 AUDIO PLAYER", Theme.DECK_A, "audio", self)
        self.audio_deck.pack(fill="x", pady=(0, 10))

        # Video deck
        self.video_deck = PlayerDeck(parent, "🎬 VIDEO PLAYER", Theme.DECK_B, "video", self)
        self.video_deck.pack(fill="x", pady=(0, 10))

        # PowerPoint
        ppt_frame = tk.Frame(parent, bg=Theme.BG_PANEL, relief="solid", borderwidth=1)
        ppt_frame.pack(fill="x", pady=(0, 10))

        header = tk.Frame(ppt_frame, bg=Theme.ACCENT_ORANGE, height=40)
        header.pack(fill="x")
        header.pack_propagate(False)

        tk.Label(header, text="📊 POWERPOINT CONTROL", bg=Theme.ACCENT_ORANGE,
                fg=Theme.TEXT_WHITE, font=("Rajdhani", 14, "bold")).pack(side="left", padx=15, pady=8)

        content = tk.Frame(ppt_frame, bg=Theme.BG_PANEL)
        content.pack(fill="x", padx=15, pady=15)

        DarkButton(content, text="◀ PREV", command=self._ppt_prev,
                  padx=20, pady=10).pack(side="left", padx=3)

        DarkButton(content, text="▶ START", bg=Theme.ACCENT_GREEN, fg=Theme.TEXT_WHITE,
                  command=self._ppt_start, padx=20, pady=10).pack(side="left", padx=3)

        DarkButton(content, text="NEXT ▶", command=self._ppt_next,
                  padx=20, pady=10).pack(side="left", padx=3)

        DarkButton(content, text="⏹ END", bg=Theme.ACCENT_RED, fg=Theme.TEXT_WHITE,
                  command=self._ppt_end, padx=20, pady=10).pack(side="left", padx=3)

        # GO LIVE
        live_frame = tk.Frame(parent, bg=Theme.ACCENT_RED, relief="solid", borderwidth=2)
        live_frame.pack(fill="x")

        DarkButton(live_frame, text="🔴 GO LIVE!", bg=Theme.ACCENT_RED, fg=Theme.TEXT_WHITE,
                  command=self._go_live, font=("Rajdhani", 20, "bold"),
                  padx=50, pady=20).pack(fill="x", padx=10, pady=10)

        tk.Label(live_frame, text="Auto-advances to next cue when finished",
                bg=Theme.ACCENT_RED, fg=Theme.TEXT_WHITE, font=("Roboto", 10)).pack(pady=(0, 10))

    def _refresh_cues(self):
        """Refresh cue list display"""
        self.listbox.delete(0, tk.END)
        for i, cue in enumerate(self.cues):
            icon = {"audio": "🎵", "video": "🎬", "ppt": "📊"}[cue.kind]
            name = Path(cue.path).name
            in_tc = _format_tc(cue.start_sec)
            out_tc = _format_tc(cue.stop_at_sec) if cue.stop_at_sec else "---"

            line = f"{i+1:3d}  {icon}  {_shorten(name, 30):32s}  IN:{in_tc}  OUT:{out_tc}"
            self.listbox.insert(tk.END, line)

            # Color coding
            if cue.kind == "audio":
                self.listbox.itemconfig(i, bg=Theme.CUE_AUDIO, fg=Theme.TEXT_WHITE)
            elif cue.kind == "video":
                self.listbox.itemconfig(i, bg=Theme.CUE_VIDEO, fg=Theme.TEXT_WHITE)
            else:
                self.listbox.itemconfig(i, bg=Theme.CUE_PPT, fg=Theme.TEXT_WHITE)

    def _on_cue_selected(self, event):
        """Handle cue selection"""
        sel = self.listbox.curselection()
        self.selected_index = sel[0] if sel else -1

    def _add_cue(self, kind: CueKind):
        """Add cue"""
        filetypes = {
            "audio": [("Audio", "*.mp3 *.m4a *.wav *.flac")],
            "video": [("Video", "*.mp4 *.mov *.avi *.mkv")],
            "ppt": [("PowerPoint", "*.ppt *.pptx")]
        }[kind]

        path = filedialog.askopenfilename(title=f"Select {kind}", filetypes=filetypes)
        if not path:
            return

        cue = Cue(id=str(uuid.uuid4()), kind=kind, path=path)
        self.cues.append(cue)
        self._refresh_cues()

    def _remove_cue(self):
        """Remove selected cue"""
        if self.selected_index >= 0:
            del self.cues[self.selected_index]
            self.selected_index = -1
            self._refresh_cues()

    def _move_cue_up(self):
        """Move cue up"""
        if self.selected_index > 0:
            i = self.selected_index
            self.cues[i], self.cues[i-1] = self.cues[i-1], self.cues[i]
            self.selected_index = i - 1
            self._refresh_cues()
            self.listbox.selection_set(self.selected_index)

    def _move_cue_down(self):
        """Move cue down"""
        if 0 <= self.selected_index < len(self.cues) - 1:
            i = self.selected_index
            self.cues[i], self.cues[i+1] = self.cues[i+1], self.cues[i]
            self.selected_index = i + 1
            self._refresh_cues()
            self.listbox.selection_set(self.selected_index)

    def _on_play_selected(self):
        """Play selected cue"""
        if self.selected_index < 0:
            return
        cue = self.cues[self.selected_index]

        if cue.kind == "audio":
            self.audio_player.play(cue)
        elif cue.kind == "video":
            self.video_player.play(cue)
        else:
            self.audio_player.play(cue)

    def _play(self, kind: str):
        """Play on specific player"""
        if self.selected_index < 0:
            return
        cue = self.cues[self.selected_index]

        if kind == "audio":
            self.audio_player.play(cue)
        else:
            self.video_player.play(cue)

    def _stop(self, kind: str):
        """Stop player"""
        if kind == "audio":
            self.audio_player.stop()
        else:
            self.video_player.stop()

    def _emergency_stop(self):
        """Emergency stop all"""
        self.audio_player.stop()
        self.video_player.stop()

    def _set_volume(self, kind: str, percent: int):
        """Set volume"""
        if kind == "audio":
            self.audio_player.set_volume(percent)
        else:
            self.video_player.set_volume(percent)

    def _mark_start(self):
        """Mark IN point"""
        if self.selected_index < 0:
            return
        cue = self.cues[self.selected_index]

        if self.audio_player.is_playing and self.audio_player.current_cue == cue:
            elapsed = self.audio_player.get_elapsed()
            cue.start_sec = cue.start_sec + elapsed
        elif self.video_player.is_playing and self.video_player.current_cue == cue:
            elapsed = self.video_player.get_elapsed()
            cue.start_sec = cue.start_sec + elapsed

        self._refresh_cues()

    def _mark_stop(self):
        """Mark OUT point"""
        if self.selected_index < 0:
            return
        cue = self.cues[self.selected_index]

        if self.audio_player.is_playing and self.audio_player.current_cue == cue:
            elapsed = self.audio_player.get_elapsed()
            cue.stop_at_sec = cue.start_sec + elapsed
        elif self.video_player.is_playing and self.video_player.current_cue == cue:
            elapsed = self.video_player.get_elapsed()
            cue.stop_at_sec = cue.start_sec + elapsed

        self._refresh_cues()

    def _go_live(self):
        """GO LIVE with auto-advance"""
        if self.selected_index < 0 and len(self.cues) > 0:
            self.selected_index = 0
            self.listbox.selection_set(0)

        if self.selected_index >= 0:
            self._on_play_selected()

            def advance():
                time.sleep(1)
                cue = self.cues[self.selected_index]

                if cue.kind == "audio" and self.audio_player.is_playing:
                    remaining = self.audio_player.get_remaining()
                    time.sleep(remaining + 0.5)
                    if self.selected_index < len(self.cues) - 1:
                        self.selected_index += 1
                        self.after(0, lambda: self.listbox.selection_set(self.selected_index))
                        self.after(100, self._go_live)

                elif cue.kind == "video" and self.video_player.is_playing:
                    remaining = self.video_player.get_remaining()
                    time.sleep(remaining + 0.5)
                    if self.selected_index < len(self.cues) - 1:
                        self.selected_index += 1
                        self.after(0, lambda: self.listbox.selection_set(self.selected_index))
                        self.after(100, self._go_live)

            threading.Thread(target=advance, daemon=True).start()

    def _ppt_prev(self):
        """PPT previous slide"""
        if platform.system() == "Darwin":
            script = 'tell application "Microsoft PowerPoint" to go to previous slide active presentation'
            subprocess.Popen(["osascript", "-e", script])

    def _ppt_start(self):
        """PPT start slideshow"""
        if platform.system() == "Darwin":
            script = 'tell application "Microsoft PowerPoint" to run slide show active presentation'
            subprocess.Popen(["osascript", "-e", script])

    def _ppt_next(self):
        """PPT next slide"""
        if platform.system() == "Darwin":
            script = 'tell application "Microsoft PowerPoint" to go to next slide active presentation'
            subprocess.Popen(["osascript", "-e", script])

    def _ppt_end(self):
        """PPT end slideshow"""
        if platform.system() == "Darwin":
            script = 'tell application "Microsoft PowerPoint" to exit slide show active presentation'
            subprocess.Popen(["osascript", "-e", script])

    def _start_update_loop(self):
        """Update displays"""
        def update():
            # Audio player
            if self.audio_player.is_playing and self.audio_player.current_cue:
                cue = self.audio_player.current_cue
                name = Path(cue.path).name
                elapsed = self.audio_player.get_elapsed()
                remaining = self.audio_player.get_remaining()

                if self.audio_player.current_cue.stop_at_sec:
                    total = self.audio_player.current_cue.stop_at_sec - self.audio_player.current_cue.start_sec
                else:
                    total = self.audio_player.duration - self.audio_player.current_cue.start_sec

                progress = elapsed / total if total > 0 else 0

                self.audio_deck.update_display(f"▶ {_shorten(name, 30)}", elapsed, remaining, progress)
            else:
                self.audio_deck.update_display("—", 0, 0, 0)

            # Video player
            if self.video_player.is_playing and self.video_player.current_cue:
                cue = self.video_player.current_cue
                name = Path(cue.path).name
                elapsed = self.video_player.get_elapsed()
                remaining = self.video_player.get_remaining()

                if self.video_player.current_cue.stop_at_sec:
                    total = self.video_player.current_cue.stop_at_sec - self.video_player.current_cue.start_sec
                else:
                    total = self.video_player.duration - self.video_player.current_cue.start_sec

                progress = elapsed / total if total > 0 else 0

                self.video_deck.update_display(f"▶ {_shorten(name, 30)}", elapsed, remaining, progress)
            else:
                self.video_deck.update_display("—", 0, 0, 0)

            self.after(100, update)

        update()

    def _load_preset(self):
        """Auto-load preset"""
        if not self.preset_path.exists():
            return

        try:
            with open(self.preset_path, "r") as f:
                data = json.load(f)

            self.settings = Settings.from_dict(data.get("settings", {}))
            self.cues = [Cue.from_dict(c) for c in data.get("cues", [])]
            self._refresh_cues()
        except Exception as e:
            print(f"Load error: {e}")

    def _save_preset(self):
        """Save preset"""
        data = {
            "version": 2,
            "settings": self.settings.to_dict(),
            "cues": [c.to_dict() for c in self.cues],
        }

        try:
            with open(self.preset_path, "w") as f:
                json.dump(data, f, indent=2)
            messagebox.showinfo("Saved", f"Preset saved")
        except Exception as e:
            messagebox.showerror("Error", f"Save failed: {e}")

    def _save_show(self):
        """Save show"""
        path = filedialog.asksaveasfilename(defaultextension=".json", filetypes=[("JSON", "*.json")])
        if not path:
            return

        data = {
            "version": 2,
            "settings": self.settings.to_dict(),
            "cues": [c.to_dict() for c in self.cues],
        }

        try:
            with open(path, "w") as f:
                json.dump(data, f, indent=2)
            messagebox.showinfo("Saved", f"Show saved")
        except Exception as e:
            messagebox.showerror("Error", f"Save failed: {e}")

    def _open_show(self):
        """Open show"""
        path = filedialog.askopenfilename(filetypes=[("JSON", "*.json")])
        if not path:
            return

        try:
            with open(path, "r") as f:
                data = json.load(f)

            self.settings = Settings.from_dict(data.get("settings", {}))
            self.cues = [Cue.from_dict(c) for c in data.get("cues", [])]
            self._refresh_cues()
            messagebox.showinfo("Loaded", f"Show loaded")
        except Exception as e:
            messagebox.showerror("Error", f"Load failed: {e}")

    def _open_settings(self):
        """Open settings"""
        dialog = tk.Toplevel(self)
        dialog.title("Settings")
        dialog.geometry("450x350")
        dialog.configure(bg=Theme.BG_PANEL)

        tk.Label(dialog, text="SETTINGS", bg=Theme.BG_PANEL, fg=Theme.TEXT_WHITE,
                font=("Rajdhani", 16, "bold")).pack(pady=20)

        # Left
        frame = tk.Frame(dialog, bg=Theme.BG_PANEL)
        frame.pack(fill="x", padx=30, pady=5)
        tk.Label(frame, text="Second Screen Left:", bg=Theme.BG_PANEL, fg=Theme.TEXT_WHITE,
                width=20, anchor="w").pack(side="left")
        left_var = tk.StringVar(value=str(self.settings.second_screen_left))
        tk.Entry(frame, textvariable=left_var, width=10, bg=Theme.BG_CONTROL,
                fg=Theme.TEXT_WHITE).pack(side="left")

        # Top
        frame = tk.Frame(dialog, bg=Theme.BG_PANEL)
        frame.pack(fill="x", padx=30, pady=5)
        tk.Label(frame, text="Second Screen Top:", bg=Theme.BG_PANEL, fg=Theme.TEXT_WHITE,
                width=20, anchor="w").pack(side="left")
        top_var = tk.StringVar(value=str(self.settings.second_screen_top))
        tk.Entry(frame, textvariable=top_var, width=10, bg=Theme.BG_CONTROL,
                fg=Theme.TEXT_WHITE).pack(side="left")

        # Fullscreen
        frame = tk.Frame(dialog, bg=Theme.BG_PANEL)
        frame.pack(fill="x", padx=30, pady=5)
        tk.Label(frame, text="Video Fullscreen:", bg=Theme.BG_PANEL, fg=Theme.TEXT_WHITE,
                width=20, anchor="w").pack(side="left")
        fs_var = tk.BooleanVar(value=self.settings.video_fullscreen)
        tk.Checkbutton(frame, variable=fs_var, bg=Theme.BG_PANEL).pack(side="left")

        # Volume
        frame = tk.Frame(dialog, bg=Theme.BG_PANEL)
        frame.pack(fill="x", padx=30, pady=5)
        tk.Label(frame, text="Startup Volume:", bg=Theme.BG_PANEL, fg=Theme.TEXT_WHITE,
                width=20, anchor="w").pack(side="left")
        vol_var = tk.StringVar(value=str(self.settings.startup_volume))
        tk.Entry(frame, textvariable=vol_var, width=10, bg=Theme.BG_CONTROL,
                fg=Theme.TEXT_WHITE).pack(side="left")

        def save():
            try:
                self.settings.second_screen_left = int(left_var.get())
                self.settings.second_screen_top = int(top_var.get())
                self.settings.video_fullscreen = fs_var.get()
                self.settings.startup_volume = int(vol_var.get())
                dialog.destroy()
            except:
                messagebox.showerror("Error", "Invalid values")

        DarkButton(dialog, text="SAVE", accent=True, command=save,
                  padx=40, pady=15).pack(pady=30)


# =====================================================================
# ENTRY POINT
# =====================================================================

def main():
    app = BroadcastController()
    app.mainloop()


if __name__ == "__main__":
    main()
