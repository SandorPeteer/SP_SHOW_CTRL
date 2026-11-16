"""Exercise the unpacked x64 application from a fresh profile on a Windows test desktop."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

APP = Path(__file__).resolve().parents[1] / "dist" / "SP Show Control"
PIPE = r"\\.\pipe\sp_show_control_mpv_OUTPUT"


def check_output():
    # Run in a separate process so the parent can bound blocking Windows pipe I/O.
    deadline = time.monotonic() + 20
    while True:
        try:
            pipe = open(PIPE, "r+b", buffering=0)
            break
        except OSError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.2)
    with pipe:
        def command(request_id, *args):
            payload = {"command": list(args), "request_id": request_id}
            pipe.write((json.dumps(payload) + "\n").encode())
            while True:
                reply = json.loads(pipe.readline())
                if reply.get("request_id") == request_id:
                    if reply["error"] == "property unavailable" and args == ("get_property", "video-params"):
                        return None
                    assert reply["error"] == "success", reply
                    return reply.get("data")

        assert command(1, "get_property", "mpv-version")
        command(2, "loadfile", sys.argv[2], "replace")
        for request_id in range(3, 103):
            if command(request_id, "get_property", "video-params") is not None:
                break
            time.sleep(0.1)
        else:
            raise RuntimeError("Timed out decoding test video")


def smoke():
    import win32gui
    import win32process

    with tempfile.TemporaryDirectory(prefix="Show Control próba ") as test:
        env = {k: v for k, v in os.environ.items() if not k.startswith("SP_SHOW_CTRL_")}
        env["APPDATA"] = test
        env["PATH"] = str(Path(env["SystemRoot"]) / "System32")
        clip = Path(test) / "árvíz teszt.mp4"
        media = APP / "_internal" / "tools" / "ffmpeg"
        subprocess.run([media / "ffmpeg.exe", "-v", "error", "-f", "lavfi", "-i",
                        "color=size=64x64:rate=5", "-t", "2", "-c:v", "mpeg4", clip],
                       check=True, cwd=test, env=env, timeout=20)
        probe = subprocess.check_output([media / "ffprobe.exe", "-v", "error", "-show_entries",
                                         "stream=width", "-of", "csv=p=0", clip], env=env, timeout=10)
        assert probe.strip() == b"64", probe
        subprocess.run([media / "ffplay.exe", "-version"], stdout=subprocess.DEVNULL,
                       check=True, env=env, timeout=10)

        app = subprocess.Popen([APP / "SP Show Control.exe"], env=env, cwd=test)
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
            subprocess.run([sys.executable, __file__, "output", str(clip)], check=True, timeout=40)
        finally:
            subprocess.run(["taskkill", "/PID", str(app.pid), "/T", "/F"], capture_output=True)
            app.wait(timeout=10)
    print("PASS: portable GUI, FFmpeg/ffprobe/ffplay, named pipe and decoded mpv video")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        check_output()
    else:
        smoke()
