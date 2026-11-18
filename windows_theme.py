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
WAVE_UNPLAYED = "#346e88"


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
    style.configure("ShowTitle.TLabel", background=SHELL, foreground=MUTED, font=("Segoe UI", 10))
    style.configure("SignalTitle.TLabel", font=("Segoe UI", 10, "bold"))
    style.configure("SignalCaption.TLabel", foreground=MUTED, font=("Segoe UI", 9))
    style.configure("Activity.TFrame", background="#122331", relief="solid", borderwidth=1)
    style.configure("Activity.TLabel", background="#122331")
    style.configure("Alert.TLabel", background="#122331")
    style.configure("CardTitle.TLabel", font=("Segoe UI", 10, "bold"))
    style.configure("Tooltip.TLabel", background="#304158", foreground=foreground)
    style.configure("Status.TLabel", foreground=MUTED)
    style.configure("LiveMonitor.TLabel", foreground=MINT, font=("Segoe UI", 9, "bold"))
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
    _apply_rounded_surfaces(app, style)


def _apply_rounded_surfaces(app, style):
    from PIL import Image, ImageDraw, ImageTk

    pictures = {}

    def surface(fill, edge, radius):
        key = (fill, edge, radius)
        if key not in pictures:
            image = Image.new("RGBA", (72, 72))
            ImageDraw.Draw(image).rounded_rectangle((0, 0, 71, 71), radius=radius * 3,
                                                     fill=fill, outline=edge, width=3)
            pictures[key] = ImageTk.PhotoImage(image.resize((24, 24), Image.Resampling.LANCZOS), master=app)
        return pictures[key]

    for name, fill, edge, radius in (
            ("WorkspaceCard", PANEL, EDGE, 6),
            ("Activity", "#122331", "#26435a", 5)):
        element = name + ".surface"
        style.element_create(element, "image", surface(fill, edge, radius), border=7, sticky="nsew")
        style.layout(name + ".TFrame", [(element, {"sticky": "nswe"})])

    for name, fill, edge, fg, padding in (
            ("Icon", BUTTON, EDGE, TEXT, (6, 5)),
            ("HeaderIcon", BUTTON, EDGE, TEXT, (3, 3)),
            ("SlideNav", BUTTON, EDGE, VIOLET, (5, 5)),
            ("Accent", CYAN, CYAN, INK, (8, 5)),
            ("Transport", BUTTON, EDGE, TEXT, (6, 5)),
            ("Playing", AMBER, AMBER, INK, (8, 5)),
            ("ReadyPlay", CYAN, CYAN, INK, (8, 5)),
            ("Show", MINT, MINT, INK, (8, 5)),
            ("Stop", STOP_BG, "#754559", CORAL, (6, 5)),
            ("Repeat", REPEAT_BG, "#655183", VIOLET, (6, 5)),
            ("Window", SHELL, SHELL, MUTED, (4, 5)),
            ("CloseWindow", SHELL, SHELL, MUTED, (4, 5)),
    ):
        element = name + ".surface"
        active = STOP_BG if name == "CloseWindow" else "#293b4f" if fill in (BUTTON, SHELL) else fill
        style.element_create(element, "image",
                             surface(fill, edge, 5),
                             ("focus", surface(fill, CYAN, 5)),
                             ("pressed", surface(active, CYAN, 5)),
                             ("active", surface(active, edge, 5)),
                             border=6, sticky="nsew")
        role = name + ".TButton"
        style.layout(role, [(element, {"sticky": "nswe", "children": [
            ("Button.padding", {"sticky": "nswe", "children": [
                ("Button.label", {"sticky": "nswe"})
            ]})
        ]})])
        style.configure(role, padding=padding, background=fill, foreground=fg,
                        font=("Segoe UI", 10, "bold"))
        style.map(role, foreground=[("disabled", MUTED), ("!disabled", fg)])

    style.layout("TMenubutton", [("Icon.surface", {"sticky": "nswe", "children": [
        ("Menubutton.indicator", {"side": "right", "sticky": ""}),
        ("Menubutton.padding", {"side": "left", "sticky": "nswe", "children": [
            ("Menubutton.label", {"sticky": "nswe"}),
        ]}),
    ]})])
    style.configure("TMenubutton", padding=(8, 5), arrowcolor=MUTED)
    app._rounded_theme_images = pictures


def controller_chrome(app, drag_surfaces, zoom_button):
    """Remove the system caption while preserving native taskbar and resizing."""
    from ctypes import WinDLL, byref, c_int, c_uint32, c_void_p, sizeof
    import win32con
    import win32gui

    hwnd = win32gui.GetAncestor(app.winfo_id(), 2)  # GA_ROOT
    if not hwnd:
        return
    style = win32gui.GetWindowLong(hwnd, win32con.GWL_STYLE)
    win32gui.SetWindowLong(hwnd, win32con.GWL_STYLE, style & ~win32con.WS_CAPTION)
    extended = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
    win32gui.SetWindowLong(hwnd, win32con.GWL_EXSTYLE, extended | win32con.WS_EX_APPWINDOW)
    win32gui.SetWindowPos(hwnd, 0, 0, 0, 0, 0, win32con.SWP_NOSIZE | win32con.SWP_NOMOVE |
                          win32con.SWP_NOZORDER | win32con.SWP_NOACTIVATE | win32con.SWP_FRAMECHANGED)

    attribute = WinDLL("dwmapi").DwmSetWindowAttribute
    attribute.argtypes = (c_void_p, c_uint32, c_void_p, c_uint32)
    enabled = c_int(1)
    if attribute(hwnd, 20, byref(enabled), sizeof(enabled)):
        attribute(hwnd, 19, byref(enabled), sizeof(enabled))
    border = c_uint32(0xFFFFFFFE)  # DWMWA_COLOR_NONE
    attribute(hwnd, 34, byref(border), sizeof(border))
    corner = c_uint32(2)  # DWMWCP_ROUND
    attribute(hwnd, 33, byref(corner), sizeof(corner))

    def drag(event):
        win32gui.ReleaseCapture()
        xy = (event.x_root & 0xFFFF) | ((event.y_root & 0xFFFF) << 16)
        win32gui.PostMessage(hwnd, win32con.WM_NCLBUTTONDOWN, win32con.HTCAPTION, xy)

    def maximize(_event=None):
        mode = win32con.SW_RESTORE if win32gui.IsZoomed(hwnd) else win32con.SW_MAXIMIZE
        win32gui.ShowWindow(hwnd, mode)
        return "break"

    zoom_button.configure(command=maximize)
    for widget in drag_surfaces:
        widget.bind("<ButtonPress-1>", drag, add="+")
        widget.bind("<Double-Button-1>", maximize, add="+")
