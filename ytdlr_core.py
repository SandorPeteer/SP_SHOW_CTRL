from __future__ import annotations

import json
import shlex
import shutil
from dataclasses import dataclass
from pathlib import Path

DEFAULT_TEMPLATE = "%(title).150B [%(id)s].%(ext)s"


def which_or_none(name: str) -> str | None:
    try:
        return shutil.which(name) or None
    except Exception:
        return None


def quote_cmd(cmd: list[str]) -> str:
    return " ".join(shlex.quote(c) for c in cmd)


def resolve_ytdlp(explicit: str = "") -> str:
    if explicit:
        p = Path(explicit).expanduser()
        if p.exists():
            return str(p)
        raise FileNotFoundError(f"yt-dlp not found at: {p}")

    # Keep env handling simple and robust (no fancy parsing).
    try:
        import os as _os

        env_p = str(_os.environ.get("YTDLR_YTDLP") or "").strip()
    except Exception:
        env_p = ""
    if env_p:
        p = Path(env_p).expanduser()
        if p.exists():
            return str(p)

    # System PATH, plus common Homebrew locations (GUI apps often get a minimal PATH).
    ytdlp = which_or_none("yt-dlp")
    if ytdlp:
        return ytdlp
    try:
        import platform as _platform

        if _platform.system() == "Darwin":
            for base in (Path("/opt/homebrew/bin"), Path("/usr/local/bin")):
                p = base / "yt-dlp"
                if p.exists():
                    return str(p)
    except Exception:
        pass

    try:
        from ytdlr_tools import local_ytdlp_path, repo_ytdlp_path  # local module, optional

        lp = local_ytdlp_path()
        if lp.exists():
            return str(lp)
        rp = repo_ytdlp_path()
        if rp.exists():
            return str(rp)
    except Exception:
        pass
    raise FileNotFoundError("yt-dlp not found (PATH or tools/ytdlp)")


def resolve_ffmpeg_location(explicit: str = "") -> str | None:
    if explicit:
        p = Path(explicit).expanduser()
        if p.is_dir():
            return str(p)
        if p.exists():
            return str(p.parent)
        raise FileNotFoundError(f"ffmpeg-location not found: {p}")

    ffmpeg = which_or_none("ffmpeg")
    if not ffmpeg:
        return None
    return str(Path(ffmpeg).parent)


def build_download_cmd(
    *,
    ytdlp: str,
    urls: list[str],
    out_dir: str,
    template: str = DEFAULT_TEMPLATE,
    mode: str = "av",  # av|video|audio
    format_selector: str | None = None,
    single_file: bool = False,
    no_playlist: bool = False,
    ffmpeg_location: str | None = None,
    merge_output_format: str | None = None,
    passthrough: list[str] | None = None,
) -> list[str]:
    cmd: list[str] = [
        ytdlp,
        "--progress",
        "--newline",
        "--windows-filenames",
        "--paths",
        out_dir,
        "-o",
        template,
        "--print",
        "after_move:filepath",
    ]

    if no_playlist:
        cmd.append("--no-playlist")

    fmt_sel = (format_selector or "").strip()
    if fmt_sel:
        cmd += ["-f", fmt_sel]
        if ffmpeg_location:
            cmd += ["--ffmpeg-location", ffmpeg_location]
        if merge_output_format:
            cmd += ["--merge-output-format", str(merge_output_format)]
        if passthrough:
            cmd += list(passthrough)
        cmd += list(urls)
        return cmd

    mode = (mode or "av").strip().lower()
    if mode not in ("av", "video", "audio"):
        mode = "av"

    if mode == "audio":
        cmd += ["-f", "ba/best"]
    elif mode == "video":
        cmd += ["-f", "bv*"]
    else:
        if single_file:
            cmd += ["-f", "best*[vcodec!=none][acodec!=none]/best"]
        else:
            cmd += ["-f", "bv*+ba/b"]
            if ffmpeg_location is None:
                raise RuntimeError("FFmpeg required for best A/V merge, but not found")
            cmd += ["--ffmpeg-location", ffmpeg_location]
            if merge_output_format:
                cmd += ["--merge-output-format", str(merge_output_format)]

    if passthrough:
        cmd += list(passthrough)
    cmd += list(urls)
    return cmd


