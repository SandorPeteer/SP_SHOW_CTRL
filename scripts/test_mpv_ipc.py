"""Regression checks for mpv telemetry and a temporarily unresponsive child."""
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from mpv_ipc import MpvIpcSession
from audio_meter import METER_PROPERTY, metered_filters


class MpvIpcTests(unittest.TestCase):
    def setUp(self):
        self.session = MpvIpcSession("mpv", name="test", second_screen_left=0, second_screen_top=0, fullscreen=False)

    def event(self, **payload):
        self.session._handle_ipc_line(json.dumps(payload))

    def test_telemetry_snapshot_uses_events_only(self):
        self.event(event="property-change", name="path", data="previous.mp4")
        self.event(event="property-change", name="time-pos", data=1.75)
        self.event(event="property-change", name="eof-reached", data=True)
        with patch.object(self.session, "command", side_effect=AssertionError("unexpected command")):
            self.assertEqual(self.session.observed("path", "time-pos", "eof-reached"), ("previous.mp4", 1.75, True))
            self.event(event="start-file")
            self.assertEqual(self.session.observed("path", "time-pos", "eof-reached"), (None, None, None))

    def test_outstanding_command_released_on_timeout_and_can_recover(self):
        with patch.object(self.session, "_send_json") as send:
            with self.assertRaises(TimeoutError):
                self.session.command(["stop"], timeout=0.01)
            with self.assertRaises(TimeoutError):
                self.session.command(["stop"], timeout=0.5)
            self.assertEqual(send.call_count, 1)
        self.assertFalse(self.session._pending)
        self.session._retry_after = 0.0

        def reply(message, _timeout):
            self.event(request_id=message["request_id"], error="success")

        with patch.object(self.session, "_send_json", side_effect=reply):
            self.assertEqual(self.session.command(["stop"])["error"], "success")
        self.assertFalse(self.session._pending)

    def test_rejected_file_is_not_marked_as_playing(self):
        with patch.object(self.session, "command", return_value={"error": "invalid parameter"}):
            with self.assertRaisesRegex(RuntimeError, "mpv rejected"):
                self.session.loadfile("test.mp4")
        self.assertFalse(self.session._playing)

    def test_each_cue_replaces_the_previous_audio_filter(self):
        with patch.object(self.session, "command", return_value={"error": "success"}), \
                patch.object(self.session, "set_property", return_value=True) as change:
            self.session.loadfile("quiet.mp4", af_lavfi="lavfi=[volume=6dB]")
            change.assert_any_call("af", metered_filters("lavfi=[volume=6dB]"))
            change.reset_mock()
            self.session.loadfile("normal.mp4")
            change.assert_any_call("af", metered_filters())

    def test_disconnected_clock_does_not_keep_stale_telemetry(self):
        class BrokenPipe:
            def lines(self):
                yield json.dumps({"event": "property-change", "name": "time-pos", "data": 4.5})
                raise BrokenPipeError("player connection ended")
        pipe = self.session._pipe = BrokenPipe()
        self.session._reader_loop_pipe(pipe)
        self.assertEqual(self.session.observed("time-pos"), (None,))

    def test_new_file_keeps_gain_observers_but_discards_previous_audio(self):
        self.event(event="property-change", name="volume", data=50)
        self.event(event="property-change", name="mute", data=False)
        self.event(event="property-change", name=METER_PROPERTY,
                   data={"lavfi.astats.1.RMS_level": "-9", "lavfi.astats.1.Peak_level": "-6"})
        self.event(event="property-change", name="time-pos", data=3)
        self.assertTrue(self.session.audio_levels()["active"])
        self.event(event="start-file")
        self.assertEqual(self.session.observed("volume", "mute", METER_PROPERTY), (50, False, None))
        self.assertFalse(self.session.audio_levels()["available"])

    def test_meter_snapshot_never_waits_for_ipc(self):
        with patch.object(self.session, "command", side_effect=AssertionError("unexpected IPC")):
            self.assertFalse(self.session.audio_levels()["available"])


if __name__ == "__main__":
    unittest.main()
