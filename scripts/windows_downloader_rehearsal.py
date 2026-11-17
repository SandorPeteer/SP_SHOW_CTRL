"""Exercise the updater and normal Download handler using the real packaged executables."""
import functools
import hashlib
import http.server
import json
from pathlib import Path
import shutil
import threading
import time
from unittest.mock import patch
import zipfile


def exercise_downloader(app, folder, tools):
    import player
    from download_tools import ASSETS, version

    assert version(tools / "ytdlp/yt-dlp.exe", player._no_console_subprocess_kwargs())
    assert version(tools / "deno/deno.exe", player._no_console_subprocess_kwargs()).startswith("deno ")
    old_resource = player._resource_path
    def resource(*parts):
        return tools.joinpath(*parts[1:]) if parts and parts[0] == "tools" else old_resource(*parts)

    # Serve a fixed release in the updater test, including a real runnable Deno zip.
    archive = folder / "deno.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as package:
        package.write(tools / "deno/deno.exe", "deno.exe")
    assets = {}
    for tool, (repository, name) in ASSETS.items():
        binary = archive if tool == "deno" else tools / "ytdlp/yt-dlp.exe"
        with binary.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        assets[tool] = (binary, {"name": name, "digest": "sha256:" + digest,
            "browser_download_url": f"https://github.com/{repository}/releases/download/rehearsal/{name}"})

    def download(url, path, **kwargs):
        for tool, (repository, _) in ASSETS.items():
            binary, asset = assets[tool]
            if url == f"https://api.github.com/repos/{repository}/releases/latest":
                path.write_text(json.dumps({"assets": [asset]}))
                return
            if url == asset["browser_download_url"]:
                shutil.copyfile(binary, path)
                return
        raise AssertionError(f"Unexpected external request: {url}")

    def wait():
        deadline = time.monotonic() + 45
        while app._ytdlp_thread.is_alive() and time.monotonic() < deadline:
            app.update()
            time.sleep(0.1)
        assert not app._ytdlp_thread.is_alive(), "Downloader operation timed out"
        app._drain_ui_tasks()

    with patch.object(player, "_resource_path", resource), patch.object(player, "_download_url_to_file", download):
        app._update_download_tools()
        wait()
        assert app.var_ytdlp_status.get().startswith("Frissítve"), app.var_ytdlp_status.get()
        assert Path(player._resolve_ytdlp()).is_relative_to(player._user_data_dir())
        app.tabs_b.select(app.tab_b_download)
        app.update()
        assert app.btn_ytdlp_update.winfo_ismapped()
        assert app.btn_ytdlp_update.winfo_rooty() + app.btn_ytdlp_update.winfo_height() <= app.winfo_rooty() + app.winfo_height()
        from PIL import ImageGrab
        ImageGrab.grab().save(tools.parents[2] / "windows-downloader-preview.png")

        class Handler(http.server.SimpleHTTPRequestHandler):
            def log_message(self, *_):
                pass
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Handler, directory=str(folder)))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            # An owned fixture avoids remote-site changes and downloads no third-party media.
            shutil.copyfile(folder / "árvíz teszt.mp4", folder / "download-fixture.mp4")
            destination = folder / "downloaded"
            app.var_download_dir.set(str(destination))
            app.var_ytdlp_url.set(f"http://127.0.0.1:{server.server_port}/download-fixture.mp4")
            app._start_ytdlp_download()
            wait()
            assert app.var_ytdlp_status.get() == "DONE", app.var_ytdlp_status.get()
            assert list(destination.glob("*.mp4"))
        finally:
            server.shutdown()
            server.server_close()
    print("PASS: update button installed validated yt-dlp and Deno; normal Download handler saved a real video")
