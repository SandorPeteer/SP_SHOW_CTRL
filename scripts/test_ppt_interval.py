"""Per-presentation slide intervals, legacy timing and portable persistence."""
import json
from pathlib import Path
import sys
import tempfile
import time
from types import MethodType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from player import App, Cue
from show_storage import save_show
from scripts.test_audio_routing import Variable


def controller():
    first = Cue(id="first", kind="ppt", path="first.pptx")
    second = Cue(id="second", kind="ppt", path="second.pptx")
    video = Cue(id="video", kind="video", path="clip.mp4")
    app = SimpleNamespace(_all_cues_b=[first, second, video], _cues_b=[first, second, video],
                          _selected_b=1, _standby_start_cue=None, _ppt_cue=first,
                          _ppt_default_interval_sec=5.0, _ppt_seconds=[2.5, None], _ppt_index=0,
                          var_ppt_interval=Variable("5"), var_ppt_interval_target=Variable(""),
                          _ppt_slide_started=123.0, _loop_b_enabled=False,
                          video_runner=Mock(), _transport_play_pause=Mock())
    for name in ("_ppt_interval_for_cue", "_set_ppt_interval", "_sync_ppt_interval_control",
                 "_commit_ppt_interval", "_ppt_still_duration", "_selected_cue_for_deck"):
        setattr(app, name, MethodType(getattr(App, name), app))
    app._sync_ppt_interval_control(second)
    return app