def build_search_cmd(
    *,
    ytdlp: str,
    query: str,
    limit: int = 20,
    fast: bool = True,
    search_kind: str = "ytsearch",  # ytsearch|ytsearchdate|ytsearchall
    passthrough: list[str] | None = None,
) -> list[str]:
    q = (query or "").strip()
    if not q:
        raise ValueError("Empty query")
    # Keep this reasonably bounded so searches stay responsive.
    lim = max(1, min(int(limit), 200))
    kind = (search_kind or "ytsearch").strip().lower()
    if kind not in ("ytsearch", "ytsearchdate", "ytsearchall"):
        kind = "ytsearch"
    url = f"{kind}{lim}:{q}"

    cmd: list[str] = [
        ytdlp,
        "--skip-download",
        "--dump-json",
        "--no-warnings",
    ]
    if fast:
        cmd += ["--flat-playlist"]
    if passthrough:
        cmd += list(passthrough)
    cmd.append(url)
    return cmd


@dataclass(frozen=True)
class SearchItem:
    title: str
    webpage_url: str
    video_id: str
    uploader: str
    duration: int | None
    view_count: int | None
    raw: dict


def parse_search_items(json_lines: list[str]) -> list[SearchItem]:
    items: list[SearchItem] = []
    for line in json_lines:
        line = (line or "").strip()
        if not line:
            continue
        try:
            data = json.loads(line)
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        vid = str(data.get("id") or "")
        title = str(data.get("title") or "")
        webpage = str(data.get("webpage_url") or data.get("url") or "")
        if webpage and webpage.startswith("ytsearch"):
            webpage = ""
        if not webpage and vid:
            webpage = f"https://www.youtube.com/watch?v={vid}"
        uploader = str(data.get("uploader") or data.get("channel") or "")
        duration = data.get("duration")
        view_count = data.get("view_count")
        try:
            duration_i = int(duration) if duration is not None else None
        except Exception:
            duration_i = None
        try:
            views_i = int(view_count) if view_count is not None else None
        except Exception:
            views_i = None
        if not webpage:
            continue
        items.append(
            SearchItem(
                title=title,
                webpage_url=webpage,
                video_id=vid,
                uploader=uploader,
                duration=duration_i,
                view_count=views_i,
                raw=data,
            )
        )
    return items


def build_info_cmd(
    *,
    ytdlp: str,
    url: str,
    no_playlist: bool = True,
    passthrough: list[str] | None = None,
) -> list[str]:
    u = (url or "").strip()
    if not u:
        raise ValueError("Missing URL")
    cmd: list[str] = [
        ytdlp,
        "--skip-download",
        "--dump-single-json",
        "--no-warnings",
    ]
    if no_playlist:
        cmd.append("--no-playlist")
    if passthrough:
        cmd += list(passthrough)
    cmd.append(u)
    return cmd


def build_get_url_cmd(
    *,
    ytdlp: str,
    url: str,
    format_selector: str,
    no_playlist: bool = True,
    passthrough: list[str] | None = None,
) -> list[str]:
    u = (url or "").strip()
    if not u:
        raise ValueError("Missing URL")
    fs = (format_selector or "").strip()
    if not fs:
        raise ValueError("Missing format selector")
    cmd: list[str] = [
        ytdlp,
        "--no-warnings",
        "--no-playlist" if no_playlist else "--yes-playlist",
        "-g",
        "-f",
        fs,
    ]
    if passthrough:
        cmd += list(passthrough)
    cmd.append(u)
    return cmd


@dataclass(frozen=True)
class FormatItem:
    format_id: str
    ext: str
    kind: str  # av|v|a
    width: int | None
    height: int | None
    fps: float | None
    vcodec: str
    acodec: str
    tbr: float | None
    abr: float | None
    filesize: int | None
    format_note: str
    raw: dict


