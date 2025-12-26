from __future__ import annotations

import os
import sys


def main() -> int:
    # Default to quiet logs in GUI mode.
    os.environ.setdefault("QT_LOGGING_RULES", "qt.multimedia.*=false")
    # Prefer system yt-dlp when available, but allow overriding via env.
    try:
        os.environ.setdefault("YTDLR_YTDLP", "")
    except Exception:
        pass

    from ytdlr_qt import run

    run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

