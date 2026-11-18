"""Qt Quick operator window and the isolated show-controller connection."""
from io import BytesIO
from pathlib import Path
import queue
import sys
import threading
import time


def controller_worker(connection):
    """Own the existing show model and media players, including auxiliary dialogs."""
    if sys.platform == "win32":
        from ctypes import WinDLL, c_void_p
        set_context = WinDLL("user32").SetThreadDpiAwarenessContext
        set_context.argtypes = (c_void_p,)
        set_context.restype = c_void_p
        set_context(c_void_p(-2))  # The hidden controller uses system DPI.
    from player import App, _format_timecode, _resolve_fftool, _no_console_subprocess_kwargs
    from operator_ui import show_tools
    from spectral_waveform import SpectralOverview

    app = App(operator_worker=True)
    spectrum = SpectralOverview()
    actions = {
        "open": app._open_show, "save": app._save_show, "save_as": app._save_show_as,
        "new": app._new_show, "settings": app._open_global_setup,
        "fullscreen": app._toggle_output_fullscreen, "close": app._on_closing,
        "scene_add": app._add_scene, "scene_remove": app._remove_scene, "scene_edit": app._edit_scene,
        "scene_up": lambda: app._move_scene(-1), "scene_down": lambda: app._move_scene(1),
        "scene_prev": app._prev_scene, "scene_next": app._next_scene,
        "a_add": app._add_media_a, "a_remove": app._remove_a,
        "a_up": lambda: app._move_a(-1), "a_down": lambda: app._move_a(1),
        "a_tools": lambda: show_tools(app.audio_tools, app.tabs_a, app.tab_a_inout),
        "a_play": app._play_deck_a, "a_stop": app._stop_deck_a, "a_repeat": lambda: app._toggle_loop("A"),
        "b_add": app._add_media_b, "b_remove": app._remove_b,
        "b_up": lambda: app._move_b(-1), "b_down": lambda: app._move_b(1),
        "b_tools": lambda: show_tools(app.projection_tools, app.tabs_b, app.tab_b_ppt),
        "download": lambda: show_tools(app.projection_tools, app.tabs_b, app.tab_b_download),
        "b_play": app._play_deck_b, "b_stop": app._stop_deck_b, "b_repeat": lambda: app._toggle_loop("B"),
        "slide_prev": app._ppt_prev_ui, "slide_next": app._ppt_next_ui,
    }
    outgoing = queue.Queue(maxsize=1)
    closing = threading.Event()

    def send_snapshots():
        sent_state = None
        sent_frames = {}
        while not closing.is_set():
            try:
                state, frames = outgoing.get(timeout=0.2)
            except queue.Empty:
                continue
            changes = {}
            for key, frame in frames.items():
                if key in sent_frames and sent_frames[key] is frame:
                    continue
                if frame is None:
                    changes[key] = b""
                else:
                    encoded = BytesIO()
                    frame.save(encoded, "JPEG", quality=86)
                    changes[key] = encoded.getvalue()
                sent_frames[key] = frame
            if changes or state != sent_state:
                try:
                    connection.send((state, changes))
                except (BrokenPipeError, EOFError, OSError):
                    return
                sent_state = state

    def run_action(name, value):
        if name in actions:
            actions[name]()
        elif name == "window":
            app._operator_hwnd = int(value)
        elif name == "scene":
            if 0 <= value < len(app._scenes):
                app.scene_listbox.select(value)
                app._on_scene_select(value)
        elif name in ("seek", "mark_out"):
            from types import SimpleNamespace
            x = max(0, min(1, float(value))) * app.canvas_a.winfo_width()
            app._waveform_click(SimpleNamespace(x=x), "A", "OUT" if name == "mark_out" else "IN")
        elif name == "slide_key":
            if app._ppt_running:
                (app._ppt_next_ui if value > 0 else app._ppt_prev_ui)()
        elif name in ("a_select", "b_select", "a_auto", "b_auto", "a_check", "b_check"):
            deck, operation = name.split("_")
            cues = app._cues_a if deck == "a" else app._cues_b
            if not 0 <= value < len(cues):
                return
            tree = app.tree_a if deck == "a" else app.tree_b
            if operation == "select":
                tree.selection_set(str(value))
                (app._on_deck_a_select if deck == "a" else app._on_deck_b_select)()
            elif operation == "auto":
                cue = cues[value]
                cue.auto_play = not cue.auto_play
                app._update_tree_item(cue)
                if deck == "b":
                    app._update_visuals_playlist_info()
            else:
                checked = app._checked_cues_a if deck == "a" else app._checked_cues_b
                if value in checked:
                    checked.remove(value)
                else:
                    checked.add(value)
        elif name in ("a_drop", "b_drop") and app._selected_scene_idx >= 0:
            scene = app._scenes[app._selected_scene_idx]
            add = app._add_paths_routed if name == "a_drop" else app._add_paths_visuals
            add(value, scene)
            app._activate_scene()

    def cue_rows(deck, cues, tree, selected, checkbox, checked):
        return [dict(name=cue.display_name(), kind=cue.kind, armed=cue.auto_play,
                     selected=index == selected, role=tree.emphasis.get(str(index), ""),
                     detail=tree.set(str(index), "duration") if tree.exists(str(index)) else "",
                     checkbox=checkbox, checked=index in checked)
                for index, cue in enumerate(cues)]

    def projection_clock():
        out = app.video_runner
        current = out.current_cue()
        if out.owner_deck not in ("A", "B") or current is None:
            return {"fraction": -1, "label": ""}
        if current.kind == "video" and out._sess:
            path, pos, duration, ended = out._sess.observed("path", "time-pos", "duration", "eof-reached")
            if path == current.path and isinstance(pos, (int, float)) and isinstance(duration, (int, float)):
                start = float(current.start_sec)
                end = min(duration, current.stop_at_sec) if current.stop_at_sec is not None else duration
                if end > start:
                    if ended:
                        return {"fraction": 1, "label": "▪ End"}
                    prefix = "Ⅱ " if out.is_paused() else "● "
                    return {"fraction": round(max(0, min(1, (pos - start) / (end - start))), 3),
                            "label": prefix + _format_timecode(max(0, end - pos), with_ms=True)[:-2]}
        if out.owner_deck == "B" and app._ppt_running and current.kind == "image":
            if app._loop_b_enabled:
                seconds = app._ppt_still_duration()
                elapsed = time.monotonic() - app._ppt_slide_started
                return {"fraction": round(max(0, min(1, elapsed / seconds)), 3),
                        "label": "● " + _format_timecode(max(0, seconds - elapsed), with_ms=True)[:-2]}
            return {"fraction": -1, "label": "● Live · Manual"}
        return {"fraction": -1, "label": ""}

    def snapshot():
        runner, active_cue = app._current_playback_source()
        selected = app._selected_cue_for_deck("A")
        duration = app._duration_for_cue(selected) if selected else None
        path = selected.path if selected and selected.kind in ("audio", "video") else None
        if path != spectrum.requested:
            spectrum.select(path, _resolve_fftool("ffmpeg") if path else None, _no_console_subprocess_kwargs())
        bars = spectrum.read()
        playing_pos = runner.playback_position_sec() if runner else None
        position = playing_pos if selected and active_cue == selected else None
        paused = app._paused_state_for_deck("A")
        if runner is None and paused is not None and selected and paused[0] == selected.id:
            position = playing_pos = float(paused[1])
            active_cue = selected
        countdown = app.var_now_a_time.get()
        if active_cue and playing_pos is not None:
            limit = active_cue.stop_at_sec
            if limit is None:
                limit = app._duration_for_cue(active_cue)
            if limit is not None:
                countdown = "−" + _format_timecode(max(0, limit - playing_pos), with_ms=True)[:-2]
        state = {
            "show": app.var_showfile.get(), "activity": app.var_activity.get(),
            "busy": bool(app._busy_jobs), "fullscreen": app.var_presentation_btn.get(),
            "scenes": [dict(name=scene.name, selected=i == app._selected_scene_idx,
                            role="next" if app._selected_scene_idx >= 0 and i == app._selected_scene_idx + 1 else "",
                            number=f"{i+1:02d}")
                       for i, scene in enumerate(app._scenes)],
            "audio": cue_rows("A", app._cues_a, app.tree_a, app._selected_a, app._checkbox_mode_a, app._checked_cues_a),
            "projection": cue_rows("B", app._cues_b, app.tree_b, app._selected_b, app._checkbox_mode_b, app._checked_cues_b),
            "waveform": bars or [], "waveLoading": bars is None, "time": countdown,
            "duration": app.var_wave_duration.get(), "source": app.var_wave_source.get(),
            "inPoint": min(1, selected.start_sec / duration) if selected and duration else 0,
            "outPoint": min(1, selected.stop_at_sec / duration) if selected and duration and selected.stop_at_sec else 0,
            "ticks": [tick.get() for tick in app._wave_scale_ticks],
            "cursor": min(1, position / duration) if position is not None and duration else -1,
            "projectionClock": projection_clock(), "liveCaption": app.output_monitor.captions["live"].get(),
            "nextCaption": app.output_monitor.captions["next"].get(), "slides": app._ppt_running,
            "slidePosition": app.var_slide_position.get(), "play": app.var_play_a.get(),
            "repeatA": app._loop_a_enabled, "repeatB": app._loop_b_enabled,
        }
        return state, dict(app.output_monitor.frames)

    def tick():
        try:
            if connection.poll():
                name, value = connection.recv()
                run_action(name, value)
                if app._closing:
                    return
        except (BrokenPipeError, EOFError, OSError):
            app._on_closing()
            return
        except Exception as exc:
            app._log(f"Operator action failed: {exc}")
            app._callback_notice = f"Operator action failed: {exc}"
            app._render_activity()
        try:
            outgoing.get_nowait()
        except queue.Empty:
            pass
        outgoing.put_nowait(snapshot())
        app.after(33, tick)

    threading.Thread(target=send_snapshots, daemon=True, name="operator-ui-sender").start()
    app.after(0, tick)
    try:
        app.mainloop()
    finally:
        closing.set()
        spectrum.close()
        connection.close()


