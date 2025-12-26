from __future__ import annotations

import os
import queue
import re
import shlex
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path

from PyQt6.QtCore import QTimer, Qt, QUrl
from PyQt6.QtGui import QAction, QFont, QFontMetrics, QStandardItem, QStandardItemModel
from PyQt6.QtMultimedia import QAudioOutput, QMediaPlayer
from PyQt6.QtMultimediaWidgets import QVideoWidget
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QSplitter,
    QStatusBar,
    QTableView,
    QVBoxLayout,
    QWidget,
    QFileDialog,
)

import ytdlr_core as core


def _split_args(text: str) -> list[str]:
    t = (text or "").strip()
    if not t:
        return []
    try:
        return shlex.split(t)
    except Exception:
        return t.split()


def _is_probable_url(text: str) -> bool:
    t = (text or "").strip().lower()
    return t.startswith("http://") or t.startswith("https://")


_DL_PCT_RE = re.compile(r"\[download\]\s+(\d+(?:\.\d+)?)%")
_DL_DEST_RE = re.compile(r"^\[download\]\s+Destination:\s+(.*)$")
_DJ_BAD_RE = re.compile(r"\b(dj\s*set|live\s*set|podcast|radio|episode|full\s*set|mix\s*set)\b", re.IGNORECASE)
_DJ_HOURS_RE = re.compile(r"\b(\d+)\s*(hour|hours|hr|hrs)\b", re.IGNORECASE)
_DJ_LONG_MIN_RE = re.compile(r"\b(\d{2,})\s*(min|mins|minutes)\b", re.IGNORECASE)
_DJ_WANTS_MIX_RE = re.compile(r"\b(mix|set|live)\b", re.IGNORECASE)


def _query_wants_mix(query: str) -> bool:
    return bool(_DJ_WANTS_MIX_RE.search((query or "").lower()))


