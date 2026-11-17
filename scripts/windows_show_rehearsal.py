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
            "scenes": [{"id": "school", "name": "School", "cue_ids_a": ["A1"], "cue_ids_b": ["B1", "B2", "B3", "B4"]}]}),
            encoding="utf-8")
        app._load_show_from_path(showfile, set_show_path=True)
        before_import = controller_dpi(app)
        with patch("player.filedialog.askopenfilenames", return_value=(str(ppt),)):
            app._add_media_b()
        assert Path(app._cues_b[-1].path) == ppt
        monitors = get_monitors()
        assert monitors and app._controller_monitor(monitors) in monitors
        assert controller_dpi(app) == before_import, "PPT import changed the controller DPI or size"
        print(f"PASS: monitor query and PPT import kept controller DPI and size {before_import}")
        def waveform_ready():
            app.update()
            return app.canvas_a.find_withtag("waveform")
        wait_for(waveform_ready, bool)
        print("PASS: real Windows waveform generation and Tk drawing")
        assert Path(app._cues_a[0].path) == music
        assert Path(app._cues_b[1].path) == clip
        assert app._cue_for_show(app._cues_a[0], folder)["path"] == "./" + music.name
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

        app._ppt_fullscreen_2nd_screen()
        hwnd = output_hwnd()
        app._selected_b = 2
        app._play_selected_ppt_b()
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
        app._exit_presentation_mode()
        visual.set_property("window-minimized", True)
        app._bring_to_front()
        app.tabs_a.select(app.tab_a_inout)
        app.tabs_b.select(app.tab_b_ppt)
        app.update()
        for panel in (app.tab_a_inout, app.tab_b_ppt):
            assert panel.winfo_rooty() + panel.winfo_height() <= app.winfo_rooty() + app.winfo_height()
        assert app.output_monitor.winfo_rooty() < app.tree_b.winfo_rooty()
        def descendants(widget):
            for child in widget.winfo_children():
                yield child
                yield from descendants(child)
        assert not any(str(widget.cget("text")) == "Teljes képernyő"
                       for widget in descendants(app.tab_b_ppt) if "text" in widget.keys())
        # Slide navigation must remain available independently of tools tabs.
        navigation = next(widget for widget in descendants(app)
                          if "text" in widget.keys() and str(widget.cget("text")) == "Következő dia")
        app.tabs_b.select(app.tab_b_setup)
        app.update()
        assert navigation.winfo_viewable()
        assert navigation.winfo_rooty() + navigation.winfo_height() <= app.winfo_rooty() + app.winfo_height()
        app.tabs_b.select(app.tab_b_ppt)
        app.update()
        print("PASS: normal/selected/hover/disabled tabs meet 4.5:1 contrast; PPT controls fit the laptop window")
        ImageGrab.grab().save(root / "dist/windows-rehearsal-preview.png")
        visual.set_property("window-minimized", False)
        app._ppt_fullscreen_2nd_screen()
        app._ppt_next_ui()
        assert_slide("0002.mp4")
        app._poll_output_monitor()
        wait_for(monitors_ready, lambda _: app.output_monitor.frames["live"] is not None)
        assert app.output_monitor.frames["next"] is None
        assert app.output_monitor.captions["next"].get() == "Utolsó dia"
        print("PASS: output monitor and upcoming slide rendered without advancing or replacing projector media")
        app.var_ppt_sound.set(True)
        app._ppt_apply_sound()
        assert visual.get_property("mute") is False
        app._ppt_prev_ui()
        assert_slide("0001.png")
        app._ppt_end()
        wait_for(current_slide, lambda v: v and Path(v) == image)
        assert visual.get_property("fullscreen") is True and output_hwnd() == hwnd
        app._play_selected_ppt_b()
        app._transport_stop("B")
        wait_for(lambda: (app._drain_ui_tasks(), app._ppt_preparer.pending)[1], lambda v: not v)
        assert not app._ppt_running
        print("PASS: slide/image/video navigation and End retained the same fullscreen Windows HWND; stopped requests did not take the output")
        app._transport_stop("B")
        wait_for(lambda: audio.get_property("time-pos"), lambda v: v and v > before)
        assert app.audio_runner.is_playing()
        print("PASS: Deck A music advanced throughout Deck B image/video/image/stop, visuals muted")
        app._selected_b = 3
        app._play_selected_ppt_b()
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
                    assert Path(app.output_monitor.targets["next"]).name == "0001.png"
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
