"""Use a colorable ttk theme so Windows paints both tab text and backgrounds."""
import sys
from tkinter import ttk

SHELL = "#0d131b"
PANEL = "#121d28"
FIELD = "#0e1722"
EDGE = "#2b3949"
TEXT = "#ecf5ff"
MUTED = "#a9bbcd"
BUTTON = "#1c2938"
SELECTED = "#193747"
CYAN = "#59d8ff"
MINT = "#57f3b9"
LIVE_ROW = "#15382f"
NEXT_ROW = "#2b2541"
AMBER = "#ffd27c"
CORAL = "#ff748b"
VIOLET = "#c3a3ff"
INK = "#07131d"
STOP_BG = "#331e2a"
REPEAT_BG = "#2a2040"
WAVE_UNPLAYED = "#346e88"


def configure_source_dpi():
    """Match the executable manifest when running player.py with Python."""
    if sys.platform == "win32" and not getattr(sys, "frozen", False):
        from ctypes import WinDLL
        WinDLL("shcore").SetProcessDpiAwareness(1)


def pixels(widget, logical):
    if sys.platform == "win32":
        return max(1, round(logical * widget.winfo_fpixels("1i") / 96))
    return logical


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
    style.configure("Cue.Treeview", rowheight=pixels(app, 33), font=("Segoe UI", 11))
    style.map("Cue.Treeview", background=[("selected", SELECTED)], foreground=[("selected", TEXT)])
    style.configure("Activity.TLabel", background=SHELL, foreground=CYAN, font=("Segoe UI", 9))
    style.configure("Alert.TLabel", background=SHELL, foreground=CORAL, font=("Segoe UI", 9))
    style.configure("AppTitle.TLabel", background=SHELL, font=("Segoe UI", 12, "bold"))
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
    _apply_workspace_surfaces(app, style)


