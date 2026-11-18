"""Real Tk tool/editor geometry with isolated data and no media processes."""
from contextlib import ExitStack
import os
from pathlib import Path
import sys
import tempfile
import tkinter as tk
from tkinter import ttk
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import player
from operator_ui import show_tools


class ToolLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.stack = ExitStack()
        directory = cls.stack.enter_context(tempfile.TemporaryDirectory())
        for name in ("_install_global_exception_logging", "_append_app_log", "_apply_logging_settings"):
            cls.stack.enter_context(patch.object(player, name))
        cls.stack.enter_context(patch.object(player, "_user_data_dir", return_value=Path(directory)))
        for name in ("_startup_sequence", "_poll_playback", "_load_persistent_settings"):
            cls.stack.enter_context(patch.object(player.App, name))
        try:
            cls.app = player.App(operator_worker=True)
        except tk.TclError as error:
            cls.stack.close()
            raise unittest.SkipTest(str(error))
        cls.app._add_scene()
        cls.app.update()

    @classmethod
    def tearDownClass(cls):
        cls.app.output_monitor.close()
        cls.app._waveform_worker.close()
        cls.app.destroy()
        cls.stack.close()

    def widgets(self, window):
        pending = list(window.winfo_children())
        while pending:
            widget = pending.pop()
            pending.extend(widget.winfo_children())
            if widget.winfo_viewable():
                yield widget

    def check_layout(self, window, name):
        self.app.update()
        for extra in (0, 120):
            width, height = window.winfo_width(), window.winfo_height()
            if extra:
                window.geometry(f"{width + extra}x{height + 80}")
                self.app.update()
            entries = []
            for widget in self.widgets(window):
                if not isinstance(widget, (ttk.Entry, ttk.Button)):
                    continue
                x = widget.winfo_rootx() - window.winfo_rootx()
                y = widget.winfo_rooty() - window.winfo_rooty()
                self.assertGreaterEqual(x, 0, str(widget))
                self.assertGreaterEqual(y, 0, str(widget))
                self.assertLessEqual(x + widget.winfo_width(), window.winfo_width(), str(widget))
                self.assertLessEqual(y + widget.winfo_height(), window.winfo_height(), str(widget))
                if isinstance(widget, ttk.Entry):
                    entries.append(widget.winfo_height())
            if entries:
                self.assertLessEqual(max(entries) - min(entries), 2)
                self.assertGreaterEqual(min(entries), 28)
            output = os.environ.get("SP_SHOW_CTRL_TEST_SCREENSHOTS")
            if output:
                from PIL import ImageGrab
                folder = Path(output)
                folder.mkdir(parents=True, exist_ok=True)
                rendered = tk.BooleanVar(self.app, value=False)
                self.app.after(250, lambda: rendered.set(True))
                self.app.wait_variable(rendered)
                left, top = window.winfo_rootx(), window.winfo_rooty()
                ImageGrab.grab(bbox=(left, top, left + window.winfo_width(), top + window.winfo_height())).save(
                    folder / f"{name}{'-resized' if extra else ''}.png")

    def test_settings_fields_and_actions_fit(self):
        self.app._open_global_setup()
        window = self.app._global_setup_win
        self.check_layout(window, "settings")
        window.destroy()

    def test_audio_only_header_uses_existing_video_mode_and_keeps_editor_in_sync(self):
        cue = player.Cue.from_dict({"id": "header-video", "kind": "video", "path": "header-video.mp4"})
        previous, selected = self.app._cues_a, self.app._selected_a
        self.app._cues_a, self.app._selected_a = [cue], 0
        try:
            with patch.object(self.app, "_update_tree_item"):
                self.app._toggle_audio_only()
                self.assertEqual(cue.video_mode, "audio_only")
                self.assertFalse(cue.open_on_second_screen)
                self.assertEqual(self.app.var_video_mode_a.get(), "Audio only (keep visual)")
                self.app._toggle_audio_only()
                self.assertEqual(cue.video_mode, "output")
                self.assertTrue(cue.open_on_second_screen)
        finally:
            self.app._cues_a, self.app._selected_a = previous, selected

    def test_media_and_projection_tools_fit(self):
        for name, window, tabs, tab in (
            ("media", self.app.audio_tools, self.app.tabs_a, self.app.tab_a_inout),
            ("projection", self.app.projection_tools, self.app.tabs_b, self.app.tab_b_ppt),
            ("downloads", self.app.projection_tools, self.app.tabs_b, self.app.tab_b_download),
        ):
            show_tools(window, tabs, tab)
            self.check_layout(window, name)
            window.withdraw()

    def test_scene_editor_cancel_preserves_values_and_save_applies_them(self):
        scene = self.app._scenes[0]
        original = scene.name
        for accept in (False, True):
            self.app._edit_scene()
            window = next(w for w in self.app.winfo_children()
                          if isinstance(w, tk.Toplevel) and w.title().startswith("Edit Scene:"))
            self.check_layout(window, "scene")
            widgets = list(self.widgets(window))
            entry = next(w for w in widgets if isinstance(w, ttk.Entry) and w.get() == original)
            entry.delete(0, "end")
            entry.insert(0, "Rehearsal renamed")
            button = next(w for w in widgets if isinstance(w, ttk.Button)
                          and w.cget("text") == ("Save scene" if accept else "Cancel"))
            button.invoke()
            self.assertEqual(scene.name, "Rehearsal renamed" if accept else original)


if __name__ == "__main__":
    unittest.main()
