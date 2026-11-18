"""Small reusable controls for the show operator's workspace."""
from functools import partial
import tkinter as tk
from tkinter import ttk

from PIL import Image, ImageDraw, ImageTk
from windows_theme import SELECTED, TEXT


_ICON_PATHS = {
    "fullscreen": (((3, 8), (3, 3), (8, 3)), ((12, 3), (17, 3), (17, 8)),
                   ((17, 12), (17, 17), (12, 17)), ((8, 17), (3, 17), (3, 12))),
    "restore": (((3, 8), (8, 8), (8, 3)), ((12, 3), (12, 8), (17, 8)),
                ((17, 12), (12, 12), (12, 17)), ((8, 17), (8, 12), (3, 12))),
    "open": (((2, 16), (2, 5), (8, 5), (10, 7), (18, 7), (16, 16), (2, 16)),),
    "save": (((3, 3), (14, 3), (17, 6), (17, 17), (3, 17), (3, 3)),
             ((6, 3), (6, 8), (13, 8), (13, 3)), ((6, 17), (6, 12), (14, 12), (14, 17))),
    "settings": (((3, 5), (17, 5)), ((3, 10), (17, 10)), ((3, 15), (17, 15)),
                 ((7, 3), (7, 7)), ((13, 8), (13, 12)), ((8, 13), (8, 17))),
    "add": (((4, 10), (16, 10)), ((10, 4), (10, 16))),
    "remove": (((4, 10), (16, 10)),),
    "up": (((5, 12), (10, 7), (15, 12)),),
    "down": (((5, 8), (10, 13), (15, 8)),),
    "previous": (((12, 4), (6, 10), (12, 16)),),
    "next": (((8, 4), (14, 10), (8, 16)),),
    "stop": (((5, 5), (15, 5), (15, 15), (5, 15), (5, 5)),),
    "play": (((6, 3), (16, 10), (6, 17), (6, 3)),),
    "pause": (((7, 4), (7, 16)), ((13, 4), (13, 16))),
    "repeat": (((16, 7), (13, 4), (6, 4), (3, 7), (3, 10)), ((13, 7), (16, 7), (16, 4)),
               ((4, 13), (7, 16), (14, 16), (17, 13), (17, 10)), ((7, 13), (4, 13), (4, 16))),
    "edit": (((4, 13), (13, 4), (16, 7), (7, 16), (3, 17), (4, 13)),),
    "download": (((10, 3), (10, 13)), ((5, 9), (10, 14), (15, 9)), ((4, 17), (16, 17))),
}


class OperatorIcons:
    def __init__(self, root):
        self.root = root
        self.images = {}

    def image(self, name, color=TEXT, size=19):
        key = (name, color, size)
        if key not in self.images:
            source = Image.new("RGBA", (60, 60))
            draw = ImageDraw.Draw(source)
            for path in _ICON_PATHS[name]:
                draw.line([(x * 3, y * 3) for x, y in path], fill=color, width=5, joint="curve")
            self.images[key] = ImageTk.PhotoImage(source.resize((size, size), Image.Resampling.LANCZOS), master=self.root)
        return self.images[key]

    def button(self, parent, icon, label, command, *, visible_label=False, style="Icon.TButton", color=TEXT, size=19):
        button = ttk.Button(parent, text=label, image=self.image(icon, color, size), command=command,
                            compound="left" if visible_label else "none", style=style)
        add_tooltip(button, label)
        return button


def add_tooltip(widget, label):
    job = popup = None

    def show():
        nonlocal job, popup
        job = None
        popup = tk.Toplevel(widget)
        popup.wm_overrideredirect(True)
        x = widget.winfo_rootx()
        y = widget.winfo_rooty() + widget.winfo_height() + 5
        popup.wm_geometry(f"+{x}+{y}")
        ttk.Label(popup, text=label, padding=(8, 5), style="Tooltip.TLabel").pack()

    def hide(_event=None):
        nonlocal job, popup
        if job is not None:
            widget.after_cancel(job)
            job = None
        if popup is not None:
            popup.destroy()
            popup = None

    def schedule(_event):
        nonlocal job
        hide()
        job = widget.after(550, show)

    widget.bind("<Enter>", schedule, add="+")
    for event in ("<Leave>", "<ButtonPress>", "<Destroy>"):
        widget.bind(event, hide, add="+")


class SceneList(tk.Text):
    """Scrollable, word-wrapped scene names with a single active row."""
    def __init__(self, parent, changed, **kwargs):
        super().__init__(parent, wrap="word", state="disabled", width=1, height=1,
                         selectborderwidth=0, exportselection=False, cursor="arrow", takefocus=True,
                         padx=9, pady=4, spacing1=6, spacing3=6, **kwargs)
        self.changed = changed
        self.row_count = 0
        self.selected = -1
        self.tag_configure("current_scene", background=SELECTED, foreground=TEXT)
        self.bind("<Button-1>", self._mouse_select)
        self.bind("<Double-1>", lambda _e: "break")
        for key, offset in (("Up", -1), ("Down", 1), ("Home", 0), ("End", 0)):
            self.bind(f"<{key}>", partial(self._key_select, key, offset))

    def set_rows(self, rows):
        self.configure(state="normal")
        self.delete("1.0", "end")
        self.row_count = len(rows)
        self.selected = -1
        for index, (name, color) in enumerate(rows):
            tag = f"scene_color_{index}"
            try:
                self.tag_configure(tag, foreground=color or "#c4d0e0")
            except tk.TclError:
                self.tag_configure(tag, foreground="#c4d0e0")
            self.insert("end", f"{index + 1:02d}  ", tag)
            self.insert("end", " ".join(name.splitlines()) + "\n")
        self.tag_raise("current_scene")
        self.configure(state="disabled")

    def select(self, index, *, reveal=True):
        self.tag_remove("current_scene", "1.0", "end")
        self.selected = index if 0 <= index < self.row_count else -1
        if self.selected >= 0:
            start = f"{self.selected + 1}.0"
            self.tag_add("current_scene", start, f"{self.selected + 2}.0")
            if reveal:
                self.see(start)

    def _choose(self, index):
        if 0 <= index < self.row_count and index != self.selected:
            self.select(index)
            self.changed(index)
        return "break"

    def _mouse_select(self, event):
        self.focus_set()
        index = int(self.index(f"@{event.x},{event.y}").split(".")[0]) - 1
        return self._choose(index)

    def _key_select(self, key, offset, _event):
        last = self.row_count - 1
        if last < 0:
            return "break"
        index = 0 if key == "Home" else last if key == "End" else min(last, max(0, self.selected + offset))
        return self._choose(index)


def tool_window(root, title):
    window = tk.Toplevel(root)
    window.withdraw()
    window.title(title)
    window.transient(root)
    window.resizable(True, False)
    window.minsize(440, 1)
    window.protocol("WM_DELETE_WINDOW", window.withdraw)
    window.bind("<Escape>", lambda _event: window.withdraw())
    tabs = ttk.Notebook(window, style="Deck.TNotebook")
    tabs.pack(fill="both", expand=True, padx=12, pady=12)
    return window, tabs


def show_tools(window, tabs, tab=None):
    if tab is not None:
        tabs.select(tab)
    window.deiconify()
    window.lift()
