"""Manual ready selection is independent of playback and survives natural EOF."""
from pathlib import Path
import sys
from types import MethodType, SimpleNamespace
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from player import App, Cue, Scene


def controller():
    cues_a = [Cue(id=f"A{i}", path=f"{i}.wav", kind="audio") for i in range(50)]
    cues_b = [Cue(id=f"B{i}", path=f"{i}.mp4", kind="video") for i in range(50)]
    app = SimpleNamespace(_cues_a=cues_a, _cues_b=cues_b, _selected_a=0, _selected_b=0,
                          _cueid_to_iid_a={cue.id: str(i) for i, cue in enumerate(cues_a)},
                          _cueid_to_iid_b={cue.id: str(i) for i, cue in enumerate(cues_b)},
                          _loop_a_enabled=False, _loop_b_enabled=False, tree_a=Mock(), tree_b=Mock(),
                          audio_runner=Mock(), video_runner=Mock(), _load_cue_into_editor=Mock(),
                          _log=Mock(), _play_deck_a=Mock(), _play_deck_b=Mock(), _next_scene=Mock(),
                          _scenes=[], _selected_scene_idx=-1, _suppress_finish={}, _ppt_running=False,
                          _playback_origin={},
                          _inhibit_auto_advance=False, _restore_last_visual_if_any=Mock(),
                          _resume_visuals_if_any=Mock(), _play_visuals_by_index=Mock())
    for name in ("_loop_enabled_for_deck", "_visuals_autoplay_indices", "_visuals_next_autoplay_index",
                 "_next_cue_index", "_select_next_cue_for_deck", "_poll_output_monitor",
                 "_playback_scene_matches", "_record_playback_origin", "_current_scene_id"):
        if hasattr(App, name):
            setattr(app, name, MethodType(getattr(App, name), app))
    app.audio_runner.current_cue.return_value = cues_a[0]
    app.audio_runner.is_playing.return_value = True
    app.audio_runner.last_exit_code = 0
    app.video_runner.current_cue.return_value = cues_b[0]
    app.video_runner.is_playing.return_value = True
    app.video_runner.owner_deck = "B"
    app.video_runner.last_exit_code = 0
    return app


