#!/usr/bin/env python3
"""
BROADCAST PRO - Professional Show Automation
Inspired by RadioDJ, SAM Broadcaster, Mairlist
"""

import atexit
import json
import os
import platform
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Literal

import tkinter as tk
from tkinter import filedialog, messagebox

# =============================================================================
# THEME - Professional Broadcast Dark UI
# =============================================================================

class BroadcastTheme:
    # Backgrounds
    MAIN_BG = "#1a1a1a"
    PANEL_BG = "#252525"
    CONTROL_BG = "#2f2f2f"
    HEADER_BG = "#0d0d0d"

    # Text
    TEXT_PRIMARY = "#ffffff"
    TEXT_SECONDARY = "#cccccc"
    TEXT_DISABLED = "#777777"

    # Accents
    BLUE = "#0099ff"
    GREEN = "#00cc44"
    RED = "#ff3344"
    ORANGE = "#ff9900"
    PURPLE = "#9966ff"

    # Player decks
    DECK_A_BG = "#1a3d5c"
    DECK_B_BG = "#1a5c3d"

    # Buttons
    BTN_BG = "#404040"
    BTN_HOVER = "#505050"
    BTN_ACTIVE = "#606060"

    # Cue types
    CUE_AUDIO_BG = "#0d2940"
    CUE_VIDEO_BG = "#0d4029"
    CUE_PPT_BG = "#40290d"

T = BroadcastTheme

# =============================================================================
# GLOBALS
# =============================================================================

_PROCESSES = []

def _cleanup():
    for p in _PROCESSES:
        try:
            p.terminate()
            p.wait(timeout=0.5)
        except:
            pass

atexit.register(_cleanup)

# =============================================================================
# UTILITIES
# =============================================================================

def fmt_time(sec: Optional[float]) -> str:
    """Format MM:SS.mmm"""
    if sec is None:
        return "00:00.000"
    ms = max(0, int(sec * 1000))
    m = ms // 60000
    s = (ms // 1000) % 60
    msec = ms % 1000
    return f"{m:02d}:{s:02d}.{msec:03d}"

def parse_time(s: str) -> Optional[float]:
    """Parse MM:SS or MM:SS.mmm"""
    s = s.strip()
    if not s:
        return None
    if ":" not in s:
        return float(s)
    parts = s.split(":")
    if len(parts) == 2:
        return float(parts[0]) * 60 + float(parts[1])
    return None

def get_duration(path: str) -> float:
    """Get media duration with ffprobe"""
    try:
        cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration",
               "-of", "default=noprint_wrappers=1:nokey=1", path]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        return float(res.stdout.strip())
    except:
        return 0.0

def shorten(text: str, max_len: int = 30) -> str:
    """Shorten text with ellipsis"""
    if len(text) <= max_len:
        return text
    return text[:max_len-3] + "..."

# =============================================================================
# DATA MODELS
# =============================================================================

@dataclass
class Settings:
    second_screen_x: int = 1920
    second_screen_y: int = 0
    video_fullscreen: bool = True
    startup_volume: int = 100

    def to_dict(self):
        return {
            "second_screen_x": self.second_screen_x,
            "second_screen_y": self.second_screen_y,
            "video_fullscreen": self.video_fullscreen,
            "startup_volume": self.startup_volume,
        }

    @staticmethod
    def from_dict(d):
        return Settings(
            second_screen_x=d.get("second_screen_x", 1920),
            second_screen_y=d.get("second_screen_y", 0),
            video_fullscreen=d.get("video_fullscreen", True),
            startup_volume=d.get("startup_volume", 100),
        )

