from __future__ import annotations

import json
import os
import platform
import queue
import re
import socket
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable


def _user_data_dir() -> Path:
    sysname = platform.system()
    if sysname == "Windows":
        root = os.environ.get("APPDATA")
        base = Path(root) if root else (Path.home() / "AppData" / "Roaming")
    elif sysname == "Darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        root = os.environ.get("XDG_DATA_HOME")
        base = Path(root) if root else (Path.home() / ".local" / "share")
    return base / "SP_Show_Control"


def _clamp_int(value: int, lo: int, hi: int) -> int:
    try:
        v = int(value)
    except Exception:
        v = int(lo)
    return int(max(int(lo), min(int(hi), v)))


class MpvIpcSession:
    def __init__(
        self,
        mpv_exe: str,
        *,
        name: str,
        second_screen_left: int,
        second_screen_top: int,
        fullscreen: bool,
        ipc_verbose: bool = False,
        app_log: Callable[[str], None] | None = None,
        swallow_exc: Callable[[BaseException, str], None] | None = None,
        no_console_subprocess_kwargs: Callable[[], dict] | None = None,
    ):
        self.mpv_exe = str(mpv_exe)
        self.name = str(name or "output")
        self.second_screen_left = int(second_screen_left)
        self.second_screen_top = int(second_screen_top)
        self.fullscreen = bool(fullscreen)
        self.native_fullscreen = False
        self.hwdec = "auto-safe"
        self.log_file = ""
        self.ipc_verbose = bool(ipc_verbose)

        self._app_log = app_log
        self._swallow = swallow_exc
        self._no_console_subprocess_kwargs = no_console_subprocess_kwargs

        self._proc: subprocess.Popen | None = None
        self._sock: socket.socket | None = None
        self._pipe = None  # Windows named pipe file handle

        self._lock = threading.Lock()
        self._pending: dict[int, "queue.Queue[dict]"] = {}
        self._next_id: int = 1
        self._playing: bool = False
        self._end_info: tuple[str | None, str | None] | None = None

        self._reader_thread: threading.Thread | None = None
        self.owner: str | None = None

        if platform.system() == "Windows":
            self.ipc_server = r"\\.\pipe\sp_show_control_mpv_" + self.name
        else:
            self.ipc_server = str(_user_data_dir() / f"mpv_ipc_{self.name}.sock")

    def _log(self, msg: str) -> None:
        if not msg:
            return
        cb = self._app_log
        if cb is None:
            return
        try:
            cb(msg)
        except Exception:
            return

    def _swallow_exc(self, exc: BaseException, note: str) -> None:
        cb = self._swallow
        if cb is None:
            return
        try:
            cb(exc, note)
        except Exception:
            return

    def _subprocess_kwargs(self) -> dict:
        cb = self._no_console_subprocess_kwargs
        if cb is None:
            return {}
        try:
            return dict(cb() or {})
        except Exception as e:
            self._swallow_exc(e, "no_console_subprocess_kwargs")
            return {}

    def is_alive(self) -> bool:
        proc = self._proc
        return bool(proc is not None and proc.poll() is None)

    def is_playing(self) -> bool:
        return bool(self._playing) and self.is_alive()

    def consume_end_info(self) -> tuple[str | None, str | None] | None:
        info = self._end_info
        self._end_info = None
        return info

    def start(self) -> None:
        if self.is_alive():
            return
        self.shutdown()

        # Remove stale unix socket file.
        if platform.system() != "Windows":
            try:
                p = Path(self.ipc_server)
                if p.exists():
                    p.unlink()
            except Exception as e:
                self._swallow_exc(e, "mpv ipc unlink stale socket")

        # Start windowed: user can drag the output window to the desired display, then hit "Presentation".
        geometry = "960x540+80+80"

        msg_level = "all=warn,ipc=v" if self.ipc_verbose else "all=warn"
        args = [
            self.mpv_exe,
            "--no-config",
            "--load-scripts=no",
            "--player-operation-mode=pseudo-gui",
            "--no-terminal",
            f"--hwdec={self.hwdec}",
            "--idle=yes",
            "--force-window=immediate",
            "--keep-open=yes",
            "--no-auto-window-resize",
            "--no-keepaspect-window",
            "--no-osc",
            "--no-input-default-bindings",
            "--osd-level=0",
            "--border=yes",
            f"--msg-level={msg_level}",
            "--ontop=no",
            "--image-display-duration=inf",
            f"--title=SP Show Control Output ({self.name})",
            f"--input-ipc-server={self.ipc_server}",
        ]
        if platform.system() != "Windows":
            args.append(f"--geometry={geometry}")
        if self.log_file:
            args.append(f"--log-file={self.log_file}")
        if platform.system() == "Darwin":
            # Avoid weird clamping when using full-display geometry on macOS.
            args.append("--macos-geometry-calculation=whole")
            # Default to pseudo-fullscreen on macOS (avoid Spaces).
            args.append("--native-fs=no")

        self._log(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] mpv start: {args!r}\n")

        self._proc = subprocess.Popen(
            args,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=False,
            bufsize=0,
            **self._subprocess_kwargs(),
        )
        self._connect_ipc()

    def apply_window_placement(self) -> None:
        """Best-effort: apply windowed vs presentation fullscreen without moving the window."""
        want_fullscreen = bool(self.fullscreen)

        is_macos = bool(platform.system() == "Darwin")
        try:
            if is_macos:
                use_native = bool(want_fullscreen) and bool(getattr(self, "native_fullscreen", False))
                self.set_property_strict("native-fs", bool(use_native), timeout=0.9, retries=3)
        except Exception as e:
            self._swallow_exc(e, "mpv apply_window_placement native-fs")
        try:
            self.set_property_strict("auto-window-resize", False, timeout=0.9, retries=3)
        except Exception as e:
            self._swallow_exc(e, "mpv apply_window_placement auto-window-resize")
        try:
            self.set_property_strict("keepaspect-window", False, timeout=0.9, retries=3)
        except Exception as e:
            self._swallow_exc(e, "mpv apply_window_placement keepaspect-window")

        if want_fullscreen:
            try:
                self.set_property_strict("fs-screen", "current", timeout=1.0, retries=3)
            except Exception as e:
                self._swallow_exc(e, "mpv apply_window_placement fs-screen")
            try:
                self.set_property_strict("border", False, timeout=0.9, retries=3)
            except Exception as e:
                self._swallow_exc(e, "mpv apply_window_placement border off")
            try:
                self.set_property_strict("ontop", True, timeout=0.9, retries=3)
            except Exception as e:
                self._swallow_exc(e, "mpv apply_window_placement ontop on")
            try:
                self.set_property_strict("fullscreen", True, timeout=1.4, retries=8)
            except Exception as e:
                self._swallow_exc(e, "mpv apply_window_placement fullscreen on")
            return

        # Windowed mode
        try:
            self.set_property_strict("fullscreen", False, timeout=1.2, retries=6)
        except Exception as e:
            self._swallow_exc(e, "mpv apply_window_placement fullscreen off")
        try:
            self.set_property_strict("border", True, timeout=0.9, retries=3)
        except Exception as e:
            self._swallow_exc(e, "mpv apply_window_placement border on")
        try:
            self.set_property_strict("ontop", False, timeout=0.9, retries=3)
        except Exception as e:
            self._swallow_exc(e, "mpv apply_window_placement ontop off")

    def _connect_ipc(self) -> None:
        deadline = time.monotonic() + 5.0

        if platform.system() == "Windows":
            import io

            def _connect_named_pipe(pipe: str) -> object:
                # Avoid blocking on open() for named pipes: use CreateFileW + WaitNamedPipeW.
                import os as _os

                try:
                    import ctypes
                    from ctypes import wintypes
                    import msvcrt
                except Exception as e:
                    raise RuntimeError(f"Windows IPC: missing ctypes/msvcrt: {e}") from e

                flags_rdwr = int(getattr(_os, "O_RDWR", 0)) | int(getattr(_os, "O_BINARY", 0))
                GENERIC_READ = 0x80000000
                GENERIC_WRITE = 0x40000000
                OPEN_EXISTING = 3
                FILE_ATTRIBUTE_NORMAL = 0x80
                INVALID_HANDLE_VALUE = wintypes.HANDLE(-1).value  # type: ignore[attr-defined]
                ERROR_PIPE_BUSY = 231
                ERROR_FILE_NOT_FOUND = 2
                ERROR_PATH_NOT_FOUND = 3

                CreateFileW = ctypes.windll.kernel32.CreateFileW
                CreateFileW.argtypes = [
                    wintypes.LPCWSTR,
                    wintypes.DWORD,
                    wintypes.DWORD,
                    wintypes.LPVOID,
                    wintypes.DWORD,
                    wintypes.DWORD,
                    wintypes.HANDLE,
                ]
                CreateFileW.restype = wintypes.HANDLE

                WaitNamedPipeW = ctypes.windll.kernel32.WaitNamedPipeW
                WaitNamedPipeW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD]
                WaitNamedPipeW.restype = wintypes.BOOL

                GetLastError = ctypes.windll.kernel32.GetLastError
                GetLastError.argtypes = []
                GetLastError.restype = wintypes.DWORD

                while time.monotonic() < deadline:
                    try:
                        ok = bool(WaitNamedPipeW(str(pipe), 200))
                    except Exception:
                        ok = False
                    if not ok:
                        err = int(GetLastError() or 0)
                        if err in (ERROR_FILE_NOT_FOUND, ERROR_PATH_NOT_FOUND):
                            time.sleep(0.05)
                            continue
                        if err == ERROR_PIPE_BUSY:
                            time.sleep(0.05)
                            continue
                    h = CreateFileW(
                        str(pipe),
                        int(GENERIC_READ | GENERIC_WRITE),
                        0,
                        None,
                        int(OPEN_EXISTING),
                        int(FILE_ATTRIBUTE_NORMAL),
                        None,
                    )
                    hv = int(h)
                    if hv != int(INVALID_HANDLE_VALUE):
                        fd = msvcrt.open_osfhandle(hv, flags_rdwr)
                        return _os.fdopen(fd, "r+b", buffering=0)
                    err = int(GetLastError() or 0)
                    if err == ERROR_PIPE_BUSY:
                        # Server exists but all instances are busy: wait briefly.
                        WaitNamedPipeW(str(pipe), 200)
                        continue
                    if err in (ERROR_FILE_NOT_FOUND, ERROR_PATH_NOT_FOUND):
                        time.sleep(0.05)
                        continue
                    time.sleep(0.05)
                raise RuntimeError("mpv IPC pipe did not become available")

            f = None
            last_err: Exception | None = None
            while time.monotonic() < deadline:
                try:
                    f = _connect_named_pipe(self.ipc_server)
                    break
                except Exception as e:
                    last_err = e
                    time.sleep(0.05)
                    continue
            if f is None:
                raise RuntimeError(f"mpv IPC pipe did not become available: {last_err}")
            self._pipe = f
            self._pipe_text = io.TextIOWrapper(f, encoding="utf-8", errors="ignore", newline="\n")  # type: ignore[attr-defined]

            self._reader_thread = threading.Thread(target=self._reader_loop_pipe, daemon=True)
            self._reader_thread.start()
            return

        while time.monotonic() < deadline:
            try:
                if Path(self.ipc_server).exists():
                    break
            except Exception as e:
                self._swallow_exc(e, "mpv ipc socket exists check")
            time.sleep(0.05)
        else:
            raise RuntimeError("mpv IPC socket did not become available")

        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            sock.connect(self.ipc_server)
        except Exception:
            sock.close()
            raise
        self._sock = sock
        self._sock_file = sock.makefile("r", encoding="utf-8", errors="ignore", newline="\n")  # type: ignore[attr-defined]
        self._reader_thread = threading.Thread(target=self._reader_loop_socket, daemon=True)
        self._reader_thread.start()

    def shutdown(self) -> None:
        proc = self._proc
        self._proc = None
        self._playing = False
        self._end_info = None

        try:
            if self._sock is not None:
                try:
                    self._sock.close()
                except Exception as e:
                    self._swallow_exc(e, "mpv ipc socket close")
        finally:
            self._sock = None
        try:
            if self._pipe is not None:
                try:
                    self._pipe.close()
                except Exception as e:
                    self._swallow_exc(e, "mpv ipc pipe close")
        finally:
            self._pipe = None

        if proc is None:
            return
        try:
            proc.terminate()
            proc.wait(timeout=1.0)
        except Exception as e:
            self._swallow_exc(e, "mpv proc terminate")
            try:
                proc.kill()
            except Exception as e2:
                self._swallow_exc(e2, "mpv proc kill")

    def _next_request_id(self) -> int:
        with self._lock:
            rid = int(self._next_id)
            self._next_id += 1
            return rid

    def _send_json(self, payload: dict) -> None:
        line = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
        if platform.system() == "Windows":
            if self._pipe is None:
                raise RuntimeError("mpv IPC pipe not connected")
            self._pipe.write(line)
            try:
                self._pipe.flush()
            except Exception:
                pass
            return
        if self._sock is None:
            raise RuntimeError("mpv IPC socket not connected")
        self._sock.sendall(line)

    def command(self, cmd: list[object], *, timeout: float = 0.6) -> dict:
        rid = self._next_request_id()
        q: "queue.Queue[dict]" = queue.Queue(maxsize=1)
        with self._lock:
            self._pending[rid] = q
        self._send_json({"command": cmd, "request_id": rid})
        try:
            return q.get(timeout=float(timeout))
        except queue.Empty as e:
            raise TimeoutError("mpv IPC request timed out") from e
        finally:
            with self._lock:
                self._pending.pop(rid, None)

    @staticmethod
    def _is_success(resp: object) -> bool:
        try:
            return bool(isinstance(resp, dict) and resp.get("error") == "success")
        except Exception:
            return False

    @staticmethod
    def _parse_geometry(value: object) -> tuple[int, int, int, int] | None:
        """Parse mpv geometry strings like '960x540+80+80'."""
        try:
            s = str(value or "").strip()
        except Exception:
            return None
        m = re.match(r"^([0-9]+)x([0-9]+)([+-][0-9]+)([+-][0-9]+)$", s)
        if not m:
            return None
        try:
            w = int(m.group(1))
            h = int(m.group(2))
            x = int(m.group(3))
            y = int(m.group(4))
            return (w, h, x, y)
        except Exception:
            return None

    def set_property(self, name: str, value: object) -> bool:
        try:
            resp = self.command(["set_property", str(name), value], timeout=0.6)
        except Exception as e:
            self._swallow_exc(e, f"mpv set_property {name}")
            return False
        return self._is_success(resp)

    def set_property_strict(self, name: str, value: object, *, timeout: float = 1.2, retries: int = 4) -> bool:
        """Set an mpv property with retries (useful for critical properties like mute/volume)."""
        last_err: Exception | None = None
        for _ in range(max(1, int(retries))):
            try:
                resp = self.command(["set_property", str(name), value], timeout=float(timeout))
                if not self._is_success(resp):
                    raise RuntimeError(f"mpv set_property failed: {name}")
                return True
            except Exception as e:
                last_err = e
                time.sleep(0.05)
                continue
        if last_err is not None:
            self._swallow_exc(last_err, f"mpv set_property_strict {name}")
        return False

    def get_property(self, name: str) -> object | None:
        try:
            resp = self.command(["get_property", str(name)], timeout=0.4)
            if isinstance(resp, dict) and resp.get("error") == "success":
                return resp.get("data")
        except Exception as e:
            self._swallow_exc(e, f"mpv get_property {name}")
            return None
        return None

    def stop(self) -> None:
        try:
            self.command(["stop"], timeout=0.4)
        except Exception as e:
            self._swallow_exc(e, "mpv stop")
        self._playing = False
        self.owner = None

    def loadfile(
        self,
        path: str,
        *,
        start: float = 0.0,
        end: float | None = None,
        volume: int | None = None,
        af_lavfi: str | None = None,
    ) -> None:
        opts: list[str] = []
        # Prefer `end=` (absolute timestamp) for OUT segments; `length=` is not supported reliably in mpv IPC.
        if end is not None and float(end) > 0:
            end_v = float(end)
            if start and float(start) > 0:
                end_v = max(float(start) + 0.001, float(end_v))
            opts.append(f"end={end_v:.3f}")
        try:
            resp = self.command(["loadfile", str(path), "replace", *opts], timeout=1.5)
        except Exception as e:
            self._swallow_exc(e, "mpv loadfile initial")
            resp = {}
        # If mpv rejected options, retry without them so playback still starts.
        try:
            if isinstance(resp, dict) and resp.get("error") not in (None, "success"):
                self.command(["loadfile", str(path), "replace"], timeout=1.5)
        except Exception as e:
            self._swallow_exc(e, "mpv loadfile retry")
        self._playing = True

        # Enforce IN (start) via an explicit seek; `start=` loadfile options are not reliable across mpv builds.
        try:
            start_v = float(start or 0.0)
        except Exception:
            start_v = 0.0
        if start_v > 0.0:
            deadline = time.monotonic() + 0.8
            last_seek_err: Exception | None = None
            while time.monotonic() < deadline:
                try:
                    self.command(["seek", float(start_v), "absolute", "exact"], timeout=0.6)
                except Exception as e:
                    last_seek_err = e
                    time.sleep(0.05)
                    continue
                # Verify we actually moved (best-effort).
                try:
                    pos = self.get_property("time-pos")
                    if pos is not None and float(pos) >= float(start_v) - 0.05:
                        break
                except Exception:
                    break
                time.sleep(0.05)
            else:
                if last_seek_err is not None:
                    self._swallow_exc(last_seek_err, "mpv seek (enforce start) failed")
        if volume is not None:
            self.set_property("volume", _clamp_int(int(volume), 0, 100))
        if af_lavfi:
            # Best-effort; mpv accepts filter chains as strings.
            self.set_property("af", str(af_lavfi))

    def _reader_loop_socket(self) -> None:
        f = getattr(self, "_sock_file", None)
        if f is None:
            return
        try:
            for line in f:
                self._handle_ipc_line(line)
        except Exception as e:
            self._swallow_exc(e, "mpv ipc reader socket")
        finally:
            self._playing = False

    def _reader_loop_pipe(self) -> None:
        f = getattr(self, "_pipe_text", None)
        if f is None:
            return
        try:
            while True:
                line = f.readline()
                if not line:
                    break
                self._handle_ipc_line(line)
        except Exception as e:
            self._swallow_exc(e, "mpv ipc reader pipe")
        finally:
            self._playing = False

    def _handle_ipc_line(self, line: str) -> None:
        try:
            msg = json.loads(str(line or "").strip())
        except Exception:
            return
        if not isinstance(msg, dict):
            return
        rid = msg.get("request_id")
        if isinstance(rid, int):
            with self._lock:
                q = self._pending.get(int(rid))
            if q is not None:
                try:
                    q.put_nowait(msg)
                except Exception:
                    pass
            return
        ev = msg.get("event")
        if ev == "file-loaded":
            self._playing = True
            return
        if ev == "end-file":
            self._playing = False
            try:
                reason = msg.get("reason")
                err = msg.get("error")
                self._end_info = (None if reason is None else str(reason), None if err is None else str(err))
            except Exception:
                self._end_info = (None, None)
            return