class PptIntervalTests(unittest.TestCase):
    def test_cue_roundtrip_and_legacy_optional_field(self):
        cue = Cue(id="ppt", kind="ppt", path="show.pptx", ppt_interval_sec=7.5)
        self.assertEqual(Cue.from_dict(cue.to_dict()).ppt_interval_sec, 7.5)
        del_data = cue.to_dict()
        del del_data["ppt_interval_sec"]
        self.assertIsNone(Cue.from_dict(del_data).ppt_interval_sec)
        for invalid in (True, False, .49, 3600.1, float("nan"), float("inf"), "7.5"):
            del_data["ppt_interval_sec"] = invalid
            self.assertIsNone(Cue.from_dict(del_data).ppt_interval_sec)

    def test_setter_rejects_invalid_seconds_non_ppt_and_stale_identity(self):
        app = controller()
        for invalid in (None, True, False, .49, 3600.1, float("nan"), float("inf"), "7.5"):
            self.assertFalse(app._set_ppt_interval(dict(cueId="second", seconds=invalid)))
        for cue_id in ("missing", "video", None):
            self.assertFalse(app._set_ppt_interval(dict(cueId=cue_id, seconds=7.5)))
        self.assertFalse(app._set_ppt_interval(None))
        for limit in (.5, 3600):
            self.assertTrue(app._set_ppt_interval(dict(cueId="second", seconds=limit)))
            self.assertEqual(app._cues_b[1].ppt_interval_sec, limit)
        self.assertIsNone(app._cues_b[0].ppt_interval_sec)
        self.assertFalse(app._loop_b_enabled)
        self.assertEqual(app._ppt_slide_started, 123)
        app.video_runner.play_for_deck.assert_not_called()
        app._transport_play_pause.assert_not_called()

    def test_explicit_interval_overrides_exported_stills_but_legacy_keeps_them(self):
        app = controller()
        self.assertEqual(app._ppt_still_duration(), 2.5)
        app._ppt_index = 1
        self.assertEqual(app._ppt_still_duration(), 5)
        app._ppt_default_interval_sec = 8
        self.assertEqual(app._ppt_still_duration(), 8)
        self.assertTrue(app._set_ppt_interval(dict(cueId="first", seconds=7.5)))
        for index in (0, 1):
            app._ppt_index = index
            self.assertEqual(app._ppt_still_duration(), 7.5)
        self.assertEqual(app._ppt_seconds, [2.5, None])

    def test_tools_edit_selected_ppt_not_the_other_running_presentation(self):
        app = controller()
        app.var_ppt_interval.set("7,5")
        self.assertTrue(app._commit_ppt_interval())
        self.assertEqual(app._cues_b[1].ppt_interval_sec, 7.5)
        self.assertIsNone(app._ppt_cue.ppt_interval_sec)
        self.assertEqual(app._ppt_still_duration(), 2.5)
        self.assertEqual(app.var_ppt_interval.get(), "7.5")
        self.assertEqual(app.var_ppt_interval_target.get(), "This presentation (s)")
        app._sync_ppt_interval_control(app._cues_b[0])
        self.assertEqual(app.var_ppt_interval.get(), "5")
        self.assertEqual(app._cues_b[1].ppt_interval_sec, 7.5)
        app.var_ppt_interval.set("bad")
        self.assertFalse(app._commit_ppt_interval())
        self.assertEqual(app.var_ppt_interval.get(), "7.5")

    def test_tools_without_selected_ppt_changes_only_default(self):
        app = controller()
        app._set_ppt_interval(dict(cueId="second", seconds=7.5))
        app._selected_b = 2
        app._sync_ppt_interval_control(app._cues_b[2])
        app.var_ppt_interval.set("9")
        self.assertTrue(app._commit_ppt_interval())
        self.assertEqual(app._ppt_default_interval_sec, 9)
        self.assertEqual(app._cues_b[1].ppt_interval_sec, 7.5)
        self.assertEqual(app.var_ppt_interval_target.get(), "Default slide time (s)")
        app._ppt_index = 1
        self.assertEqual(app._ppt_still_duration(), 9)

    def test_unchanged_tools_focusout_preserves_legacy_exported_timing(self):
        app = controller()
        app._selected_b = 0
        app._ppt_seconds = [6]
        app._sync_ppt_interval_control(app._ppt_cue)
        self.assertEqual(app.var_ppt_interval.get(), "5")
        self.assertTrue(app._commit_ppt_interval(SimpleNamespace(keysym="??")))
        self.assertIsNone(app._ppt_cue.ppt_interval_sec)
        self.assertEqual(app._ppt_still_duration(), 6)
        self.assertTrue(app._commit_ppt_interval(SimpleNamespace(keysym="Return")))
        self.assertEqual(app._ppt_cue.ppt_interval_sec, 5)

    def test_late_tools_event_cannot_write_into_a_changed_selection(self):
        app = controller()
        app.var_ppt_interval.set("12")
        app._selected_b = 0
        self.assertFalse(app._commit_ppt_interval(SimpleNamespace(keysym="??")))
        self.assertIsNone(app._cues_b[0].ppt_interval_sec)
        self.assertIsNone(app._cues_b[1].ppt_interval_sec)
        self.assertEqual(app.var_ppt_interval.get(), "5")

    def test_video_slide_uses_eof_and_repeat_off_remains_manual(self):
        app = controller()
        app._set_ppt_interval(dict(cueId="first", seconds=.5))
        app._ppt_running = True
        app._ppt_slide_started = time.monotonic() - 10
        app._ppt_slides = [Path("clip.mp4"), Path("0002.png")]
        app._show_mpv_slide = Mock()
        app.video_runner.owner_deck = "B"
        with patch("player.sys.platform", "win32"):
            App._advance_ppt_loop(app)
            app._show_mpv_slide.assert_not_called()
            app._loop_b_enabled = True
            app.video_runner._sess.observed.return_value = ("clip.mp4", False)
            App._advance_ppt_loop(app)
            app._show_mpv_slide.assert_not_called()
            app.video_runner._sess.observed.return_value = ("clip.mp4", True)
            App._advance_ppt_loop(app)
        app._show_mpv_slide.assert_called_once_with(app._ppt_cue, app._ppt_slides, 1)

    def test_portable_save_keeps_independent_ppt_intervals_and_legacy_default(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            source = folder / "slides.pptx"
            source.write_bytes(b"prepared-presentation-fixture")
            cues = [Cue(id="a", kind="ppt", path=str(source), ppt_interval_sec=7.5),
                    Cue(id="b", kind="ppt", path=str(source), ppt_interval_sec=12),
                    Cue(id="legacy", kind="ppt", path=str(source))]
            show = folder / "show.json"
            save_show(show, dict(cues_a=[], cues_b=[cue.to_dict() for cue in cues],
                                 playback=dict(slide_seconds="8")), {})
            saved = json.loads(show.read_text())
            loaded = [Cue.from_dict(value) for value in saved["cues_b"]]
            self.assertEqual([cue.ppt_interval_sec for cue in loaded], [7.5, 12, None])
            self.assertEqual(saved["playback"]["slide_seconds"], "8")
            self.assertEqual(len({cue.path for cue in loaded}), 1)
            self.assertTrue((folder / loaded[0].path).is_file())

    def test_real_show_loader_migrates_legacy_default_without_overriding_exported_timing(self):
        app = Mock(spec=App)
        app.audio_runner, app.video_runner = Mock(), Mock()
        app.var_output_aspect = Variable("Source")
        app.var_loop_a, app.var_loop_b = Variable(""), Variable("")
        app.var_ppt_sound, app.var_ppt_interval = Variable(False), Variable("5")
        app.scene_listbox = Mock()
        app._ppt_interval_for_cue = MethodType(App._ppt_interval_for_cue, app)
        data = dict(cues_b=[dict(id="legacy", kind="ppt", path="legacy.pptx")],
                    playback=dict(slide_seconds="8"))
        with patch("player.sys.platform", "win32"):
            App._load_show_from_path(app, Path("old-show.json"), set_show_path=True, data=data)
        cue = app._all_cues_b[0]
        self.assertIsNone(cue.ppt_interval_sec)
        self.assertEqual(app._ppt_interval_for_cue(cue), 8)
        app._ppt_cue, app._ppt_seconds, app._ppt_index = cue, [2.5, None], 0
        self.assertEqual(App._ppt_still_duration(app), 2.5)
        app._ppt_index = 1
        self.assertEqual(App._ppt_still_duration(app), 8)
        app.video_runner.play_for_deck.assert_not_called()
        with patch("player.sys.platform", "win32"):
            App._load_show_from_path(app, Path("older-show.json"), set_show_path=True,
                                     data=dict(cues_b=[dict(id="older", kind="ppt", path="older.pptx")]))
        self.assertEqual(app._ppt_default_interval_sec, 5)


if __name__ == "__main__":
    unittest.main()