@dataclass
class Cue:
    id: str
    type: Literal["audio", "video", "ppt"]
    path: str
    title: str = ""
    in_point: float = 0.0
    out_point: Optional[float] = None
    second_screen: bool = False

    def to_dict(self):
        return {
            "id": self.id,
            "type": self.type,
            "path": self.path,
            "title": self.title,
            "in_point": self.in_point,
            "out_point": self.out_point,
            "second_screen": self.second_screen,
        }

    @staticmethod
    def from_dict(d):
        return Cue(
            id=d.get("id", str(uuid.uuid4())),
            type=d["type"],
            path=d["path"],
            title=d.get("title", ""),
            in_point=d.get("in_point", 0.0),
            out_point=d.get("out_point"),
            second_screen=d.get("second_screen", False),
        )

# =============================================================================
# PLAYER ENGINE
# =============================================================================

class Player:
    """Single player deck"""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.cue: Optional[Cue] = None
        self.proc: Optional[subprocess.Popen] = None
        self.playing = False
        self.start_time = 0.0
        self.volume = 100
        self.duration = 0.0

    def play(self, cue: Cue):
        """Play a cue"""
        self.stop()
        self.cue = cue
        self.duration = get_duration(cue.path)

        if cue.type == "ppt":
            self._open_ppt(cue.path)
            return

        cmd = ["ffplay", "-nodisp", "-autoexit"]

        if cue.type == "video":
            if cue.second_screen:
                cmd.extend(["-left", str(self.settings.second_screen_x)])
                cmd.extend(["-top", str(self.settings.second_screen_y)])
                if self.settings.video_fullscreen:
                    cmd.append("-fs")
            else:
                cmd.remove("-nodisp")

        if cue.in_point > 0:
            cmd.extend(["-ss", str(cue.in_point)])

        if cue.out_point and cue.out_point > cue.in_point:
            cmd.extend(["-t", str(cue.out_point - cue.in_point)])

        cmd.extend(["-af", f"volume={self.volume/100.0}"])
        cmd.append(cue.path)

        try:
            self.proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            _PROCESSES.append(self.proc)
            self.playing = True
            self.start_time = time.time()
        except Exception as e:
            print(f"Play error: {e}")

    def stop(self):
        """Stop playback"""
        if self.proc:
            try:
                self.proc.terminate()
                self.proc.wait(timeout=0.5)
            except:
                pass
            if self.proc in _PROCESSES:
                _PROCESSES.remove(self.proc)
            self.proc = None
        self.playing = False
        self.cue = None

    def set_vol(self, vol: int):
        """Change volume (requires restart)"""
        self.volume = max(0, min(100, vol))
        if self.playing and self.cue:
            elapsed = time.time() - self.start_time
            self.cue.in_point += elapsed
            self.play(self.cue)

    def elapsed(self) -> float:
        """Get elapsed time"""
        if not self.playing:
            return 0.0
        return time.time() - self.start_time

    def remaining(self) -> float:
        """Get remaining time"""
        if not self.playing or not self.cue:
            return 0.0
        elapsed = self.elapsed()
        if self.cue.out_point:
            total = self.cue.out_point - self.cue.in_point
        else:
            total = self.duration - self.cue.in_point
        return max(0, total - elapsed)

    def progress(self) -> float:
        """Get progress 0-1"""
        if not self.playing or not self.cue:
            return 0.0
        elapsed = self.elapsed()
        if self.cue.out_point:
            total = self.cue.out_point - self.cue.in_point
        else:
            total = self.duration - self.cue.in_point
        return min(1.0, elapsed / total) if total > 0 else 0.0

    def _open_ppt(self, path: str):
        """Open PowerPoint"""
        if platform.system() == "Darwin":
            script = f'tell application "Microsoft PowerPoint" to activate\ntell application "Microsoft PowerPoint" to open POSIX file "{path}"'
            subprocess.Popen(["osascript", "-e", script])
        else:
            os.startfile(path)

# =============================================================================
# UI COMPONENTS
# =============================================================================

