"""Download and verify the Windows x64 media binaries used by the portable build."""
import hashlib
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile

MPV_URL = "https://github.com/shinchiro/mpv-winbuild-cmake/releases/download/20261111/mpv-x86_64-20261111-git-bcde6b93cc.7z"
FFMPEG_URL = "https://github.com/GyanD/codexffmpeg/releases/download/9.0.2/ffmpeg-9.0.2-essentials_build.zip"
MPV_SHA = "f259b5fd4b5c395617c948a4fadcdf55bba1c5282fb7c13e88626b3f71072baf"
FFMPEG_SHA = "60f467265b1e312373dbcd92200c2618a74850f98d3d078e94296bb3fa2047ba"


def sha256(path):
    with path.open("rb") as src:
        return hashlib.file_digest(src, "sha256").hexdigest()


def archive(cache, name, url, digest):
    target = cache / name
    if target.is_file() and sha256(target) == digest:
        return target
    with tempfile.TemporaryDirectory(dir=cache) as tmp:
        download = Path(tmp) / name
        urllib.request.urlretrieve(url, download)
        if sha256(download) != digest:
            raise ValueError(f"Checksum mismatch for {name}")
        download.replace(target)
    return target


def prepare(cache, tools):
    cache.mkdir(exist_ok=True, parents=True)
    mpv = tools / "mpv"
    ffmpeg = tools / "ffmpeg"
    for directory in (mpv, ffmpeg):
        directory.mkdir(exist_ok=True, parents=True)
    mpv_archive = archive(cache, "mpv.7z", MPV_URL, MPV_SHA)
    tar = "tar.exe" if sys.platform == "win32" else "bsdtar"
    subprocess.run([tar, "-xf", str(mpv_archive.resolve()), "-C", str(mpv),
                    "mpv.exe", "mpv.com", "d3dcompiler_43.dll", "mpv/fonts.conf"], check=True)
    with zipfile.ZipFile(archive(cache, "ffmpeg.zip", FFMPEG_URL, FFMPEG_SHA)) as z:
        for name in ["bin/ffmpeg.exe", "bin/ffprobe.exe", "bin/ffplay.exe", "LICENSE", "README.txt"]:
            with z.open("ffmpeg-9.0.2-essentials_build/" + name) as src:
                with (ffmpeg / Path(name).name).open("wb") as dst:
                    shutil.copyfileobj(src, dst)
    # Both executables are portable and carry their own runtime dependencies.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from download_tools import install
    for tool, subdir, name, url, digest in (
        ("yt-dlp", "ytdlp", "yt-dlp.exe",
         "https://github.com/yt-dlp/yt-dlp/releases/download/2026.10.26/yt-dlp.exe",
         "f5389d3698ecde4ef06f4e6b1ce0294fe9ac1396228c8990b8d3416d6d177a7d"),
        ("deno", "deno", "deno-x86_64-pc-windows-msvc.zip",
         "https://github.com/denoland/deno/releases/download/v2.9.8/deno-x86_64-pc-windows-msvc.zip",
         "cba84b26e0df5ae7f74fc66b86a95b866872a86292ebd84fa1e959927edd93bf"),
    ):
        def download(url, destination, filename=name, expected=digest):
            shutil.copyfile(archive(cache, filename, url, expected), destination)
        install(tool, tools / subdir, download, {}, asset={
            "name": name, "browser_download_url": url, "digest": "sha256:" + digest,
        })
    for subdir, url in (
        ("deno", "https://raw.githubusercontent.com/denoland/deno/v2.9.8/LICENSE.md"),
        ("ytdlp", "https://raw.githubusercontent.com/yt-dlp/yt-dlp/2026.10.26/LICENSE"),
    ):
        urllib.request.urlretrieve(url, tools / subdir / "LICENSE.txt")
    (mpv / "SOURCE.txt").write_text(MPV_URL + "\nhttps://github.com/mpv-player/mpv\n", encoding="utf-8")


if __name__ == "__main__":
    prepare(Path(sys.argv[1]), Path(sys.argv[2]))
