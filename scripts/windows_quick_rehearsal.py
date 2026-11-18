"""Exercise the Qt Quick operator with a real show and a native resize series."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))

# The Windows runner has no sound device. Multiprocessing imports this test
# module in its spawned controller too, so both real mpv players get the null
# audio output even when production --no-config intentionally ignores MPV_HOME.
# Only this rehearsal's known bundled executable is affected.
_bundled_mpv = os.path.normcase(os.path.abspath(root / "dist/SP Show Control/_internal/tools/mpv/mpv.exe"))
_process_launcher = subprocess.Popen


def _rehearsal_process(args, *positional, **options):
    if (isinstance(args, (list, tuple)) and args and
            os.path.normcase(os.path.abspath(args[0])) == _bundled_mpv):
        args = [*args, "--ao=null"]
    return _process_launcher(args, *positional, **options)


subprocess.Popen = _rehearsal_process

from PySide6.QtCore import QObject, Qt, QTimer
from PySide6.QtQuick import QQuickWindow
from PySide6.QtTest import QTest
from quick_controller import run_quick


def rehearse(folder):
    tools = root / "dist/SP Show Control/_internal/tools"
    os.environ["SP_SHOW_CTRL_MPV"] = str(tools / "mpv/mpv.exe")
    os.environ["SP_SHOW_CTRL_FFMPEG_DIR"] = str(tools / "ffmpeg")
    os.environ["MPV_HOME"] = str(folder)
    (folder / "mpv.conf").write_text("ao=null\n", encoding="utf-8")
    sound = folder / "Sound.wav"
    film = folder / "árvíz teszt.mp4"
    slideshow = folder / "Rehearsal slides.pptx"
    slideshow.write_bytes(b"rehearsal prepared presentation")
    slides = slideshow.with_name(slideshow.name + ".mpv")
    slides.mkdir()
    shutil.copyfile(root / "assets/logo.png", slides / "0001.png")
    shutil.copyfile(film, slides / "0002.mp4")
    shutil.copyfile(root / "assets/logo.png", slides / "0003.png")
    (slides / "slides.json").write_text(json.dumps({"version": 1,
        "sha256": hashlib.sha256(slideshow.read_bytes()).hexdigest(),
        "slides": ["png", "mp4", "png"], "seconds": [6, None, None]}))
    signal = "(0.08+0.30*sin(3*t)^2)*sin(2*PI*120*t)*lt(mod(t,9),3)"
    signal += "+(0.04+0.36*sin(2*t)^2)*sin(2*PI*900*t)*between(mod(t,9),3,6)"
    signal += "+(0.06+0.26*sin(4*t)^2)*sin(2*PI*6300*t)*gte(mod(t,9),6)"
    subprocess.run([tools / "ffmpeg/ffmpeg.exe", "-v", "error", "-f", "lavfi", "-i",
                    f"aevalsrc='{signal}':d=35", sound], check=True, timeout=15)
    model = dict(version=3, cues_a=[
        {"id": "music", "kind": "audio", "path": str(sound)},
        {"id": "music2", "kind": "audio", "path": str(sound), "auto_play": True},
    ], cues_b=[{"id": "slides", "kind": "ppt", "path": str(slideshow)},
               {"id": "video", "kind": "video", "path": str(film)}],
       scenes=[{"id": "scene", "name": "Class 12 · Welcome and introduction",
                "cue_ids_a": ["music", "music2"], "cue_ids_b": ["slides", "video"]},
               {"id": "later", "name": "Closing and final bow", "cue_ids_a": [], "cue_ids_b": []}])
    preset = folder / "show_preset.json"
    preset.write_text(json.dumps(model), encoding="utf-8")
    result = []

    def test(qt_app, window, bridge):
        import win32api
        import win32con
        import win32gui
        assert isinstance(window, QQuickWindow)
        start = time.monotonic()
        phase = 0
        times = []
        swaps = []
        previous_live = ""
        previous_next = ""
        modal_verified = False
        saved_rows = None
        event_ticks = []
        meter_evidence = {}
        presenter = {"sent": False, "shortcut_activated": False}
        shortcut = next((item for item in window.findChildren(QObject)
                         if item.metaObject().className() == "QQuickShortcut" and
                         item.property("sequence") == "PgDown"), None)
        assert shortcut is not None, "Presenter shortcut must use Qt's valid PgDown sequence"

        def shortcut_activated():
            presenter["shortcut_activated"] = True

        shortcut.activated.connect(shortcut_activated)
        window.frameSwapped.connect(lambda: swaps.append(time.perf_counter()))
        resize_timer = QTimer(window)
        resize_timer.setTimerType(Qt.TimerType.PreciseTimer)
        resize_timer.setInterval(16)

        def poll():
            nonlocal phase, modal_verified, saved_rows
            try:
                if time.monotonic() - start > 45:
                    raise RuntimeError(f"Timed out in Qt rehearsal phase {phase}, {bridge.current!r}")
                state = bridge.current
                if presenter["sent"] and not presenter["shortcut_activated"] and time.monotonic() - presenter["sent_at"] > .5:
                    raise AssertionError(f"PageDown reached an active operator but did not activate its Qt shortcut: {presenter}")
                meter = state.get("vu", {})
                audible_a = meter.get("source") == "audio" and meter.get("available") and any(
                    channel["level"] > .01 for channel in meter.get("channels", []))
                if phase == 0 and bridge.bars and len(bridge.rows.get("audio", [])) == 2:
                    assert state.get("audioMix") is False, "A fresh show must use exclusive audio routing"
                    assert bridge.rows["audio"][1]["armed"] and bridge.rows["scenes"][1]["role"] == "next"
                    bridge.press("a_play", None)
                    bridge.press("b_select", 0)
                    bridge.press("b_play", None)
                    phase = 1
                if phase == 1 and audible_a and bridge.revisions.get("live") and bridge.revisions.get("next") and state.get("cursor", -1) > 0:
                    if not modal_verified:
                        saved_rows = {key: list(rows) for key, rows in bridge.rows.items()}
                        bridge.press("new", None)
                        phase = 10
                        QTimer.singleShot(50, poll)
                        return
                    assert state.get("projectionClock", {}).get("label") == "● Live · Manual"
                    assert state["time"].startswith("−") and state["slidePosition"] == "01 / 03"
                    assert bridge.rows["projection"][1]["role"] == "next"
                    phase = 2
                    fullscreen = win32gui.FindWindow(None, "SP Show Control Output (OUTPUT)")
                    if fullscreen:
                        win32gui.ShowWindow(fullscreen, 6)
                    window.requestActivate()
                    cursor_before = state["cursor"]
                    tick = time.perf_counter()
                    resize_frame = 0

                    def resize():
                        nonlocal resize_frame
                        try:
                            if resize_frame == 75:
                                resize_timer.stop()
                                ordered = sorted(times)
                                p95 = ordered[71]
                                elapsed = time.perf_counter() - tick
                                frame_count = sum(timestamp >= tick for timestamp in swaps)
                                print(f"Qt Quick resize: p95 {p95:.2f} ms application work, "
                                      f"{frame_count} swaps in {elapsed:.2f}s ({frame_count / elapsed:.1f}/s), "
                                      f"backend {window.rendererInterface().graphicsApi()}", flush=True)
                                assert p95 < 16.667, p95
                                gaps = [(b - a) * 1000 for a, b in zip(event_ticks, event_ticks[1:])]
                                # Measure event delivery as well as the cheap resize() call.
                                # A virtual GPU's swap rate is reported, not a laptop guarantee.
                                assert max(gaps) < 750, gaps
                                measured = {"environment": "virtual Windows desktop, source Qt operator with bundled mpv",
                                            "resize_work_p95_ms": p95, "qt_event_gap_max_ms": max(gaps),
                                            "qt_event_gap_p95_ms": sorted(gaps)[int(len(gaps) * .95)],
                                            "swaps": frame_count, "seconds": elapsed,
                                            "swaps_per_second": frame_count / elapsed,
                                            "audio_clock_advanced": bridge.current.get("cursor", 0) > cursor_before}
                                (root / "dist/windows-qt-responsiveness.json").write_text(json.dumps(measured, indent=2))
                                assert bridge.current.get("cursor", 0) > cursor_before
                                window.resize(980, 620)
                                QTimer.singleShot(150, capture)
                                return
                            started = time.perf_counter()
                            event_ticks.append(started)
                            step = min(resize_frame, 75 - resize_frame)
                            window.resize(910 + step * 3, 555 + step * 2)
                            times.append((time.perf_counter() - started) * 1000)
                            resize_frame += 1
                        except Exception as exc:
                            resize_timer.stop()
                            result.append(exc)
                            qt_app.quit()

                    def capture():
                        nonlocal previous_live, previous_next
                        try:
                            assert window.grabWindow().save(str(root / "dist/windows-qt-operator-preview.png"))
                            previous_live = bridge.revisions.get("live")
                            previous_next = bridge.revisions.get("next")
                            if fullscreen:
                                win32gui.ShowWindow(fullscreen, 9)
                            presenter["foreground_before"] = win32gui.GetForegroundWindow()
                            presenter["qt_active_before"] = window.isActive()
                            hwnd = int(window.winId())
                            win32api.keybd_event(win32con.VK_MENU, 0, 0, 0)
                            win32api.keybd_event(win32con.VK_MENU, 0, win32con.KEYEVENTF_KEYUP, 0)
                            win32gui.SetForegroundWindow(hwnd)
                            window.requestActivate()
                            deadline = time.monotonic() + 3

                            def send_when_active():
                                try:
                                    foreground = win32gui.GetForegroundWindow()
                                    if foreground != hwnd or not window.isActive():
                                        if time.monotonic() >= deadline:
                                            raise AssertionError(f"Operator did not receive focus before PageDown: HWND {foreground}/{hwnd}, Qt active={window.isActive()}")
                                        QTimer.singleShot(16, send_when_active)
                                        return
                                    assert shortcut.property("enabled") and bridge.current.get("slides")
                                    presenter.update(foreground_at_key=foreground, operator_hwnd=hwnd,
                                                     qt_active_at_key=window.isActive(), sent=True,
                                                     sent_at=time.monotonic())
                                    # Native focus and Qt activation are both established.
                                    # Exercise the real QML shortcut, never the model handler.
                                    QTest.keyClick(window, Qt.Key.Key_PageDown)
                                    (root / "dist/windows-qt-presenter-input.json").write_text(json.dumps(presenter, indent=2))
                                except Exception as exc:
                                    result.append(exc)
                                    qt_app.quit()

                            QTimer.singleShot(0, send_when_active)
                        except Exception as exc:
                            result.append(exc)
                            qt_app.quit()

                    resize_timer.timeout.connect(resize)
                    resize_timer.start()
                if phase == 10 and state.get("dialog"):
                    dialog = window.findChild(QObject, "operatorMessageDialog")
                    assert dialog and dialog.property("visible") and dialog.property("modal")
                    assert state["dialog"]["title"] == "New show"
                    assert window.grabWindow().save(str(root / "dist/windows-qt-new-show-active-playback.png"))
                    window.requestActivate()
                    QTest.keyClick(window, Qt.Key.Key_Escape)
                    phase = 11
                elif phase == 11 and not state.get("dialog"):
                    assert {key: [row["name"] for row in rows] for key, rows in bridge.rows.items()} == {
                        key: [row["name"] for row in rows] for key, rows in saved_rows.items()}
                    assert state.get("cursor", -1) > 0 and state.get("slides")
                    modal_verified = True
                    phase = 1
                fraction = state.get("projectionClock", {}).get("fraction", -1)
                if phase == 2 and state.get("slidePosition") == "02 / 03" and 0.1 < fraction < 1:
                    assert state["projectionClock"]["label"].startswith("● ")
                    live, following = bridge.revisions.get("live"), bridge.revisions.get("next")
                    if live and following and live != previous_live and following != previous_next:
                        assert audible_a and meter.get("active") and meter.get("label") == "Audio"
                        meter_evidence["active_audio_with_silent_presentation"] = meter
                        assert window.grabWindow().save(str(root / "dist/windows-qt-projection-timing.png"))
                        bridge.press("a_play", None)
                        phase = 20
                if phase == 20 and not meter.get("active"):
                    assert all(channel["level"] == 0 for channel in meter.get("channels", []))
                    meter_evidence["paused_audio"] = meter
                    bridge.press("a_play", None)
                    phase = 21
                elif phase == 21 and audible_a and meter.get("active"):
                    meter_evidence["resumed_audio"] = meter
                    (root / "dist/windows-qt-vu-verification.json").write_text(json.dumps(meter_evidence, indent=2))
                    print("PASS: one active Audio VU with silent video, pause zero and manual resume signal", flush=True)
                    bridge.connection.close()
                    phase = 3
                if phase == 3 and state.get("controllerError"):
                    assert "Logs:" in state["controllerError"]
                    assert window.isVisible()
                    window.resize(1000, 640)
                    (root / "dist/windows-qt-controller-failure.json").write_text(json.dumps({
                        "trigger": "deliberate operator transport disconnect after playback checks",
                        "visible_error": state["controllerError"], "operator_remained_visible": True,
                        "limitation": "This exercises disconnect, not a physically stalled audio driver or laptop."}, indent=2))
                    phase = 4
                elif phase == 4:
                    assert window.width() == 1000 and window.height() == 640
                    assert window.grabWindow().save(str(root / "dist/windows-qt-controller-failure.png"))
                    print("PASS: disconnected controller left the operator responsive with its log path visible", flush=True)
                    if not result:
                        result.append(True)
                        qt_app.quit()
                        return
            except Exception as exc:
                result.append(exc)
                qt_app.quit()
                return
            QTimer.singleShot(50, poll)

        poll()

    run_quick(test)
    assert result == [True], result
    print("PASS: Qt Quick drives real audio, slide cues, frequency waveform, countdown and the header projection timer")


if __name__ == "__main__":
    from multiprocessing import freeze_support
    freeze_support()
    rehearse(Path(sys.argv[1]))