class NextSelectionTests(unittest.TestCase):
    def test_long_lists_show_independent_manual_ready_targets(self):
        app = controller()
        app._selected_a, app._selected_b = 40, 32
        App._update_tree_playing_highlight(app)
        app.tree_a.set_emphasis.assert_called_with("0", "40")
        app.tree_b.set_emphasis.assert_called_with("0", "32")
        self.assertEqual((app._selected_a, app._selected_b), (40, 32))
        app.tree_a.see.assert_not_called()
        app.tree_b.see.assert_not_called()

    def test_current_selection_keeps_natural_next(self):
        app = controller()
        App._update_tree_playing_highlight(app)
        app.tree_a.set_emphasis.assert_called_with("0", "1")
        app.tree_b.set_emphasis.assert_called_with("0", "1")

    def test_eof_preserves_manual_target_and_its_autoplay_flag(self):
        for deck in ("A", "B"):
            for auto in (False, True):
                with self.subTest(deck=deck, auto=auto):
                    app = controller()
                    setattr(app, "_selected_" + deck.lower(), 40)
                    getattr(app, "_cues_" + deck.lower())[40].auto_play = auto
                    runner = app.audio_runner if deck == "A" else app.video_runner
                    App._handle_runner_finished(app, deck, runner)
                    self.assertEqual(getattr(app, "_selected_" + deck.lower()), 40)
                    play = getattr(app, "_play_deck_" + deck.lower())
                    self.assertEqual(play.call_count, int(auto))
                    getattr(app, "tree_" + deck.lower()).see.assert_not_called()

    def test_explicit_target_overrides_natural_visual_playlist_next(self):
        app = controller()
        app._cues_b[0].auto_play = app._cues_b[1].auto_play = True
        app._selected_b = 40
        App._handle_runner_finished(app, "B", app.video_runner)
        self.assertEqual(app._selected_b, 40)
        app._play_visuals_by_index.assert_not_called()
        app.video_runner.play_for_deck.assert_not_called()

    def test_eof_advances_normally_without_a_manual_target(self):
        app = controller()
        app._cues_a[1].auto_play = True
        App._handle_runner_finished(app, "A", app.audio_runner)
        self.assertEqual(app._selected_a, 1)
        app._play_deck_a.assert_called_once()

    def test_old_scene_completion_and_empty_show_cannot_start_stale_target(self):
        app = controller()
        app._selected_a = 40
        app._cues_a = [Cue(id=f"new-scene{i}", path="new.wav", kind="audio", auto_play=True) for i in range(50)]
        app._selected_a = 30
        app._select_next_cue_for_deck("A", from_cue_id="A0")
        app._play_deck_a.assert_not_called()
        self.assertEqual(app._selected_a, 30)
        app._loop_a_enabled = True
        App._handle_runner_finished(app, "A", app.audio_runner)
        app.audio_runner.play.assert_not_called()
        app._next_scene.assert_not_called()
        app._cues_a = []
        app._selected_a = -1
        app._select_next_cue_for_deck("A", from_cue_id="A0")
        app._play_deck_a.assert_not_called()
        self.assertEqual(app._selected_a, -1)

    def test_user_stop_keeps_current_selection_without_auto_start(self):
        app = controller()
        app._selected_a = 40
        app._cues_a[40].auto_play = True
        app._suppress_finish["A"] = "stop"
        App._handle_runner_finished(app, "A", app.audio_runner)
        self.assertEqual(app._selected_a, 40)
        app._play_deck_a.assert_not_called()

    def test_play_starts_manual_audio_target_instead_of_pausing_old_cue(self):
        app = controller()
        app._starting_up = False
        app._selected_a = 40
        app._selected_cue_for_deck = Mock(return_value=app._cues_a[40])
        app._paused_state_for_deck = Mock(return_value=None)
        app._set_paused_state_for_deck = Mock()
        app._pause_media_at_clock = Mock()
        app._update_transport_button_visuals = Mock()
        App._transport_play_pause(app, "A")
        app.audio_runner.play.assert_called_once_with(app._cues_a[40])
        app._pause_media_at_clock.assert_not_called()

    def test_next_preview_follows_ready_target_or_natural_next(self):
        app = controller()
        app._selected_cue_for_deck = lambda deck: app._cues_b[app._selected_b]
        app.output_monitor = Mock()
        app.settings = SimpleNamespace(output_aspect="Source")
        app.after = Mock()
        for selected, expected in ((40, 40), (0, 1)):
            app._selected_b = selected
            App._poll_output_monitor(app)
            self.assertEqual(app.output_monitor.update_targets.call_args.args[1], app._cues_b[expected].path)

    def test_repeat_does_not_commit_the_browsed_ready_target(self):
        app = controller()
        app._loop_a_enabled = True
        app._selected_a = 40
        app._playback_origin["A"] = (None, "A0")
        App._handle_runner_finished(app, "A", app.audio_runner)
        app.audio_runner.play.assert_called_once_with(app._cues_a[0])
        self.assertEqual(app._selected_a, 40)
        app._play_deck_a.assert_not_called()
        self.assertEqual(app._playback_origin["A"], (None, "A0"))

    def test_visual_repeat_playlist_keeps_manual_selection_and_scroll(self):
        app = controller()
        app._loop_b_enabled = True
        app._cues_b[0].auto_play = app._cues_b[1].auto_play = True
        app._selected_b = 40
        App._handle_runner_finished(app, "B", app.video_runner)
        self.assertTrue(app._play_visuals_by_index.call_args.kwargs["preserve_ready"])
        started = []
        app._transport_play_pause = lambda deck: started.append(app._selected_b)
        App._play_visuals_by_index(app, 1, preserve_ready=True)
        self.assertEqual(started, [1])
        self.assertEqual(app._selected_b, 40)
        app.tree_b.see.assert_not_called()
        app.tree_b.selection_set.assert_not_called()

    def test_shared_cue_eof_cannot_advance_another_scene(self):
        for deck in ("A", "B"):
            with self.subTest(deck=deck):
                app = controller()
                app._scenes = [Scene(id="old", name="Old"), Scene(id="new", name="New", auto_advance=True)]
                app._selected_scene_idx = 1
                app._playback_origin[deck] = ("old", f"{deck}0")
                getattr(app, "_cues_" + deck.lower())[1].auto_play = True
                runner = app.audio_runner if deck == "A" else app.video_runner
                App._handle_runner_finished(app, deck, runner)
                self.assertEqual(getattr(app, "_selected_" + deck.lower()), 0)
                getattr(app, "_play_deck_" + deck.lower()).assert_not_called()
                app._next_scene.assert_not_called()

    def test_explicit_play_in_new_scene_rebinds_shared_cue_origin(self):
        app = controller()
        app._scenes = [Scene(id="old", name="Old"), Scene(id="new", name="New")]
        app._selected_scene_idx = 1
        app._playback_origin["A"] = ("old", "A0")
        app._starting_up = False
        app._selected_cue_for_deck = Mock(return_value=app._cues_a[0])
        app._paused_state_for_deck = Mock(return_value=("A0", 12.5))
        app._set_paused_state_for_deck = Mock()
        app._pause_media_at_clock = Mock()
        app._update_transport_button_visuals = Mock()
        App._transport_play_pause(app, "A")
        app.audio_runner.play.assert_called_once_with(app._cues_a[0])
        app.audio_runner.play_at.assert_not_called()
        app._pause_media_at_clock.assert_not_called()
        self.assertEqual(app._playback_origin["A"], ("new", "A0"))

    def test_same_scene_resume_keeps_origin_and_stop_clears_only_that_deck(self):
        app = controller()
        app._scenes = [Scene(id="same", name="Same")]
        app._selected_scene_idx = 0
        app._playback_origin = {"A": ("same", "A0"), "B": ("same", "B0")}
        app.audio_runner.is_playing.return_value = False
        app._starting_up = False
        app._selected_cue_for_deck = Mock(return_value=app._cues_a[0])
        app._paused_state_for_deck = Mock(return_value=("A0", 12.5))
        app._set_paused_state_for_deck = Mock()
        app._update_transport_button_visuals = Mock()
        App._transport_play_pause(app, "A")
        app.audio_runner.play_at.assert_called_once_with(app._cues_a[0], 12.5, volume_override=None)
        self.assertEqual(app._playback_origin["A"], ("same", "A0"))
        App._transport_stop(app, "A")
        self.assertNotIn("A", app._playback_origin)
        self.assertEqual(app._playback_origin["B"], ("same", "B0"))

    def test_shared_last_cue_cannot_auto_advance_new_scene_but_new_play_can(self):
        app = controller()
        app._cues_a = [app._cues_a[0]]
        app._scenes = [Scene(id="old", name="Old"), Scene(id="new", name="New", auto_advance=True),
                       Scene(id="later", name="Later")]
        app._selected_scene_idx = 1
        app._playback_origin["A"] = ("old", "A0")
        App._handle_runner_finished(app, "A", app.audio_runner)
        app._next_scene.assert_not_called()
        app._record_playback_origin("A", app._cues_a[0])
        App._handle_runner_finished(app, "A", app.audio_runner)
        app._next_scene.assert_called_once()

    def test_explicit_visual_play_records_current_scene(self):
        app = controller()
        app._scenes = [Scene(id="new", name="New")]
        app._selected_scene_idx = 0
        app._playback_origin["B"] = ("old", "B0")
        app._starting_up = False
        app._clear_mpv_ppt = Mock()
        app._selected_cue_for_deck = Mock(return_value=app._cues_b[0])
        app._update_transport_button_visuals = Mock()
        App._transport_play_pause(app, "B")
        app.video_runner.play_for_deck.assert_called_once_with("B", app._cues_b[0])
        self.assertEqual(app._playback_origin["B"], ("new", "B0"))

    def test_shared_paused_cue_in_another_scene_is_labelled_play_not_resume(self):
        app = controller()
        app._scenes = [Scene(id="new", name="New")]
        app._selected_scene_idx = 0
        app._playback_origin["A"] = ("old", "A0")
        app._transport_visual_cache = {}
        app._paused_state_for_deck = Mock(return_value=("A0", 12.5))
        app._selected_cue_for_deck = lambda deck: app._cues_a[0] if deck == "A" else app._cues_b[0]
        app.audio_runner.is_playing.return_value = False
        app.video_runner.is_playing.return_value = False
        app.var_play_a = Mock()
        App._update_transport_button_visuals(app)
        app.var_play_a.set.assert_called_with("Play")


if __name__ == "__main__":
    unittest.main()
