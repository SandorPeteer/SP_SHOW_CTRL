"""Audio handoff/Mix policy: real acknowledgements precede audible starts."""
from pathlib import Path
import subprocess
import sys
from types import MethodType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from audio_meter import empty_levels
from player import App, Cue, MediaRunner, Settings


class Variable:
    def __init__(self, value): self.value = value
    def get(self): return self.value
    def set(self, value): self.value = value


class AudioRoutingTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.cue = Cue(id="audio", path="song.wav", kind="audio")
        a, b = Mock(), Mock()
        a.is_playing.return_value = True
        a.current_cue.return_value = self.cue
        a._proc.poll.return_value = None
        a._media_clock.set_property.side_effect = lambda name, value: self.calls.append(("A", name, value)) or True
        a._media_clock.get_property.return_value = 4.25
        def stopped():
            self.calls.append(("A", "stop"))
            a.is_playing.return_value = False
            a._proc.poll.return_value = 0
            return True
        a.stop.side_effect = stopped
        a.play_at.side_effect = lambda *args, **kwargs: self.calls.append(("A", "play_at", args[1]))
        a.play.side_effect = lambda *args, **kwargs: self.calls.append(("A", "play"))
        b.owner_deck = "B"
        b.is_playing.return_value = True
        b._sess.is_alive.return_value = True
        b._sess.set_property.side_effect = lambda name, value: self.calls.append(("B", name, value)) or True
        self.app = SimpleNamespace(audio_runner=a, video_runner=b, audio_mix=False,
            _last_audio_source="A", _audio_auto_start=False, _ppt_sound_preference=True,
            _ppt_running=True, _ppt_slides=[Path("slide.mp4")], _ppt_index=0,
            var_ppt_sound=Variable(True), settings=Settings(), _suppress_finish={},
            _paused_a=None, _paused_b=None, _callback_notice="", _render_activity=Mock(),
            _update_transport_button_visuals=Mock(), _log=Mock(), _starting_up=False,
            _selected_cue_for_deck=Mock(return_value=self.cue),
            _playback_scene_matches=Mock(return_value=True), _record_playback_origin=Mock(),
            _clear_mpv_ppt=Mock(), _resume_visuals_if_any=Mock(), _restore_last_visual_if_any=Mock())
        for name in ("_audio_route_error", "_pause_audio_for_presentation", "_prepare_audio_source",
                     "_mute_presentation_for_audio", "_audio_source_started", "_toggle_audio_mix",
                     "_reset_audio_routing", "_active_audio_meter", "_ppt_apply_sound",
                     "_paused_state_for_deck", "_set_paused_state_for_deck"):
            setattr(self.app, name, MethodType(getattr(App, name), self.app))

    def test_presentation_unmutes_only_after_audio_pause_and_verified_stop(self):
        self.assertTrue(self.app._ppt_apply_sound())
        self.assertEqual(self.app._paused_a, ("audio", 4.25))
        self.assertLess(self.calls.index(("A", "pause", True)), self.calls.index(("A", "stop")))
        self.assertLess(self.calls.index(("A", "stop")), self.calls.index(("B", "mute", False)))
        self.assertEqual(self.app._last_audio_source, "B")

    def test_failed_pause_or_stop_does_not_unmute_presentation(self):
        for fail in ("pause", "stop", "clock"):
            with self.subTest(fail=fail):
                self.setUp()
                if fail == "pause": self.app.audio_runner._media_clock.set_property.side_effect = lambda *_: False
                elif fail == "stop": self.app.audio_runner.stop.side_effect = lambda: False
                else: self.app.audio_runner._media_clock.get_property.return_value = None
                self.assertFalse(self.app._ppt_apply_sound())
                self.assertNotIn(("B", "mute", False), self.calls)
                self.assertTrue(self.app._callback_notice)

    def test_manual_audio_resume_mutes_presentation_and_keeps_its_picture(self):
        self.app._ppt_apply_sound()
        self.calls.clear()
        App._transport_play_pause(self.app, "A")
        self.assertEqual(self.calls[0], ("B", "mute", True))
        self.assertIn(("A", "play_at", 4.25), self.calls)
        self.app.video_runner.stop.assert_not_called()
        self.assertFalse(self.app.var_ppt_sound.get())
        self.assertTrue(self.app._ppt_sound_preference)
        self.assertEqual(self.app._last_audio_source, "A")
        # An automatic next slide cannot take audio back after explicit A Play.
        self.calls.clear()
        self.app._ppt_apply_sound(explicit=False)
        self.assertEqual(self.calls, [("B", "mute", True)])

    def test_live_unresponsive_presentation_blocks_audio_but_dead_output_does_not(self):
        self.app.audio_runner.is_playing.return_value = False
        self.app.video_runner._sess.set_property.side_effect = lambda *_: False
        App._transport_play_pause(self.app, "A")
        self.app.audio_runner.play.assert_not_called()
        self.app.video_runner._sess.is_alive.return_value = False
        App._transport_play_pause(self.app, "A")
        self.app.audio_runner.play.assert_called_once()

    def test_mix_enable_never_starts_and_explicit_sources_can_run_together(self):
        self.assertTrue(self.app._toggle_audio_mix())
        self.assertEqual(self.calls, [])
        self.assertTrue(self.app._ppt_apply_sound())
        self.assertNotIn(("A", "stop"), self.calls)
        self.assertIn(("B", "mute", False), self.calls)
        self.assertEqual(self.app._last_audio_source, "B")

    def test_mix_off_keeps_last_explicit_source_and_failure_keeps_mix(self):
        for keep in ("A", "B"):
            with self.subTest(keep=keep):
                self.setUp()
                self.app.audio_mix = True
                self.app._last_audio_source = keep
                self.assertTrue(self.app._toggle_audio_mix())
                self.assertFalse(self.app.audio_mix)
                self.assertIn(("A", "stop") if keep == "B" else ("B", "mute", True), self.calls)
        self.setUp()
        self.app.audio_mix = True
        self.app.video_runner._sess.set_property.side_effect = lambda *_: False
        self.assertFalse(self.app._toggle_audio_mix())
        self.assertTrue(self.app.audio_mix)

    def test_presentation_eof_last_slide_and_end_never_resume_audio(self):
        self.app._ppt_apply_sound()
        with patch("player.sys.platform", "win32"):
            App._handle_runner_finished(self.app, "B", self.app.video_runner)
            App._show_mpv_slide(self.app, None, [Path("slide.mp4")], 1)
            App._ppt_end(self.app)
        self.assertEqual(self.app._paused_a, ("audio", 4.25))
        self.app.audio_runner.play.assert_not_called()
        self.app.audio_runner.play_at.assert_not_called()

    def test_automatic_start_does_not_change_mix_off_priority(self):
        self.app._audio_auto_start = True
        self.app._last_audio_source = "B"
        self.app._audio_source_started("A")
        self.assertEqual(self.app._last_audio_source, "B")

    def test_reset_only_clears_session_mix_and_keeps_presentation_preference(self):
        self.app.audio_mix = True
        self.app._reset_audio_routing()
        self.assertFalse(self.app.audio_mix)
        self.assertIsNone(self.app._last_audio_source)
        self.assertTrue(self.app.var_ppt_sound.get())
        self.assertTrue(self.app._ppt_sound_preference)

    def test_new_explicit_show_restores_sound_preference_after_audio_takeover(self):
        self.app._prepare_audio_source("A")
        self.assertFalse(self.app.var_ppt_sound.get())
        self.app._ppt_pending = None
        self.app._ppt_errors = {}
        self.app._ppt_preparer = Mock()
        self.app._ppt_prepared = Mock()
        self.app._update_ppt_activity = Mock()
        self.app.var_ppt_status = Variable("")
        with patch("player.sys.platform", "win32"):
            App._queue_ppt(self.app, Cue(id="show", path="slides.pptx", kind="ppt"), play=True)
        self.assertTrue(self.app.var_ppt_sound.get())
        self.assertTrue(self.app._ppt_audio_explicit)

    def test_stop_all_and_new_clear_mix_without_resuming_or_erasing_preference(self):
        for action in (App._stop, App._new_show):
            with self.subTest(action=action.__name__):
                self.setUp()
                self.app.audio_mix = True
                self.app._paused_a = ("audio", 4.25)
                self.app._playback_origin = {}
                self.app._ppt_end = Mock()
                self.app._scenes = []
                self.app._saving_show = self.app._opening_show = False
                self.app._stop_deck_a = Mock()
                self.app._stop_deck_b = Mock()
                self.app._add_scene = Mock()
                self.app._update_showfile_label = Mock()
                action(self.app)
                self.assertFalse(self.app.audio_mix)
                self.assertTrue(self.app._ppt_sound_preference)
                self.app.audio_runner.play.assert_not_called()
                self.app.audio_runner.play_at.assert_not_called()

    def test_save_keeps_sound_preference_but_never_persists_mix(self):
        app = self.app
        app.audio_mix = True
        app.var_ppt_sound.set(False)  # Temporarily handed to Audio.
        app._scenes, app._all_cues_a, app._all_cues_b = [], [], []
        app._selected_scene_idx = -1
        app._loop_a_enabled = app._loop_b_enabled = False
        app.var_ppt_interval = Variable("5")
        app._ppt_preparer = SimpleNamespace(ready={})
        app.btn_save_show, app.var_showfile, app._set_busy = Mock(), Mock(), Mock()
        app._show_media_cache, app._ui_tasks = None, Mock()
        with patch("player.sys.platform", "win32"), patch("show_storage.save_show") as save, \
                patch("player.threading.Thread", side_effect=lambda target, **_: SimpleNamespace(start=target)):
            App._save_show_to(app, Path("show.json"))
        payload = save.call_args.args[1]
        self.assertTrue(payload["playback"]["slide_audio"])
        self.assertNotIn("mix", str(payload).lower())
        self.assertTrue(app.audio_mix)

    def test_unavailable_meter_does_not_claim_there_is_no_audio(self):
        self.app._vu_snapshot = Mock(return_value=empty_levels())
        meter = self.app._active_audio_meter()
        self.assertEqual(meter["source"], "audio")
        self.assertFalse(meter["available"])
        self.assertEqual(meter["channels"], [])
        self.app.audio_mix = True
        self.assertEqual([d["name"] for d in self.app._active_audio_meter()["decks"]], ["A", "B"])

    def test_failed_process_stop_keeps_handle_for_retry(self):
        runner = MediaRunner(Settings())
        process = runner._proc = Mock()
        process.poll.return_value = None
        process.wait.side_effect = subprocess.TimeoutExpired("mpv", 1)
        self.assertFalse(runner.stop())
        self.assertIs(runner._proc, process)


if __name__ == "__main__":
    unittest.main()
