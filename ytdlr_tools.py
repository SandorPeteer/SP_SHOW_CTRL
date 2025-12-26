from __future__ import annotations

import os
import platform
import shutil
import ssl
import stat
import subprocess
import tempfile
from pathlib import Path
from urllib import request as urlrequest
from urllib.error import URLError
import sys


def tools_root() -> Path:
    env = ""
    try:
        env = str(os.environ.get("YTDLR_TOOLS_DIR") or "").strip()
    except Exception:
        env = ""
    if env:
        return Path(env).expanduser()

    sysname = platform.system()
    if sysname == "Darwin":
        base = Path.home() / "Library" / "Application Support" / "yt-dlr"
    elif sysname == "Windows":
        base = Path(os.environ.get("APPDATA") or (Path.home() / "AppData" / "Roaming")) / "yt-dlr"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share")) / "yt-dlr"
    return base / "tools"


def repo_tools_root() -> Path:
    return Path(__file__).resolve().parent / "tools"


def ytdlp_dir() -> Path:
    return tools_root() / "ytdlp"


def ytdlp_exe_name() -> str:
    return "yt-dlp.exe" if platform.system() == "Windows" else "yt-dlp"


def local_ytdlp_path() -> Path:
    return ytdlp_dir() / ytdlp_exe_name()


def repo_ytdlp_path() -> Path:
    return (repo_tools_root() / "ytdlp") / ytdlp_exe_name()


def _ensure_executable(p: Path) -> None:
    try:
        mode = p.stat().st_mode
        p.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    except Exception:
        try:
            p.chmod(0o755)
        except Exception:
            pass


def _best_ytdlp_url() -> str:
    sysname = platform.system()
    if sysname == "Windows":
        return "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe"
    if sysname == "Darwin":
        return "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp_macos"
    return "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp"


def download_latest_ytdlp(*, progress_cb=None) -> Path:
    """
    Download latest yt-dlp standalone binary into the per-user app tools dir.
    progress_cb(done:int, total:int|None) is optional.
    """
    url = _best_ytdlp_url()
    dest = local_ytdlp_path()
    dest.parent.mkdir(parents=True, exist_ok=True)

    def _is_ssl_verify_error(e: BaseException) -> bool:
        if isinstance(e, ssl.SSLError):
            return True
        if isinstance(e, URLError):
            reason = getattr(e, "reason", None)
            return isinstance(reason, ssl.SSLError)
        return False

    def _open(req: urlrequest.Request):
        # 1) System defaults
        try:
            return urlrequest.urlopen(req, timeout=30)
        except Exception as e:
            if not _is_ssl_verify_error(e):
                raise

        # 2) certifi, if available (common fix for macOS Python SSL chain issues)
        try:
            import certifi  # type: ignore

            cafile = str(certifi.where() or "")
            if cafile:
                ctx = ssl.create_default_context(cafile=cafile)
                return urlrequest.urlopen(req, timeout=30, context=ctx)
        except Exception:
            pass
        raise ssl.SSLError(
            "SSL verification failed (missing local issuer). Fix options: "
            "1) `python3 -m pip install certifi`, "
            "2) macOS python.org: run 'Install Certificates.command', "
            "3) ensure system certificates are installed."
        )

    def _download_with_curl(url: str, out_path: Path) -> None:
        curl = shutil.which("curl")
        if not curl:
            raise FileNotFoundError("curl not found")
        cmd = [curl, "-L", "--fail", "--silent", "--show-error", "-o", str(out_path), url]
        subprocess.run(cmd, check=True)

    with tempfile.TemporaryDirectory(prefix="ytdlr_ytdlp_") as tmp:
        tmp_path = Path(tmp) / (dest.name + ".download")
        req = urlrequest.Request(url, headers={"User-Agent": "yt-dlr/1.0"})
        try:
            with _open(req) as resp:
                total = None
                try:
                    total = int(resp.headers.get("Content-Length") or 0) or None
                except Exception:
                    total = None
                done = 0
                with open(tmp_path, "wb") as f:
                    while True:
                        chunk = resp.read(256 * 1024)
                        if not chunk:
                            break
                        f.write(chunk)
                        done += len(chunk)
                        if progress_cb is not None:
                            try:
                                progress_cb(done, total)
                            except Exception:
                                pass
        except Exception as e:
            # Last-resort: system curl often succeeds even when Python SSL is misconfigured.
            if _is_ssl_verify_error(e):
                _download_with_curl(url, tmp_path)
            else:
                raise

        try:
            shutil.move(str(tmp_path), str(dest))
        except Exception:
            shutil.copy2(str(tmp_path), str(dest))
        _ensure_executable(dest)

        if platform.system() == "Darwin":
            try:
                subprocess.run(
                    ["xattr", "-dr", "com.apple.quarantine", str(dest)],
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except Exception:
                pass

    return dest
