"""A per-show Still returns without hijacking ready selection or audio policy."""
import json
from pathlib import Path
import sys
import tempfile
import time
from types import MethodType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from player import App, Cue, Scene, Settings
from scripts.test_next_selection import controller
from scripts.test_audio_routing import Variable


def bind(app, *names):
    for name in names:
        setattr(app, name, MethodType(getattr(App, name), app))


def standby_controller():
    app = controller()
    app._all_cues_a = list(app._cues_a)
    app._all_cues_b = list(app._cues_b)
    app._standby_cue_id = None
    app._standby_active_cue_id = None
    app._standby_start_cue = None
    app._transport_play_pause = Mock()
    app._transport_is_active = Mock(return_value=True)
    app._transport_stop = Mock()
    app._ppt_pending = None
    app._starting_up = app._saving_show = app._opening_show = False
    bind(app, "_standby_cue", "_set_standby_cue", "_return_to_standby",
         "_selected_cue_for_deck", "_play_deck_b", "_stop_deck_b", "_prune_orphan_cues")
    return app


class StandbyMediaTests(unittest.TestCase):
    def test_loaded_projection_types_toggle_without_starting_or_selecting(self):
        app = standby_controller()
        for kind in ("image", "video", "ppt"):
            cue = Cue(id=kind, kind=kind, path=f"fallback.{kind}")
            app._all_cues_b.append(cue)
            self.assertTrue(app._set_standby_cue(cue.id))
            self.assertEqual(app._standby_cue_id, cue.id)
            self.assertTrue(app._set_standby_cue(cue.id))
            self.assertIsNone(app._standby_cue_id)
        for invalid in ("A0", "missing", None, 1):
            self.assertFalse(app._set_standby_cue(invalid))
        app._transport_play_pause.assert_not_called()
        app.tree_b.selection_set.assert_not_called()
        app.tree_b.see.assert_not_called()

    def test_return_uses_global_cue_and_restores_ready_context_even_on_error(self):
        app = standby_controller()
        still = Cue(id="other-scene", kind="image", path="logo.png")
        app._all_cues_b.append(still)
        app._set_standby_cue(still.id)
        app._selected_b = 40
        seen = []
        app._transport_play_pause.side_effect = lambda deck: seen.append((app._selected_cue_for_deck(deck), app._audio_auto_start))
        self.assertTrue(app._return_to_standby())
        self.assertEqual(seen, [(still, True)])
        self.assertEqual(app._selected_b, 40)
        self.assertIs(app._selected_cue_for_deck("B"), app._cues_b[40])
        self.assertFalse(app._audio_auto_start)
        app.tree_b.see.assert_not_called()
        app.tree_b.selection_set.assert_not_called()
        app._transport_play_pause.side_effect = RuntimeError("decoder failed")
        with self.assertRaises(RuntimeError):
            app._return_to_standby()
        self.assertIsNone(app._standby_start_cue)
        self.assertFalse(app._audio_auto_start)
        self.assertEqual(app._selected_b, 40)

    def test_show_same_actual_cue_returns_but_other_selection_starts_it(self):
        app = standby_controller()
        app._set_standby_cue("B2")
        seen = []
        app._transport_play_pause.side_effect = lambda deck: seen.append(app._selected_cue_for_deck(deck).id)
        app._play_deck_b()
        self.assertEqual(seen, ["B2"])
        app._selected_b = 40
        app._play_deck_b()
        self.assertEqual(seen, ["B2", "B40"])
        app._selected_b = 0
        app._transport_is_active.return_value = False
        app._play_deck_b()
        self.assertEqual(seen[-1], "B0")
        app._transport_is_active.return_value = True
        app._selected_b = 2
        app.video_runner.current_cue.return_value = app._cues_b[2]
        app._play_deck_b()
        self.assertEqual(len(seen), 3, "Show on the Still itself must not restart or recurse")

    def test_manual_stop_returns_but_cleanup_stop_does_not(self):
        app = standby_controller()
        app._set_standby_cue("B2")
        app._stop_deck_b()
        app._transport_stop.assert_not_called()  # Switch directly: no black frame before the Still.
        app._transport_play_pause.assert_called_once_with("B")
        app._transport_play_pause.reset_mock()
        app._transport_stop("B")
        app._transport_play_pause.assert_not_called()

    def test_already_still_stop_and_return_hold_the_actual_eof_frame(self):
        app = standby_controller()
        app._set_standby_cue("B2")
        cue = app._cues_b[2]
        app.video_runner.current_cue.return_value = cue
        app.video_runner._sess.observed.return_value = (cue.path, True)
        app._transport_is_active.return_value = False
        self.assertTrue(app._return_to_standby())
        app._stop_deck_b()
        app._selected_b = 2
        app._play_deck_b()
        app._transport_play_pause.assert_not_called()
        app._transport_stop.assert_not_called()
        self.assertEqual(app._standby_active_cue_id, cue.id)
        app._set_standby_cue("B3")
        self.assertIsNone(app._standby_active_cue_id)

    def test_video_fallback_keeps_repeat_and_current_sound_not_saved_preference(self):
        for repeat in (False, True):
            for sound in (False, True):
                with self.subTest(repeat=repeat, sound=sound):
                    app = standby_controller()
                    app.settings = Settings()
                    app.var_ppt_sound = Variable(sound)
                    app._ppt_sound_preference = not sound
                    app._loop_b_enabled = repeat
                    app._cues_b[2].auto_play = app._cues_b[3].auto_play = True
                    app._set_standby_cue("B2")
                    app._clear_mpv_ppt = Mock()
                    app._pause_audio_for_presentation = Mock(return_value=True)
                    app._update_transport_button_visuals = Mock()
                    app._suppress_finish["B"] = "stop"
                    bind(app, "_transport_play_pause")
                    with patch("player.sys.platform", "win32"):
                        self.assertTrue(app._return_to_standby())
                    app.video_runner.play_for_deck.assert_called_once_with("B", app._cues_b[2])
                    app.video_runner._sess.set_property.assert_any_call("loop-file", "inf" if repeat else "no")
                    app._apply_projection_sound.assert_called_once_with(explicit=False)
                    self.assertEqual((app._loop_b_enabled, app.var_ppt_sound.get(), app._ppt_sound_preference),
                                     (repeat, sound, not sound))
                    self.assertEqual(app._pause_audio_for_presentation.call_count, int(sound))
                    self.assertNotIn("B", app._suppress_finish)

    def test_repeat_switch_updates_active_still_without_starting_scene_playlist(self):
        app = standby_controller()
        app._standby_active_cue_id = "B2"
        app._cues_b[2].auto_play = app._cues_b[3].auto_play = True
        app.video_runner.current_cue.return_value = app._cues_b[2]
        app.var_loop_b = Variable("Repeat off")
        app._update_transport_button_visuals = Mock()
        App._toggle_loop(app, "B")
        app.video_runner._sess.set_property.assert_called_once_with("loop-file", "inf")
        self.assertTrue(app._loop_b_enabled)
        app._transport_play_pause.assert_not_called()

    def test_eof_prefers_ready_autoplay_and_otherwise_returns_without_scroll(self):
        for auto in (False, True):
            app = standby_controller()
            app._set_standby_cue("B2")
            app._selected_b = 40
            app._cues_b[40].auto_play = auto
            seen = []
            app._transport_play_pause.side_effect = lambda deck: seen.append(app._selected_cue_for_deck(deck).id)
            App._handle_runner_finished(app, "B", app.video_runner)
            self.assertEqual(seen, ["B40" if auto else "B2"])
            self.assertEqual(app._selected_b, 40)
            app.tree_b.selection_set.assert_not_called()
            app.tree_b.see.assert_not_called()

    def test_still_eof_holds_last_frame_without_repeat_or_scene_advance(self):
        app = standby_controller()
        app._set_standby_cue("B2")
        app._standby_active_cue_id = "B2"
        app.video_runner.current_cue.return_value = app._cues_b[2]
        app._return_to_standby = Mock()
        App._handle_runner_finished(app, "B", app.video_runner)
        app._return_to_standby.assert_not_called()
        app._transport_play_pause.assert_not_called()
        app._next_scene.assert_not_called()
        app._loop_b_enabled = True
        App._handle_runner_finished(app, "B", app.video_runner)
        app._return_to_standby.assert_called_once_with(restart=True)

    def test_ppt_preparation_uses_current_sound_and_late_removed_cue_cannot_start(self):
        app = standby_controller()
        cue = Cue(id="ppt", path="slides.pptx", kind="ppt")
        app._all_cues_b.append(cue)
        app._set_standby_cue(cue.id)
        app.var_ppt_sound, app.var_ppt_status = Variable(False), Variable("")
        app._ppt_sound_preference = True
        app._ppt_errors = {}
        app._ppt_preparer = SimpleNamespace(submit=Mock())
        app._update_ppt_activity = Mock()
        app._show_mpv_slide = Mock()
        bind(app, "_queue_ppt", "_ppt_prepared", "_transport_play_pause")
        with patch("player.sys.platform", "win32"):
            app._return_to_standby()
        self.assertIs(app._ppt_pending, cue)
        self.assertFalse(app._ppt_audio_explicit)
        self.assertFalse(app.var_ppt_sound.get())
        app._all_cues_b.remove(cue)
        app._ppt_prepared(cue.path, [Path("0001.png")], None)
        app._show_mpv_slide.assert_not_called()
        app._ppt_pending = None
        app._ppt_prepared(cue.path, [Path("0001.png")], None)
        app._show_mpv_slide.assert_not_called()

    def test_ppt_fallback_only_advances_when_current_repeat_is_on(self):
        app = standby_controller()
        app._ppt_running = True
        app._ppt_cue = Cue(id="ppt", path="slides.pptx", kind="ppt")
        app._ppt_slides = [Path("0001.png"), Path("0002.png")]
        app._ppt_seconds = [.1, .1]
        app._ppt_index = 0
        app._ppt_slide_started = time.monotonic() - 1
        app._show_mpv_slide = Mock()
        bind(app, "_ppt_still_duration")
        with patch("player.sys.platform", "win32"):
            App._advance_ppt_loop(app)
            app._show_mpv_slide.assert_not_called()
            app._loop_b_enabled = True
            App._advance_ppt_loop(app)
        app._show_mpv_slide.assert_called_once_with(app._ppt_cue, app._ppt_slides, 1)

    def test_delete_still_cancels_pending_playback_without_returning(self):
        app = standby_controller()
        app._set_standby_cue("B2")
        cue = app._cues_b[2]
        app._checkbox_mode_b, app._checked_cues_b = True, {2}
        app._ppt_pending = cue
        app.video_runner.current_cue.return_value = cue
        app._scenes = [Scene(id="scene", name="Scene", cue_ids_b=[cue.id])]
        app._selected_scene_idx = 0
        for name in ("_schedule_tree_column_layout", "_refresh_tree_b", "_refresh_scene_list",
                     "_set_wave_title", "_sync_target_setting_controls"):
            setattr(app, name, Mock())
        App._remove_b(app)
        self.assertIsNone(app._standby_cue_id)
        self.assertIsNone(app._ppt_pending)
        self.assertNotIn(cue, app._all_cues_b)
        app._transport_stop.assert_called_once_with("B")
        app._transport_play_pause.assert_not_called()

    def test_stop_all_preserves_configuration_but_never_starts_still(self):
        app = standby_controller()
        app._set_standby_cue("B2")
        app._standby_active_cue_id = "B2"
        app._ppt_end = app._set_paused_state_for_deck = app._reset_audio_routing = Mock()
        App._stop(app)
        self.assertEqual(app._standby_cue_id, "B2")
        self.assertIsNone(app._standby_active_cue_id)
        app._transport_play_pause.assert_not_called()

    def test_new_and_load_cleanup_clear_old_reference_before_stop(self):
        app = standby_controller()
        app._set_standby_cue("B2")
        app._add_scene = app._update_showfile_label = app._reset_audio_routing = Mock()
        app._stop_deck_a = Mock()
        app._ppt_end = Mock()  # Windows New show explicitly closes the slide presenter.
        app._scenes = []
        App._new_show(app)
        self.assertIsNone(app._standby_cue_id)
        app._transport_play_pause.assert_not_called()

    def test_save_and_load_keep_still_media_even_without_a_scene_reference(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            still = Cue(id="global-still", kind="image", path=str(folder / "logo.png"))
            Path(still.path).write_bytes(b"portable still fixture")
            app = standby_controller()
            app._all_cues_a, app._all_cues_b = [], [still]
            app._scenes = [Scene(id="scene", name="Empty")]
            app._selected_scene_idx = 0
            app._set_standby_cue(still.id)
            app._prune_orphan_cues()
            self.assertEqual(app._all_cues_b, [still])
            app.settings = Settings()
            app.btn_save_show, app.var_showfile, app._set_busy = Mock(), Mock(), Mock()
            app._show_media_cache, app._ui_tasks = {}, Mock()
            app._ppt_preparer = SimpleNamespace(ready={})
            app.var_ppt_interval = Variable("5")
            path = folder / "show.json"
            with patch("player.threading.Thread", side_effect=lambda target, **_: SimpleNamespace(start=target)):
                App._save_show_to(app, path)
            saved = json.loads(path.read_text())
            self.assertEqual(saved["playback"]["standby_cue_id"], still.id)
            self.assertEqual(saved["cues_b"][0]["id"], still.id)
            self.assertEqual((folder / saved["cues_b"][0]["path"]).read_bytes(), b"portable still fixture")
            for name in ("_stop_deck_a", "_reset_audio_routing", "_load_persistent_settings", "_queue_ppt",
                         "_set_display_vars", "_refresh_tree_a", "_refresh_tree_b", "_refresh_scene_list",
                         "_activate_scene", "_load_selected_into_editor", "_update_showfile_label", "_ppt_end"):
                setattr(app, name, Mock())
            app.var_output_aspect, app.var_loop_a, app.var_loop_b = Variable("Source"), Variable(""), Variable("")
            app.var_ppt_sound = Variable(False)
            app.scene_listbox = Mock()
            App._load_show_from_path(app, path, set_show_path=True)
            self.assertEqual(app._standby_cue_id, still.id)
            self.assertEqual(len(app._all_cues_b), 1)
            self.assertTrue(Path(app._all_cues_b[0].path).is_file())
            app._transport_play_pause.assert_not_called()
            saved["playback"]["standby_cue_id"] = "missing"
            App._load_show_from_path(app, path, set_show_path=True, data=saved)
            self.assertIsNone(app._standby_cue_id)


if __name__ == "__main__":
    unittest.main()
