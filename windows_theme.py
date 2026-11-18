"""Use a colorable ttk theme so Windows paints both tab text and backgrounds."""
from tkinter import ttk

SHELL = "#0b1017"
PANEL = "#131c27"
FIELD = "#0d141e"
EDGE = "#2b3949"
TEXT = "#ecf5ff"
MUTED = "#a9bbcd"
BUTTON = "#1c2938"
SELECTED = "#192f40"
CYAN = "#59d8ff"
MINT = "#57f3b9"
AMBER = "#ffd27c"
CORAL = "#ff748b"
VIOLET = "#c3a3ff"
INK = "#07131d"
STOP_BG = "#331e2a"
REPEAT_BG = "#2a2040"


def apply_windows_theme(app):
    background = PANEL
    field = FIELD
    foreground = TEXT
    disabled = "#b8b8b8"
    raised = BUTTON
    selected = SELECTED
    border = EDGE
    style = ttk.Style(app)
    style.theme_use("clam")
    app.configure(background=SHELL)
    for option, color in (("*Background", background), ("*Foreground", foreground),
                          ("*insertBackground", foreground), ("*selectBackground", selected),
                          ("*selectForeground", foreground), ("*disabledForeground", disabled)):
        app.option_add(option, color)
    style.configure(".", background=background, foreground=foreground,
                    bordercolor=border, lightcolor=background, darkcolor=background,
                    troughcolor=field, font=("Segoe UI", 10))
    style.configure("Shell.TFrame", background=SHELL)
    style.configure("WorkspaceCard.TFrame", background=PANEL, relief="solid", borderwidth=1)
    style.layout("Cue.Treeview", [("Treeview.treearea", {"sticky": "nswe"})])
    style.layout("Cue.Treeview.Item", [("Treeitem.padding", {"sticky": "nswe", "children": [
        ("Treeitem.image", {"side": "left", "sticky": ""}),
        ("Treeitem.text", {"side": "left", "sticky": ""}),
    ]})])
    style.configure("Cue.Treeview", rowheight=33, font=("Segoe UI", 11))
    style.map("Cue.Treeview", background=[("selected", SELECTED)], foreground=[("selected", TEXT)])
    style.configure("Activity.TLabel", background=SHELL, foreground=CYAN, font=("Segoe UI", 9))
    style.configure("Alert.TLabel", background=SHELL, foreground=CORAL, font=("Segoe UI", 9))
    style.configure("AppTitle.TLabel", background=SHELL, font=("Segoe UI", 12, "bold"))
    style.configure("Activity.Horizontal.TProgressbar", background=CYAN, troughcolor=FIELD, thickness=4)
    style.configure("ShowTitle.TLabel", background=SHELL, font=("Segoe UI", 12, "bold"))
    style.configure("CardTitle.TLabel", font=("Segoe UI", 13, "bold"))
    style.configure("Tooltip.TLabel", background="#304158", foreground=foreground)
    style.configure("Status.TLabel", foreground=MUTED)
    style.configure("LiveMonitor.TLabel", foreground=MINT, font=("Segoe UI", 10, "bold"))
    style.configure("NextMonitor.TLabel", foreground=VIOLET, font=("Segoe UI", 10, "bold"))
    style.map(".", foreground=[("disabled", disabled)])
    style.configure("TButton", background=raised, padding=(8, 4), relief="flat")
    style.map("TButton", background=[("disabled", background), ("pressed", selected), ("active", "#3b4d68")],
              foreground=[("disabled", disabled), ("!disabled", foreground)])
    style.configure("Icon.TButton", padding=(5, 4))
    style.configure("HeaderIcon.TButton", padding=(3, 3))
    style.configure("SlideNav.TButton", padding=(6, 4))
    style.configure("Accent.TButton", background=CYAN, foreground=INK, padding=(9, 5), font=("Segoe UI", 10, "bold"))
    style.map("Accent.TButton", background=[("pressed", "#43b8e1"), ("active", "#8de5ff")],
              foreground=[("!disabled", INK)])
    for widget in ("TCheckbutton", "TRadiobutton"):
        style.map(widget, background=[("active", background)],
                  indicatorbackground=[("selected", selected), ("!selected", field)],
                  indicatorforeground=[("disabled", disabled), ("!disabled", foreground)])
    for widget in ("TEntry", "TCombobox", "TSpinbox"):
        style.configure(widget, fieldbackground=field, foreground=foreground,
                        insertcolor=foreground, arrowcolor=foreground)
        style.map(widget, fieldbackground=[("disabled", background), ("readonly", field)],
                  foreground=[("disabled", disabled), ("!disabled", foreground)],
                  background=[("active", raised), ("!active", raised)],
                  selectbackground=[("!disabled", selected)], selectforeground=[("!disabled", foreground)])
    app.option_add("*TCombobox*Listbox.background", field)
    app.option_add("*TCombobox*Listbox.foreground", foreground)
    style.configure("Treeview", background=field, fieldbackground=field, foreground=foreground, rowheight=27)
    style.map("Treeview", background=[("selected", selected)], foreground=[("selected", foreground)])
    style.configure("Treeview.Heading", background=raised, foreground=foreground, relief="flat", padding=(4, 5))
    style.map("Treeview.Heading", background=[("active", "#4a4e55")], foreground=[("active", foreground)])
    style.configure("TScrollbar", background=raised, arrowcolor=foreground)
    style.map("TScrollbar", background=[("active", "#666a72")])
    style.configure("TScale", background=raised)
    style.configure("TProgressbar", background=selected)
    style.configure("Deck.TNotebook", background=background, borderwidth=0, padding=0)
    style.configure("Deck.TNotebook.Tab", background=raised, foreground=foreground, padding=(10, 5))
    style.map("Deck.TNotebook.Tab",
              background=[("disabled", background), ("selected", selected), ("active", "#4a4e55")],
              foreground=[("disabled", disabled), ("selected", "#ffffff"), ("active", "#ffffff")])


def dark_window_frame(app):
    """Keep native Windows resize and window commands with matching dark chrome."""
    from ctypes import WinDLL, byref, c_int, c_uint32, c_void_p, sizeof

    ancestor = WinDLL("user32").GetAncestor
    ancestor.argtypes = (c_void_p, c_uint32)
    ancestor.restype = c_void_p
    window = ancestor(app.winfo_id(), 2)  # GA_ROOT
    if not window:
        return
    attribute = WinDLL("dwmapi").DwmSetWindowAttribute
    attribute.argtypes = (c_void_p, c_uint32, c_void_p, c_uint32)
    enabled = c_int(1)
    if attribute(window, 20, byref(enabled), sizeof(enabled)):
        attribute(window, 19, byref(enabled), sizeof(enabled))
    for key, color in ((34, EDGE), (35, SHELL), (36, TEXT)):
        r, g, b = bytes.fromhex(color[1:])
        value = c_uint32(r | (g << 8) | (b << 16))
        attribute(window, key, byref(value), sizeof(value))
