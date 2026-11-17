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
        assert manifest.findtext(".//{*}dpiAwareness") == "Unaware"
    finally:
        win32api.FreeLibrary(resources)
    print("PASS: packaged controller requests Windows scaling at startup")

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
            # Read pixels from the actual frozen executable with a clean PATH/profile.
            from PIL import ImageGrab
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                output_hwnd = win32gui.FindWindow(None, "SP Show Control Output (OUTPUT)")
                if output_hwnd:
                    win32gui.ShowWindow(output_hwnd, 6)
                bounds = win32gui.GetWindowRect(hwnd)
                frame = ImageGrab.grab(bbox=bounds)
                blue = sum(count for count, rgb in (frame.convert("RGB").getcolors(frame.width * frame.height) or [])
                           if abs(rgb[0] - 74) < 5 and abs(rgb[1] - 158) < 5 and abs(rgb[2] - 255) < 5)
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
        for test_script in ("test_ppt_media.py", "test_media_previews.py", "test_download_tools.py", "test_show_storage.py"):
            subprocess.run([sys.executable, Path(__file__).with_name(test_script)], env=env, check=True)
        subprocess.run([sys.executable, Path(__file__).with_name("windows_show_rehearsal.py"), test],
                       env=env, cwd=test, check=True, timeout=120)
    print("PASS: portable GUI, FFmpeg/ffprobe/ffplay, Show Control IPC play/pause/resume and decoded mpv video")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        check_output()
    else:
        smoke()
