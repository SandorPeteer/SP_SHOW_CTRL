"""Transport state must follow the media engine, including failed IPC and final frames."""
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from player import App, Cue, MediaRunner, OutputRunner, Settings


class PlaybackStateTests(unittest.TestCase):
    def setUp(self):
        self.output = OutputRunner(Settings())
        self.session = self.output._sess = Mock()
        self.session.consume_end_info.return_value = None
        self.session.set_property.return_value = True

    def test_pause_and_resume_change_state_only_after_acknowledgement(self):
        self.session.set_property.return_value = False
        self.output.pause()
        self.assertFalse(self.output.is_paused())
        self.session.set_property.return_value = True
        self.output.pause()
        self.assertTrue(self.output.is_paused())
        self.session.set_property.return_value = False
        self.output.resume()
        self.assertTrue(self.output.is_paused())
        self.session.set_property.return_value = True
        self.output.resume()
        self.assertFalse(self.output.is_paused())

    def test_last_fifty_milliseconds_do_not_finish_a_video(self):
        self.output._playing_cue = Cue(id="video", path="movie.mp4", kind="video")
        self.session.is_playing.return_value = True
        self.session.observed.return_value = ("movie.mp4", 9.96, 10.0, False)
        self.assertTrue(self.output.is_playing())
        self.session.stop.assert_not_called()
        self.session.observed.return_value = ("movie.mp4", 10.0, 10.0, True)
        self.assertFalse(self.output.is_playing())
        self.assertEqual(self.output.last_end_reason, "eof")

    def test_image_replacement_clears_a_paused_video(self):
        self.output._paused = True
        with patch.object(self.output, "ensure_window", return_value=True):
            self.output.play_for_deck("B", Cue(id="image", path="image.png", kind="image"))
        self.session.set_property.assert_any_call("pause", False)
        self.assertFalse(self.output.is_paused())

    def test_windows_audio_countdown_never_substitutes_the_wall_clock(self):
        runner = MediaRunner(Settings())
        runner.observe_clock = True
        runner._backend = "mpv"
        runner._proc = Mock()
        runner._proc.poll.return_value = None
        runner._playing_cue = Cue(id="music", path="music.wav", kind="audio")
        runner._started_at_monotonic = 10
        runner._playing_seek_sec = 2
        with patch("player.sys.platform", "win32"), patch("player.time.monotonic", return_value=30):
            self.assertIsNone(runner.playback_position_sec())
            runner._media_clock = Mock()
            runner._media_clock.observed.return_value = (None,)
            self.assertIsNone(runner.playback_position_sec())
            runner._media_clock.observed.return_value = (3.25,)
            self.assertEqual(runner.playback_position_sec(), 3.25)

    def test_video_handler_preserves_resume_point_when_command_fails(self):
        cue = Cue(id="video", path="movie.mp4", kind="video", video_mode="output")
        self.output._playing_cue = cue
        self.output.owner_deck = "A"
        self.session.set_property.return_value = False
        app = SimpleNamespace(_starting_up=False, _selected_cue_for_deck=Mock(return_value=cue),
                              _clear_mpv_ppt=Mock(), video_runner=self.output, _log=Mock(),
                              _set_paused_state_for_deck=Mock(), _update_transport_button_visuals=Mock(),
                              _render_activity=Mock())
        with patch.object(self.output, "is_playing", return_value=True), \
                patch.object(self.output, "playback_position_sec", return_value=2.5):
            App._transport_play_pause(app, "A")
            app._set_paused_state_for_deck.assert_not_called()
            self.assertIn("not acknowledged", app._callback_notice)
            self.output._paused = True
            App._transport_play_pause(app, "A")
            app._set_paused_state_for_deck.assert_not_called()
            self.assertIn("not acknowledged", app._callback_notice)
            self.session.set_property.return_value = True
            App._transport_play_pause(app, "A")
            app._set_paused_state_for_deck.assert_called_once_with("A", None)
            self.assertEqual(app._callback_notice, "")

    def test_audio_pause_waits_for_real_clock_and_ignores_replaced_process(self):
        cue = Cue(id="music", path="music.wav", kind="audio")
        runner = Mock()
        runner.current_cue.return_value = cue
        runner.is_playing.return_value = True
        runner.playback_position_sec.return_value = None
        process = runner._proc
        callbacks = []
        app = SimpleNamespace(_suppress_finish={}, _callback_notice="", _render_activity=Mock(),
                              _set_paused_state_for_deck=Mock(), _log=Mock(),
                              _update_transport_button_visuals=Mock(),
                              after=lambda delay, callback, *args: callbacks.append((callback, args)))
        app._pause_media_at_clock = lambda *args: App._pause_media_at_clock(app, *args)
        with patch("player.time.monotonic", return_value=10):
            app._pause_media_at_clock(runner, cue, process, 11)
        runner.stop.assert_not_called()
        callback, args = callbacks.pop()
        runner.playback_position_sec.return_value = 0.125
        callback(*args)
        app._set_paused_state_for_deck.assert_called_once_with("A", (cue.id, 0.125))
        runner.stop.assert_called_once()
        runner.stop.reset_mock()
        runner._proc = object()  # Stop/Play replaced the player before the next retry.
        callback(*args)
        runner.stop.assert_not_called()

    def test_audio_pause_missing_clock_reports_the_problem(self):
        cue = Cue(id="music", path="music.wav", kind="audio")
        runner = Mock()
        runner.current_cue.return_value = cue
        runner.playback_position_sec.return_value = None
        app = SimpleNamespace(_render_activity=Mock(), _log=Mock())
        with patch("player.time.monotonic", return_value=12):
            App._pause_media_at_clock(app, runner, cue, runner._proc, 11)
        self.assertIn("position", app._callback_notice)
        app._render_activity.assert_called_once()
        runner.stop.assert_not_called()


if __name__ == "__main__":
    unittest.main()
