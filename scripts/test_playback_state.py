"""Transport state must follow the media engine, including failed IPC and final frames."""
from pathlib import Path
import sys
from types import MethodType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from player import App, Cue, MediaRunner, OutputRunner, Settings
import windows_theme as palette


class PlaybackStateTests(unittest.TestCase):
    def setUp(self):
        self.output = OutputRunner(Settings())
        self.session = self.output._sess = Mock()
        self.session.consume_end_info.return_value = None
        self.session.set_property.return_value = True

    def projection_controller(self):
        self.output.owner_deck = "B"
        self.output._playing_cue = Cue(id="B-video", path="movie.mp4", kind="video", start_sec=10, stop_at_sec=30)
        self.session.is_playing.return_value = True
        self.session.observed.return_value = ("movie.mp4", 15.0, 60.0)
        app = SimpleNamespace(video_runner=self.output, _callback_notice="", _render_activity=Mock(), _log=Mock(),
                              audio_mix=True, _last_audio_source="A", audio_runner=Mock(is_playing=Mock(return_value=False)))
        for name in ("_projection_timeline", "_seek_projection", "_audio_route_error", "_transport_is_active"):
            setattr(app, name, MethodType(getattr(App, name), app))
        return app

    def test_projection_timeline_uses_real_clock_and_cue_in_out(self):
        app = self.projection_controller()
        timeline = app._projection_timeline()
        self.assertTrue(timeline["available"])
        self.assertEqual((timeline["position"], timeline["start"], timeline["end"]), (15, 10, 30))
        self.assertEqual(timeline["fraction"], .25)
        self.assertEqual((timeline["elapsed"], timeline["remaining"], timeline["duration"]), ("0:05", "0:15", "0:20"))
        self.session.get_property.assert_not_called()
        for values in (("old.mp4", 15, 60), ("movie.mp4", None, 60), ("movie.mp4", float("nan"), 60)):
            self.session.observed.return_value = values
            self.assertFalse(app._projection_timeline()["available"])

    def test_projection_seek_is_in_place_and_preserves_pause_mix_and_audio(self):
        app = self.projection_controller()
        self.output._paused = True
        self.assertTrue(app._seek_projection(dict(cueId="B-video", path="movie.mp4", fraction=.75)))
        self.session.set_property.assert_called_once_with("time-pos", 25.0)
        self.session.loadfile.assert_not_called()
        self.assertTrue(self.output.is_paused())
        self.assertTrue(app.audio_mix)
        self.assertEqual(app._last_audio_source, "A")

    def test_projection_seek_rejects_replaced_cues_and_invalid_positions(self):
        app = self.projection_controller()
        for value in (dict(cueId="old", path="movie.mp4", fraction=.5),
                      dict(cueId="B-video", path="previous-slide.mp4", fraction=.5),
                      dict(cueId="B-video", path="movie.mp4", fraction=float("nan"))):
            self.assertFalse(app._seek_projection(value))
        self.output.owner_deck = "A"
        self.assertFalse(app._seek_projection(dict(cueId="B-video", path="movie.mp4", fraction=.5)))
        self.session.set_property.assert_not_called()

    def test_projection_seek_failure_is_visible_and_does_not_fabricate_position(self):
        app = self.projection_controller()
        self.session.set_property.return_value = False
        self.assertFalse(app._seek_projection(dict(cueId="B-video", path="movie.mp4", fraction=.8)))
        self.assertIn("not acknowledged", app._callback_notice)
        self.assertEqual(app._projection_timeline()["position"], 15.0)

    def test_transport_highlights_only_a_matching_running_decoder(self):
        app = self.projection_controller()
        for deck in ("A", "B"):
            self.output.owner_deck = deck
            for clock, active in ((("movie.mp4", 15, False, False, False), True),
                                  (("movie.mp4", 15, True, True, False), False),
                                  (("movie.mp4", 15, False, True, False), False),
                                  (("old.mp4", 15, False, False, False), False),
                                  (("movie.mp4", None, False, False, False), False),
                                  (("movie.mp4", 30, False, False, True), False)):
                with self.subTest(deck=deck, clock=clock):
                    self.session.observed.return_value = clock
                    self.assertEqual(app._transport_is_active(deck), active)
            self.session.is_playing.return_value = False
            self.assertFalse(app._transport_is_active(deck))
            self.session.is_playing.return_value = True
        self.session.get_property.assert_not_called()

    def test_silent_running_audio_and_held_image_still_highlight_their_transport(self):
        app = self.projection_controller()
        app.audio_runner.is_playing.return_value = True
        app.audio_runner.current_cue.return_value = Cue(id="A", path="silence.wav", kind="audio")
        clock = app.audio_runner._media_clock
        clock.is_playing.return_value = False  # A's non-owning observer has no process handle.
        clock.observed.return_value = ("silence.wav", 2, False, False, False)
        self.assertTrue(app._transport_is_active("A"))
        clock.observed.return_value = ("silence.wav", 2, True, True, False)
        self.assertFalse(app._transport_is_active("A"))
        self.output._playing_cue = Cue(id="B", path="still.png", kind="image")
        self.session.observed.return_value = ("still.png", None, False, True, False)
        self.assertTrue(app._transport_is_active("B"))
        self.assertFalse(app._projection_timeline()["available"])

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
                              _playback_scene_matches=Mock(return_value=True),
                              _prepare_audio_source=Mock(return_value=True), _audio_source_started=Mock(),
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

    def test_near_end_countdown_stays_readable_without_red_flashing(self):
        cue = Cue(id="music", path="music.wav", kind="audio", start_sec=10, stop_at_sec=30)
        runner = Mock()
        runner.current_cue.return_value = cue
        runner.is_playing.return_value = True
        runner.playback_length_sec.return_value = 60
        for deck in ("A", "B"):
            with self.subTest(deck=deck):
                label, value = Mock(), Mock()
                app = SimpleNamespace(_now_time_cache={}, _now_fg_cache={},
                                      **{f"lbl_now_{deck.lower()}_time": label,
                                         f"_now_time_default_fg_{deck.lower()}": palette.CYAN})
                colors = []
                for instant, position in ((10.0, 27), (10.26, 27.5), (10.5, 28), (10.76, 29.99)):
                    runner.playback_position_sec.return_value = position
                    with patch("player.time.monotonic", return_value=instant):
                        App._update_deck_now_playing(app, deck, runner, value)
                    colors.append(label.configure.call_args.kwargs["fg"])
                self.assertEqual(colors, [palette.AMBER] * 4)
                self.assertEqual(value.set.call_count, 4, "The countdown must keep advancing")
                self.assertTrue(value.set.call_args.args[0].startswith("-0:00"))
                runner.playback_position_sec.return_value = 12
                App._update_deck_now_playing(app, deck, runner, value)
                self.assertEqual(label.configure.call_args.kwargs["fg"], palette.CYAN)

    def test_waveform_darkening_still_tracks_player_progress(self):
        cue = Cue(id="music", path="music.wav", kind="audio")
        runner, canvas = Mock(), Mock()
        runner.current_cue.return_value = cue
        runner.is_playing.return_value = True
        runner.is_paused.return_value = False
        canvas.winfo_width.return_value = 600
        canvas.winfo_height.return_value = 80
        items = {name: i for i, name in enumerate(("seg_bg", "played", "remain", "cursor", "out"))}
        app = SimpleNamespace(_selected_a=0, _cues_a=[cue], canvas_a=canvas,
                              _duration_for_cue=Mock(return_value=100), _tint_waveform_a=Mock(),
                              _ensure_playback_items=Mock(return_value=items), _set_playback_visibility=Mock())
        for position in (25, 75):
            runner.playback_position_sec.return_value = position
            App._update_waveform_playback_for_deck(app, "A", runner)
        self.assertEqual([call.args[0] for call in app._tint_waveform_a.call_args_list], [0.25, 0.75])


if __name__ == "__main__":
    unittest.main()
