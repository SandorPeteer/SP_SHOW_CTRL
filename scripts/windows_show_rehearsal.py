"""Run the production A/B transport handlers with real bundled players on a Windows CI desktop."""
import faulthandler
import hashlib
import shutil
import json
import os
from pathlib import Path
import subprocess
import sys
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
    long_scene = "Grade 13D · Welcome, class photos and the full awards ceremony"
    try:
        app.update()
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
        app._load_show_from_path(showfile, set_show_path=True)
        def waveform_ready():
            app.update()
            return app.canvas_a.find_withtag("waveform")
        wait_for(waveform_ready, bool)
        print("PASS: real Windows waveform generation and Tk drawing")
        assert Path(app._cues_a[0].path) == music
        assert Path(app._cues_b[1].path) == clip
        before_import = controller_dpi(app)
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
        measured = []
        for width, height in ((980, 620), (1440, 820), (980, 620)):
            app.geometry(f"{width}x{height}+20+40")
            app.update()
            assert app.winfo_width() == width and app.winfo_height() == height
            widgets = (app.btn_output_fullscreen, app.btn_save_show, app.scene_listbox,
                       app.btn_play_a, app.btn_play_b, app.btn_next_slide, app.tree_b)
            for widget in widgets:
                assert widget.winfo_viewable() and widget.winfo_width() > 20
                assert widget.winfo_rootx() + widget.winfo_width() <= app.winfo_rootx() + width
                assert widget.winfo_rooty() + widget.winfo_height() <= app.winfo_rooty() + height
            assert long_scene in app.scene_listbox.get("1.0", "2.0")
            assert app.scene_listbox.count("1.0", "2.0", "displaylines")[0] > 1
            assert app.output_monitor.winfo_rooty() < app.tree_b.winfo_rooty()
            measured.append((app.scene_listbox.winfo_width(), app.output_monitor.canvases["live"].winfo_height()))
        assert measured[1][0] > measured[0][0] and measured[1][1] > measured[0][1], measured
        assert measured[2] == measured[0], measured
        print("PASS: 980x620 and 1440x820 workspace reflows and wraps full scene names")
        ImageGrab.grab().save(root / "dist/windows-rehearsal-preview.png")
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
