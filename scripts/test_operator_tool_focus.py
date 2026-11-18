"""Tool close returns native keyboard input to the Qt operator, never output."""
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from operator_dialogs import OperatorDialogs


class ToolFocusTests(unittest.TestCase):
    def setUp(self):
        self.callbacks = []
        self.app = Mock(_operator_hwnd=0x300)
        self.app.after_idle.side_effect = self.callbacks.append
        self.relay = OperatorDialogs(self.app)
        self.tool = Mock()
        self.tool.winfo_exists.return_value = True
        self.tool.state.return_value = "withdrawn"
        self.relay._owned_tools.add(self.tool)
        self.native = SimpleNamespace(
            IsWindow=Mock(return_value=True),
            IsWindowVisible=Mock(return_value=True),
            GetForegroundWindow=Mock(return_value=0x900),  # mpv/native fallback
            SetForegroundWindow=Mock(),
        )
        self.patches = patch.dict(sys.modules, win32gui=self.native)
        self.patches.start()
        self.addCleanup(self.patches.stop)

    def close_event(self, tool=None):
        self.relay._release_tool_focus(SimpleNamespace(widget=tool or self.tool))

    def drain(self):
        while self.callbacks:
            self.callbacks.pop(0)()

    def test_withdraw_returns_focus_after_native_teardown(self):
        self.close_event()
        self.native.SetForegroundWindow.assert_not_called()
        self.drain()
        self.native.SetForegroundWindow.assert_called_once_with(0x300)
        self.assertNotIn(self.tool, self.relay._owned_tools)

    def test_destroy_and_unmap_coalesce_and_reopen_can_close_again(self):
        self.tool.winfo_exists.return_value = False
        self.close_event()
        self.close_event()
        self.assertEqual(len(self.callbacks), 1)
        self.drain()
        self.relay._owned_tools.add(self.tool)  # the next successful Map attach
        self.tool.winfo_exists.return_value = True
        self.close_event()
        self.drain()
        self.assertEqual(self.native.SetForegroundWindow.call_count, 2)

    def test_minimize_or_remap_does_not_steal_focus(self):
        for state in ("iconic", "normal"):
            with self.subTest(state=state):
                self.tool.state.return_value = state
                self.close_event()
                self.drain()
                self.native.SetForegroundWindow.assert_not_called()

    def test_other_editor_or_qt_dialog_keeps_input(self):
        other = Mock()
        other.winfo_exists.return_value = True
        other.state.return_value = "normal"
        self.relay._owned_tools.add(other)
        self.close_event()
        self.drain()
        self.native.SetForegroundWindow.assert_not_called()
        self.relay._owned_tools = {self.tool}
        self.relay.pending = [object()]
        self.close_event()
        self.drain()
        self.native.SetForegroundWindow.assert_not_called()

    def test_children_and_projector_events_never_activate_operator(self):
        self.close_event(Mock())
        self.assertEqual(self.callbacks, [])
        self.native.SetForegroundWindow.assert_not_called()

    def test_closed_or_hidden_operator_is_not_activated(self):
        for property_name in ("IsWindow", "IsWindowVisible"):
            with self.subTest(property_name=property_name):
                self.relay._owned_tools.add(self.tool)
                check = getattr(self.native, property_name)
                check.return_value = False
                self.close_event()
                self.drain()
                self.native.SetForegroundWindow.assert_not_called()
                check.return_value = True


if __name__ == "__main__":
    unittest.main()