def run_quick(on_ready=None):
    from multiprocessing import get_context
    from PySide6.QtCore import QObject, Property, QSize, QTimer, QUrl, Signal, Slot, Qt
    from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter, QPainterPath, QPen
    from PySide6.QtQml import QQmlApplicationEngine
    from PySide6.QtQuick import QQuickImageProvider
    from operator_ui import _ICON_PATHS

    class PreviewImages(QQuickImageProvider):
        def __init__(self):
            super().__init__(QQuickImageProvider.ImageType.Image)
            self.images = {}

        def requestImage(self, image_id, size, requested_size):
            return self.images.get(image_id.partition("/")[0], QImage())

    class Icons(QQuickImageProvider):
        def __init__(self):
            super().__init__(QQuickImageProvider.ImageType.Image)
            self.cache = {}

        def requestImage(self, image_id, size, requested_size):
            name, _, color = image_id.partition("/")
            edge = min(128, max(16, requested_size.width()))
            key = (name, color, edge)
            cached = self.cache.get(key)
            if cached is not None:
                return cached
            icon = QImage(edge, edge, QImage.Format.Format_ARGB32_Premultiplied)
            icon.fill(Qt.GlobalColor.transparent)
            painter = QPainter(icon)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.scale(edge / 20, edge / 20)
            painter.setPen(QPen(QColor("#" + color) if color else QColor("#ecf5ff"), 1.5,
                                Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
            for points in _ICON_PATHS.get(name, ()):
                path = QPainterPath()
                path.moveTo(*points[0])
                for point in points[1:]:
                    path.lineTo(*point)
                painter.drawPath(path)
            painter.end()
            self.cache[key] = icon
            return icon

    class Bridge(QObject):
        stateChanged = Signal()
        listsChanged = Signal()
        previewsChanged = Signal()
        waveformChanged = Signal()

        def __init__(self, connection, previews):
            super().__init__()
            self.connection = connection
            self.preview_provider = previews
            self.current = {"activity": "Starting Show Control…", "busy": True, "fullscreen": "Fullscreen"}
            self.rows = {}
            self.revisions = {}
            self.generation = 0
            self.bars = []

        @Property("QVariantMap", notify=stateChanged)
        def state(self):
            return self.current

        @Property("QVariantMap", notify=listsChanged)
        def lists(self):
            return self.rows

        @Property("QVariantMap", notify=previewsChanged)
        def previews(self):
            return self.revisions

        @Property("QVariantList", notify=waveformChanged)
        def waveform(self):
            return self.bars

        @Slot(str, "QVariant")
        def press(self, name, value=None):
            try:
                self.connection.send((name, value))
            except (OSError, EOFError):
                qt_app.quit()

        @Slot(str, "QVariantList")
        def drop(self, deck, urls):
            paths = [(url if isinstance(url, QUrl) else QUrl(str(url))).toLocalFile() for url in urls]
            self.press(deck + "_drop", [path for path in paths if path])

        def receive(self):
            try:
                if not self.connection.poll():
                    if not worker.is_alive():
                        qt_app.quit()
                    return
                state, changes = self.connection.recv()
                for _ in range(4):
                    if not self.connection.poll():
                        break
                    state, extra = self.connection.recv()
                    changes.update(extra)
            except (OSError, EOFError):
                qt_app.quit()
                return
            for key, data in changes.items():
                self.generation += 1
                self.preview_provider.images[key] = QImage.fromData(data)
                self.revisions[key] = f"image://preview/{key}/{self.generation}" if data else ""
            if changes:
                self.previewsChanged.emit()
            bars = state.pop("waveform")
            if self.bars != bars:
                self.bars = bars
                self.waveformChanged.emit()
            rows = {key: state.pop(key) for key in ("audio", "projection", "scenes")}
            if self.rows != rows:
                self.rows = rows
                self.listsChanged.emit()
            if self.current != state:
                self.current = state
                self.stateChanged.emit()

    qt_app = QGuiApplication([sys.argv[0]])
    preview = PreviewImages()
    context = get_context("spawn")
    frontend, backend = context.Pipe()
    worker = context.Process(target=controller_worker, args=(backend,), name="Show Control transport")
    worker.start()
    backend.close()
    bridge = Bridge(frontend, preview)
    engine = QQmlApplicationEngine()
    engine.addImageProvider("preview", preview)
    engine.addImageProvider("icon", Icons())
    engine.rootContext().setContextProperty("operatorBridge", bridge)
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    engine.load(QUrl.fromLocalFile(str(base / "operator.qml")))
    windows = engine.rootObjects()
    if windows:
        if sys.platform == "win32":
            bridge.press("window", int(windows[0].winId()))
        timer = QTimer()
        timer.timeout.connect(bridge.receive)
        timer.start(16)
        if on_ready is not None:
            on_ready(qt_app, windows[0], bridge)
        qt_app.exec()
    if worker.is_alive():
        bridge.press("close", None)
    frontend.close()
    worker.join(8)
    if worker.is_alive():
        worker.terminate()
        worker.join(3)
    if not windows:
        raise RuntimeError("The operator window could not load.")
