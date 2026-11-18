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

    @property
    def state(self):
        return self.pending[-1][0] if self.pending else None

    def request(self, spec):
        self.sequence += 1
        spec = dict(spec, id=self.sequence)
        ready = tk.BooleanVar(master=self.app, value=False)
        answer = [spec.get("cancel", "")]
        item = (spec, ready, answer)
        self.pending.append(item)
        try:
            self.app.wait_variable(ready)
        except tk.TclError:
            # The controller was closed while a dialog was waiting.
            pass
        finally:
            self.pending.remove(item)
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
        initial = folder / options.get("initialfile", "")
        filters = [f"{label} ({' '.join(pattern) if isinstance(pattern, (tuple, list)) else pattern})"
                   for label, pattern in options.get("filetypes", [("All files", "*")])]
        return self.request(dict(kind=kind, title=options.get("title", "Select file"),
                                 filters=filters, folder=folder.as_uri(), initial=initial.as_uri(),
                                 suffix=options.get("defaultextension", "").lstrip("."),
                                 multiple=bool(options.get("multiple")), cancel=""))

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

    def _own_tool_window(self, event):
        window = event.widget
        owner = getattr(self.app, "_operator_hwnd", None)
        if not owner or not isinstance(window, tk.Toplevel):
            return
        if window.overrideredirect() or window is getattr(self.app, "_background_window", None):
            return
        # Real owner semantics keep tool/editor windows with the Qt operator,
        # without the global TOPMOST flag that could cover the projector.
        import win32con
        import win32gui
        from windows_theme import prepare_tool_window, style_native_tool_window
        prepare_tool_window(window)
        handle = win32gui.GetParent(window.winfo_id()) or window.winfo_id()
        win32gui.SetWindowLong(handle, win32con.GWL_HWNDPARENT, owner)
        style_native_tool_window(handle)
        left, top, right, bottom = win32gui.GetWindowRect(owner)
        x, y, x2, y2 = win32gui.GetWindowRect(handle)
        width, height = x2 - x, y2 - y
        win32gui.SetWindowPos(handle, win32con.HWND_TOP,
                              left + max(0, (right - left - width) // 2),
                              top + max(0, (bottom - top - height) // 2), width, height, 0)
        window.lift()
        window.focus_set()
