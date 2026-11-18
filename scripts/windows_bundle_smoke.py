"""Exercise the unpacked x64 application from a fresh profile on a Windows test desktop."""
import os
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time

APP = Path(__file__).resolve().parents[1] / "dist" / "SP Show Control"


def check_output():
    # Run in a separate process so the parent can bound blocking Windows pipe I/O.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from mpv_ipc import MpvIpcSession

    session = MpvIpcSession(str(APP / "_internal/tools/mpv/mpv.exe"), name="OUTPUT",
                            second_screen_left=0, second_screen_top=0, fullscreen=False)
    deadline = time.monotonic() + 20
    while True:
        try:
            session._connect_ipc()
            break
        except (RuntimeError, OSError):
            if time.monotonic() >= deadline:
                raise
    print("Show Control IPC connected", flush=True)
    assert session.get_property("mpv-version"), "No response on the application IPC channel"
    session.loadfile(sys.argv[2])
    for _ in range(50):
        if session.get_property("video-params"):
            break
        time.sleep(0.1)
    else:
        raise RuntimeError("Timed out decoding test video")
    for paused in (True, False):
        assert session.set_property("pause", paused)
        assert session.get_property("pause") is paused
    session.stop()
    session.shutdown()


def smoke():
    from xml.etree import ElementTree
    import win32api
    import win32gui
    import win32process

    exe = APP / "SP Show Control.exe"
    resources = win32api.LoadLibraryEx(str(exe), 0, 2)  # LOAD_LIBRARY_AS_DATAFILE
    try:
        manifest = ElementTree.fromstring(win32api.LoadResource(resources, 24, 1))  # RT_MANIFEST
        assert manifest.findtext(".//{*}dpiAwareness") == "System"
    finally:
        win32api.FreeLibrary(resources)
    print("PASS: packaged controller requests system-DPI rendering at startup")

    with tempfile.TemporaryDirectory(prefix="Show Control próba ") as test:
        env = {k: v for k, v in os.environ.items() if not k.startswith("SP_SHOW_CTRL_")}
        env["APPDATA"] = test
        env["PATH"] = str(Path(os.environ["SystemRoot"]) / "System32")
        clip = Path(test) / "árvíz teszt.mp4"
        media = APP / "_internal" / "tools" / "ffmpeg"
        subprocess.run([media / "ffmpeg.exe", "-v", "error", "-f", "lavfi", "-i",
                        "testsrc2=size=160x90:rate=10", "-f", "lavfi", "-i", "sine=frequency=880",
                        "-t", "4", "-c:v", "mpeg4", "-c:a", "aac", clip],
                       check=True, cwd=test, env=env, timeout=20)
        probe = subprocess.check_output([media / "ffprobe.exe", "-v", "error", "-show_entries",
                                         "stream=width", "-of", "csv=p=0", clip], env=env, timeout=10)
        assert probe.strip() == b"160", probe
        subprocess.run([media / "ffplay.exe", "-version"], stdout=subprocess.DEVNULL,
                       check=True, env=env, timeout=10)

        profile = Path(test) / "SP_Show_Control"
        profile.mkdir(exist_ok=True)
        (profile / "show_preset.json").write_text(json.dumps({
            "version": 3, "cues_a": [{"id": "waveform", "kind": "audio", "path": str(clip)}],
            "cues_b": [], "scenes": [{"id": "rehearsal", "name": "Rehearsal",
                                      "cue_ids_a": ["waveform"], "cue_ids_b": []}],
        }), encoding="utf-8")
        app = subprocess.Popen([exe], env=env, cwd=test)
        try:
            deadline = time.monotonic() + 45
            while time.monotonic() < deadline:
                if app.poll() is not None:
                    raise RuntimeError(f"GUI exited early: {app.returncode}")
                hwnd = win32gui.FindWindow(None, "S.P. Show Control")
                if hwnd and win32process.GetWindowThreadProcessId(hwnd)[1] == app.pid:
                    break
                time.sleep(0.2)
            else:
                raise RuntimeError("Show Control window did not start")
            from ctypes import c_int, c_void_p, windll
            hwnd_context = windll.user32.GetWindowDpiAwarenessContext
            hwnd_context.argtypes = (c_void_p,)
            hwnd_context.restype = c_void_p
            awareness = windll.user32.GetAwarenessFromDpiAwarenessContext
            awareness.argtypes = (c_void_p,)
            awareness.restype = c_int
            assert awareness(hwnd_context(hwnd)) == 1
            import win32con
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline:
                window_style = win32gui.GetWindowLong(hwnd, win32con.GWL_STYLE)
                if not window_style & win32con.WS_CAPTION:
                    break
                time.sleep(0.1)
            else:
                raise AssertionError(f"Integrated toolbar did not replace the caption: {window_style:#x}")
            assert window_style & win32con.WS_THICKFRAME
            import win32api
            work = win32api.GetMonitorInfo(win32api.MonitorFromWindow(hwnd, 2))["Work"]
            bounds = win32gui.GetWindowRect(hwnd)
            assert bounds[1] >= work[1] and bounds[3] <= work[3], (bounds, work)
            print("PASS: frozen EXE has integrated chrome and a native resize frame")
            # A second click must reuse the controller, even during initial loading.
            duplicate = subprocess.Popen([exe], env=env, cwd=test)
            try:
                assert duplicate.wait(timeout=8) == 0
                assert app.poll() is None and win32gui.IsWindow(hwnd)
            finally:
                if duplicate.poll() is None:
                    subprocess.run(["taskkill", "/PID", str(duplicate.pid), "/T", "/F"], capture_output=True)
                    duplicate.wait(timeout=10)
            print("PASS: starting the portable EXE twice kept a single controller")
            # Read pixels from the actual frozen executable with a clean PATH/profile.
            from PIL import ImageGrab
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                output_hwnd = win32gui.FindWindow(None, "SP Show Control Output (OUTPUT)")
                if output_hwnd:
                    win32gui.ShowWindow(output_hwnd, 6)
                bounds = win32gui.GetWindowRect(hwnd)
                frame = ImageGrab.grab(bbox=bounds)
                # Restrict detection to the upper-left waveform lane. The
                # Fullscreen button uses the same neon color elsewhere.
                lane = frame.convert("RGB").crop((20, 67, round(frame.width * 0.38), round(frame.height * 0.31)))
                blue = sum(count for count, rgb in (lane.getcolors(lane.width * lane.height) or [])
                           if abs(rgb[0] - 89) < 5 and abs(rgb[1] - 216) < 5 and abs(rgb[2] - 255) < 5)
                if blue > 700:
                    break
                time.sleep(0.2)
            else:
                frame.save(APP.parent / "windows-waveform-failure.png")
                raise AssertionError(f"Packaged waveform not painted, matching pixels {blue}")
            print(f"PASS: frozen EXE rendered waveform ({blue} blue pixels), with no external tools on PATH")
            frame.save(APP.parent / "windows-controller-preview.png")
            if output_hwnd:
                win32gui.ShowWindow(output_hwnd, 9)
            subprocess.run([sys.executable, __file__, "output", str(clip)], check=True, timeout=40)
        except Exception:
            def report_window(hwnd, _):
                if win32process.GetWindowThreadProcessId(hwnd)[1] == app.pid:
                    print("Application window:", ascii(win32gui.GetWindowText(hwnd)), flush=True)
            win32gui.EnumWindows(report_window, None)
            for log in (Path(test) / "SP_Show_Control" / "logs").glob("*.log"):
                print(log.name, ascii(log.read_text(encoding="utf-8", errors="replace")[-3000:]), flush=True)
            raise
        finally:
            subprocess.run(["taskkill", "/PID", str(app.pid), "/T", "/F"], capture_output=True)
            app.wait(timeout=10)
        for test_script in ("test_ppt_media.py", "test_media_previews.py", "test_download_tools.py",
                            "test_show_storage.py", "test_mpv_ipc.py"):
            subprocess.run([sys.executable, Path(__file__).with_name(test_script)], env=env, check=True)
        subprocess.run([sys.executable, Path(__file__).with_name("windows_show_rehearsal.py"), test],
                       env=env, cwd=test, check=True, timeout=120)
    print("PASS: portable GUI, FFmpeg/ffprobe/ffplay, Show Control IPC play/pause/resume and decoded mpv video")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        check_output()
    else:
        smoke()
