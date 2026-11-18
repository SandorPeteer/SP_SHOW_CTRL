"""Small reusable controls for the show operator's workspace."""
from functools import partial
import tkinter as tk
from tkinter import ttk

from PIL import Image, ImageDraw, ImageTk
from windows_theme import CYAN, EDGE, LIVE_ROW, MINT, NEXT_ROW, SELECTED, TEXT, VIOLET, pixels


_ICON_PATHS = {
    "minimize": (((5, 11), (15, 11)),),
    "maximize": (((5, 5), (15, 5), (15, 15), (5, 15), (5, 5)),),
    "close": (((5, 5), (15, 15)), ((15, 5), (5, 15))),
    "waveform": (((2, 8), (2, 12)), ((6, 5), (6, 15)), ((10, 2), (10, 18)),
                 ((14, 6), (14, 14)), ((18, 8), (18, 12))),
    "screen": (((2, 3), (18, 3), (18, 14), (2, 14), (2, 3)), ((10, 14), (10, 18)), ((6, 18), (14, 18))),
    "slides": (((10, 2), (18, 6), (10, 10), (2, 6), (10, 2)),
               ((2, 10), (10, 14), (18, 10)), ((2, 14), (10, 18), (18, 14))),
    "show": (((2, 3), (18, 3), (18, 14), (2, 14), (2, 3)), ((10, 14), (10, 18)), ((6, 18), (14, 18)),
             ((8, 6), (13, 8.5), (8, 11), (8, 6))),
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
    "audio": (((9, 14), (9, 3), (16, 6)), ((9, 14), (7, 12), (4, 13), (4, 16), (7, 17), (9, 14))),
    "video": (((3, 4), (17, 4), (17, 16), (3, 16), (3, 4)), ((6, 4), (6, 16)), ((14, 4), (14, 16)),
              ((3, 8), (6, 8)), ((3, 12), (6, 12)), ((14, 8), (17, 8)), ((14, 12), (17, 12))),
    "image": (((3, 4), (17, 4), (17, 16), (3, 16), (3, 4)), ((3, 14), (8, 10), (11, 13), (14, 8), (17, 11))),
    "ppt": (((2, 4), (18, 4)), ((4, 4), (4, 13), (16, 13), (16, 4)), ((6, 17), (10, 13), (14, 17))),
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

    @staticmethod
    def _raster(name, color, size):
        source = Image.new("RGBA", (60, 60))
        draw = ImageDraw.Draw(source)
        for path in _ICON_PATHS[name]:
            draw.line([(x * 3, y * 3) for x, y in path], fill=color, width=5, joint="curve")
        return source.resize((size, size), Image.Resampling.LANCZOS)

    def image(self, name, color=TEXT, size=19):
        size = pixels(self.root, size)
        key = (name, color, size)
        if key not in self.images:
            self.images[key] = ImageTk.PhotoImage(self._raster(name, color, size), master=self.root)
        return self.images[key]

    def row_image(self, kind, autoplay, emphasis=None):
        key = ("cue", kind, autoplay, emphasis)
        if key not in self.images:
            size = pixels(self.root, 18)
            strip = Image.new("RGBA", (pixels(self.root, 46), pixels(self.root, 22)))
            accent = {"selected": CYAN, "playing": MINT, "next": VIOLET}.get(emphasis)
            if accent:
                ImageDraw.Draw(strip).rectangle((0, 0, pixels(self.root, 2) - 1, strip.height), fill=accent)
            for x, name, color in ((6, "play", accent or (MINT if autoplay else "#798d9e")),
                                   (27, kind if kind in _ICON_PATHS else "video", TEXT)):
                strip.alpha_composite(self._raster(name, color, size), (pixels(self.root, x), pixels(self.root, 2)))
            self.images[key] = ImageTk.PhotoImage(strip, master=self.root)
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


class ActivityIndicator(tk.Canvas):
    """Small motion indicator for work without a measurable completion percentage."""
    def __init__(self, parent, background):
        super().__init__(parent, width=52, height=5, bg=background, bd=0, highlightthickness=0)
        self.create_line(2, 2, 50, 2, width=4, capstyle="round", fill="#284256")
        self.segment = self.create_line(2, 2, 16, 2, width=4, capstyle="round", fill=CYAN)
        self.phase = 0
        self._job = None
        self.bind("<Destroy>", lambda _event: self.stop(), add="+")

    def start(self):
        if self._job is None:
            self._advance()

    def stop(self):
        if self._job is not None:
            self.after_cancel(self._job)
            self._job = None

    def _advance(self):
        self.phase = (self.phase + 1) % 40
        offset = round(34 * (1 - abs(1 - self.phase / 20)))
        self.coords(self.segment, 2 + offset, 2, 16 + offset, 2)
        self._job = self.after(32, self._advance)


class ScrollIndicator:
    """A transient thumb in the list's inset. It reserves no space when shown."""
    FADE = ("#4a5c70", "#3a4b5e", EDGE, "#232f3e", "#17212c")

    def __init__(self, host, widget):
        self.host = host
        self.thumb = tk.Frame(host, bg=self.FADE[0], bd=0, highlightthickness=0, takefocus=False)
        self.first = 0.0
        self.job = None
        widget.configure(yscrollcommand=self.update)
        host.bind("<Destroy>", self.close, add="+")

    def update(self, first, last):
        first, last = float(first), float(last)
        moved = abs(first - self.first) > 0.000001
        self.first = first
        if last - first >= 0.99999:
            self.hide()
            return
        if not moved:
            return
        self.close()
        height = max(1, self.host.winfo_height() - 8)
        length = min(height, max(16, round(height * (last - first))))
        y = min(height - length, round(height * first)) + 4
        self.thumb.configure(bg=self.FADE[0])
        self.thumb.place(relx=1.0, x=-3, y=y, width=3, height=length)
        self.thumb.lift()
        self.job = self.host.after(330, self.fade, 1)

    def fade(self, stage):
        self.job = None
        if stage >= len(self.FADE):
            self.hide()
            return
        self.thumb.configure(bg=self.FADE[stage])
        self.job = self.host.after(32, self.fade, stage + 1)

    def close(self, _event=None):
        if self.job is not None:
            self.host.after_cancel(self.job)
            self.job = None

    def hide(self):
        self.close()
        self.thumb.place_forget()


class CueList(ttk.Treeview):
    """A flat rendition of the existing cue table, with two row icons."""
    def __init__(self, parent, icons):
        self.icons = icons
        super().__init__(parent, style="Cue.Treeview", show="tree", selectmode="browse", height=2,
                         columns=("checkbox", "auto", "idx", "kind", "name", "duration"),
                         displaycolumns=("checkbox", "name", "duration"))
        icon_width = pixels(parent, 52)
        self.column("#0", width=icon_width, minwidth=icon_width, stretch=False)
        self.column("checkbox", width=0, minwidth=0, stretch=False, anchor="center")
        self.column("name", width=110, minwidth=40)
        self.column("duration", width=72, minwidth=62, stretch=False, anchor="e")
        self.tag_configure("playing", background=LIVE_ROW, foreground=MINT)
        self.tag_configure("next", background=NEXT_ROW, foreground=VIOLET)
        self.emphasis = {}
        self.scroll_indicator = ScrollIndicator(parent, self)

    def insert(self, parent, index, iid=None, **kwargs):
        if "values" in kwargs:
            self._icons_for_values(kwargs)
        return super().insert(parent, index, iid, **kwargs)

    def item(self, item, option=None, **kwargs):
        if "values" in kwargs:
            self._icons_for_values(kwargs, self.emphasis.get(item))
        return super().item(item, option, **kwargs)

    def delete(self, *items):
        for item in items:
            self.emphasis.pop(item, None)
        super().delete(*items)

    def _icons_for_values(self, options, emphasis=None):
        values = options["values"]
        options["image"] = self.icons.row_image(values[3], bool(values[1]), emphasis)

    def set_emphasis(self, playing, following):
        updated = {iid: "selected" for iid in self.selection()}
        if following is not None:
            updated[following] = "next"
        if playing is not None:
            updated[playing] = "playing"
        if updated == self.emphasis:
            return
        for iid in self.emphasis.keys() | updated.keys():
            state = updated.get(iid)
            if state == self.emphasis.get(iid) or not self.exists(iid):
                continue
            row = super().item(iid)
            values = row["values"]
            tags = [tag for tag in row["tags"] if tag not in ("playing", "next")]
            if state in ("playing", "next"):
                tags.append(state)
            super().item(iid, tags=tags, image=self.icons.row_image(values[3], bool(values[1]), state))
        self.emphasis = updated

    def autoswitch_hit(self, event):
        return 0 <= event.x < pixels(self, 26) and self.identify_column(event.x) == "#0"


class SceneList(tk.Text):
    """Scrollable, word-wrapped scene names with a single active row."""
    def __init__(self, parent, changed, **kwargs):
        super().__init__(parent, wrap="word", state="disabled", width=1, height=1,
                         selectborderwidth=0, exportselection=False, cursor="arrow", takefocus=True,
                         padx=9, pady=4, spacing1=6, spacing3=6, **kwargs)
        self.changed = changed
        self.row_count = 0
        self.selected = -1
        self.tag_configure("scene_row", lmargin2=24)
        self.tag_configure("next_scene", background=NEXT_ROW, foreground=VIOLET)
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
            self.insert("end", f"{index + 1:02d}  ", ("scene_row", tag))
            self.insert("end", " ".join(name.splitlines()) + "\n", ("scene_row",))
        self.tag_raise("next_scene")
        self.tag_raise("current_scene")
        self.configure(state="disabled")

    def select(self, index, *, reveal=True):
        self.tag_remove("current_scene", "1.0", "end")
        self.tag_remove("next_scene", "1.0", "end")
        self.selected = index if 0 <= index < self.row_count else -1
        if self.selected >= 0:
            start = f"{self.selected + 1}.0"
            self.tag_add("current_scene", start, f"{self.selected + 2}.0")
            if self.selected + 1 < self.row_count:
                self.tag_add("next_scene", f"{self.selected + 2}.0", f"{self.selected + 3}.0")
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
