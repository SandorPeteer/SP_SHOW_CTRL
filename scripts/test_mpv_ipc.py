"""Regression checks for mpv telemetry and a temporarily unresponsive child."""
import json
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import Mock, patch

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
        self.event(event="property-change", name="audio-out-params", data={"channels": "mono", "channel-count": 1})
        self.session._meter_ready_layout = "mono"
        self.assertTrue(self.session.audio_levels()["active"])
        self.event(event="start-file")
        self.assertEqual(self.session.observed("volume", "mute", METER_PROPERTY), (50, False, None))
        self.assertFalse(self.session.audio_levels()["available"])

    def test_meter_snapshot_never_waits_for_ipc(self):
        with patch.object(self.session, "command", side_effect=AssertionError("unexpected IPC")):
            self.assertFalse(self.session.audio_levels()["available"])

    def test_layout_update_waits_until_new_output_is_ready(self):
        self.event(event="start-file")
        self.event(event="file-loaded")
        with patch.object(self.session, "command", side_effect=AssertionError("old output must not be reused")):
            self.session._configure_output_meter()
        self.assertTrue(self.session._meter_loading)
        self.event(event="playback-restart")
        self.assertFalse(self.session._meter_loading)

    def test_format_change_event_does_not_issue_ipc_from_reader(self):
        with patch.object(self.session, "command", side_effect=AssertionError("reader blocked itself")):
            self.event(event="property-change", name="audio-out-params", data={"channels": "5.1", "channel-count": 6})
            self.event(event="audio-reconfig")
        self.assertTrue(self.session._meter_wake.is_set())

    def test_pre_downmix_metadata_is_not_presented_as_output(self):
        self.session._meter_ready_layout = "stereo"
        self.event(event="property-change", name="audio-out-params", data={"channels": "stereo", "channel-count": 2})
        self.event(event="property-change", name="volume", data=100)
        self.event(event="property-change", name="time-pos", data=1)
        metadata = {}
        for channel in range(1, 7):
            metadata[f"lavfi.astats.{channel}.RMS_level"] = "-9"
            metadata[f"lavfi.astats.{channel}.Peak_level"] = "-6"
        self.event(event="property-change", name=METER_PROPERTY, data=metadata)
        self.assertFalse(self.session.audio_levels()["available"])

    def test_shutdown_cannot_replace_connection_during_filter_update(self):
        proc = self.session._proc = Mock()
        entered, release, closed = threading.Event(), threading.Event(), threading.Event()
        stop, wake = self.session._meter_stop, self.session._meter_wake
        observed = []

        def configure():
            entered.set()
            release.wait(2)
            observed.append(self.session._proc)

        def shutdown():
            self.session.shutdown()
            closed.set()

        with patch.object(self.session, "_configure_output_meter", side_effect=configure):
            worker = threading.Thread(target=self.session._meter_loop, args=(stop, wake, proc))
            closer = threading.Thread(target=shutdown)
            worker.start()
            wake.set()
            self.assertTrue(entered.wait(1))
            closer.start()
            try:
                self.assertFalse(closed.wait(0.05), "shutdown replaced an active worker's connection")
            finally:
                release.set()
                worker.join(2)
                closer.join(2)
        self.assertEqual(observed, [proc])
        self.assertFalse(worker.is_alive())
        self.assertFalse(closer.is_alive())
        self.assertIsNone(self.session._proc)

    def test_rejected_meter_filter_is_never_published_as_ready(self):
        output = {"channels": "stereo", "channel-count": 2}
        for failed_command in (0, 1, 2):
            with self.subTest(failed_command=failed_command):
                self.session._meter_ready_layout = None
                replies = [{"error": "success"} for _ in range(3)]
                replies[failed_command] = {"error": "invalid parameter"}
                with patch.object(self.session, "get_property", side_effect=[
                    [{"label": "sp-output", "params": {}}], output
                ]), patch.object(self.session, "command", side_effect=replies):
                    self.session._configure_output_meter()
                self.assertIsNone(self.session._meter_ready_layout)


if __name__ == "__main__":
    unittest.main()
