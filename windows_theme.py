"""Use a colorable ttk theme so Windows paints both tab text and backgrounds."""
from tkinter import ttk


def apply_windows_theme(app):
    background = "#202a3a"
    field = "#171f2c"
    foreground = "#edf3fc"
    disabled = "#b8b8b8"
    raised = "#2b3a51"
    selected = "#2c5393"
    border = "#38465b"
    style = ttk.Style(app)
    style.theme_use("clam")
    app.configure(background="#111825")
    for option, color in (("*Background", background), ("*Foreground", foreground),
                          ("*insertBackground", foreground), ("*selectBackground", selected),
                          ("*selectForeground", foreground), ("*disabledForeground", disabled)):
        app.option_add(option, color)
    style.configure(".", background=background, foreground=foreground,
                    bordercolor=border, lightcolor=background, darkcolor=background,
                    troughcolor=field, font=("Segoe UI", 10))
    style.configure("Shell.TFrame", background="#111825")
    style.configure("ShowTitle.TLabel", background="#111825", font=("Segoe UI", 12, "bold"))
    style.configure("CardTitle.TLabel", font=("Segoe UI", 13, "bold"))
    style.configure("Tooltip.TLabel", background="#304158", foreground=foreground)
    style.configure("Status.TLabel", foreground="#bdcbe0")
    style.map(".", foreground=[("disabled", disabled)])
    style.configure("TButton", background=raised, padding=(8, 4), relief="flat")
    style.map("TButton", background=[("disabled", background), ("pressed", selected), ("active", "#3b4d68")],
              foreground=[("disabled", disabled), ("!disabled", foreground)])
    style.configure("Icon.TButton", padding=(5, 4))
    style.configure("Accent.TButton", background="#285da8", padding=(9, 5), font=("Segoe UI", 10, "bold"))
    style.map("Accent.TButton", background=[("pressed", "#214b86"), ("active", "#366cbb")])
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
