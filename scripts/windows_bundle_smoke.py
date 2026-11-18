"""Exercise the unpacked x64 application from a fresh profile on a Windows test desktop."""
import os
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import traceback

APP = Path(__file__).resolve().parents[1] / "dist" / "SP Show Control"


def run_stage(failures, name, action, *args, **kwargs):
    """Keep independent diagnostics running; every failure still rejects the build."""
    print(f"START Windows gate: {name}", flush=True)
    try:
        action(*args, **kwargs)
    except Exception as exc:
        failures.append(f"{name}: {type(exc).__name__}: {exc}")
        traceback.print_exc()
        return False
    print(f"PASS Windows gate: {name}", flush=True)
    return True


def check_output():
    # Run in a separate process so the parent can bound blocking Windows pipe I/O.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from mpv_ipc import MpvIpcSession

    session = MpvIpcSession(str(APP / "_internal/tools/mpv/mpv.exe"), name="BUNDLE_SMOKE_" + str(os.getpid()),
                            second_screen_left=0, second_screen_top=0, fullscreen=False)
    try:
        # Own a separate player and pipe; never load/stop media in the frozen
        # operator's OUTPUT session behind its show model's back.
        session.start()
        print("Isolated Show Control IPC connected", flush=True)
        assert session.get_property("mpv-version"), "No response on the test IPC channel"
        session.set_property("mute", True)
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
    finally:
        session.shutdown()


def smoke():
    from xml.etree import ElementTree
    import win32api
    import win32gui
    import win32process

    failures = []
    exe = APP / "SP Show Control.exe"
    dnd = APP / "_internal/tkinterdnd2/tkdnd"
    assert sorted(path.name for path in dnd.iterdir()) == ["win-x64"]
    assert (dnd / "win-x64/pkgIndex.tcl").is_file()
    assert list((dnd / "win-x64").glob("*.dll"))
    print("PASS: portable bundle includes only the Windows x64 drag-and-drop runtime")
    resources = win32api.LoadLibraryEx(str(exe), 0, 2)  # LOAD_LIBRARY_AS_DATAFILE
    try:
        manifest = ElementTree.fromstring(win32api.LoadResource(resources, 24, 1))  # RT_MANIFEST
        assert manifest.findtext(".//{*}dpiAwareness") == "PerMonitorV2"
    finally:
        win32api.FreeLibrary(resources)
    print("PASS: packaged controller requests per-monitor DPI rendering at startup")

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
            "cues_b": [{"id": "still", "kind": "image", "path": str(APP / "_internal/assets/logo.png")}],
            "scenes": [{"id": "rehearsal", "name": "Rehearsal",
                        "cue_ids_a": ["waveform"], "cue_ids_b": ["still"]}],
        }), encoding="utf-8")
        app = subprocess.Popen([exe], env=env, cwd=test)

        def report_application():
            def report_window(hwnd, _):
                if win32process.GetWindowThreadProcessId(hwnd)[1] == app.pid:
                    print("Application window:", ascii(win32gui.GetWindowText(hwnd)), flush=True)
            win32gui.EnumWindows(report_window, None)
            for log in (Path(test) / "SP_Show_Control" / "logs").glob("*.log"):
                print(log.name, ascii(log.read_text(encoding="utf-8", errors="replace")[-3000:]), flush=True)

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
            assert awareness(hwnd_context(hwnd)) == 2
            import win32con
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline:
                window_style = win32gui.GetWindowLong(hwnd, win32con.GWL_STYLE)
                if not window_style & win32con.WS_CAPTION:
                    break
                time.sleep(0.1)
            else:
                raise AssertionError(f"Integrated toolbar did not replace the caption: {window_style:#x}")
            import win32api
            work = win32api.GetMonitorInfo(win32api.MonitorFromWindow(hwnd, 2))["Work"]
            bounds = win32gui.GetWindowRect(hwnd)
            assert bounds[1] >= work[1] and bounds[3] <= work[3], (bounds, work)
            print("PASS: frozen EXE has the integrated Qt Quick titlebar")
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
                lane = frame.convert("RGB").crop((20, 67, round(frame.width * 0.23), round(frame.height * 0.28)))
                wave_pixels = sum(count for count, (red, green, blue) in lane.getcolors(lane.width * lane.height)
                                  if red > 170 and green > 136 and 60 < blue < 175)
                if wave_pixels > 400:
                    break
                time.sleep(0.2)
            else:
                frame.save(APP.parent / "windows-waveform-failure.png")
                raise AssertionError(f"Packaged waveform not painted, matching pixels {wave_pixels}")
            print(f"PASS: frozen EXE rendered waveform ({wave_pixels} frequency-colored pixels), with no external tools on PATH")
            frame.save(APP.parent / "windows-controller-preview.png")
            if output_hwnd:
                win32gui.ShowWindow(output_hwnd, 9)
            run_stage(failures, "isolated player IPC", subprocess.run,
                      [sys.executable, str(Path(__file__).resolve()), "output", str(clip)],
                      env=env, cwd=test, check=True, timeout=40)
            def rehearse_dialogs():
                from windows_dialog_rehearsal import exercise_dialogs
                exercise_dialogs(app, hwnd, Path(test), env)

            if not run_stage(failures, "frozen dialogs and show lifecycle", rehearse_dialogs):
                report_application()
        except Exception:
            report_application()
            raise
        finally:
            subprocess.run(["taskkill", "/PID", str(app.pid), "/T", "/F"], capture_output=True)
            app.wait(timeout=10)
        run_stage(failures, "spectral waveform", subprocess.run,
                  [sys.executable, Path(__file__).with_name("test_spectral_waveform.py"), media / "ffmpeg.exe"],
                  env=env, cwd=test, check=True, timeout=20)
        # The frozen Save/Open/relaunch test intentionally remembers a show.
        # Source rehearsals own different fixtures and must not inherit it.
        env = dict(env, APPDATA=str(Path(test) / "source-rehearsal-profile"))
        run_stage(failures, "Qt playback and responsiveness", subprocess.run,
                  [sys.executable, Path(__file__).with_name("windows_quick_rehearsal.py"), test],
                  env=env, cwd=test, check=True, timeout=100)
        for test_script in ("test_ppt_media.py", "test_media_previews.py", "test_download_tools.py",
                            "test_show_storage.py", "test_mpv_ipc.py", "test_playback_state.py",
                            "test_operator_transport.py", "test_operator_dialogs.py", "test_operator_tool_focus.py", "test_operator_qml.py",
                            "test_operator_models.py", "test_tool_layout.py", "test_next_selection.py",
                            "test_audio_routing.py"):
            run_stage(failures, test_script, subprocess.run,
                      [sys.executable, Path(__file__).with_name(test_script)], env=env, check=True, timeout=30)
        run_stage(failures, "real PCM audio meter", subprocess.run,
                  [sys.executable, Path(__file__).with_name("test_audio_meter.py")],
                  env=env, check=True, timeout=90)
        run_stage(failures, "Tk media and routing rehearsal", subprocess.run,
                  [sys.executable, Path(__file__).with_name("windows_show_rehearsal.py"), test],
                  env=env, cwd=test, check=True, timeout=120)
    if failures:
        raise AssertionError("Windows gates failed:\n" + "\n".join(failures))
    print("PASS: portable GUI, FFmpeg/ffprobe/ffplay, Show Control IPC play/pause/resume and decoded mpv video")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        check_output()
    else:
        smoke()