def _apply_workspace_surfaces(app, style):
    from PIL import Image, ImageDraw, ImageTk

    rounded = sys.platform == "win32"
    pictures = {}

    def surface(fill, edge, radius, underlay):
        key = (fill, edge, radius, underlay)
        if key not in pictures:
            size = pixels(app, 20)
            image = Image.new("RGB", (size * 3, size * 3), underlay)
            ImageDraw.Draw(image).rounded_rectangle((0, 0, image.width - 1, image.height - 1),
                                                     radius=pixels(app, radius) * 3,
                                                     fill=fill, outline=edge, width=3)
            pictures[key] = ImageTk.PhotoImage(image.resize((size, size), Image.Resampling.LANCZOS), master=app)
        return pictures[key]

    app._card_corners = ()
    if rounded:
        size = pixels(app, 18)
        cap = pixels(app, 7)
        picture = Image.new("RGB", (size * 3, size * 3), SHELL)
        ImageDraw.Draw(picture).rounded_rectangle((0, 0, picture.width - 1, picture.height - 1),
                                                   radius=pixels(app, 6) * 3, fill=PANEL, outline=EDGE, width=3)
        picture = picture.resize((size, size), Image.Resampling.LANCZOS)
        app._card_corners = tuple(ImageTk.PhotoImage(picture.crop(box), master=app) for box in (
            (0, 0, cap, cap), (size - cap, 0, size, cap),
            (0, size - cap, cap, size), (size - cap, size - cap, size, size)
        ))
        style.element_create("Activity.surface", "image", surface("#122331", "#26435a", 5, SHELL),
                             border=pixels(app, 7), sticky="nsew")
        style.layout("Activity.TFrame", [("Activity.surface", {"sticky": "nswe"})])

    for name, fill, edge, fg, padding in (
            ("Icon", BUTTON, EDGE, TEXT, (6, 5)),
            ("HeaderIcon", BUTTON, EDGE, TEXT, (3, 3)),
            ("Toolbar", BUTTON, EDGE, TEXT, (6, 5)),
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
        role = name + ".TButton"
        if rounded:
            element = name + ".surface"
            active = STOP_BG if name == "CloseWindow" else "#293b4f" if fill in (BUTTON, SHELL) else fill
            underlay = SHELL if name in ("Toolbar", "Accent", "Window", "CloseWindow") else PANEL
            style.element_create(element, "image",
                                 surface(fill, edge, 5, underlay),
                                 ("pressed", surface(active, CYAN, 5, underlay)),
                                 ("focus", surface(fill, CYAN, 5, underlay)),
                                 ("active", surface(active, edge, 5, underlay)),
                                 border=pixels(app, 6), padding=1, sticky="nsew")
            style.layout(role, [(element, {"sticky": "nswe", "children": [
                ("Button.padding", {"sticky": "nswe", "children": [
                    ("Button.label", {"sticky": "nswe"})
                ]})
            ]})])
        style.configure(role, padding=tuple(pixels(app, n) for n in padding), background=fill, foreground=fg,
                        font=("Segoe UI", 10, "bold"))
        style.map(role, foreground=[("disabled", MUTED), ("!disabled", fg)])

    if rounded:
        style.layout("TMenubutton", [("Toolbar.surface", {"sticky": "nswe", "children": [
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
    import win32api
    import win32con
    import win32gui

    hwnd = win32gui.GetAncestor(app.winfo_id(), 2)  # GA_ROOT
    if not hwnd:
        return
    _, _, original_width, original_height = win32gui.GetClientRect(hwnd)
    style = win32gui.GetWindowLong(hwnd, win32con.GWL_STYLE)
    win32gui.SetWindowLong(hwnd, win32con.GWL_STYLE, style & ~win32con.WS_CAPTION)
    extended = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
    win32gui.SetWindowLong(hwnd, win32con.GWL_EXSTYLE, extended | win32con.WS_EX_APPWINDOW)
    win32gui.SetWindowPos(hwnd, 0, 0, 0, 0, 0, win32con.SWP_NOSIZE | win32con.SWP_NOMOVE |
                          win32con.SWP_NOZORDER | win32con.SWP_NOACTIVATE | win32con.SWP_FRAMECHANGED)
    _, _, width, height = win32gui.GetClientRect(hwnd)
    removed_width = width - original_width
    removed_height = height - original_height
    # Tk still budgets the original non-client area for its minimum size.
    min_width, min_height = app.minsize()
    app.minsize(max(1, min_width - removed_width), max(1, min_height - removed_height))

    monitor = win32api.MonitorFromWindow(hwnd, 2)  # MONITOR_DEFAULTTONEAREST
    work_left, work_top, work_right, work_bottom = win32api.GetMonitorInfo(monitor)["Work"]
    left, top, right, bottom = win32gui.GetWindowRect(hwnd)
    margin = 8
    width = min(right - left, work_right - work_left - 2 * margin)
    height = min(bottom - top, work_bottom - work_top - 2 * margin)
    left = max(work_left + margin, min(left, work_right - width - margin))
    top = max(work_top + margin, min(top, work_bottom - height - margin))
    win32gui.SetWindowPos(hwnd, 0, left, top, width, height,
                          win32con.SWP_NOZORDER | win32con.SWP_NOACTIVATE)

    attribute = WinDLL("dwmapi").DwmSetWindowAttribute
    attribute.argtypes = (c_void_p, c_uint32, c_void_p, c_uint32)
    enabled = c_int(1)
    if attribute(hwnd, 20, byref(enabled), sizeof(enabled)):
        attribute(hwnd, 19, byref(enabled), sizeof(enabled))
    border = c_uint32(0xFFFFFFFE)  # DWMWA_COLOR_NONE
    attribute(hwnd, 34, byref(border), sizeof(border))
    corner = c_uint32(2)  # DWMWCP_ROUND
    attribute(hwnd, 33, byref(corner), sizeof(corner))

    user32 = WinDLL("user32")
    release_capture = user32.ReleaseCapture
    is_zoomed = user32.IsZoomed
    is_zoomed.argtypes = (c_void_p,)
    is_zoomed.restype = c_int

    def drag(event):
        release_capture()
        xy = (event.x_root & 0xFFFF) | ((event.y_root & 0xFFFF) << 16)
        win32gui.PostMessage(hwnd, win32con.WM_NCLBUTTONDOWN, win32con.HTCAPTION, xy)

    def maximize(_event=None):
        mode = win32con.SW_RESTORE if is_zoomed(hwnd) else win32con.SW_MAXIMIZE
        win32gui.ShowWindow(hwnd, mode)
        return "break"

    zoom_button.configure(command=maximize)
    for widget in drag_surfaces:
        widget.bind("<ButtonPress-1>", drag, add="+")
        widget.bind("<Double-Button-1>", maximize, add="+")
