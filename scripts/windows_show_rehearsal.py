"""Run the production A/B transport handlers with real bundled players on a Windows CI desktop."""
import faulthandler
import json
import os
from pathlib import Path
import subprocess
import sys
import time

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))
from player import App, MpvIpcSession

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
                    "sine=frequency=220:duration=45", music], check=True)
    app = App()
    try:
        app.update()
        # Open a show saved on another machine, from outside its media directory.
        showfile = folder / "portable.json"
        showfile.write_text(json.dumps({"version": 3, "media_paths": "show-folder",
            "cues_a": [{"id": "A1", "kind": "audio", "path": "./" + music.name}],
            "cues_b": [{"id": "B1", "kind": "image", "path": str(image)},
                       {"id": "B2", "kind": "video", "path": "./" + clip.name}],
            "scenes": [{"id": "school", "name": "School", "cue_ids_a": ["A1"], "cue_ids_b": ["B1", "B2"]}]}),
            encoding="utf-8")
        app._load_show_from_path(showfile, set_show_path=True)
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
        app._transport_stop("B")
        wait_for(lambda: audio.get_property("time-pos"), lambda v: v and v > before)
        assert app.audio_runner.is_playing()
        print("PASS: Deck A music advanced throughout Deck B image/video/image/stop, visuals muted")
    finally:
        audio.shutdown()
        app._on_closing()


if __name__ == "__main__":
    faulthandler.dump_traceback_later(30)
    rehearse(Path(sys.argv[1]))
    faulthandler.cancel_dump_traceback_later()
