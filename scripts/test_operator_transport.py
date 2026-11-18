"""The operator stays responsive even while either direction of its pipe stalls."""
from pathlib import Path
import queue
import sys
import threading
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from operator_transport import OperatorTransport


class ControlledPipe:
    def __init__(self):
        self.incoming = queue.Queue()
        self.writing = threading.Event()
        self.allow_write = threading.Event()
        self.sent = []
        self.read_count = 0
        self.processed = threading.Event()
        self.fail_write = False

    def send(self, message):
        self.writing.set()
        self.allow_write.wait(3)
        if self.fail_write:
            raise BrokenPipeError("transport stopped")
        self.sent.append(message)

    def recv(self):
        self.read_count += 1
        if self.read_count == 3:
            self.processed.set()
        value = self.incoming.get(timeout=5)
        if isinstance(value, Exception):
            raise value
        return value

    def close(self):
        self.incoming.put(EOFError())
        self.allow_write.set()


class OperatorTransportTests(unittest.TestCase):
    def setUp(self):
        self.pipe = ControlledPipe()
        self.transport = OperatorTransport(self.pipe)

    def tearDown(self):
        self.transport.close()
        self.transport._reader.join(1)
        self.transport._writer.join(1)

    def test_stalled_writer_never_blocks_operator_and_bounds_commands(self):
        self.assertTrue(self.transport.send(("play", None)))
        self.assertTrue(self.pipe.writing.wait(1))
        # The writer is deliberately blocked; all these GUI calls must return.
        for index in range(128):
            self.assertTrue(self.transport.send(("seek", index)))
        self.assertFalse(self.transport.send(("new", None)))
        self.assertIn("not sent", self.transport.error)
        self.assertFalse(self.transport.closed)
        self.assertIsNone(self.transport.receive_latest())
        self.assertEqual(self.pipe.sent, [])

    def test_latest_state_preserves_changed_frames_and_dialog(self):
        waveform = [{"minimum": -0.4, "maximum": 0.6, "color": "#59d8ff"}]
        self.pipe.incoming.put(({"position": 1, "waveform": waveform}, {"live": b"live-frame", "next": b"old"}))
        self.pipe.incoming.put(({"position": 2, "dialog": {"id": 4}}, {"next": b"new"}))
        self.assertTrue(self.pipe.processed.wait(1))
        state, frames = self.transport.receive_latest()
        self.assertEqual(state, {"position": 2, "dialog": {"id": 4}, "waveform": waveform})
        self.assertEqual(frames, {"live": b"live-frame", "next": b"new"})
        # A blocked next recv must not make the GUI wait for a full message.
        self.assertIsNone(self.transport.receive_latest())

    def test_broken_reader_surfaces_failure_and_refuses_new_commands(self):
        self.pipe.incoming.put(EOFError())
        self.transport._reader.join(1)
        self.assertFalse(self.transport._reader.is_alive())
        self.assertIn("connection lost", self.transport.error)
        self.assertTrue(self.transport.closed)
        self.assertFalse(self.transport.send(("play", None)))

    def test_broken_writer_surfaces_failure(self):
        self.pipe.fail_write = True
        self.pipe.allow_write.set()
        self.assertTrue(self.transport.send(("play", None)))
        self.transport._writer.join(1)
        self.assertFalse(self.transport._writer.is_alive())
        self.assertIn("command failed", self.transport.error)

    def test_windows_cancelled_read_is_handled(self):
        self.pipe.incoming.put(RuntimeError("overlapped read was cancelled"))
        self.transport._reader.join(1)
        self.assertFalse(self.transport._reader.is_alive())
        self.assertIn("cancelled", self.transport.error)

    def test_close_does_not_wait_for_stalled_io(self):
        self.transport.send(("play", None))
        self.assertTrue(self.pipe.writing.wait(1))
        self.transport.close()
        self.assertFalse(self.transport.send(("close", None)))

    @unittest.skipUnless(sys.platform == "win32", "Windows overlapped named-pipe close")
    def test_real_windows_pipe_closes_while_both_directions_are_blocked(self):
        from multiprocessing import Pipe

        local, peer = Pipe()
        writing, written, closed = (threading.Event() for _ in range(3))

        class PipeProbe:
            def recv(self):
                return local.recv()

            def send(self, message):
                writing.set()
                try:
                    local.send(message)
                finally:
                    written.set()

            def close(self):
                local.close()
                closed.set()

        transport = OperatorTransport(PipeProbe())
        try:
            self.assertTrue(transport.send(("payload", b"x" * (4 * 1024 * 1024))))
            self.assertTrue(writing.wait(1))
            self.assertFalse(written.wait(0.05), "The peer must not drain the blocked write")
            threading.Thread(target=transport.close, daemon=True).start()
            self.assertTrue(closed.wait(1), "Closing a blocked pipe must not block the GUI")
        finally:
            peer.close()
            transport.close()
        transport._reader.join(1)
        transport._writer.join(1)
        self.assertFalse(transport._reader.is_alive())
        self.assertFalse(transport._writer.is_alive())


if __name__ == "__main__":
    unittest.main()
