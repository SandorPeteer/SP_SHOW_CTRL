"""Verified, per-user Windows download tools, independent of the system PATH."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import zipfile


ASSETS = {
    "yt-dlp": ("yt-dlp/yt-dlp", "yt-dlp.exe"),
    "deno": ("denoland/deno", "deno-x86_64-pc-windows-msvc.zip"),
}


def version(executable, subprocess_kwargs):
    result = subprocess.run([str(executable), "--version"], capture_output=True, text=True,
                            timeout=20, **subprocess_kwargs)
    if result.returncode or not result.stdout.strip():
        raise RuntimeError(f"{Path(executable).name}: {result.stderr.strip() or 'Nem indítható.'}")
    return result.stdout.strip().splitlines()[0]


def latest_asset(tool, download, folder):
    repository, filename = ASSETS[tool]
    metadata = folder / "release.json"
    download(f"https://api.github.com/repos/{repository}/releases/latest", metadata)
    release = json.loads(metadata.read_text(encoding="utf-8"))
    asset = next(a for a in release["assets"] if a["name"] == filename)
    if not asset["browser_download_url"].startswith(f"https://github.com/{repository}/releases/download/"):
        raise ValueError("A letöltőeszköz kiadási címe érvénytelen.")
    return asset


def install(tool, directory, download, subprocess_kwargs, *, asset=None):
    """Validate in a sibling temporary directory, then atomically replace one executable."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / f"{tool}.exe"
    with tempfile.TemporaryDirectory(prefix="update-", dir=directory) as tmp:
        stage = Path(tmp)
        if asset is None:
            asset = latest_asset(tool, download, stage)
        expected = asset.get("digest") or ""
        if not expected.startswith("sha256:") or len(expected) != 71:
            raise ValueError(f"{tool}: a kiadáshoz nem érkezett SHA-256 ellenőrzőösszeg.")
        archive = stage / asset["name"]
        download(asset["browser_download_url"], archive)
        with archive.open("rb") as stream:
            actual = hashlib.file_digest(stream, "sha256").hexdigest()
        if actual != expected[7:]:
            raise ValueError(f"{tool}: hibás letöltés, az ellenőrzőösszeg eltér.")
        candidate = stage / destination.name
        if tool == "deno":
            with zipfile.ZipFile(archive) as package:
                with package.open("deno.exe") as source, candidate.open("wb") as target:
                    shutil.copyfileobj(source, target)
        with candidate.open("rb") as stream:
            if stream.read(2) != b"MZ":
                raise ValueError(f"{tool}: a letöltött fájl nem Windows-program.")
        installed_version = version(candidate, subprocess_kwargs)
        candidate.replace(destination)
    return destination, installed_version


def resolve(tool, user_tools, bundled_tools):
    subdir = "ytdlp" if tool == "yt-dlp" else "deno"
    for path in (Path(user_tools) / subdir / "bin" / f"{tool}.exe",
                 Path(bundled_tools) / subdir / f"{tool}.exe"):
        try:
            with path.open("rb") as stream:
                if stream.read(2) == b"MZ":
                    return str(path)
        except OSError:
            continue
    return None
