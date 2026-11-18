"""Headless regression tests for modal answers and scene/show preservation."""
import ast
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from operator_dialogs import OperatorDialogs
import tkinter as tk


def model_method(name):
    source = Path(__file__).resolve().parents[1] / "player.py"
    app = next(node for node in ast.parse(source.read_text()).body
               if isinstance(node, ast.ClassDef) and node.name == "App")
    method = next(node for node in app.body if isinstance(node, ast.FunctionDef) and node.name == name)
    namespace = {"sys": SimpleNamespace(platform="win32"), "messagebox": Mock()}
    exec(compile(ast.Module(body=[method], type_ignores=[]), str(source), "exec"), namespace)
    return namespace[name], namespace["messagebox"]


class DialogTests(unittest.TestCase):
    def native_owner_fixture(self):
        window = Mock(spec=tk.Toplevel)
        window.overrideredirect.return_value = False
        window.winfo_exists.return_value = window.winfo_viewable.return_value = True
        frame = [0x200]
        window.wm_frame.side_effect = lambda: hex(frame[0])
        window.winfo_id.return_value = 0x100
        app = Mock(_operator_hwnd=0x300, _background_window=None)
        callbacks = []
        app.after_idle.side_effect = callbacks.append
        owners = {}
        window.lift.side_effect = lambda: owners.update({frame[0]: 0x400})
        native = SimpleNamespace(
            IsWindow=Mock(return_value=True),
            GetParent=Mock(return_value=0x400),
            SetWindowLong=Mock(side_effect=lambda handle, _index, owner: owners.update({handle: owner})),
            GetWindowRect=Mock(side_effect=lambda handle: (0, 0, 1000, 800) if handle == 0x300 else (0, 0, 600, 400)),
            SetWindowPos=Mock(),
            GetWindow=Mock(side_effect=lambda handle, _flag: owners.get(handle, 0x400)),
            # Tk tools are overlapped windows: GA_ROOTOWNER can be the tool
            # itself even when GW_OWNER correctly names the Qt controller.
            GetAncestor=Mock(side_effect=lambda handle, _flag: handle),
        )
        constants = SimpleNamespace(GWL_HWNDPARENT=-8, GW_OWNER=4, GA_ROOTOWNER=3, HWND_TOP=0)
        return window, frame, OperatorDialogs(app), callbacks, native, constants

    def test_tool_owner_waits_for_final_tk_frame_and_reapplies_after_reopen(self):
        window, frame, relay, callbacks, native, constants = self.native_owner_fixture()
        with patch.dict(sys.modules, win32gui=native, win32con=constants), \
                patch("windows_theme.prepare_tool_window"), patch("windows_theme.style_native_tool_window"):
            relay._own_tool_window(SimpleNamespace(widget=window))
            relay._own_tool_window(SimpleNamespace(widget=window))
            native.SetWindowLong.assert_not_called()
            self.assertEqual(len(callbacks), 1)
            # Tk completes/recreates its wrapper after the Map notification.
            frame[0] = 0x201
            callbacks.pop(0)()
            native.SetWindowLong.assert_called_once_with(0x201, -8, 0x300)
            native.GetParent.assert_not_called()
            self.assertEqual(native.GetWindow(0x201, 4), 0x300)
            self.assertEqual(native.GetAncestor(0x201, 3), 0x201)
            frame[0] = 0x202
            relay._own_tool_window(SimpleNamespace(widget=window))
            callbacks.pop(0)()
            native.SetWindowLong.assert_called_with(0x202, -8, 0x300)
            self.assertEqual(native.GetWindow(0x202, 4), 0x300)
            self.assertEqual(native.GetAncestor(0x202, 3), 0x202)

    def test_tool_owner_failure_is_verified_after_tk_lift(self):
        window, _frame, relay, callbacks, native, constants = self.native_owner_fixture()
        native.SetWindowLong.side_effect = None
        with patch.dict(sys.modules, win32gui=native, win32con=constants), \
                patch("windows_theme.prepare_tool_window"), patch("windows_theme.style_native_tool_window"):
            relay._own_tool_window(SimpleNamespace(widget=window))
            self.assertTrue(callbacks)
            with self.assertRaisesRegex(RuntimeError, "Tool window ownership failed"):
                callbacks.pop(0)()
            window.lift.assert_called_once()
            window.focus_set.assert_called_once()

    def test_projector_and_borderless_windows_are_never_reparented(self):
        window = Mock(spec=tk.Toplevel)
        window.overrideredirect.return_value = True
        app = Mock(_operator_hwnd=123)
        OperatorDialogs(app)._own_tool_window(SimpleNamespace(widget=window))
        window.winfo_id.assert_not_called()

    def relay(self, answer):
        app = Mock()
        relay = OperatorDialogs(app)
        def wait(variable):
            self.assertIsNotNone(relay.state)
            # Late answers from another dialog must not complete this request.
            relay.respond({"id": relay.state["id"] - 1, "value": "yes"})
            variable.set.assert_not_called()
            relay.respond({"id": relay.state["id"], "value": answer})
            variable.set.assert_called_once_with(True)
        app.wait_variable.side_effect = wait
        return relay

    def test_new_show_keep_is_explicit_and_safe_default(self):
        relay = self.relay("no")
        with patch("operator_dialogs.tk.BooleanVar", return_value=Mock()):
            self.assertEqual(relay.message("New show", "Unsaved edits", "question", "yesno"), "no")
        self.assertIsNone(relay.state)

    def test_file_multiselect_and_cancel_keep_return_types(self):
        relay = self.relay(["C:/Media/one.mp4", "C:/Media/two.mp4"])
        with patch("operator_dialogs.tk.BooleanVar", return_value=Mock()):
            self.assertEqual(len(relay.files("open", multiple=True)), 2)
        relay = self.relay("")
        with patch("operator_dialogs.tk.BooleanVar", return_value=Mock()):
            self.assertEqual(relay.files("save"), "")

    def test_new_show_cancel_preserves_model_and_playback(self):
        method, messages = model_method("_new_show")
        messages.askyesno.return_value = False
        app = Mock(_saving_show=False, _opening_show=False, _starting_up=False,
                   _scenes=["original"], _show_path=Path("Original.json"))
        method(app)
        self.assertEqual(app._scenes, ["original"])
        self.assertEqual(app._show_path, Path("Original.json"))
        app._stop_deck_a.assert_not_called()
        app._stop_deck_b.assert_not_called()
        app._add_scene.assert_not_called()

    def test_new_show_confirm_stops_both_decks_before_empty_model(self):
        method, messages = model_method("_new_show")
        messages.askyesno.return_value = True
        app = Mock(_saving_show=False, _opening_show=False, _starting_up=False, _scenes=["original"])
        app._stop_deck_a.side_effect = lambda: self.assertEqual(app._scenes, ["original"])
        app._stop_deck_b.side_effect = lambda: self.assertEqual(app._scenes, ["original"])
        method(app)
        app._stop_deck_a.assert_called_once()
        app._stop_deck_b.assert_called_once()
        self.assertEqual(app._all_cues_a, [])
        self.assertEqual(app._all_cues_b, [])
        self.assertIsNone(app._show_path)
        app._add_scene.assert_called_once()

    def test_removing_scene_clears_old_visible_lists(self):
        method, messages = model_method("_remove_scene")
        messages.askyesno.return_value = True
        app = Mock(_selected_scene_idx=0, _scenes=[SimpleNamespace(name="One")])
        method(app)
        self.assertEqual(app._scenes, [])
        self.assertEqual(app._selected_scene_idx, -1)
        app._activate_scene.assert_called_once()

    def test_scene_change_cannot_remove_previous_scenes_checked_rows(self):
        method, _ = model_method("_activate_scene")
        app = Mock(_scenes=[], _selected_scene_idx=-1, _checked_cues_a={0}, _checked_cues_b={1},
                   _checkbox_mode_a=True, _checkbox_mode_b=True, _selected_a=0, _selected_b=1)
        method(app)
        self.assertEqual(app._checked_cues_a, set())
        self.assertEqual(app._checked_cues_b, set())
        self.assertFalse(app._checkbox_mode_a)
        self.assertFalse(app._checkbox_mode_b)
        self.assertEqual((app._selected_a, app._selected_b), (-1, -1))
        self.assertEqual(app._cues_a, [])
        self.assertEqual(app._cues_b, [])


if __name__ == "__main__":
    unittest.main()
