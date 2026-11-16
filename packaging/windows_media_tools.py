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
    (mpv / "SOURCE.txt").write_text(MPV_URL + "\nhttps://github.com/mpv-player/mpv\n", encoding="utf-8")


if __name__ == "__main__":
    prepare(Path(sys.argv[1]), Path(sys.argv[2]))