def _as_int(v: object) -> int | None:
    try:
        if v is None:
            return None
        return int(v)  # type: ignore[arg-type]
    except Exception:
        return None


def _as_float(v: object) -> float | None:
    try:
        if v is None:
            return None
        return float(v)  # type: ignore[arg-type]
    except Exception:
        return None


def parse_info_json(text: str) -> dict:
    try:
        data = json.loads(text)
    except Exception as e:
        raise ValueError(f"Invalid JSON from yt-dlp: {e}") from e
    if not isinstance(data, dict):
        raise ValueError("Invalid info JSON (not an object)")
    return data


def extract_format_items(info: dict) -> list[FormatItem]:
    formats = info.get("formats")
    if not isinstance(formats, list):
        return []
    out: list[FormatItem] = []
    for f in formats:
        if not isinstance(f, dict):
            continue
        format_id = str(f.get("format_id") or "")
        ext = str(f.get("ext") or "")
        vcodec = str(f.get("vcodec") or "")
        acodec = str(f.get("acodec") or "")
        if not format_id:
            continue
        v_has = vcodec and vcodec != "none"
        a_has = acodec and acodec != "none"
        if v_has and a_has:
            kind = "av"
        elif v_has:
            kind = "v"
        elif a_has:
            kind = "a"
        else:
            continue
        filesize = _as_int(f.get("filesize"))
        if filesize is None:
            filesize = _as_int(f.get("filesize_approx"))
        out.append(
            FormatItem(
                format_id=format_id,
                ext=ext,
                kind=kind,
                width=_as_int(f.get("width")),
                height=_as_int(f.get("height")),
                fps=_as_float(f.get("fps")),
                vcodec=vcodec,
                acodec=acodec,
                tbr=_as_float(f.get("tbr")),
                abr=_as_float(f.get("abr")),
                filesize=filesize,
                format_note=str(f.get("format_note") or ""),
                raw=f,
            )
        )
    return out


def format_resolution(width: int | None, height: int | None) -> str:
    if height:
        if width:
            return f"{width}x{height}"
        return f"{height}p"
    return ""


def format_bitrate_kbps(v: float | None) -> str:
    if v is None:
        return ""
    try:
        return f"{float(v):.0f}k"
    except Exception:
        return ""


def format_filesize(n: int | None) -> str:
    if n is None:
        return ""
    try:
        b = int(n)
    except Exception:
        return ""
    if b < 1024:
        return f"{b} B"
    kb = b / 1024
    if kb < 1024:
        return f"{kb:.0f} KB"
    mb = kb / 1024
    if mb < 1024:
        return f"{mb:.1f} MB"
    gb = mb / 1024
    return f"{gb:.2f} GB"


def format_duration(seconds: int | None) -> str:
    if seconds is None:
        return ""
    try:
        s = max(0, int(seconds))
    except Exception:
        return ""
    h = s // 3600
    m = (s % 3600) // 60
    sec = s % 60
    if h:
        return f"{h}:{m:02d}:{sec:02d}"
    return f"{m}:{sec:02d}"


def format_views(count: int | None) -> str:
    if count is None:
        return ""
    try:
        n = int(count)
    except Exception:
        return ""
    if n < 1000:
        return str(n)
    if n < 1_000_000:
        return f"{n/1000:.1f}K".replace(".0K", "K")
    if n < 1_000_000_000:
        return f"{n/1_000_000:.1f}M".replace(".0M", "M")
    return f"{n/1_000_000_000:.1f}B".replace(".0B", "B")


def recordbox_recommended_format_selector() -> str:
    return (
        "bestvideo[ext=mp4][vcodec^=avc1]+bestaudio[ext=m4a][acodec^=mp4a]/"
        "best[ext=mp4][vcodec^=avc1][acodec^=mp4a]/"
        "bestvideo[ext=mp4]+bestaudio[ext=m4a]/"
        "best[ext=mp4]/best"
    )


def recordbox_audio_m4a_format_selector() -> str:
    return "bestaudio[ext=m4a][acodec^=mp4a]/bestaudio[ext=m4a]/bestaudio[acodec^=mp4a]/bestaudio/best"
