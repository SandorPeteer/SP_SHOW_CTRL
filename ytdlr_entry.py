from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ytdlr_core as core


def _parse_args(argv: list[str]) -> tuple[argparse.Namespace, list[str]]:
    p = argparse.ArgumentParser(
        prog="yt-dlr",
        description="YouTube letöltés yt-dlp-vel (újrakódolás/konvertálás nélkül) + opcionális GUI.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
        allow_abbrev=False,
    )

    p.add_argument("--gui", action="store_true", help="Tkinter GUI indítása (régi)")
    p.add_argument("--qt", action="store_true", help="Qt (PyQt6) DJ GUI indítása")
    p.add_argument("--debug", action="store_true", help="Ne némítsa el a Qt/preview logokat a terminálban")
    p.add_argument("urls", nargs="*", help="YouTube URL(ek) (CLI módhoz)")

    p.add_argument(
        "-P",
        "--paths",
        dest="out_dir",
        default=str(Path.cwd()),
        help="Kimeneti mappa (letöltések helye)",
    )
    p.add_argument("-o", "--output", dest="template", default=core.DEFAULT_TEMPLATE, help="Fájlnév sablon")
    p.add_argument(
        "--mode",
        choices=("av", "video", "audio"),
        default="av",
        help="Mit töltsön le: A/V együtt, csak videó, vagy csak hang (mindegyik eredeti formátumban)",
    )
    p.add_argument(
        "--single-file",
        action="store_true",
        help="Csak 1 fájlt tölt (progressive best), nem próbál video+audio merge-öt",
    )
    p.add_argument("--no-playlist", action="store_true", help="Playlist URL esetén csak az adott elemet töltse")
    p.add_argument("--merge-output-format", default="", help="A/V merge esetén konténer (pl. mp4/mkv/webm), üres = auto")
    p.add_argument("--ytdlp", default="", help="yt-dlp bináris elérési út (ha üres, PATH alapján keresi)")
    p.add_argument("--ffmpeg-location", default="", help="FFmpeg helye (könyvtár vagy bináris). Csak A/V merge-nél kellhet.")
    p.add_argument("--print-command", action="store_true", help="Parancs kiírása futtatás előtt")
    p.add_argument("--dry-run", action="store_true", help="Ne futtassa, csak írja ki a parancsot")
    p.add_argument("--version", action="store_true", help="Verziók kiírása (yt-dlr + yt-dlp), majd kilép")

    args, passthrough = p.parse_known_args(argv)
    return args, passthrough


def _log_file_path() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Logs" / "yt-dlr" / "yt-dlr.log"
    return Path.home() / ".cache" / "yt-dlr" / "yt-dlr.log"


def _redirect_stderr_to_log() -> None:
    try:
        log_file = _log_file_path()
        log_file.parent.mkdir(parents=True, exist_ok=True)
        f = open(log_file, "a", buffering=1)
        try:
            os.dup2(f.fileno(), 2)
        except Exception:
            pass
        sys.stderr = f  # type: ignore[assignment]
    except Exception:
        pass


def main(argv: list[str]) -> int:
    args, passthrough = _parse_args(argv)

    if args.qt:
        if not args.debug:
            os.environ.setdefault("QT_LOGGING_RULES", "qt.multimedia.*=false")
            _redirect_stderr_to_log()
        from ytdlr_qt import run

        run()
        return 0

    if args.gui:
        from ytdlr_gui import run_gui

        run_gui()
        return 0

    try:
        ytdlp = core.resolve_ytdlp(args.ytdlp)
    except Exception as e:
        raise SystemExit(f"yt-dlp not found. Install: `brew install yt-dlp`\n\n{e}")

    if args.version:
        print("yt-dlr 0.2.0")
        try:
            proc = subprocess.run([ytdlp, "--version"], check=False, text=True, capture_output=True)
            print(f"yt-dlp {((proc.stdout or proc.stderr) or '').strip()}")
        except Exception as e:
            print(f"yt-dlp (version check failed): {e}")
        return 0

    if not args.urls:
        raise SystemExit("Missing URL (vagy használd: `--gui`). Try: `./yt-dlr --help`")

    out_dir_path = Path(args.out_dir).expanduser()
    if not args.dry_run:
        out_dir_path.mkdir(parents=True, exist_ok=True)
    out_dir = str(out_dir_path)

    ffmpeg_location = None
    if args.mode == "av" and not args.single_file:
        ffmpeg_location = core.resolve_ffmpeg_location(args.ffmpeg_location)

    merge = (args.merge_output_format or "").strip()
    merge_fmt = merge if merge else None

    cmd = core.build_download_cmd(
        ytdlp=ytdlp,
        urls=list(args.urls),
        out_dir=out_dir,
        template=str(args.template),
        mode=str(args.mode),
        single_file=bool(args.single_file),
        no_playlist=bool(args.no_playlist),
        ffmpeg_location=ffmpeg_location,
        merge_output_format=merge_fmt,
        passthrough=list(passthrough),
    )

    if args.print_command or args.dry_run:
        print(core.quote_cmd(cmd))
    if args.dry_run:
        return 0

    proc = subprocess.run(cmd)
    return int(proc.returncode or 0)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