def _dj_score(row: SearchRow, query: str, *, prefer_official: bool) -> int:
    title = (row.title or "").lower()
    uploader = (row.uploader or "").lower()
    q = (query or "").lower()

    score = 0
    if prefer_official:
        if uploader.endswith(" - topic"):
            score += 80
        if "official audio" in title:
            score += 60
        if "vevo" in uploader:
            score += 25
        if "official" in uploader:
            score += 20

    # DJ-friendly hints
    for kw in ("remix", "edit", "rework", "bootleg", "mashup", "club", "extended", "vip", "version"):
        if kw in title:
            score += 12
    if "free download" in title:
        score += 4

    # Penalize likely long mixes/sets (unless user explicitly searches for it)
    wants_mix = _query_wants_mix(q)
    if not wants_mix:
        if _DJ_BAD_RE.search(title):
            score -= 80
        if _DJ_HOURS_RE.search(title):
            score -= 120
        m = _DJ_LONG_MIN_RE.search(title)
        if m:
            try:
                mins = int(m.group(1))
            except Exception:
                mins = 0
            if mins >= 30:
                score -= 100

    # Prefer song-length
    d = row.duration_s
    if d is not None:
        if d <= 600:
            score += 20
        elif d <= 900:
            score += 10
        elif d <= 1200:
            score += 0
        else:
            score -= min(80, (d - 1200) // 60)

    # Views as tiebreaker
    if row.views:
        try:
            v = int(row.views)
        except Exception:
            v = 0
        if v > 0:
            score += min(30, int((v.bit_length() - 1) * 2))
    return score


def _dj_filter_rows(rows: list[SearchRow], query: str, *, max_duration_s: int) -> tuple[list[SearchRow], int]:
    wants_mix = _query_wants_mix(query)
    out: list[SearchRow] = []
    removed = 0
    for r in rows:
        d = r.duration_s
        title = (r.title or "")
        if max_duration_s and d is not None and d > max_duration_s and not wants_mix:
            removed += 1
            continue
        if not wants_mix:
            title_has_hours = bool(_DJ_HOURS_RE.search(title))
            title_has_bad = bool(_DJ_BAD_RE.search(title))
            title_has_long_mins = False
            m = _DJ_LONG_MIN_RE.search(title)
            if m:
                try:
                    mins = int(m.group(1))
                except Exception:
                    mins = 0
                title_has_long_mins = mins >= 30

            if title_has_hours or title_has_long_mins or title_has_bad:
                # Allow if it's still short
                if d is not None and max_duration_s and d <= max_duration_s:
                    out.append(r)
                else:
                    removed += 1
                continue
        out.append(r)
    return out, removed


class ElidedLabel(QLabel):
    def __init__(self, text: str = "") -> None:
        super().__init__("")
        self._full_text = ""
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.setMinimumWidth(0)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        if text:
            self.setFullText(text)

    def setFullText(self, text: str) -> None:
        self._full_text = str(text or "")
        self.setToolTip(self._full_text)
        self._update_elide()

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        self._update_elide()

    def _update_elide(self) -> None:
        w = max(10, int(self.width() or 0))
        fm = QFontMetrics(self.font())
        el = fm.elidedText(self._full_text, Qt.TextElideMode.ElideMiddle, w)
        super().setText(el)


def _default_dj_dir() -> Path:
    candidates = [
        Path.home() / "Music" / "Rekordbox" / "Auto Import",
        Path.home() / "Music" / "rekordbox" / "Auto Import",
        Path.home() / "Music" / "Rekordbox",
        Path.home() / "Music",
        Path.home() / "Downloads",
    ]
    for p in candidates:
        try:
            if p.exists():
                return p
        except Exception:
            continue
    return Path.home() / "Downloads"


@dataclass(frozen=True)
class SearchRow:
    title: str
    uploader: str
    duration_s: int | None
    views: int | None
    url: str
    rank: int = 0


def _pick_recordbox_audio(formats: list[core.FormatItem]) -> core.FormatItem | None:
    audio = [f for f in formats if f.kind == "a"]
    if not audio:
        return None
    rb = [f for f in audio if (f.ext == "m4a") and (f.acodec or "").startswith("mp4a")]
    if not rb:
        rb = [f for f in audio if f.ext == "m4a"]
    candidates = rb if rb else audio
    candidates.sort(key=lambda f: (-(f.abr or f.tbr or 0.0), -(f.filesize or 0)))
    return candidates[0] if candidates else None

def _fmt_time(ms: int) -> str:
    try:
        s = max(0, int(ms) // 1000)
    except Exception:
        s = 0
    return core.format_duration(s)


class YtDlrQt(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("yt-dlr (DJ)")
        self.resize(1100, 700)

        self._video_enabled = False
        self._current_url: str = ""
        self._current_formats: list[core.FormatItem] = []
        self._selected_format_id: str = ""
        self._preview_duration_ms: int = 0
        self._preview_slider_dragging = False
        self._downloading = False
        self._updating_tools = False
        self._formats_cache: dict[str, list[core.FormatItem]] = {}
        self._direct_cache: dict[tuple[str, str], str] = {}
        self._busy_count: int = 0
        self._busy_phase: int = 0
        self._pending_load_url: str = ""
        self._pending_busy: bool = False
        self._last_search_meta: str = ""

        self._player = QMediaPlayer(self)
        self._audio = QAudioOutput(self)
        self._audio.setVolume(1.0)
        self._player.setAudioOutput(self._audio)
        self._player.errorOccurred.connect(self._on_player_error)
        self._player.durationChanged.connect(self._on_duration_changed)
        self._player.positionChanged.connect(self._on_position_changed)

        self._tasks: "queue.Queue[tuple[str, object]]" = queue.Queue()
        self._results: "queue.Queue[tuple[str, object]]" = queue.Queue()
        self._bg_thread = threading.Thread(target=self._bg_loop, daemon=True)
        self._bg_thread.start()

        self._init_ui()
        self._init_menu()

        QTimer.singleShot(0, self._ensure_ytdlp_ready)
        self._busy_timer = QTimer(self)
        self._busy_timer.timeout.connect(self._tick_busy)
        self._busy_timer.start(140)
        self._load_timer = QTimer(self)
        self._load_timer.setSingleShot(True)
        self._load_timer.timeout.connect(self._do_pending_load)
        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(self._drain_bg_queue)
        self._poll_timer.start(80)

    def _init_ui(self) -> None:
        root = QWidget()
        self.setCentralWidget(root)

        layout = QVBoxLayout(root)

        top = QHBoxLayout()
        layout.addLayout(top)

        top.addWidget(QLabel("Search:"))
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("pl. 'artist - track' (Enter)")
        self.search_edit.returnPressed.connect(self._on_search)
        top.addWidget(self.search_edit, 2)
        top.addWidget(QLabel("Limit:"))
        self.spin_limit = QSpinBox()
        self.spin_limit.setRange(10, 200)
        self.spin_limit.setValue(50)
        self.spin_limit.setFixedWidth(70)
        top.addWidget(self.spin_limit)
        self.chk_newest = QCheckBox("Newest")
        self.chk_newest.setChecked(False)
        top.addWidget(self.chk_newest)
        self.chk_dj = QCheckBox("DJ filter")
        self.chk_dj.setChecked(True)
        top.addWidget(self.chk_dj)
        top.addWidget(QLabel("Max:"))
        self.cmb_max = QComboBox()
        self.cmb_max.addItem("Any", 0)
        self.cmb_max.addItem("6 min", 6 * 60)
        self.cmb_max.addItem("8 min", 8 * 60)
        self.cmb_max.addItem("10 min", 10 * 60)
        self.cmb_max.addItem("12 min", 12 * 60)
        self.cmb_max.addItem("15 min", 15 * 60)
        self.cmb_max.addItem("20 min", 20 * 60)
        self.cmb_max.setCurrentText("12 min")
        self.cmb_max.setFixedWidth(90)
        top.addWidget(self.cmb_max)
        self.chk_official = QCheckBox("Prefer official")
        self.chk_official.setChecked(True)
        top.addWidget(self.chk_official)
        self.btn_search = QPushButton("Search")
        self.btn_search.clicked.connect(self._on_search)
        top.addWidget(self.btn_search)

        top.addSpacing(14)
        top.addWidget(QLabel("URL:"))
        self.url_edit = QLineEdit()
        self.url_edit.setPlaceholderText("https://www.youtube.com/watch?v=...")
        self.url_edit.returnPressed.connect(self._on_load_url)
        top.addWidget(self.url_edit, 3)
        self.btn_load = QPushButton("Load")
        self.btn_load.clicked.connect(self._on_load_url)
        top.addWidget(self.btn_load)

        main = QSplitter()
        main.setOrientation(Qt.Orientation.Vertical)
        layout.addWidget(main, 1)

        results = QWidget()
        results_l = QVBoxLayout(results)
        results_l.setContentsMargins(0, 0, 0, 0)
        main.addWidget(results)

        self.results_model = QStandardItemModel(0, 4)
        self.results_model.setHorizontalHeaderLabels(["Title", "Uploader", "Dur", "Views"])
        self.results_view = QTableView()
        self.results_view.setModel(self.results_model)
        self.results_view.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self.results_view.setSelectionMode(QTableView.SelectionMode.SingleSelection)
        self.results_view.setSortingEnabled(True)
        self.results_view.doubleClicked.connect(self._on_result_double_clicked)
        self.results_view.clicked.connect(self._on_result_clicked)
        results_l.addWidget(QLabel("Results (top)"))
        results_l.addWidget(self.results_view, 1)

        bottom = QSplitter()
        bottom.setOrientation(Qt.Orientation.Horizontal)
        main.addWidget(bottom)

        left = QWidget()
        left_l = QVBoxLayout(left)
        left_l.setContentsMargins(0, 0, 0, 0)
        bottom.addWidget(left)

        right = QWidget()
        right_l = QVBoxLayout(right)
        right_l.setContentsMargins(0, 0, 0, 0)
        bottom.addWidget(right)

        right_l.addWidget(QLabel("Preview"))
        self.video_widget = QVideoWidget()
        self.video_widget.setMinimumHeight(180)
        self.video_widget.setStyleSheet("background-color: #000;")
        self.video_widget.setVisible(False)
        right_l.addWidget(self.video_widget)
        self._player.setVideoOutput(self.video_widget)

        preview_controls = QHBoxLayout()
        right_l.addLayout(preview_controls)
        self.chk_video = QCheckBox("Video")
        self.chk_video.setChecked(False)
        self.chk_video.stateChanged.connect(self._on_video_changed)
        preview_controls.addWidget(self.chk_video)
        self.chk_auto_preview = QCheckBox("Auto preview")
        self.chk_auto_preview.setChecked(True)
        preview_controls.addWidget(self.chk_auto_preview)
        self.btn_play = QPushButton("Play")
        self.btn_play.clicked.connect(self._on_preview_play)
        preview_controls.addWidget(self.btn_play)
        self.btn_pause = QPushButton("Pause/Resume")
        self.btn_pause.clicked.connect(self._on_preview_pause)
        preview_controls.addWidget(self.btn_pause)
        self.btn_stop = QPushButton("Stop")
        self.btn_stop.clicked.connect(self._on_preview_stop)
        preview_controls.addWidget(self.btn_stop)
        preview_controls.addStretch(1)

        seek = QHBoxLayout()
        right_l.addLayout(seek)
        self.lbl_time = QLabel("0:00 / 0:00")
        seek.addWidget(self.lbl_time)
        self.slider_pos = QSlider(Qt.Orientation.Horizontal)
        self.slider_pos.setRange(0, 0)
        self.slider_pos.sliderPressed.connect(self._on_slider_pressed)
        self.slider_pos.sliderReleased.connect(self._on_slider_released)
        self.slider_pos.sliderMoved.connect(self._on_slider_moved)
        seek.addWidget(self.slider_pos, 1)

        fmt_top = QHBoxLayout()
        left_l.addLayout(fmt_top)
        fmt_top.addWidget(QLabel("Recordbox audio formats (m4a/AAC)"))
        self.btn_list_formats = QPushButton("List formats")
        self.btn_list_formats.clicked.connect(self._on_list_formats)
        fmt_top.addWidget(self.btn_list_formats)
        fmt_top.addStretch(1)

        self.formats_model = QStandardItemModel(0, 6)
        self.formats_model.setHorizontalHeaderLabels(["ID", "Ext", "ACodec", "ABR", "Size", "Note"])
        self.formats_view = QTableView()
        self.formats_view.setModel(self.formats_model)
        self.formats_view.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self.formats_view.setSelectionMode(QTableView.SelectionMode.SingleSelection)
        self.formats_view.setSortingEnabled(True)
        self.formats_view.doubleClicked.connect(self._on_format_double_clicked)
        left_l.addWidget(self.formats_view, 1)

        dl = QHBoxLayout()
        right_l.addLayout(dl)
        dl.addWidget(QLabel("Watch folder:"))
        self.dj_dir_edit = QLineEdit(str(_default_dj_dir()))
        dl.addWidget(self.dj_dir_edit, 2)
        self.btn_browse = QPushButton("Browse…")
        self.btn_browse.clicked.connect(self._on_browse_dir)
        dl.addWidget(self.btn_browse)
        self.btn_dj_download = QPushButton("DJ Download (m4a)")
        self.btn_dj_download.clicked.connect(self._on_dj_download)
        dl.addWidget(self.btn_dj_download)

        self.dl_progress = QProgressBar()
        self.dl_progress.setRange(0, 100)
        self.dl_progress.setValue(0)
        self.dl_progress.setVisible(False)
        right_l.addWidget(self.dl_progress)

        self.lbl_dl = ElidedLabel("")
        right_l.addWidget(self.lbl_dl)

        self.extra_opts = QLineEdit()
        self.extra_opts.setPlaceholderText("Extra yt-dlp opts (optional), pl: --cookies-from-browser chrome")
        right_l.addWidget(self.extra_opts)

        self.setStatusBar(QStatusBar())
        self.busy_bar = QProgressBar()
        self.busy_bar.setFixedWidth(110)
        self.busy_bar.setRange(0, 0)  # indeterminate
        self.busy_bar.setVisible(False)
        self.statusBar().addPermanentWidget(self.busy_bar)
        self.busy_lbl = QLabel("")
        self.busy_lbl.setVisible(False)
        self.statusBar().addPermanentWidget(self.busy_lbl)
        main.setStretchFactor(0, 2)
        main.setStretchFactor(1, 3)
        bottom.setStretchFactor(0, 3)
        bottom.setStretchFactor(1, 4)

    def _init_menu(self) -> None:
        tools = self.menuBar().addMenu("Tools")

        act_use_system = QAction("Use system yt-dlp (recommended)", self)
        act_use_system.triggered.connect(self._use_system_ytdlp)
        tools.addAction(act_use_system)

        act_use_managed = QAction("Use managed yt-dlp (app data)", self)
        act_use_managed.triggered.connect(self._use_managed_ytdlp)
        tools.addAction(act_use_managed)

        act_show = QAction("Show active yt-dlp", self)
        act_show.triggered.connect(self._show_active_ytdlp)
        tools.addAction(act_show)

        tools.addSeparator()
        act_update = QAction("Update yt-dlp", self)
        act_update.triggered.connect(self._on_update_ytdlp)
        tools.addAction(act_update)

        act_quit = QAction("Quit", self)
        act_quit.triggered.connect(self.close)
        self.menuBar().addAction(act_quit)

    def closeEvent(self, event) -> None:  # type: ignore[override]
        try:
            self._player.stop()
        except Exception:
            pass
        event.accept()

    def _on_video_changed(self) -> None:
        self._video_enabled = bool(self.chk_video.isChecked())
        self.video_widget.setVisible(bool(self._video_enabled))
        # Restart preview with the new mode (audio vs video).
        if self._player.playbackState() != QMediaPlayer.PlaybackState.StoppedState:
            QTimer.singleShot(0, self._on_preview_play)

    def _bg_loop(self) -> None:
        while True:
            task, payload = self._tasks.get()
            if task == "search":
                try:
                    query = str(payload.get("query") or "").strip()  # type: ignore[union-attr]
                    limit = int(payload.get("limit") or 50)  # type: ignore[union-attr]
                    newest = bool(payload.get("newest") or False)  # type: ignore[union-attr]
                    dj = bool(payload.get("dj") or False)  # type: ignore[union-attr]
                    max_dur = int(payload.get("max_dur") or 0)  # type: ignore[union-attr]
                    prefer_official = bool(payload.get("prefer_official") or False)  # type: ignore[union-attr]
                except Exception:
                    query = str(payload)
                    limit = 50
                    newest = False
                    dj = False
                    max_dur = 0
                    prefer_official = False
                try:
                    ytdlp = core.resolve_ytdlp("")
                    kind = "ytsearchdate" if newest else "ytsearch"
                    cmd = core.build_search_cmd(
                        ytdlp=ytdlp,
                        query=query,
                        limit=max(1, min(limit, 200)),
                        fast=True,
                        search_kind=kind,
                    )
                    proc = subprocess.run(cmd, check=False, text=True, capture_output=True, env=os.environ.copy())
                    if proc.returncode != 0:
                        raise RuntimeError((proc.stderr or proc.stdout or "").strip() or f"yt-dlp exit={proc.returncode}")
                    lines = [ln for ln in (proc.stdout or "").splitlines() if ln.strip().startswith("{")]
                    items = core.parse_search_items(lines)
                    rows: list[SearchRow] = []
                    for idx, it in enumerate(items):
                        rows.append(
                            SearchRow(
                                title=it.title,
                                uploader=it.uploader,
                                duration_s=it.duration,
                                views=it.view_count,
                                url=it.webpage_url,
                                rank=int(idx),
                            )
                        )
                    total = len(rows)
                    removed = 0
                    if dj:
                        rows, removed = _dj_filter_rows(rows, query, max_duration_s=int(max_dur or 0))
                    if rows and dj:
                        rows.sort(
                            key=lambda r: (
                                _dj_score(r, query, prefer_official=prefer_official),
                                -int(r.rank) if newest else 0,
                            ),
                            reverse=True,
                        )
                    meta = f"Results: {len(rows)}/{total}"
                    if removed:
                        meta += f" (filtered {removed})"
                    self._results.put(("search_result", (rows, meta)))
                except Exception as e:
                    self._results.put(("error", f"Search error: {e}"))
            elif task == "formats":
                url = str(payload)
                try:
                    ytdlp = core.resolve_ytdlp("")
                    cmd = core.build_info_cmd(ytdlp=ytdlp, url=url, no_playlist=True)
                    proc = subprocess.run(cmd, check=False, text=True, capture_output=True, env=os.environ.copy())
                    if proc.returncode != 0:
                        raise RuntimeError((proc.stderr or proc.stdout or "").strip() or f"yt-dlp exit={proc.returncode}")
                    info = core.parse_info_json(proc.stdout or "")
                    formats = core.extract_format_items(info)
                    self._results.put(("formats_result", (url, formats)))
                except Exception as e:
                    self._results.put(("error", f"Formats error: {e}"))
            elif task == "direct_url":
                url, format_selector, passthrough = payload  # type: ignore[misc]
                try:
                    ytdlp = core.resolve_ytdlp("")
                    cmd = core.build_get_url_cmd(
                        ytdlp=ytdlp,
                        url=str(url),
                        format_selector=str(format_selector),
                        no_playlist=True,
                        passthrough=list(passthrough),
                    )
                    proc = subprocess.run(cmd, check=False, text=True, capture_output=True, env=os.environ.copy())
                    if proc.returncode != 0:
                        raise RuntimeError((proc.stderr or proc.stdout or "").strip() or f"yt-dlp exit={proc.returncode}")
                    direct = [ln.strip() for ln in (proc.stdout or "").splitlines() if ln.strip()]
                    if not direct:
                        raise RuntimeError("yt-dlp -g returned empty output")
                    self._results.put(("direct_url_result", (str(url), str(format_selector), direct[0])))
                except Exception as e:
                    self._results.put(("error", f"Preview URL error: {e}"))
            elif task == "download":
                url, out_dir, format_id, passthrough = payload  # type: ignore[misc]
                try:
                    ytdlp = core.resolve_ytdlp("")
                    out = str(Path(str(out_dir)).expanduser())
                    Path(out).mkdir(parents=True, exist_ok=True)
                    cmd = core.build_download_cmd(
                        ytdlp=ytdlp,
                        urls=[str(url)],
                        out_dir=out,
                        template=core.DEFAULT_TEMPLATE,
                        mode="audio",
                        format_selector=str(format_id),
                        no_playlist=True,
                        passthrough=list(passthrough),
                    )
                    proc = subprocess.Popen(
                        cmd,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        text=True,
                        bufsize=1,
                        env=os.environ.copy(),
                    )
                    self._results.put(("download_status", "Starting…"))
                    final_path = ""
                    tail: list[str] = []
                    out_stream = proc.stdout
                    if out_stream is not None:
                        for raw in out_stream:
                            line = str(raw or "").strip()
                            if not line:
                                continue
                            tail.append(line)
                            if len(tail) > 60:
                                tail = tail[-60:]
                            m = _DL_PCT_RE.search(line)
                            if m:
                                try:
                                    pct = float(m.group(1))
                                except Exception:
                                    pct = 0.0
                                self._results.put(("download_progress", int(max(0.0, min(100.0, pct)))))
                                self._results.put(("download_status", f"Downloading… {pct:.1f}%"))
                            md = _DL_DEST_RE.match(line)
                            if md:
                                dest_full = md.group(1).strip()
                                try:
                                    dest_name = Path(dest_full).name
                                except Exception:
                                    dest_name = dest_full
                                self._results.put(("download_status", f"Destination: {dest_name}"))
                            if ("/" in line or "\\\\" in line) and (len(line) < 1024) and Path(line).suffix:
                                final_path = line
                    rc = proc.wait()
                    if rc != 0:
                        msg = "\n".join(tail[-12:])
                        raise RuntimeError(msg or f"yt-dlp exit={rc}")
                    self._results.put(("download_progress", 100))
                    self._results.put(("download_done", final_path or out))
                except Exception as e:
                    self._results.put(("error", f"Download error: {e}"))
            elif task == "update_ytdlp":
                try:
                    from ytdlr_tools import download_latest_ytdlp, local_ytdlp_path

                    self._results.put(("tools_status", "Updating yt-dlp…"))
                    self._results.put(("tools_progress", (0, None)))

                    def _p(done: int, total: int | None) -> None:
                        self._results.put(("tools_progress", (done, total)))

                    p = download_latest_ytdlp(progress_cb=_p)
                    self._results.put(("tools_done", str(p)))
                    try:
                        self._results.put(("tools_status", f"yt-dlp ready: {local_ytdlp_path()}"))
                    except Exception:
                        pass
                except Exception as e:
                    self._results.put(
                        (
                            "error",
                            "Update yt-dlp error: "
                            f"{e}\n\n"
                            "Fix options:\n"
                            "- Try again (now has curl fallback)\n"
                            "- Install certifi: `python3 -m pip install certifi`\n"
                            "- If using python.org on macOS: run 'Install Certificates.command'\n"
                            "- Debug logs: `./yt-dlr --qt --debug`",
                        )
                    )

    def _drain_bg_queue(self) -> None:
        for _ in range(20):
            try:
                task, payload = self._results.get_nowait()
            except queue.Empty:
                return

            if task == "search_result":
                self._busy_pop()
                rows, meta = payload  # type: ignore[misc]
                self._last_search_meta = str(meta or "")
                self._apply_search_results(rows)  # type: ignore[arg-type]
            elif task == "formats_result":
                self._busy_pop()
                url, formats = payload  # type: ignore[misc]
                self._formats_cache[str(url)] = list(formats)
                if str(url) == str(self._current_url):
                    self._apply_formats(formats)  # type: ignore[arg-type]
            elif task == "direct_url_result":
                self._busy_pop()
                url, fmt, direct = payload  # type: ignore[misc]
                self._direct_cache[(str(url), str(fmt))] = str(direct)
                if str(url) == str(self._current_url):
                    self._play_direct(str(direct))
            elif task == "download_progress":
                try:
                    self.dl_progress.setVisible(True)
                    self.dl_progress.setValue(int(payload or 0))
                except Exception:
                    pass
            elif task == "download_status":
                try:
                    self.lbl_dl.setFullText(str(payload or ""))
                except Exception:
                    pass
            elif task == "download_done":
                self._downloading = False
                try:
                    self.btn_dj_download.setEnabled(True)
                except Exception:
                    pass
                self.statusBar().showMessage(f"Downloaded: {payload}", 9000)
                try:
                    self.lbl_dl.setFullText(f"Done: {payload}")
                    self.dl_progress.setValue(100)
                except Exception:
                    pass
            elif task == "error":
                self._downloading = False
                try:
                    self.btn_dj_download.setEnabled(True)
                except Exception:
                    pass
                self._busy_pop()
                self._show_error(str(payload))
            elif task == "tools_progress":
                try:
                    done, total = payload  # type: ignore[misc]
                    self.dl_progress.setVisible(True)
                    if total:
                        pct = int(max(0, min(100, (int(done) * 100) // int(total))))
                        self.dl_progress.setRange(0, 100)
                        self.dl_progress.setValue(pct)
                    else:
                        self.dl_progress.setRange(0, 0)  # indeterminate
                except Exception:
                    pass
            elif task == "tools_status":
                try:
                    self.lbl_dl.setFullText(str(payload or ""))
                except Exception:
                    pass
            elif task == "tools_done":
                self._updating_tools = False
                try:
                    self.dl_progress.setRange(0, 100)
                    self.dl_progress.setValue(100)
                    self.lbl_dl.setFullText(f"yt-dlp updated: {payload}")
                except Exception:
                    pass
                self.statusBar().showMessage(f"yt-dlp updated: {payload}", 8000)
            else:
                pass

    def _show_error(self, msg: str) -> None:
        self.statusBar().showMessage(msg, 8000)
        QMessageBox.warning(self, "yt-dlr", msg)

    def _ensure_ytdlp_ready(self) -> None:
        try:
            core.resolve_ytdlp("")
            return
        except Exception:
            pass
        r = QMessageBox.question(
            self,
            "yt-dlr",
            "yt-dlp nincs telepítve (vagy nem található). Letöltsem a legfrissebb standalone yt-dlp-t?",
        )
        if r == QMessageBox.StandardButton.Yes:
            self._on_update_ytdlp()

    def _on_update_ytdlp(self) -> None:
        if self._updating_tools:
            return
        self._updating_tools = True
        try:
            self.dl_progress.setVisible(True)
            self.dl_progress.setRange(0, 0)
            self.lbl_dl.setFullText("Updating yt-dlp…")
        except Exception:
            pass
        self._tasks.put(("update_ytdlp", None))

    def _use_system_ytdlp(self) -> None:
        try:
            os.environ.pop("YTDLR_YTDLP", None)
        except Exception:
            pass
        try:
            p = core.which_or_none("yt-dlp") or ""
            self.statusBar().showMessage(f"Using system yt-dlp: {p}" if p else "Using system yt-dlp (PATH)", 7000)
        except Exception:
            self.statusBar().showMessage("Using system yt-dlp (PATH)", 5000)

    def _use_managed_ytdlp(self) -> None:
        try:
            from ytdlr_tools import local_ytdlp_path

            p = local_ytdlp_path()
            if not p.exists():
                self._show_error("Managed yt-dlp not found yet. Use Tools → Update yt-dlp first.")
                return
            os.environ["YTDLR_YTDLP"] = str(p)
            self.statusBar().showMessage(f"Using managed yt-dlp: {p}", 7000)
        except Exception as e:
            self._show_error(f"Cannot switch to managed yt-dlp: {e}")

    def _show_active_ytdlp(self) -> None:
        try:
            p = core.resolve_ytdlp("")
        except Exception as e:
            self._show_error(f"yt-dlp not available: {e}")
            return
        QMessageBox.information(self, "yt-dlr", f"Active yt-dlp:\n\n{p}")

    def _on_duration_changed(self, ms: int) -> None:
        self._preview_duration_ms = int(ms or 0)
        self.slider_pos.setRange(0, max(0, self._preview_duration_ms))
        self._update_time_label(self._player.position(), self._preview_duration_ms)

    def _on_position_changed(self, ms: int) -> None:
        if self._preview_slider_dragging:
            return
        self.slider_pos.setValue(int(ms or 0))
        self._update_time_label(int(ms or 0), self._preview_duration_ms)

    def _update_time_label(self, pos_ms: int, dur_ms: int) -> None:
        self.lbl_time.setText(f"{_fmt_time(pos_ms)} / {_fmt_time(dur_ms)}")

    def _on_slider_pressed(self) -> None:
        self._preview_slider_dragging = True

    def _on_slider_moved(self, v: int) -> None:
        self._update_time_label(int(v or 0), self._preview_duration_ms)

    def _on_slider_released(self) -> None:
        v = int(self.slider_pos.value() or 0)
        self._preview_slider_dragging = False
        try:
            self._player.setPosition(v)
        except Exception:
            pass

    def _on_search(self) -> None:
        q = (self.search_edit.text() or "").strip()
        if not q:
            return
        self.statusBar().showMessage("Searching…")
        self._busy_push("Searching")
        try:
            max_dur = int(self.cmb_max.currentData() or 0)
        except Exception:
            max_dur = 0
        payload = {
            "query": q,
            "limit": int(self.spin_limit.value() or 50),
            "newest": bool(self.chk_newest.isChecked()),
            "dj": bool(self.chk_dj.isChecked()),
            "max_dur": int(max_dur),
            "prefer_official": bool(self.chk_official.isChecked()),
        }
        self._tasks.put(("search", payload))

    def _apply_search_results(self, rows: list[SearchRow]) -> None:
        self.results_model.removeRows(0, self.results_model.rowCount())
        for r in rows:
            items = [
                QStandardItem(r.title),
                QStandardItem(r.uploader),
                QStandardItem(core.format_duration(r.duration_s)),
                QStandardItem(core.format_views(r.views)),
            ]
            for it in items:
                it.setEditable(False)
            items[0].setData(r.url, Qt.ItemDataRole.UserRole)
            self.results_model.appendRow(items)
        self.results_view.resizeColumnsToContents()
        if self._last_search_meta:
            self.statusBar().showMessage(self._last_search_meta, 5000)
        else:
            self.statusBar().showMessage(f"Results: {len(rows)}", 4000)

    def _selected_result_url(self) -> str:
        idx = self.results_view.currentIndex()
        if not idx.isValid():
            return ""
        model_idx = self.results_model.index(idx.row(), 0)
        url = self.results_model.data(model_idx, Qt.ItemDataRole.UserRole)
        return str(url or "")

    def _on_result_double_clicked(self, _idx) -> None:
        url = self._selected_result_url()
        if url:
            self.url_edit.setText(url)
            self._schedule_load_url(url, immediate=True)

    def _on_result_clicked(self, _idx) -> None:
        url = self._selected_result_url()
        if url and url != self._current_url:
            self.url_edit.setText(url)
            self._schedule_load_url(url, immediate=False)

    def _on_load_url(self) -> None:
        t = (self.url_edit.text() or "").strip()
        if not t:
            return
        if _is_probable_url(t):
            self._schedule_load_url(t, immediate=True)
        else:
            # Allow pasting a query into URL field
            self.search_edit.setText(t)
            self._on_search()

    def _schedule_load_url(self, url: str, *, immediate: bool) -> None:
        self._pending_load_url = str(url or "").strip()
        if not self._pending_load_url:
            return
        self.statusBar().showMessage("Selecting…")
        if not self._pending_busy:
            self._pending_busy = True
            self._busy_push("Loading")
        self._load_timer.start(1 if immediate else 220)

    def _do_pending_load(self) -> None:
        if self._pending_busy:
            self._pending_busy = False
            self._busy_pop()
        u = (self._pending_load_url or "").strip()
        if not u:
            return
        self._load_url(u)

    def _load_url(self, url: str) -> None:
        self._current_url = url
        self._current_formats = []
        self.formats_model.removeRows(0, self.formats_model.rowCount())
        self._selected_format_id = ""

        # Start preview ASAP (audio) before full formats arrive.
        if bool(self.chk_auto_preview.isChecked()) and (not bool(self._video_enabled)):
            extra = _split_args(self.extra_opts.text())
            self._start_preview_direct(url=url, format_selector=core.recordbox_audio_m4a_format_selector(), passthrough=extra)

        cached = self._formats_cache.get(url)
        if cached:
            self._apply_formats(cached)
            return

        self.statusBar().showMessage("Fetching formats…")
        self._busy_push("Fetching formats")
        self._tasks.put(("formats", url))

    def _on_list_formats(self) -> None:
        url = (self.url_edit.text() or self._current_url or "").strip()
        if not url:
            self._show_error("Missing URL")
            return
        self._load_url(url)

    def _apply_formats(self, formats: list[core.FormatItem]) -> None:
        self._current_formats = formats
        self.formats_model.removeRows(0, self.formats_model.rowCount())

        audio = [f for f in formats if f.kind == "a"]
        rb = [f for f in audio if f.ext == "m4a" and (f.acodec or "").startswith("mp4a")]
        if not rb:
            rb = [f for f in audio if f.ext == "m4a"]
        shown = rb if rb else audio

        if not shown:
            self.statusBar().showMessage("No audio formats found", 6000)
            return

        shown.sort(key=lambda f: (-(f.abr or f.tbr or 0.0), -(f.filesize or 0)))
        for f in shown:
            row = [
                QStandardItem(f.format_id),
                QStandardItem(f.ext),
                QStandardItem(f.acodec),
                QStandardItem(core.format_bitrate_kbps(f.abr or f.tbr)),
                QStandardItem(core.format_filesize(f.filesize)),
                QStandardItem(f.format_note),
            ]
            for it in row:
                it.setEditable(False)
            row[0].setData(f.format_id, Qt.ItemDataRole.UserRole)
            self.formats_model.appendRow(row)

        self.formats_view.resizeColumnsToContents()
        self.statusBar().showMessage(
            f"Recordbox formats: {len(rb)}" if rb else f"No m4a/AAC found; showing audio formats: {len(shown)}",
            5000,
        )

        best = _pick_recordbox_audio(formats)
        if best:
            self._selected_format_id = best.format_id
            self._auto_select_format(best.format_id)
        if bool(self.chk_auto_preview.isChecked()) and self._player.playbackState() == QMediaPlayer.PlaybackState.StoppedState:
            QTimer.singleShot(0, self._on_preview_play)

    def _auto_select_format(self, format_id: str) -> None:
        for row in range(self.formats_model.rowCount()):
            idx = self.formats_model.index(row, 0)
            if str(self.formats_model.data(idx)) == str(format_id):
                self.formats_view.selectRow(row)
                self.formats_view.scrollTo(idx)
                return

    def _selected_format_id_from_ui(self) -> str:
        idx = self.formats_view.currentIndex()
        if not idx.isValid():
            return ""
        model_idx = self.formats_model.index(idx.row(), 0)
        return str(self.formats_model.data(model_idx) or "")

    def _on_format_double_clicked(self, _idx) -> None:
        self._on_preview_play()

    def _on_preview_play(self) -> None:
        url = (self.url_edit.text() or self._current_url or "").strip()
        if not url:
            self._show_error("Missing URL")
            return
        if bool(self._video_enabled):
            fmt_id = "best[ext=mp4][vcodec^=avc1][acodec^=mp4a]/best[ext=mp4]/best"
        else:
            fmt_id = self._selected_format_id_from_ui() or self._selected_format_id
            if not fmt_id:
                self._show_error("No recordbox-compatible audio format found (m4a/AAC).")
                return
        extra = _split_args(self.extra_opts.text())
        self.statusBar().showMessage("Resolving preview URL…")
        self._start_preview_direct(url=url, format_selector=fmt_id, passthrough=extra)

    def _start_preview_direct(self, *, url: str, format_selector: str, passthrough: list[str]) -> None:
        u = str(url or "").strip()
        fs = str(format_selector or "").strip()
        if not u or not fs:
            return
        cached = self._direct_cache.get((u, fs))
        if cached and u == self._current_url:
            self._play_direct(cached)
            return
        self._busy_push("Loading preview")
        self._tasks.put(("direct_url", (u, fs, list(passthrough or []))))

    def _play_direct(self, direct_url: str) -> None:
        try:
            # Avoid restarting if we already play the same stream.
            cached = getattr(self, "_current_direct_url", "")
            if str(cached) == str(direct_url) and self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
                return
            self._current_direct_url = str(direct_url)
            self._player.setSource(QUrl(str(direct_url)))
            self._player.play()
            self.statusBar().showMessage("Playing…", 2000)
        except Exception as e:
            self._show_error(f"Preview play failed: {e}")

    def _busy_push(self, base: str) -> None:
        self._busy_count += 1
        try:
            self.busy_bar.setVisible(True)
            self.busy_lbl.setVisible(True)
            self.busy_lbl.setText(f"{base} {self._busy_frame()}")
        except Exception:
            pass

    def _busy_pop(self) -> None:
        if self._busy_count > 0:
            self._busy_count -= 1
        if self._busy_count <= 0:
            self._busy_count = 0
            try:
                self.busy_bar.setVisible(False)
                self.busy_lbl.setVisible(False)
            except Exception:
                pass

    def _busy_frame(self) -> str:
        frames = ["|", "/", "-", "\\\\"]
        return frames[self._busy_phase % len(frames)]

    def _tick_busy(self) -> None:
        if self._busy_count <= 0:
            return
        self._busy_phase = (self._busy_phase + 1) % 1000000
        try:
            t = str(self.busy_lbl.text() or "")
            if not t:
                self.busy_lbl.setText(self._busy_frame())
                return
            parts = t.split()
            if parts:
                parts[-1] = self._busy_frame()
                self.busy_lbl.setText(" ".join(parts))
        except Exception:
            pass

    def _on_preview_pause(self) -> None:
        st = self._player.playbackState()
        if st == QMediaPlayer.PlaybackState.PlayingState:
            self._player.pause()
            return
        self._player.play()

    def _on_preview_stop(self) -> None:
        self._player.stop()

    def _on_player_error(self, err, msg: str) -> None:  # noqa: ARG002
        if msg:
            self.statusBar().showMessage(f"Preview error: {msg}", 8000)

    def _on_browse_dir(self) -> None:
        start = self.dj_dir_edit.text().strip() or str(Path.home())
        path = QFileDialog.getExistingDirectory(self, "Select watch folder", start)
        if path:
            self.dj_dir_edit.setText(path)

    def _on_dj_download(self) -> None:
        if self._downloading:
            return
        url = (self.url_edit.text() or self._current_url or "").strip()
        if not url:
            self._show_error("Missing URL")
            return
        fmt_id = self._selected_format_id_from_ui() or self._selected_format_id
        if not fmt_id:
            self._show_error("No recordbox-compatible audio format selected.")
            return
        out_dir = self.dj_dir_edit.text().strip()
        if not out_dir:
            self._show_error("Missing watch folder")
            return
        extra = _split_args(self.extra_opts.text())
        self._downloading = True
        try:
            self.btn_dj_download.setEnabled(False)
        except Exception:
            pass
        try:
            self.dl_progress.setVisible(True)
            self.dl_progress.setValue(0)
            self.lbl_dl.setFullText("Starting…")
        except Exception:
            pass
        self.statusBar().showMessage("Downloading…")
        self._tasks.put(("download", (url, out_dir, fmt_id, extra)))


def run() -> None:
    app = QApplication([])
    app.setApplicationName("yt-dlr")
    w = YtDlrQt()
    f = QFont()
    f.setPointSize(13)
    w.setFont(f)
    w.show()
    app.exec()


if __name__ == "__main__":
    run()
