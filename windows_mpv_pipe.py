"""Byte-stream IPC to mpv. Windows needs overlapped I/O for simultaneous reads and writes."""
import threading
import time
import _winapi


class MpvPipe:
    def __init__(self, name, deadline):
        while True:
            try:
                self._handle = _winapi.CreateFile(
                    name, _winapi.GENERIC_READ | _winapi.GENERIC_WRITE, 0, 0,
                    _winapi.OPEN_EXISTING, _winapi.FILE_FLAG_OVERLAPPED, 0)
                break
            except OSError as exc:
                if exc.winerror not in (2, 3, 231) or time.monotonic() >= deadline:
                    raise
                time.sleep(0.05)
        self._state = threading.Condition()
        self._pending = set()
        self._closed = False

    def _transfer(self, data=None, timeout=1.0):
        with self._state:
            if self._closed:
                raise BrokenPipeError("mpv connection closed")
            if data is None:
                op, status = _winapi.ReadFile(self._handle, 8192, overlapped=True)
            else:
                op, status = _winapi.WriteFile(self._handle, data, overlapped=True)
            self._pending.add(op)
        try:
            if data is not None and status == _winapi.ERROR_IO_PENDING:
                if _winapi.WaitForSingleObject(op.event, max(1, int(timeout * 1000))) == _winapi.WAIT_TIMEOUT:
                    op.cancel()
                    op.GetOverlappedResult(True)
                    raise TimeoutError("mpv pipe write timed out")
            count, error = op.GetOverlappedResult(True)
            if error:
                raise OSError(f"mpv pipe I/O failed (Windows error {error})")
            if data is None:
                return op.getbuffer()
            if count != len(data):
                raise BrokenPipeError("Incomplete mpv command")
        finally:
            with self._state:
                self._pending.remove(op)
                if self._closed and not self._pending:
                    _winapi.CloseHandle(self._handle)

    def write(self, data, timeout):
        self._transfer(data, timeout)

    def lines(self):
        pending = bytearray()
        while chunk := self._transfer():
            pending.extend(chunk)
            *lines, remainder = pending.split(b"\n")
            pending = remainder
            for line in lines:
                yield line.decode("utf-8", errors="replace")

    def close(self):
        with self._state:
            if self._closed:
                return
            self._closed = True
            for op in self._pending:
                op.cancel()
            # The last cancelled transfer closes the handle after draining its OVERLAPPED.
            if not self._pending:
                _winapi.CloseHandle(self._handle)
