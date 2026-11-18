"""Route controller dialogs to its visible Qt operator window.

The model keeps its synchronous Tk dialog API. A nested Tk event loop waits for
the answer while the controller transport and playback callbacks keep running.
Only the isolated operator worker installs these adapters.
"""
from pathlib import Path
import sys
import tkinter as tk
from tkinter import colorchooser, filedialog, messagebox


class OperatorDialogs:
    def __init__(self, app):
        self.app = app
        self.pending = []
        self.sequence = 0
        self._tool_owner_pending = set()
        self._owned_tools = set()
        self._tool_focus_pending = False
        self._deferred_file_action = None

    @property
    def state(self):
        return self.pending[-1][0] if self.pending else None

    def defer_file_action(self, name, value):
        # A native picker can re-enable its owner before Qt emits rejected or
        # accepted. Keep one following file action through that close boundary;
        # never collect playback/destructive actions or message-popup input.
        if (self.state and self.state.get("kind") in ("open", "save", "folder")
                and name in ("open", "save", "save_as", "a_add", "b_add")
                and self._deferred_file_action is None):
            self._deferred_file_action = (name, value)
            self.app._log(f"Operator file action deferred until native dialog closes: {name}")

    def take_deferred_file_action(self):
        if self.state:
            return None
        action = self._deferred_file_action
        self._deferred_file_action = None
        return action

    def request(self, spec):
        self.sequence += 1
        spec = dict(spec, id=self.sequence)
        ready = tk.BooleanVar(master=self.app, value=False)
        answer = [spec.get("cancel", "")]
        item = (spec, ready, answer)
        self.pending.append(item)
        self.app._log(f"Operator dialog request: id={spec['id']} kind={spec.get('kind')} title={spec.get('title')}")
        try:
            self.app.wait_variable(ready)
        except tk.TclError:
            # The controller was closed while a dialog was waiting.
            pass
        finally:
            self.pending.remove(item)
            self.app._log(f"Operator dialog finished: id={spec['id']}")
        return answer[0]

    def respond(self, value):
        if not isinstance(value, dict) or not self.pending:
            return
        spec, ready, answer = self.pending[-1]
        if value.get("id") != spec["id"]:
            return
        answer[0] = value.get("value", spec.get("cancel", ""))
        ready.set(True)

    def message(self, title=None, message=None, _icon=None, _type=None, **options):
        kind = options.get("type", _type) or "ok"
        values = {"ok": ("ok",), "okcancel": ("cancel", "ok"),
                  "yesno": ("no", "yes"), "yesnocancel": ("cancel", "no", "yes"),
                  "retrycancel": ("cancel", "retry"),
                  "abortretryignore": ("abort", "retry", "ignore")}.get(kind, ("ok",))
        labels = {"ok": "OK", "cancel": "Cancel", "yes": "Yes", "no": "No",
                  "retry": "Retry", "abort": "Abort", "ignore": "Ignore"}
        if title == "New show":
            labels.update(no="Keep show", yes="New show")
        return self.request(dict(kind="message", title=title or "Show Control", message=message or "",
                                 buttons=[dict(value=value, label=labels[value]) for value in values],
                                 cancel=values[0]))

    def files(self, kind, **options):
        folder = Path(options.get("initialdir") or Path.home()).resolve()
        media_picker = kind == "open" and options.get("title") in ("Add Media", "Add Visuals (Deck B)")
        if media_picker:
            remembered = getattr(self.app, "_last_media_folder", "")
            if remembered:
                try:
                    candidate = Path(remembered)
                    if candidate.is_dir():
                        folder = candidate.resolve()
                except (OSError, ValueError, RuntimeError):
                    pass  # A disconnected/removable media folder is optional.
        initial = folder / options.get("initialfile", "")
        filters = [f"{label} ({' '.join(pattern) if isinstance(pattern, (tuple, list)) else pattern})"
                   for label, pattern in options.get("filetypes", [("All files", "*")])]
        selected = self.request(dict(kind=kind, title=options.get("title", "Select file"),
                                 filters=filters, folder=folder.as_uri(), initial=initial.as_uri(),
                                 suffix=options.get("defaultextension", "").lstrip("."),
                                 multiple=bool(options.get("multiple")), cancel=""))
        if media_picker and selected:
            first = selected[0] if isinstance(selected, (list, tuple)) else selected
            try:
                selected_folder = Path(first).resolve().parent
                if selected_folder.is_dir():
                    self.app._last_media_folder = str(selected_folder)
                    self.app._save_persistent_settings()
            except (OSError, ValueError, RuntimeError):
                pass  # A vanished source must not prevent adding other files.
        return selected

    def color(self, color=None, **options):
        from PIL import ImageColor
        selected = self.request(dict(kind="color", title=options.get("title", "Select color"),
                                     color=options.get("initialcolor", color) or "#ffffff", cancel=""))
        return (ImageColor.getrgb(selected), selected) if selected else (None, None)

    def install(self):
        messagebox._show = self.message
        filedialog.askopenfilename = lambda **kw: self.files("open", **kw)
        filedialog.askopenfilenames = lambda **kw: self.files("open", **dict(kw, multiple=True))
        filedialog.asksaveasfilename = lambda **kw: self.files("save", **kw)
        filedialog.askdirectory = lambda **kw: self.files("folder", **kw)
        colorchooser.askcolor = self.color
        if sys.platform == "win32":
            self.app.bind_all("<Map>", self._own_tool_window, add="+")
            self.app.bind_all("<Unmap>", self._release_tool_focus, add="+")
            self.app.bind_all("<Destroy>", self._release_tool_focus, add="+")

    def _own_tool_window(self, event):
        window = event.widget
        owner = getattr(self.app, "_operator_hwnd", None)
        if not owner or not isinstance(window, tk.Toplevel):
            return
        if window.overrideredirect() or window is getattr(self.app, "_background_window", None):
            return
        if window in self._tool_owner_pending:
            return
        self._tool_owner_pending.add(window)

        def mapped():
            self._tool_owner_pending.discard(window)
            if window.winfo_exists() and window.winfo_viewable():
                self._attach_tool_owner(window)

        # Tk can finish/recreate its Windows wrapper after the Map notification.
        # Resolve and own that final frame after mapping, on every reopen too.
        self.app.after_idle(mapped)

    def _attach_tool_owner(self, window):
        # Real owner semantics keep tool/editor windows with the Qt operator,
        # without the global TOPMOST flag that could cover the projector.
        import win32con
        import win32gui
        from windows_theme import prepare_tool_window, style_native_tool_window
        owner = getattr(self.app, "_operator_hwnd", None)
        if not owner or not win32gui.IsWindow(owner):
            return
        prepare_tool_window(window)
        window.lift()
        window.focus_set()
        # wm frame is Tk's native decorated top-level HWND. GetParent is
        # ambiguous: on a top-level WS_POPUP it returns the owner instead.
        handle = int(str(window.wm_frame()), 0)
        win32gui.SetWindowLong(handle, win32con.GWL_HWNDPARENT, owner)
        style_native_tool_window(handle)
        left, top, right, bottom = win32gui.GetWindowRect(owner)
        x, y, x2, y2 = win32gui.GetWindowRect(handle)
        width, height = x2 - x, y2 - y
        win32gui.SetWindowPos(handle, win32con.HWND_TOP,
                              left + max(0, (right - left - width) // 2),
                              top + max(0, (bottom - top - height) // 2), width, height, 0)
        direct_owner = win32gui.GetWindow(handle, win32con.GW_OWNER)
        # GA_ROOTOWNER walks GetParent, which follows top-level owners only
        # for WS_POPUP. Tk's overlapped tools require the GW_OWNER contract.
        if direct_owner != owner:
            raise RuntimeError(f"Tool window ownership failed: window={handle}, owner={direct_owner}, "
                               f"expected={owner}")
        self._owned_tools.add(window)

    def _release_tool_focus(self, event):
        if event.widget not in self._owned_tools or self._tool_focus_pending:
            return
        self._tool_focus_pending = True

        def closed():
            self._tool_focus_pending = False
            closed_tools = set()
            for window in self._owned_tools:
                if not window.winfo_exists() or window.state() == "withdrawn":
                    closed_tools.add(window)
            self._owned_tools.difference_update(closed_tools)
            # Ignore minimize/remap and leave any other editor or Qt dialog in
            # charge of input. Child widgets and projector windows never enter
            # _owned_tools, so their lifecycle cannot activate the operator.
            if not closed_tools or self._owned_tools or self.pending:
                return
            import win32gui
            owner = getattr(self.app, "_operator_hwnd", None)
            if (owner and win32gui.IsWindow(owner) and win32gui.IsWindowVisible(owner)
                    and win32gui.GetForegroundWindow() != owner):
                # The Tk worker received Escape/the close click, so Windows
                # permits it to return activation to the separate Qt process.
                win32gui.SetForegroundWindow(owner)

        # Tk's withdraw/destroy can activate another native window. Wait until
        # that teardown has finished, then explicitly return to the operator.
        self.app.after_idle(closed)
