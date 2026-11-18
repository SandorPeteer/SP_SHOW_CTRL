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

from PySide6.QtCore import QObject, QPointF, Qt, QTimer
from PySide6.QtQuick import QQuickWindow
from PySide6.QtTest import QTest
from quick_controller import run_quick


def visual_items(item):
    """Include QML delegates, which need not be in the QObject child tree."""
    yield item
    for child in item.childItems():
        yield from visual_items(child)


def click_item(window, item, *, fraction=None, right=False):
    assert item.isVisible() and item.isEnabled(), item.objectName()
    x = item.width() / 2
    if fraction is not None:
        handle = item.property("handle")
        x = item.property("leftPadding") + handle.width() / 2 + fraction * (item.property("availableWidth") - handle.width())
    point = item.mapToScene(QPointF(x, item.height() / 2)).toPoint()
    assert 0 <= point.x() < window.width() and 0 <= point.y() < window.height(), point
    QTest.mouseClick(window, Qt.MouseButton.RightButton if right else Qt.MouseButton.LeftButton,
                     Qt.KeyboardModifier.NoModifier, point)


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
    observers = []

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
        routing_evidence = {"input": "real QTest clicks on Qt controls; engine clocks and RMS/peak snapshots",
                            "audio_output": "real bundled mpv with test-only ao=null", "actions": [],
                            "transport_states": {}}
        checkpoint = {}
        seek_check = {}
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

        def click_control(*, name=None, command=None, row=None, fraction=None, right=False):
            hwnd = int(window.winId())
            if win32gui.GetForegroundWindow() != hwnd or not window.isActive():
                win32api.keybd_event(win32con.VK_MENU, 0, 0, 0)
                win32api.keybd_event(win32con.VK_MENU, 0, win32con.KEYEVENTF_KEYUP, 0)
                win32gui.SetForegroundWindow(hwnd)
                window.requestActivate()
                return False  # Wait for native AND Qt activation before injecting input.
            items = visual_items(window.contentItem())
            if row is not None:
                listing = next(item for item in items if item.objectName() == "cueList_b")
                item = next(item for item in visual_items(listing) if item.property("index") == row)
            else:
                matches = [item for item in items if item.isVisible() and
                           ((name is not None and item.objectName() == name) or
                            (command is not None and item.property("command") == command))]
                assert len(matches) == 1, (name, command, len(matches))
                item = matches[0]
            click_item(window, item, fraction=fraction, right=right)
            routing_evidence["actions"].append({"phase": phase, "control": name or command or f"B row {row}",
                                                "foreground": hwnd, "qt_active": window.isActive(),
                                                "fraction": fraction, "right_click": right})
            return True

        def has_signal(value):
            return value.get("active") and value.get("available") and any(
                channel["level"] > .01 for channel in value.get("channels", []))

        def is_silent(value):
            return not value.get("active") and all(channel["level"] == 0 for channel in value.get("channels", []))

        def check_transport(state, audio, projection, stage):
            assert state.get("audioPlaying") is audio and state.get("projectionPlaying") is projection, (
                stage, state.get("audioPlaying"), state.get("projectionPlaying"))
            buttons = {item.objectName(): item for item in visual_items(window.contentItem())
                       if item.objectName() in ("playA", "showB")}
            assert set(buttons) == {"playA", "showB"}, buttons
            routing_evidence["transport_states"][stage] = {
                "audioPlaying": audio, "projectionPlaying": projection,
                "button_colors": {name: item.property("base").name() for name, item in buttons.items()}}

        def mark_clock(state):
            timeline = state["projectionTimeline"]
            assert timeline["available"] and state["projectionPlaying"]
            checkpoint.update(at=time.monotonic(), a=state["cursor"],
                              b=timeline["fraction"], b_position=timeline["position"],
                              b_start=timeline["start"], b_end=timeline["end"],
                              b_cue=timeline["cueId"], b_path=timeline["path"])

        def clocks_advanced(state, *, audio):
            timeline = state["projectionTimeline"]
            assert timeline["available"] and state["projectionPlaying"], timeline
            assert timeline["cueId"] == checkpoint["b_cue"] and timeline["path"] == checkpoint["b_path"]
            assert timeline["start"] == checkpoint["b_start"] and timeline["end"] == checkpoint["b_end"]
            current = timeline["fraction"]
            # The existing four-second fixture repeats. Accept crossing its end.
            assert 0 <= checkpoint["b"] < 1 and 0 <= current < 1, (checkpoint, current)
            duration = timeline["end"] - timeline["start"]
            advanced = (timeline["position"] - checkpoint["b_position"]) % duration
            assert duration * .02 < advanced < duration * .8, (checkpoint, timeline)
            if audio:
                assert state["cursor"] > checkpoint["a"] + .005, (checkpoint, state["cursor"])
            else:
                assert abs(state["cursor"] - checkpoint["a"]) < .002, (checkpoint, state["cursor"])

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
                    check_transport(state, False, False, "idle")
                    assert state.get("audioMix") is False, "A fresh show must use exclusive audio routing"
                    assert bridge.rows["audio"][1]["armed"] and bridge.rows["scenes"][1]["role"] == "next"
                    bridge.press("a_play", None)
                    bridge.press("b_select", 0)
                    bridge.press("b_play", None)
                    phase = 1
                if phase == 1 and audible_a and bridge.revisions.get("live") and bridge.revisions.get("next") and state.get("cursor", -1) > 0:
                    check_transport(state, True, True, "audio_and_shown_slide")
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
                    check_transport(state, False, True, "audio_paused_projection_running")
                    meter_evidence["paused_audio"] = meter
                    bridge.press("a_play", None)
                    phase = 21
                elif phase == 21 and audible_a and meter.get("active"):
                    check_transport(state, True, True, "audio_resumed_projection_running")
                    meter_evidence["resumed_audio"] = meter
                    (root / "dist/windows-qt-vu-verification.json").write_text(json.dumps(meter_evidence, indent=2))
                    print("PASS: one active Audio VU with silent video, pause zero and manual resume signal", flush=True)
                    assert state.get("projectionSound") is False
                    phase = 22
                direct_timeline = state.get("projectionTimeline", {})
                direct_fraction = direct_timeline.get("fraction", -1) if direct_timeline.get("available") else -1
                if phase == 22 and click_control(row=1):
                    phase = 23
                elif phase == 23 and bridge.rows["projection"][1]["selected"]:
                    if click_control(command="b_play"):
                        phase = 24
                elif phase == 24 and not state.get("slides") and bridge.rows["projection"][1]["role"] == "playing" and 0 < direct_fraction < 1:
                    check_transport(state, True, True, "direct_show_with_muted_video")
                    assert state.get("projectionSound") is False and audible_a
                    assert not state.get("repeatB")
                    if click_control(command="b_repeat"):
                        phase = 25
                elif phase == 25 and state.get("repeatB"):
                    if click_control(name="projectionSoundButton"):
                        phase = 26
                elif phase == 26 and state.get("projectionSound") and meter.get("source") == "presentation" and has_signal(meter) and state.get("play") == "Resume" and 0 <= direct_fraction < 1:
                    check_transport(state, False, True, "single_video_takeover")
                    mark_clock(state)
                    routing_evidence["single_b_takeover"] = {"meter": meter, **checkpoint}
                    phase = 27
                elif phase == 27 and time.monotonic() - checkpoint["at"] >= .5 and 0 <= direct_fraction < 1:
                    clocks_advanced(state, audio=False)
                    assert state.get("play") == "Resume" and has_signal(meter)
                    if click_control(name="mixAudioButton"):
                        phase = 28
                elif phase == 28 and state.get("audioMix") and meter.get("mixed") and 0 <= direct_fraction < 1:
                    a, b = meter["decks"]
                    assert a["name"] == "A" and b["name"] == "B"
                    assert is_silent(a) and has_signal(b) and state.get("play") == "Resume"
                    mark_clock(state)
                    phase = 29
                elif phase == 29 and time.monotonic() - checkpoint["at"] >= .5 and 0 <= direct_fraction < 1:
                    a, b = meter["decks"]
                    assert is_silent(a) and has_signal(b) and state.get("play") == "Resume"
                    clocks_advanced(state, audio=False)
                    routing_evidence["mix_on_did_not_resume_audio"] = {"meter": meter, "cursor": state["cursor"]}
                    if click_control(command="a_play"):
                        phase = 30
                elif phase == 30 and meter.get("mixed") and all(has_signal(value) for value in meter["decks"]) and 0 <= direct_fraction < 1:
                    check_transport(state, True, True, "mix_both_playing")
                    assert state.get("play") == "Pause" and state.get("projectionSound")
                    mark_clock(state)
                    routing_evidence["mix_both_real_signals"] = {"meter": meter, **checkpoint}
                    phase = 31
                elif phase == 31 and time.monotonic() - checkpoint["at"] >= .5 and 0 <= direct_fraction < 1:
                    assert all(has_signal(value) for value in meter["decks"])
                    clocks_advanced(state, audio=True)
                    routing_evidence["mix_both_clocks_advanced"] = {"a": state["cursor"], "b": direct_fraction}
                    assert window.grabWindow().save(str(root / "dist/windows-qt-direct-video-mix.png"))
                    from mpv_ipc import MpvIpcSession
                    observer = MpvIpcSession(str(tools / "mpv/mpv.exe"), name="OUTPUT",
                                             second_screen_left=0, second_screen_top=0, fullscreen=False)
                    observers.append(observer)
                    observer._connect_ipc()  # Read-only; the real QML gesture must issue the seek.
                    seek_check["observer"] = observer
                    phase = 37
                elif phase == 37 and state.get("projectionTimeline", {}).get("available"):
                    observer = seek_check["observer"]
                    before = {key: observer.get_property(key) for key in ("path", "duration", "time-pos", "mute", "volume", "pause")}
                    assert Path(before["path"]).resolve() == film.resolve(), before
                    assert before["mute"] is False and before["pause"] is False
                    assert isinstance(before["volume"], (int, float)) and before["volume"] > 0
                    target = .75 if before["time-pos"] / before["duration"] < .5 else .25
                    if click_control(name="projectionSeek", fraction=target):
                        seek_check.update(before=before, target=target, clicked=time.monotonic(), a=state["cursor"],
                                          timeline_before=state["projectionTimeline"])
                        phase = 38
                elif phase == 38:
                    elapsed = time.monotonic() - seek_check["clicked"]
                    assert elapsed < 1, ("Qt seek did not reach the real engine within one second", seek_check["before"], state.get("projectionTimeline"))
                    observer = seek_check["observer"]
                    position = observer.get_property("time-pos")
                    duration = seek_check["before"]["duration"]
                    expected = (seek_check["target"] * duration + elapsed) % duration
                    unseeked = (seek_check["before"]["time-pos"] + elapsed) % duration
                    distance = lambda a, b: min(abs(a - b), duration - abs(a - b))
                    timeline = state.get("projectionTimeline", {})
                    if (elapsed >= .2 and isinstance(position, (int, float)) and
                            distance(position, expected) < .45 and distance(position, unseeked) > .65 and
                            timeline.get("available") and distance(timeline["position"], position) < .3 and
                            state["cursor"] > seek_check["a"] + .002):
                        after = {key: observer.get_property(key) for key in ("path", "mute", "volume", "pause")}
                        assert after == {key: seek_check["before"][key] for key in after}, (seek_check["before"], after)
                        assert state["audioMix"] and state["projectionSound"] and state["play"] == "Pause"
                        check_transport(state, True, True, "mix_after_video_seek")
                        assert timeline["cueId"] == seek_check["timeline_before"]["cueId"] and timeline["path"] == seek_check["timeline_before"]["path"]
                        routing_evidence["real_projection_seek"] = {
                            "target_fraction": seek_check["target"], "before": seek_check["before"],
                            "after": dict(after, position=position), "response_seconds": elapsed,
                            "position_without_seek": unseeked, "timeline": timeline,
                            "audio_cursor_before": seek_check["a"], "audio_cursor_after": state["cursor"],
                            "audio_mix_unchanged": True, "projection_sound_unchanged": True}
                        assert window.grabWindow().save(str(root / "dist/windows-qt-projection-seek.png"))
                        phase = 39
                elif phase == 39 and meter.get("mixed") and all(has_signal(value) for value in meter["decks"]):
                    if click_control(name="projectionSoundButton"):
                        phase = 32
                elif phase == 32 and state.get("projectionSound") is False and meter.get("mixed") and is_silent(meter["decks"][1]) and 0 <= direct_fraction < 1:
                    check_transport(state, True, True, "muted_video_still_playing")
                    assert has_signal(meter["decks"][0])
                    mark_clock(state)
                    phase = 33
                elif phase == 33 and time.monotonic() - checkpoint["at"] >= .5 and 0 <= direct_fraction < 1:
                    assert has_signal(meter["decks"][0]) and is_silent(meter["decks"][1])
                    assert bridge.rows["projection"][1]["role"] == "playing"
                    clocks_advanced(state, audio=True)
                    routing_evidence["sound_off_video_kept_running"] = {"meter": meter, "a": state["cursor"], "b": direct_fraction}
                    if click_control(name="projectionSoundButton"):
                        phase = 34
                elif phase == 34 and state.get("projectionSound") and meter.get("mixed") and all(has_signal(value) for value in meter["decks"]):
                    # This explicit Sound on makes B the last requested source.
                    if click_control(name="mixAudioButton"):
                        phase = 35
                elif phase == 35 and state.get("audioMix") is False and meter.get("source") == "presentation" and has_signal(meter) and state.get("play") == "Resume" and 0 <= direct_fraction < 1:
                    check_transport(state, False, True, "mix_off_video_remains_playing")
                    mark_clock(state)
                    phase = 36
                elif phase == 36 and time.monotonic() - checkpoint["at"] >= .5 and 0 <= direct_fraction < 1:
                    assert state.get("projectionSound") and has_signal(meter) and state.get("play") == "Resume"
                    clocks_advanced(state, audio=False)
                    routing_evidence["mix_off_kept_last_explicit_b"] = {"meter": meter, "a": state["cursor"], "b": direct_fraction}
                    assert window.grabWindow().save(str(root / "dist/windows-qt-direct-video-single.png"))
                    (root / "dist/windows-qt-direct-video-audio.json").write_text(json.dumps(routing_evidence, indent=2))
                    print("PASS: direct video Sound on/off, exclusive handoff, explicit Mix clocks/meters and last-source Mix off", flush=True)
                    phase = 40
                elif phase == 40 and click_control(row=0, right=True):
                    phase = 41
                elif phase == 41:
                    menu = window.findChild(QObject, "projectionStillMenu")
                    if menu is not None and menu.property("visible"):
                        QTest.keyClick(window, Qt.Key.Key_Down)
                        QTest.keyClick(window, Qt.Key.Key_Down)
                        QTest.keyClick(window, Qt.Key.Key_Return)
                        phase = 42
                elif phase == 42:
                    dialog = window.findChild(QObject, "pptIntervalDialog")
                    if dialog is not None and dialog.property("visible") and click_control(name="pptIntervalInput"):
                        QTest.keyClick(window, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
                        for key in (Qt.Key.Key_7, Qt.Key.Key_Period, Qt.Key.Key_5):
                            QTest.keyClick(window, key)
                        phase = 43
                elif phase == 43 and click_control(name="pptIntervalSave"):
                    phase = 44
                elif phase == 44 and bridge.rows["projection"][0].get("intervalSeconds") == 7.5:
                    ppt_row, video_row = bridge.rows["projection"]
                    assert ppt_row["intervalCustom"] and not ppt_row["intervalActive"]
                    assert video_row["selected"] and not ppt_row["selected"]
                    check_transport(state, False, True, "ppt_interval_edit_kept_video_playing")
                    assert state["projectionSound"] and not state["audioMix"]
                    mark_clock(state)
                    phase = 45
                elif phase == 45 and time.monotonic() - checkpoint["at"] >= .5:
                    clocks_advanced(state, audio=False)
                    routing_evidence["ppt_interval_edit"] = {
                        "seconds": bridge.rows["projection"][0]["intervalSeconds"],
                        "current_video_unchanged": True, "ready_selection_unchanged": True,
                        "input": "right-click PPT row, Down twice, Enter, type 7.5, Save"}
                    (root / "dist/windows-qt-direct-video-audio.json").write_text(json.dumps(routing_evidence, indent=2))
                    assert window.grabWindow().save(str(root / "dist/windows-qt-ppt-interval.png"))
                    print("PASS: PPT interval edited through its actual Qt context menu and saved to its row without interrupting video", flush=True)
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
                routing_evidence.update(failed_phase=phase, error=repr(exc), state=bridge.current)
                (root / "dist/windows-qt-direct-video-audio.json").write_text(json.dumps(routing_evidence, indent=2))
                result.append(exc)
                qt_app.quit()
                return
            QTimer.singleShot(50, poll)

        poll()

    try:
        run_quick(test)
    finally:
        for observer in observers:
            observer.shutdown()
    assert result == [True], result
    print("PASS: Qt Quick drives real audio, slide cues, frequency waveform, countdown and the header projection timer")


if __name__ == "__main__":
    from multiprocessing import freeze_support
    freeze_support()
    rehearse(Path(sys.argv[1]))
