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

from PySide6.QtCore import QObject, QTimer
from PySide6.QtQuick import QQuickWindow
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
    (slides / "slides.json").write_text(json.dumps({"version": 1,
        "sha256": hashlib.sha256(slideshow.read_bytes()).hexdigest(), "slides": ["png", "mp4"], "seconds": [6, None]}))
    subprocess.run([tools / "ffmpeg/ffmpeg.exe", "-v", "error", "-f", "lavfi", "-i",
                    "sine=frequency=120:duration=25", sound], check=True, timeout=15)
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
        import win32gui
        assert isinstance(window, QQuickWindow)
        start = time.monotonic()
        phase = 0
        times = []
        swaps = []
        window.frameSwapped.connect(lambda: swaps.append(time.perf_counter()))

        def poll():
            nonlocal phase
            try:
                if time.monotonic() - start > 45:
                    raise RuntimeError(f"Timed out in Qt rehearsal phase {phase}, {bridge.current!r}")
                state = bridge.current
                if phase == 0 and bridge.bars and len(bridge.rows.get("audio", [])) == 2:
                    assert bridge.rows["audio"][1]["armed"] and bridge.rows["scenes"][1]["role"] == "next"
                    bridge.press("a_play", None)
                    bridge.press("b_select", 0)
                    bridge.press("b_play", None)
                    phase = 1
                if phase == 1 and bridge.revisions.get("live") and bridge.revisions.get("next") and state.get("cursor", -1) > 0:
                    assert state.get("projectionClock", {}).get("label") == "● Live · Manual"
                    assert state["time"].startswith("−") and state["slidePosition"] == "01 / 02"
                    assert bridge.rows["projection"][1]["role"] == "next"
                    phase = 2
                    fullscreen = win32gui.FindWindow(None, "SP Show Control Output (OUTPUT)")
                    if fullscreen:
                        win32gui.ShowWindow(fullscreen, 6)
                    window.requestActivate()
                    cursor_before = state["cursor"]
                    tick = time.perf_counter()

                    def resize(frame):
                        try:
                            if frame == 75:
                                ordered = sorted(times)
                                p95 = ordered[71]
                                print(f"Qt Quick resize: p95 {p95:.2f} ms application work, "
                                      f"{len(swaps)} displayed frames in {time.perf_counter() - tick:.2f}s, "
                                      f"backend {window.rendererInterface().graphicsApi()}", flush=True)
                                assert p95 < 16.667, p95
                                assert bridge.current.get("cursor", 0) > cursor_before
                                window.resize(980, 620)
                                QTimer.singleShot(150, capture)
                                return
                            started = time.perf_counter()
                            step = min(frame, 75 - frame)
                            window.resize(910 + step * 3, 555 + step * 2)
                            times.append((time.perf_counter() - started) * 1000)
                            QTimer.singleShot(16, lambda: resize(frame + 1))
                        except Exception as exc:
                            result.append(exc)
                            qt_app.quit()

                    def capture():
                        try:
                            assert window.grabWindow().save(str(root / "dist/windows-qt-operator-preview.png"))
                            bridge.press("slide_next", None)
                        except Exception as exc:
                            result.append(exc)
                            qt_app.quit()

                    resize(0)
                if phase == 2 and state.get("slidePosition") == "02 / 02" and state.get("projectionClock", {}).get("fraction", -1) > 0:
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