class BroadcastButton(tk.Button):
    """Styled broadcast button"""

    def __init__(self, parent, text="", cmd=None, color=None, **kw):
        bg = color or kw.pop("bg", T.BTN_BG)
        fg = kw.pop("fg", T.TEXT_PRIMARY)
        px = kw.pop("padx", 18)
        py = kw.pop("pady", 9)
        fnt = kw.pop("font", ("Arial", 10, "bold"))

        super().__init__(
            parent, text=text, command=cmd, bg=bg, fg=fg,
            activebackground=T.BTN_HOVER, activeforeground=T.TEXT_PRIMARY,
            relief="flat", bd=0, padx=px, pady=py, font=fnt,
            cursor="hand2", **kw
        )
        self._bg = bg
        self.bind("<Enter>", lambda e: self.config(bg=T.BTN_HOVER))
        self.bind("<Leave>", lambda e: self.config(bg=self._bg))

class DeckWidget(tk.Frame):
    """Professional player deck display"""

    def __init__(self, parent, title: str, color: str, player_id: str, app):
        super().__init__(parent, bg=T.PANEL_BG, relief="solid", bd=1)
        self.player_id = player_id
        self.app = app

        # Header
        hdr = tk.Frame(self, bg=color, height=35)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)
        tk.Label(hdr, text=title, bg=color, fg=T.TEXT_PRIMARY,
                font=("Arial", 14, "bold")).pack(side="left", padx=12)

        # Body
        body = tk.Frame(self, bg=T.PANEL_BG)
        body.pack(fill="both", expand=True, padx=12, pady=12)

        # Now playing
        self.lbl_now = tk.StringVar(value="—")
        tk.Label(body, textvariable=self.lbl_now, bg=T.PANEL_BG, fg=T.TEXT_PRIMARY,
                font=("Arial", 11, "bold"), anchor="w").pack(fill="x")

        # Timecode
        self.lbl_tc = tk.StringVar(value="00:00.000 / 00:00.000")
        tk.Label(body, textvariable=self.lbl_tc, bg=T.PANEL_BG, fg=T.BLUE,
                font=("Courier New", 16, "bold"), anchor="w").pack(fill="x", pady=(3,0))

        # Progress
        prog_bg = tk.Frame(body, bg=T.HEADER_BG, height=6)
        prog_bg.pack(fill="x", pady=(8,0))
        prog_bg.pack_propagate(False)
        self.prog_bar = tk.Frame(prog_bg, bg=T.BLUE, width=0)
        self.prog_bar.pack(side="left", fill="y")

        # Transport
        trans = tk.Frame(body, bg=T.PANEL_BG)
        trans.pack(fill="x", pady=(12,0))

        BroadcastButton(trans, "▶ PLAY", color=T.GREEN,
                       cmd=lambda: app.play_deck(player_id)).pack(side="left", padx=(0,6))
        BroadcastButton(trans, "⏹ STOP", color=T.RED,
                       cmd=lambda: app.stop_deck(player_id)).pack(side="left")

        # Volume
        vol = tk.Frame(body, bg=T.PANEL_BG)
        vol.pack(fill="x", pady=(8,0))
        tk.Label(vol, text="VOL:", bg=T.PANEL_BG, fg=T.TEXT_SECONDARY,
                font=("Arial", 9, "bold")).pack(side="left", padx=(0,8))
        BroadcastButton(vol, "0%", cmd=lambda: app.set_deck_vol(player_id, 0),
                       padx=12, pady=6).pack(side="left", padx=1)
        BroadcastButton(vol, "50%", cmd=lambda: app.set_deck_vol(player_id, 50),
                       padx=12, pady=6).pack(side="left", padx=1)
        BroadcastButton(vol, "100%", cmd=lambda: app.set_deck_vol(player_id, 100),
                       padx=12, pady=6).pack(side="left", padx=1)

        # Cue markers
        markers = tk.Frame(body, bg=T.PANEL_BG)
        markers.pack(fill="x", pady=(8,0))
        tk.Label(markers, text="CUE:", bg=T.PANEL_BG, fg=T.TEXT_SECONDARY,
                font=("Arial", 9, "bold")).pack(side="left", padx=(0,8))
        BroadcastButton(markers, "⏵ IN", color=T.ORANGE,
                       cmd=app.mark_in, padx=16, pady=6).pack(side="left", padx=1)
        BroadcastButton(markers, "⏹ OUT", color=T.ORANGE,
                       cmd=app.mark_out, padx=16, pady=6).pack(side="left", padx=1)

    def update(self, now: str, elapsed: float, remaining: float, progress: float):
        """Update display"""
        self.lbl_now.set(now)
        self.lbl_tc.set(f"{fmt_time(elapsed)} / {fmt_time(remaining)}")
        w = int(self.prog_bar.master.winfo_width() * progress)
        self.prog_bar.config(width=max(0, w))

