"""Bounded background pipe I/O for the Qt operator's event thread."""
import queue
import threading


class OperatorTransport:
    def __init__(self, connection):
        self.connection = connection
        self._commands = queue.Queue(maxsize=128)
        self._lock = threading.Lock()
        self._stopped = threading.Event()
        self._pending = None
        self._error = None
        self._reader = threading.Thread(target=self._read, name="operator-state", daemon=True)
        self._writer = threading.Thread(target=self._write, name="operator-commands", daemon=True)
        self._reader.start()
        self._writer.start()

    @property
    def error(self):
        with self._lock:
            return self._error

    @property
    def closed(self):
        return self._stopped.is_set()

    def _failed(self, message):
        with self._lock:
            if not self._stopped.is_set():
                self._error = message
                self._stopped.set()

    def send(self, message):
        """Queue in order without ever waiting for the controller's pipe."""
        with self._lock:
            if self._stopped.is_set():
                return False
            try:
                self._commands.put_nowait(message)
            except queue.Full:
                self._error = "Controller command queue is full. This action was not sent."
                return False
            self._error = None
        return True

    def receive_latest(self):
        """Take the newest snapshot, retaining each preview changed since the last take."""
        with self._lock:
            pending, self._pending = self._pending, None
        return pending

    def _read(self):
        while not self._stopped.is_set():
            try:
                state, changes = self.connection.recv()
                if not isinstance(state, dict) or not isinstance(changes, dict):
                    raise ValueError("Invalid controller snapshot")
            except (EOFError, OSError, ValueError, TypeError, RuntimeError) as exc:
                self._failed(f"Controller connection lost: {exc or type(exc).__name__}")
                return
            with self._lock:
                frames = self._pending[1] if self._pending is not None else {}
                if self._pending is not None and "waveform" not in state:
                    previous_state = self._pending[0]
                    if "waveform" in previous_state:
                        state["waveform"] = previous_state["waveform"]
                frames.update(changes)
                self._pending = (state, frames)

    def _write(self):
        while not self._stopped.is_set():
            try:
                message = self._commands.get(timeout=0.2)
            except queue.Empty:
                continue
            if self._stopped.is_set():
                return
            try:
                self.connection.send(message)
            except (EOFError, OSError, ValueError, RuntimeError) as exc:
                self._failed(f"Controller command failed: {exc or type(exc).__name__}")
                return
            with self._lock:
                if not self._stopped.is_set():
                    self._error = None

    def close(self):
        # Neither join nor a final synchronous send is safe on the GUI thread:
        # a child may be stuck halfway through a pipe read/write. The owning
        # application stops that child; these daemon threads cannot delay exit.
        self._stopped.set()
        self.connection.close()
