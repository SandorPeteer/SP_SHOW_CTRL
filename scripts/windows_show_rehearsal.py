"""Run the production A/B transport handlers with real bundled players on a Windows CI desktop."""
import faulthandler
import hashlib
import shutil
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from unittest.mock import patch

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))
from player import App, MpvIpcSession, get_monitors


def controller_dpi(app):
    from ctypes import byref, c_int, c_void_p, windll
    from tkinter import font

    mode = c_int()
    assert windll.shcore.GetProcessDpiAwareness(None, byref(mode)) == 0
    current = windll.user32.GetThreadDpiAwarenessContext
    current.restype = c_void_p
    app.update_idletasks()
    return (mode.value, current(), float(app.tk.call("tk", "scaling")),
            font.nametofont("TkDefaultFont", root=app).metrics("linespace"),
            app.winfo_width(), app.winfo_height())

def wait_for(read, matches):
    until = time.monotonic() + 5
    while True:
        value = read()
        if matches(value):
            return value
        if time.monotonic() >= until:
            raise AssertionError(f"Timed out, last value: {value!r}")
        time.sleep(0.1)


def rehearse(folder):
    tools = root / "dist/SP Show Control/_internal/tools"
    os.environ["SP_SHOW_CTRL_MPV"] = str(tools / "mpv/mpv.exe")
    os.environ["SP_SHOW_CTRL_FFMPEG_DIR"] = str(tools / "ffmpeg")
    # CI has no physical sound card. Decode audio against the real-time null output.
    os.environ["MPV_HOME"] = str(folder)
    name = "CI_AUDIO_" + str(os.getpid())
    audio = MpvIpcSession(os.environ["SP_SHOW_CTRL_MPV"], name=name,
                          second_screen_left=0, second_screen_top=0, fullscreen=False)
    (folder / "mpv.conf").write_text(f"ao=null\ninput-ipc-server={audio.ipc_server}\n")
    music = folder / "iskolai próba.wav"
    clip = folder / "árvíz teszt.mp4"
    image = root / "assets/logo.png"
    subprocess.run([tools / "ffmpeg/ffmpeg.exe", "-v", "error", "-f", "lavfi", "-i",
                    "sine=frequency=220:duration=180", music], check=True)
    # A bound rehearsal export exercises the real presentation transport on CI without Office.
    ppt = folder / "próba.pptx"
    ppt.write_bytes(b"CI source for prepared slide media")
    sidecar = ppt.with_name(ppt.name + ".mpv")
    sidecar.mkdir()
    shutil.copyfile(image, sidecar / "0001.png")
    shutil.copyfile(clip, sidecar / "0002.mp4")
    (sidecar / "slides.json").write_text(json.dumps({"version": 1,
        "sha256": hashlib.sha256(ppt.read_bytes()).hexdigest(), "slides": ["png", "mp4"]}))
    from PIL import Image
    loop_ppt = folder / "four slides.pptx"
    loop_ppt.write_bytes(b"Four-slide prepared presentation")
    loop_dir = loop_ppt.with_name(loop_ppt.name + ".mpv")
    loop_dir.mkdir()
    for i, color in enumerate(("red", "green", "blue", "yellow"), 1):
        Image.new("RGB", (160, 90), color).save(loop_dir / f"{i:04d}.png")
    (loop_dir / "slides.json").write_text(json.dumps({"version": 1,
        "sha256": hashlib.sha256(loop_ppt.read_bytes()).hexdigest(),
        "slides": ["png"] * 4, "seconds": [0.4, 0.5, 0.4, 0.5]}))
    app = App()
    errors = []
    app.report_callback_exception = lambda error, value, tb: errors.append((value, tb))
    long_scene = "Grade 13D · Welcome, class photos and the full awards ceremony"
    try:
        def pump(read):
            app.update()
            if errors:
                exc, tb = errors.pop(0)
                raise exc.with_traceback(tb)
            return read()

        # The startup bar animates while projector IPC starts, even with a delayed process.
        from player import OutputRunner
        original_start = OutputRunner.ensure_window
        startup_gate = threading.Event()
        startup_entered = threading.Event()

        def slow_start(runner):
            startup_entered.set()
            assert startup_gate.wait(timeout=5)
            return original_start(runner)

        with patch.object(OutputRunner, "ensure_window", slow_start):
            wait_for(lambda: pump(startup_entered.is_set), bool)
            assert app.activity_progress.winfo_viewable()
            assert app.activity_frame.master == app.btn_output_fullscreen.master
            assert app.activity_progress.winfo_rootx() < app.btn_output_fullscreen.winfo_rootx()
            progress = app.activity_progress.phase
            wait_for(lambda: pump(lambda: app.activity_progress.phase), lambda value: value != progress)
            assert app._starting_up
            startup_gate.set()
            wait_for(lambda: pump(lambda: not app._starting_up), bool)
        print("PASS: startup progress and Tk event loop remained responsive during a blocked projector launch")
        from tkinter import ttk
        style = ttk.Style(app)
        assert style.theme_use() == "clam"
        def luminance(color):
            rgb = [channel / 65535 for channel in app.winfo_rgb(color)]
            rgb = [v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4 for v in rgb]
            return sum(channel * weight for channel, weight in zip(rgb, (0.2126, 0.7152, 0.0722)))
        for state in ((), ("selected",), ("active",), ("disabled",)):
            fg = luminance(style.lookup("Deck.TNotebook.Tab", "foreground", state))
            bg = luminance(style.lookup("Deck.TNotebook.Tab", "background", state))
            assert (max(fg, bg) + 0.05) / (min(fg, bg) + 0.05) >= 4.5, state
        # Open a show saved on another machine, from outside its media directory.
        showfile = folder / "portable.json"
        showfile.write_text(json.dumps({"version": 3, "media_paths": "show-folder",
            "cues_a": [{"id": "A1", "kind": "audio", "path": "./" + music.name}],
            "cues_b": [{"id": "B1", "kind": "image", "path": str(image)},
                       {"id": "B2", "kind": "video", "path": "./" + clip.name},
                       {"id": "B3", "kind": "ppt", "path": "./" + ppt.name},
                       {"id": "B4", "kind": "ppt", "path": "./" + loop_ppt.name}],
            "scenes": [{"id": "school", "name": long_scene, "cue_ids_a": ["A1"], "cue_ids_b": ["B1", "B2", "B3", "B4"]}]}),
            encoding="utf-8")
        from player import probe_media_duration_sec
        probe_gate = threading.Event()
        probe_entered = threading.Event()

        def slow_probe(path):
            probe_entered.set()
            assert probe_gate.wait(timeout=5)
            return probe_media_duration_sec(path)

        with patch("player.probe_media_duration_sec", slow_probe):
            app._load_show_from_path(showfile, set_show_path=True)
            wait_for(lambda: pump(probe_entered.is_set), bool)
            assert app.activity_progress.winfo_viewable()
            assert app.tree_a.set("0", "duration") == "—"
            probe_gate.set()
            wait_for(lambda: pump(lambda: app.tree_a.set("0", "duration")), lambda value: value == "3:00")
        print("PASS: an outstanding media probe left the rundown responsive and updated its time in place")

        def waveform_ready():
            app.update()
            return app.canvas_a.find_withtag("waveform")
        wait_for(waveform_ready, bool)
        print("PASS: real Windows waveform generation and Tk drawing")
        assert Path(app._cues_a[0].path) == music
        assert Path(app._cues_b[1].path) == clip
        before_import = controller_dpi(app)
        assert before_import[0] == 1, before_import
        with patch("player.filedialog.askopenfilenames", return_value=(str(ppt),)):
            app._add_media_b()
        assert Path(app._cues_b[-1].path) == ppt
        assert app.canvas_a.find_withtag("waveform")
        monitors = get_monitors()
        assert monitors and app._controller_monitor(monitors) in monitors
        assert controller_dpi(app) == before_import, "PPT import changed the controller DPI or size"
        print(f"PASS: monitor query and PPT import kept controller DPI and size {before_import}")
        app._selected_a = 0
        app._transport_play_pause("A")
        audio._connect_ipc()
        before = wait_for(lambda: audio.get_property("time-pos"), lambda v: isinstance(v, (int, float)) and v > 0)
        for index in (0, 1, 0):
            app._selected_b = index
            app._transport_play_pause("B")
            visual = app.video_runner._sess
            expected = Path(app._cues_b[index].path)
            wait_for(lambda: visual.get_property("path"), lambda v: v and Path(v) == expected)
            wait_for(lambda: visual.get_property("video-params"), bool)
            assert visual.get_property("mute") is True
            before = wait_for(lambda: audio.get_property("time-pos"), lambda v: v and v > before)
        import win32gui
        import win32process

        def output_hwnd():
            windows = []
            def collect(hwnd, _):
                if (win32gui.IsWindowVisible(hwnd) and
                        win32process.GetWindowThreadProcessId(hwnd)[1] == visual._proc.pid):
                    windows.append(hwnd)
            win32gui.EnumWindows(collect, None)
            assert windows
            return windows[0]

        hwnd = output_hwnd()
        for full in (True, False, True):
            app.btn_output_fullscreen.invoke()
            assert visual.get_property("fullscreen") is full and output_hwnd() == hwnd
            assert app.var_presentation_btn.get() == ("Windowed" if full else "Fullscreen")
        print("PASS: one projector button toggles fullscreen on the same output window")
        app._selected_b = 2
        app.btn_play_b.invoke()
        def current_slide():
            app._drain_ui_tasks()
            return visual.get_property("path")

        def assert_slide(name):
            wait_for(current_slide, lambda v: v and Path(v).samefile(sidecar / name))
            wait_for(lambda: visual.get_property("video-params"), bool)
            assert visual.get_property("fullscreen") is True
            assert output_hwnd() == hwnd
            assert visual.get_property("mute") is True

        assert_slide("0001.png")
        assert controller_dpi(app) == before_import, "PPT playback changed the controller DPI or size"
        app._poll_output_monitor()
        def monitors_ready():
            app.update()
            return all(frame is not None for frame in app.output_monitor.frames.values())
        wait_for(monitors_ready, bool)
        assert app.output_monitor.captions["next"].get().startswith("2/2")
        assert visual.get_property("path").endswith("0001.png")
        from PIL import ImageGrab
        app.update_idletasks()
        # Save the GUI for layout review before the source app closes.
        app.btn_output_fullscreen.invoke()
        visual.set_property("window-minimized", True)
        app._bring_to_front()
        app.maxsize(1600, 900)
        import tkinter as tk
        import windows_theme as palette

        def main_controls(parent):
            for child in parent.winfo_children():
                if isinstance(child, tk.Toplevel):
                    continue
                if isinstance(child, (tk.Button, ttk.Button, ttk.Menubutton)):
                    yield child
                yield from main_controls(child)

        controls = list(main_controls(app))
        assert len(controls) == 34, len(controls)
        caption_controls = (app.btn_minimize_window, app.btn_zoom_window, app.btn_close_window)
        assert app.btn_output_fullscreen.master == caption_controls[0].master.master
        assert app.btn_output_fullscreen.winfo_rootx() < caption_controls[0].winfo_rootx()
        from ctypes import c_int, c_void_p, windll
        import win32con
        import win32gui
        is_zoomed = windll.user32.IsZoomed
        is_zoomed.argtypes = (c_void_p,)
        is_zoomed.restype = c_int
        wrapper = win32gui.GetAncestor(app.winfo_id(), 2)
        assert not is_zoomed(wrapper)
        app.btn_zoom_window.invoke()
        app.update()
        assert is_zoomed(wrapper)
        app.btn_zoom_window.invoke()
        app.update()
        assert not is_zoomed(wrapper)
        print("PASS: window, file and projection controls share a single toolbar; maximize and restore work")

        def tool_order(last):
            tool = next(button for button in controls if button.cget("text") == last)
            return [button.cget("text") for button in tool.master.winfo_children()]

        assert tool_order("Move scene down") == ["Add scene", "Remove scene", "Move scene up", "Move scene down"]
        assert tool_order("Media tools") == ["Add files", "Remove files", "Move item up", "Move item down", "Media tools"]
        assert tool_order("Projection tools") == ["Add files", "Remove files", "Move item up", "Move item down",
                                                    "Downloads", "Projection tools"]
        assert app.var_slide_position.get() == "01 / 02"
        assert style.lookup(app.btn_play_a.cget("style"), "background") == palette.AMBER
        assert style.lookup(app.btn_play_b.cget("style"), "background") == palette.MINT
        assert style.lookup(app.btn_stop_b.cget("style"), "foreground") == palette.CORAL
        def resize_client(width, height):
            x0, y0, x1, y1 = win32gui.GetWindowRect(wrapper)
            _, _, client_width, client_height = win32gui.GetClientRect(wrapper)
            win32gui.SetWindowPos(wrapper, 0, 20, 40, width + x1 - x0 - client_width,
                                  height + y1 - y0 - client_height,
                                  win32con.SWP_NOZORDER | win32con.SWP_NOACTIVATE)

        measured = []
        for width, height in ((900, 550), (980, 620), (1440, 820), (980, 620)):
            resize_client(width, height)
            actual = wait_for(lambda: pump(lambda: (app.winfo_width(), app.winfo_height())),
                              lambda size: size == (width, height))
            assert actual == (width, height)
            assert all(button.winfo_viewable() for button in controls)
            widgets = (*controls, app.scene_listbox, app.tree_a, app.tree_b, app.canvas_a,
                       *app.output_monitor.canvases.values())
            for widget in widgets:
                assert widget.winfo_viewable() and widget.winfo_width() > 20, (
                    width, height, str(widget), widget.winfo_geometry(),
                    widget.cget("text") if "text" in widget.keys() else None,
                )
                assert widget.winfo_rootx() + widget.winfo_width() <= app.winfo_rootx() + width
                assert widget.winfo_rooty() + widget.winfo_height() <= app.winfo_rooty() + height
            assert long_scene in app.scene_listbox.get("1.0", "2.0")
            assert app.scene_listbox.count("1.0", "2.0", "displaylines")[0] > 1
            monitor = app.output_monitor
            lower = app.tree_b.master.master.master
            assert monitor.winfo_rooty() + monitor.winfo_height() < lower.winfo_rooty()
            assert abs(2 * monitor.winfo_height() - lower.winfo_height()) <= 2
            left, right = monitor.canvases.values()
            wave = app.canvas_a
            assert wave.winfo_rootx() + wave.winfo_width() < left.winfo_rootx() < right.winfo_rootx()
            assert wave.winfo_rooty() == left.winfo_rooty() == right.winfo_rooty()
            assert wave.winfo_width() > left.winfo_width()
            assert abs(left.winfo_width() - right.winfo_width()) <= 1
            assert left.winfo_height() == right.winfo_height()
            assert app.tree_a.winfo_rooty() == app.tree_b.winfo_rooty()
            assert app.btn_play_a.winfo_rooty() == app.btn_play_b.winfo_rooty()
            assert app.btn_play_b.winfo_rootx() < app.btn_prev_slide.winfo_rootx()
            assert app.btn_prev_slide.winfo_rootx() < app.btn_next_slide.winfo_rootx() < app.btn_stop_b.winfo_rootx()
            measured.append((app.scene_listbox.winfo_width(), app.output_monitor.canvases["live"].winfo_height()))
        assert measured[2][0] > measured[1][0] and measured[2][1] > measured[1][1], measured
        assert measured[3] == measured[1], measured
        print("PASS: waveform and both monitors fill the upper third; all 34 controls fit from 900x550 through 1440x820")
        wait_for(lambda: pump(lambda: len(app.output_monitor.photos)), lambda count: count == 2)
        assert "playing" in app.tree_a.item("0", "tags")
        assert "playing" in app.tree_b.item("2", "tags")
        assert "next" in app.tree_b.item("3", "tags")
        bars = tuple(app.canvas_a.find_withtag("waveform"))
        audio_before_resize = audio.get_property("time-pos")
        timings = []
        start_tick = time.perf_counter()
        last_resize = None

        def run_resize(frame):
            if frame == 75 or errors:
                app.quit()
                return
            phase = frame if frame < 38 else 75 - frame
            size = (920 + phase * 4, 560 + phase * 3)
            tick = time.perf_counter()
            resize_client(*size)

            def rendered():
                nonlocal last_resize
                window_size = (app.winfo_width(), app.winfo_height())
                workspace = app.wave_a_frame.master.master
                expected = round(workspace.winfo_width() * 0.40) - 6
                actual = app.wave_a_frame.winfo_width()
                last_resize = (frame, size, window_size, expected, actual)
                if window_size != size or abs(actual - expected) > 1:
                    app.after(1, rendered)
                    return
                timings.append((time.perf_counter() - tick) * 1000)
                delay = start_tick + (frame + 1) / 60 - time.perf_counter()
                app.after(max(1, round(1000 * delay)), run_resize, frame + 1)

            app.after_idle(rendered)

        abort_resize = app.after(6500, app.quit)
        app.after(0, run_resize, 0)
        app.mainloop()
        app.after_cancel(abort_resize)
        if errors:
            value, tb = errors.pop(0)
            raise value.with_traceback(tb)
        assert len(timings) == 75, f"Completed {len(timings)} resizing passes, last request {last_resize}"
        assert tuple(app.canvas_a.find_withtag("waveform")) == bars
        assert audio.get_property("time-pos") > audio_before_resize
        ordered = sorted(timings)
        p95 = ordered[(len(ordered) * 95 + 99) // 100 - 1]
        print(f"Resize with both previews and live audio (75 frames at 60Hz): "
              f"p95 {p95:.2f} ms, max {max(timings):.2f} ms", flush=True)
        assert p95 < 16.667 and max(timings) < 66.667, (p95, max(timings))
        resize_client(980, 620)
        wait_for(lambda: pump(lambda: (app.winfo_width(), app.winfo_height())), lambda size: size == (980, 620))
        wait_for(lambda: pump(lambda: not app.output_monitor.resize_jobs), bool)
        print("PASS: kept the waveform items and media clock running during a 60Hz resize")
        # Scroll only overflows. The indicator overlays the edge and leaves row geometry alone.
        tree = app.tree_b
        assert tuple(map(str, tree["show"])) == ("tree",)
        assert not any(isinstance(widget, ttk.Scrollbar) for widget in tree.master.winfo_children())
        assert app.tree_a.item("0", "image") and tree.item("0", "image")
        from operator_ui import ScrollIndicator
        for widget in (app.scene_listbox, app.tree_a, tree):
            assert isinstance(widget.scroll_indicator, ScrollIndicator)
            assert not any(isinstance(child, ttk.Scrollbar) for child in widget.master.winfo_children())
        indicator = tree.scroll_indicator
        for i in range(40):
            tree.insert("", "end", iid=f"scroll-{i}", values=("", "", i, "video", f"Rehearsal {i}", "0:04"))
        pump(lambda: None)
        assert not indicator.thumb.winfo_ismapped()
        initial = (tree.winfo_width(), tree.column("name", "width"))
        tree.yview_moveto(0.54)
        wait_for(lambda: pump(indicator.thumb.winfo_ismapped), bool)
        assert (tree.winfo_width(), tree.column("name", "width")) == initial
        wait_for(lambda: pump(indicator.thumb.winfo_ismapped), lambda shown: not shown)
        assert (tree.winfo_width(), tree.column("name", "width")) == initial
        tree.delete(*(f"scroll-{i}" for i in range(40)))
        tree.yview_moveto(0)
        pump(lambda: None)
        print("PASS: scrolling overlays a transient indicator without shifting the cue columns")
        x, y = app.winfo_rootx(), app.winfo_rooty()
        ImageGrab.grab(bbox=(x, y, x + app.winfo_width(), y + app.winfo_height())).save(
            root / "dist/windows-rehearsal-preview.png")
        visual.set_property("window-minimized", False)
        app.btn_output_fullscreen.invoke()
        app._ppt_next_ui()
        assert_slide("0002.mp4")
        aspect = lambda: visual.get_property("video-aspect-override")
        source_aspect = aspect()
        from output_monitor import capture_output

        def visible_aspect():
            layout = visual.get_property("osd-dimensions")
            w = layout["w"] - layout["ml"] - layout["mr"]
            h = layout["h"] - layout["mt"] - layout["mb"]
            return w * layout["par"] / h

        for label, expected in (("4:3", 4 / 3), ("16:9", 16 / 9), ("Source", source_aspect)):
            app.var_output_aspect.set(label)
            app._apply_output_aspect()
            actual = aspect()
            assert actual == expected or abs(actual - expected) < 0.001, (label, actual)
            assert output_hwnd() == hwnd
            ratio = 4 / 3 if label == "4:3" else 16 / 9
            wait_for(visible_aspect, lambda value: abs(value - ratio) < 0.02)
            frame = capture_output(visual)
            left, top, right, bottom = frame.getbbox()
            assert abs((right - left) / (bottom - top) - ratio) < 0.02, (label, frame.size, frame.getbbox())
        print("PASS: 4:3, 16:9 and source aspect settings apply to the same live output")
        print("PASS: preview pixels keep the projected aspect ratio and letterbox margins")
        app._poll_output_monitor()
        wait_for(monitors_ready, lambda _: app.output_monitor.frames["live"] is not None)
        assert app.output_monitor.frames["next"] is None
        assert app.output_monitor.captions["next"].get() == "Last slide"
        print("PASS: output monitor and upcoming slide rendered without advancing or replacing projector media")
        app.var_ppt_sound.set(True)
        app._ppt_apply_sound()
        assert visual.get_property("mute") is False
        app.btn_output_fullscreen.invoke()
        visual.set_property("window-minimized", True)
        app.tree_b.focus_force()
        app.update()
        assert app.focus_get() == app.tree_b
        for key, index in (("Prior", 0), ("Right", 1), ("Left", 0), ("Next", 1)):
            app.tree_b.event_generate(f"<KeyPress-{key}>")
            expected = sidecar / ("0001.png" if index == 0 else "0002.mp4")
            assert app._ppt_index == index, (key, app._ppt_index)
            wait_for(current_slide, lambda path: path and Path(path).samefile(expected))
        visual.set_property("window-minimized", False)
        app.btn_output_fullscreen.invoke()
        print("PASS: arrow and presenter Page Up/Page Down keys step the live slideshow")
        app._ppt_prev_ui()
        assert_slide("0001.png")
        app._ppt_end()
        wait_for(current_slide, lambda v: v and Path(v) == image)
        assert visual.get_property("fullscreen") is True and output_hwnd() == hwnd
        app.btn_play_b.invoke()
        app._transport_stop("B")
        wait_for(lambda: (app._drain_ui_tasks(), app._ppt_preparer.pending)[1], lambda v: not v)
        assert not app._ppt_running
        print("PASS: slide/image/video navigation and End retained the same fullscreen Windows HWND; stopped requests did not take the output")
        app._transport_stop("B")
        wait_for(lambda: audio.get_property("time-pos"), lambda v: v and v > before)
        assert app.audio_runner.is_playing()
        print("PASS: Deck A music advanced throughout Deck B image/video/image/stop, visuals muted")
        app._selected_b = 3
        app.btn_play_b.invoke()
        wait_for(lambda: (app.update(), app._ppt_running)[1], bool)
        hold = time.monotonic() + 0.6
        while time.monotonic() < hold:
            app.update()
            time.sleep(0.02)
        assert app._ppt_index == 0, "Loop OFF must retain manual slide control"
        app._toggle_loop("B")
        order = [app._ppt_index]
        deadline = time.monotonic() + 10
        while len(order) < 9 and time.monotonic() < deadline:
            app.update()
            if app._ppt_index != order[-1]:
                order.append(app._ppt_index)
                if app._ppt_index == 3:
                    app.after_cancel(app._monitor_after_id)
                    app._poll_output_monitor()
                    next_path, _ = app.output_monitor.targets["next"]
                    assert Path(next_path).name == "0001.png"
            time.sleep(0.03)
        assert order == [0, 1, 2, 3, 0, 1, 2, 3, 0], order
        assert visual.get_property("fullscreen") is True and output_hwnd() == hwnd
        app._toggle_loop("B")
        paused_index = app._ppt_index
        hold = time.monotonic() + 0.6
        while time.monotonic() < hold:
            app.update()
            time.sleep(0.02)
        assert app._ppt_index == paused_index
        app._ppt_end()
        print("PASS: four slides repeated twice in order; loop OFF retained manual control; lookahead wrapped to the first slide")
        from windows_downloader_rehearsal import exercise_downloader
        before = audio.get_property("time-pos")
        exercise_downloader(app, folder, tools)
        assert audio.get_property("time-pos") > before
        assert app.audio_runner.is_playing()
        app.projection_tools.withdraw()
        # Save repeated cue positions independently while collecting one source file.
        from player import Cue, Scene
        duplicate = Cue(id="A2", kind="audio", path=str(music), start_sec=3.0)
        app._all_cues_a.append(duplicate)
        app._scenes[0].cue_ids_a.append(duplicate.id)
        app._scenes.append(Scene(id="second", name="Grade 13C", cue_ids_a=[duplicate.id], cue_ids_b=["B1"]))
        app.var_ppt_interval.set("8")
        app.btn_save_show.invoke()
        wait_for(lambda: (app.update(), app._saving_show)[1], lambda busy: not busy)
        assert app._auto_open_last_show()
        assert app._show_path.samefile(showfile) and len(app._scenes) == 2
        assert app.var_ppt_interval.get() == "8"
        assert app._scenes[0].cue_ids_a == ["A1", "A2"] and app._scenes[1].cue_ids_a == ["A2"]
        first, second = app._cues_a
        assert first.id != second.id and second.start_sec == 3.0 and first.path == second.path
        assert Path(first.path).resolve().is_relative_to(showfile.with_suffix(".media").resolve())
        saved_ppt = Path(app._cues_b[2].path)
        assert saved_ppt.with_name(saved_ppt.name + ".mpv").joinpath("0001.png").is_file()
        assert music.is_file() and ppt.is_file()
        print("PASS: quick save and reopen preserve repeated cues, shared media and portable prepared slides")
    except Exception:
        print("PPT status:", ascii(app.var_ppt_status.get()), flush=True)
        print("Recent events:", ascii(app._log_buffer[-10:]) if isinstance(app._log_buffer, list)
              else ascii(list(app._log_buffer)[-10:]), flush=True)
        raise
    finally:
        audio.shutdown()
        app._on_closing()


if __name__ == "__main__":
    faulthandler.dump_traceback_later(100)
    try:
        rehearse(Path(sys.argv[1]))
    except Exception:
        # The production GUI displays unhandled errors in a modal. CI needs stderr.
        sys.excepthook = sys.__excepthook__
        raise
    faulthandler.cancel_dump_traceback_later()