# =============================================================================
# MAIN APPLICATION
# =============================================================================

class BroadcastApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("BROADCAST PRO")
        self.geometry("1600x900")
        self.configure(bg=T.MAIN_BG)

        # Data
        self.settings = Settings()
        self.cues: list[Cue] = []
        self.selected = -1
        self.preset_file = Path("show_preset.json")

        # Players
        self.deck_a = Player(self.settings)
        self.deck_b = Player(self.settings)

        # Build UI
        self._build_ui()
        self._load_preset()
        self._start_updates()

        # Shortcuts
        self.bind("<space>", lambda e: self.play_selected())
        self.bind("<Escape>", lambda e: self.emergency_stop())
        self.bind("n", lambda e: self.go_live())
        self.bind("<Right>", lambda e: self.go_live())
        self.bind("m", lambda e: self.mark_in())
        self.bind(".", lambda e: self.mark_out())

        # Foreground
        self.lift()
        self.attributes("-topmost", True)
        self.after_idle(self.attributes, "-topmost", False)

    def _build_ui(self):
        """Build interface"""

        # Top toolbar
        toolbar = tk.Frame(self, bg=T.HEADER_BG, height=48)
        toolbar.pack(fill="x")
        toolbar.pack_propagate(False)

        tk.Label(toolbar, text="BROADCAST PRO", bg=T.HEADER_BG, fg=T.BLUE,
                font=("Arial", 16, "bold")).pack(side="left", padx=16)

        BroadcastButton(toolbar, "📂 OPEN", self.open_show,
                       padx=14, pady=7).pack(side="right", padx=4)
        BroadcastButton(toolbar, "💾 SAVE", self.save_show,
                       padx=14, pady=7).pack(side="right", padx=4)
        BroadcastButton(toolbar, "📌 SAVE PRESET", self.save_preset, color=T.BLUE,
                       padx=14, pady=7).pack(side="right", padx=4)
        BroadcastButton(toolbar, "⚙️ SETTINGS", self.open_settings,
                       padx=14, pady=7).pack(side="right", padx=4)

        # Main area
        main = tk.Frame(self, bg=T.MAIN_BG)
        main.pack(fill="both", expand=True, padx=8, pady=8)

        # Left: Cue list
        left = tk.Frame(main, bg=T.MAIN_BG, width=600)
        left.pack(side="left", fill="both", expand=False, padx=(0,8))
        left.pack_propagate(False)

        self._build_cuelist(left)

        # Right: Players
        right = tk.Frame(main, bg=T.MAIN_BG)
        right.pack(side="right", fill="both", expand=True)

        self._build_players(right)

    def _build_cuelist(self, parent):
        """Build cue list panel"""

        # Header
        hdr = tk.Frame(parent, bg=T.CONTROL_BG, height=35)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)
        tk.Label(hdr, text="📋 CUE LIST", bg=T.CONTROL_BG, fg=T.TEXT_PRIMARY,
                font=("Arial", 13, "bold")).pack(side="left", padx=12)

        # Listbox
        list_frame = tk.Frame(parent, bg=T.PANEL_BG)
        list_frame.pack(fill="both", expand=True, pady=(8,0))

        self.listbox = tk.Listbox(
            list_frame, bg=T.PANEL_BG, fg=T.TEXT_PRIMARY,
            selectbackground=T.BLUE, selectforeground=T.TEXT_PRIMARY,
            font=("Consolas", 10), relief="flat", bd=0,
            highlightthickness=0, activestyle="none"
        )
        self.listbox.pack(side="left", fill="both", expand=True, padx=1, pady=1)

        scroll = tk.Scrollbar(list_frame, orient="vertical", command=self.listbox.yview)
        self.listbox.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")

        self.listbox.bind("<<ListboxSelect>>", self._on_select)
        self.listbox.bind("<Double-1>", lambda e: self.play_selected())

        # Controls
        ctrl = tk.Frame(parent, bg=T.MAIN_BG)
        ctrl.pack(fill="x", pady=(8,0))

        BroadcastButton(ctrl, "+ AUDIO", color=T.CUE_AUDIO_BG,
                       cmd=lambda: self.add_cue("audio"), padx=12, pady=7).pack(side="left", padx=1)
        BroadcastButton(ctrl, "+ VIDEO", color=T.CUE_VIDEO_BG,
                       cmd=lambda: self.add_cue("video"), padx=12, pady=7).pack(side="left", padx=1)
        BroadcastButton(ctrl, "+ PPT", color=T.CUE_PPT_BG,
                       cmd=lambda: self.add_cue("ppt"), padx=12, pady=7).pack(side="left", padx=1)

        BroadcastButton(ctrl, "🗑", color=T.RED,
                       cmd=self.remove_cue, padx=12, pady=7).pack(side="right", padx=1)
        BroadcastButton(ctrl, "▼", cmd=self.move_down,
                       padx=10, pady=7).pack(side="right", padx=1)
        BroadcastButton(ctrl, "▲", cmd=self.move_up,
                       padx=10, pady=7).pack(side="right", padx=1)

    def _build_players(self, parent):
        """Build player decks"""

        # Deck A (Audio)
        self.widget_a = DeckWidget(parent, "🎵 DECK A", T.DECK_A_BG, "a", self)
        self.widget_a.pack(fill="x", pady=(0,8))

        # Deck B (Video)
        self.widget_b = DeckWidget(parent, "🎬 DECK B", T.DECK_B_BG, "b", self)
        self.widget_b.pack(fill="x", pady=(0,8))

        # PowerPoint
        ppt = tk.Frame(parent, bg=T.PANEL_BG, relief="solid", bd=1)
        ppt.pack(fill="x", pady=(0,8))

        hdr = tk.Frame(ppt, bg=T.ORANGE, height=35)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)
        tk.Label(hdr, text="📊 POWERPOINT", bg=T.ORANGE, fg=T.TEXT_PRIMARY,
                font=("Arial", 13, "bold")).pack(side="left", padx=12)

        body = tk.Frame(ppt, bg=T.PANEL_BG)
        body.pack(fill="x", padx=12, pady=12)

        BroadcastButton(body, "◀ PREV", self.ppt_prev, padx=16, pady=8).pack(side="left", padx=2)
        BroadcastButton(body, "▶ START", self.ppt_start, color=T.GREEN,
                       padx=16, pady=8).pack(side="left", padx=2)
        BroadcastButton(body, "NEXT ▶", self.ppt_next, padx=16, pady=8).pack(side="left", padx=2)
        BroadcastButton(body, "⏹ END", self.ppt_end, color=T.RED,
                       padx=16, pady=8).pack(side="left", padx=2)

        # GO LIVE
        live = tk.Frame(parent, bg=T.RED, relief="solid", bd=2)
        live.pack(fill="x")

        BroadcastButton(live, "🔴 GO LIVE!", self.go_live, color=T.RED,
                       font=("Arial", 18, "bold"), padx=40, pady=16).pack(fill="x", padx=8, pady=8)
        tk.Label(live, text="Auto-advance playlist", bg=T.RED, fg=T.TEXT_PRIMARY,
                font=("Arial", 9)).pack(pady=(0,8))

    def _refresh_list(self):
        """Refresh cue list"""
        self.listbox.delete(0, tk.END)
        for i, cue in enumerate(self.cues):
            icon = {"audio": "🎵", "video": "🎬", "ppt": "📊"}[cue.type]
            name = Path(cue.path).name
            in_str = fmt_time(cue.in_point)
            out_str = fmt_time(cue.out_point) if cue.out_point else "---"

            line = f"{i+1:3}  {icon}  {shorten(name, 28):30}  IN:{in_str}  OUT:{out_str}"
            self.listbox.insert(tk.END, line)

            bg = {"audio": T.CUE_AUDIO_BG, "video": T.CUE_VIDEO_BG, "ppt": T.CUE_PPT_BG}[cue.type]
            self.listbox.itemconfig(i, bg=bg, fg=T.TEXT_PRIMARY)

    def _on_select(self, event):
        """Handle selection"""
        sel = self.listbox.curselection()
        self.selected = sel[0] if sel else -1

    def add_cue(self, typ: str):
        """Add cue"""
        ftypes = {
            "audio": [("Audio", "*.mp3 *.m4a *.wav *.flac")],
            "video": [("Video", "*.mp4 *.mov *.avi *.mkv")],
            "ppt": [("PowerPoint", "*.ppt *.pptx")]
        }[typ]

        path = filedialog.askopenfilename(filetypes=ftypes)
        if not path:
            return

        cue = Cue(id=str(uuid.uuid4()), type=typ, path=path)
        self.cues.append(cue)
        self._refresh_list()

    def remove_cue(self):
        """Remove selected cue"""
        if self.selected >= 0:
            del self.cues[self.selected]
            self.selected = -1
            self._refresh_list()

    def move_up(self):
        """Move cue up"""
        if self.selected > 0:
            i = self.selected
            self.cues[i], self.cues[i-1] = self.cues[i-1], self.cues[i]
            self.selected = i - 1
            self._refresh_list()
            self.listbox.selection_set(self.selected)

    def move_down(self):
        """Move cue down"""
        if 0 <= self.selected < len(self.cues) - 1:
            i = self.selected
            self.cues[i], self.cues[i+1] = self.cues[i+1], self.cues[i]
            self.selected = i + 1
            self._refresh_list()
            self.listbox.selection_set(self.selected)

    def play_selected(self):
        """Play selected cue"""
        if self.selected < 0:
            return
        cue = self.cues[self.selected]

        if cue.type == "audio":
            self.deck_a.play(cue)
        elif cue.type == "video":
            self.deck_b.play(cue)
        else:
            self.deck_a.play(cue)

    def play_deck(self, deck_id: str):
        """Play on specific deck"""
        if self.selected < 0:
            return
        cue = self.cues[self.selected]

        if deck_id == "a":
            self.deck_a.play(cue)
        else:
            self.deck_b.play(cue)

    def stop_deck(self, deck_id: str):
        """Stop deck"""
        if deck_id == "a":
            self.deck_a.stop()
        else:
            self.deck_b.stop()

    def emergency_stop(self):
        """Stop all"""
        self.deck_a.stop()
        self.deck_b.stop()

    def set_deck_vol(self, deck_id: str, vol: int):
        """Set deck volume"""
        if deck_id == "a":
            self.deck_a.set_vol(vol)
        else:
            self.deck_b.set_vol(vol)

    def mark_in(self):
        """Mark IN point"""
        if self.selected < 0:
            return
        cue = self.cues[self.selected]

        if self.deck_a.playing and self.deck_a.cue == cue:
            cue.in_point = cue.in_point + self.deck_a.elapsed()
        elif self.deck_b.playing and self.deck_b.cue == cue:
            cue.in_point = cue.in_point + self.deck_b.elapsed()

        self._refresh_list()

    def mark_out(self):
        """Mark OUT point"""
        if self.selected < 0:
            return
        cue = self.cues[self.selected]

        if self.deck_a.playing and self.deck_a.cue == cue:
            cue.out_point = cue.in_point + self.deck_a.elapsed()
        elif self.deck_b.playing and self.deck_b.cue == cue:
            cue.out_point = cue.in_point + self.deck_b.elapsed()

        self._refresh_list()

    def go_live(self):
        """GO LIVE with auto-advance"""
        if self.selected < 0 and len(self.cues) > 0:
            self.selected = 0
            self.listbox.selection_set(0)

        if self.selected >= 0:
            self.play_selected()

            def advance():
                time.sleep(1)
                cue = self.cues[self.selected]

                if cue.type == "audio" and self.deck_a.playing:
                    rem = self.deck_a.remaining()
                    time.sleep(rem + 0.5)
                    if self.selected < len(self.cues) - 1:
                        self.selected += 1
                        self.after(0, lambda: self.listbox.selection_set(self.selected))
                        self.after(100, self.go_live)

                elif cue.type == "video" and self.deck_b.playing:
                    rem = self.deck_b.remaining()
                    time.sleep(rem + 0.5)
                    if self.selected < len(self.cues) - 1:
                        self.selected += 1
                        self.after(0, lambda: self.listbox.selection_set(self.selected))
                        self.after(100, self.go_live)

            threading.Thread(target=advance, daemon=True).start()

    def ppt_prev(self):
        """PPT previous"""
        if platform.system() == "Darwin":
            script = 'tell application "Microsoft PowerPoint" to go to previous slide active presentation'
            subprocess.Popen(["osascript", "-e", script])

    def ppt_start(self):
        """PPT start"""
        if platform.system() == "Darwin":
            script = 'tell application "Microsoft PowerPoint" to run slide show active presentation'
            subprocess.Popen(["osascript", "-e", script])

    def ppt_next(self):
        """PPT next"""
        if platform.system() == "Darwin":
            script = 'tell application "Microsoft PowerPoint" to go to next slide active presentation'
            subprocess.Popen(["osascript", "-e", script])

    def ppt_end(self):
        """PPT end"""
        if platform.system() == "Darwin":
            script = 'tell application "Microsoft PowerPoint" to exit slide show active presentation'
            subprocess.Popen(["osascript", "-e", script])

    def _start_updates(self):
        """Start update loop"""
        def update():
            # Deck A
            if self.deck_a.playing and self.deck_a.cue:
                name = Path(self.deck_a.cue.path).name
                self.widget_a.update(
                    f"▶ {shorten(name, 28)}",
                    self.deck_a.elapsed(),
                    self.deck_a.remaining(),
                    self.deck_a.progress()
                )
            else:
                self.widget_a.update("—", 0, 0, 0)

            # Deck B
            if self.deck_b.playing and self.deck_b.cue:
                name = Path(self.deck_b.cue.path).name
                self.widget_b.update(
                    f"▶ {shorten(name, 28)}",
                    self.deck_b.elapsed(),
                    self.deck_b.remaining(),
                    self.deck_b.progress()
                )
            else:
                self.widget_b.update("—", 0, 0, 0)

            self.after(100, update)

        update()

    def _load_preset(self):
        """Load preset"""
        if not self.preset_file.exists():
            return

        try:
            with open(self.preset_file) as f:
                data = json.load(f)

            self.settings = Settings.from_dict(data.get("settings", {}))
            self.cues = [Cue.from_dict(c) for c in data.get("cues", [])]
            self._refresh_list()
        except Exception as e:
            print(f"Load error: {e}")

    def save_preset(self):
        """Save preset"""
        data = {
            "version": 2,
            "settings": self.settings.to_dict(),
            "cues": [c.to_dict() for c in self.cues],
        }

        try:
            with open(self.preset_file, "w") as f:
                json.dump(data, f, indent=2)
            messagebox.showinfo("Saved", "Preset saved")
        except Exception as e:
            messagebox.showerror("Error", f"Save failed: {e}")

    def save_show(self):
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
            messagebox.showinfo("Saved", "Show saved")
        except Exception as e:
            messagebox.showerror("Error", f"Save failed: {e}")

    def open_show(self):
        """Open show"""
        path = filedialog.askopenfilename(filetypes=[("JSON", "*.json")])
        if not path:
            return

        try:
            with open(path) as f:
                data = json.load(f)

            self.settings = Settings.from_dict(data.get("settings", {}))
            self.cues = [Cue.from_dict(c) for c in data.get("cues", [])]
            self._refresh_list()
            messagebox.showinfo("Loaded", "Show loaded")
        except Exception as e:
            messagebox.showerror("Error", f"Load failed: {e}")

    def open_settings(self):
        """Open settings"""
        dlg = tk.Toplevel(self)
        dlg.title("Settings")
        dlg.geometry("420x320")
        dlg.configure(bg=T.PANEL_BG)

        tk.Label(dlg, text="SETTINGS", bg=T.PANEL_BG, fg=T.TEXT_PRIMARY,
                font=("Arial", 15, "bold")).pack(pady=18)

        # Second screen X
        f = tk.Frame(dlg, bg=T.PANEL_BG)
        f.pack(fill="x", padx=25, pady=4)
        tk.Label(f, text="Second Screen X:", bg=T.PANEL_BG, fg=T.TEXT_PRIMARY,
                width=18, anchor="w").pack(side="left")
        x_var = tk.StringVar(value=str(self.settings.second_screen_x))
        tk.Entry(f, textvariable=x_var, width=10, bg=T.CONTROL_BG,
                fg=T.TEXT_PRIMARY).pack(side="left")

        # Second screen Y
        f = tk.Frame(dlg, bg=T.PANEL_BG)
        f.pack(fill="x", padx=25, pady=4)
        tk.Label(f, text="Second Screen Y:", bg=T.PANEL_BG, fg=T.TEXT_PRIMARY,
                width=18, anchor="w").pack(side="left")
        y_var = tk.StringVar(value=str(self.settings.second_screen_y))
        tk.Entry(f, textvariable=y_var, width=10, bg=T.CONTROL_BG,
                fg=T.TEXT_PRIMARY).pack(side="left")

        # Fullscreen
        f = tk.Frame(dlg, bg=T.PANEL_BG)
        f.pack(fill="x", padx=25, pady=4)
        tk.Label(f, text="Video Fullscreen:", bg=T.PANEL_BG, fg=T.TEXT_PRIMARY,
                width=18, anchor="w").pack(side="left")
        fs_var = tk.BooleanVar(value=self.settings.video_fullscreen)
        tk.Checkbutton(f, variable=fs_var, bg=T.PANEL_BG).pack(side="left")

        # Volume
        f = tk.Frame(dlg, bg=T.PANEL_BG)
        f.pack(fill="x", padx=25, pady=4)
        tk.Label(f, text="Startup Volume:", bg=T.PANEL_BG, fg=T.TEXT_PRIMARY,
                width=18, anchor="w").pack(side="left")
        vol_var = tk.StringVar(value=str(self.settings.startup_volume))
        tk.Entry(f, textvariable=vol_var, width=10, bg=T.CONTROL_BG,
                fg=T.TEXT_PRIMARY).pack(side="left")

        def save():
            try:
                self.settings.second_screen_x = int(x_var.get())
                self.settings.second_screen_y = int(y_var.get())
                self.settings.video_fullscreen = fs_var.get()
                self.settings.startup_volume = int(vol_var.get())
                dlg.destroy()
            except:
                messagebox.showerror("Error", "Invalid values")

        BroadcastButton(dlg, "SAVE", save, color=T.BLUE,
                       padx=35, pady=12).pack(pady=25)

# =============================================================================
# ENTRY
# =============================================================================

if __name__ == "__main__":
    app = BroadcastApp()
    app.mainloop()
