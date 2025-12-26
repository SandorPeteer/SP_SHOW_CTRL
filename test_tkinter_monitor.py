#!/usr/bin/env python3
"""
Tkinter teszt a 2. monitor fullscreen pozicionáláshoz
"""

import tkinter as tk

try:
    from screeninfo import get_monitors as _screeninfo_get_monitors  # type: ignore
except Exception:
    _screeninfo_get_monitors = None


def _macos_coregraphics_monitors():
    import platform
    if platform.system() != "Darwin":
        return []
    try:
        import ctypes
        import ctypes.util
    except Exception:
        return []

    lib = ctypes.util.find_library("CoreGraphics") or "/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics"
    try:
        coregraphics = ctypes.cdll.LoadLibrary(lib)
    except Exception:
        return []

    CGDirectDisplayID = ctypes.c_uint32
    UInt32 = ctypes.c_uint32

    class CGPoint(ctypes.Structure):
        _fields_ = [("x", ctypes.c_double), ("y", ctypes.c_double)]

    class CGSize(ctypes.Structure):
        _fields_ = [("width", ctypes.c_double), ("height", ctypes.c_double)]

    class CGRect(ctypes.Structure):
        _fields_ = [("origin", CGPoint), ("size", CGSize)]

    try:
        coregraphics.CGGetActiveDisplayList.argtypes = [UInt32, ctypes.POINTER(CGDirectDisplayID), ctypes.POINTER(UInt32)]
        coregraphics.CGGetActiveDisplayList.restype = ctypes.c_int32
        coregraphics.CGMainDisplayID.argtypes = []
        coregraphics.CGMainDisplayID.restype = CGDirectDisplayID
        coregraphics.CGDisplayBounds.argtypes = [CGDirectDisplayID]
        coregraphics.CGDisplayBounds.restype = CGRect
        coregraphics.CGDisplayPixelsWide.argtypes = [CGDirectDisplayID]
        coregraphics.CGDisplayPixelsWide.restype = UInt32
        coregraphics.CGDisplayPixelsHigh.argtypes = [CGDirectDisplayID]
        coregraphics.CGDisplayPixelsHigh.restype = UInt32
    except Exception:
        return []

    max_displays = 16
    active = (CGDirectDisplayID * max_displays)()
    count = UInt32(0)
    try:
        err = int(coregraphics.CGGetActiveDisplayList(UInt32(max_displays), active, ctypes.byref(count)))
    except Exception:
        return []
    if err != 0 or int(count.value) <= 0:
        return []

    try:
        main_id = int(coregraphics.CGMainDisplayID())
    except Exception:
        main_id = -1

    out = []
    display_count = int(count.value)
    for idx in range(display_count):
        try:
            did = int(active[idx])
        except Exception:
            continue
        try:
            bounds = coregraphics.CGDisplayBounds(CGDirectDisplayID(did))
            left = int(round(float(bounds.origin.x)))
            top = int(round(float(bounds.origin.y)))
        except Exception:
            continue
        try:
            width = int(coregraphics.CGDisplayPixelsWide(CGDirectDisplayID(did)))
            height = int(coregraphics.CGDisplayPixelsHigh(CGDirectDisplayID(did)))
        except Exception:
            try:
                width = int(round(float(bounds.size.width)))
                height = int(round(float(bounds.size.height)))
            except Exception:
                continue
        is_primary = bool(did == main_id)
        name = f"Display {idx + 1}" + (" (main)" if is_primary else "")
        out.append(type("Mon", (), {"x": left, "y": top, "width": width, "height": height, "name": name, "is_primary": is_primary})())
    # Primary first.
    primary = next((m for m in out if getattr(m, "is_primary", False)), None)
    if primary is None:
        return out
    others = [m for m in out if m is not primary]
    return [primary, *others]


def get_monitors():
    # Prefer CoreGraphics on macOS: screeninfo can be flaky in some setups.
    mons = _macos_coregraphics_monitors()
    if mons:
        return mons
    if _screeninfo_get_monitors is not None:
        try:
            return _screeninfo_get_monitors()
        except Exception:
            return []
    return []


def _monitor_area(m) -> int:
    try:
        return int(getattr(m, "width", 0)) * int(getattr(m, "height", 0))
    except Exception:
        return 0


def _aspect_score(m) -> float:
    try:
        w = float(int(getattr(m, "width", 0)))
        h = float(int(getattr(m, "height", 0)))
        if w <= 0 or h <= 0:
            return 10.0
        ratio = w / h
        portrait_penalty = 1.5 if ratio < 1.05 else 0.0
        return abs(ratio - (16.0 / 9.0)) + portrait_penalty
    except Exception:
        return 10.0


def pick_output_monitor(monitors):
    if not monitors:
        return None
    primary = next((m for m in monitors if getattr(m, "is_primary", False)), monitors[0])
    candidates = [m for m in monitors if m is not primary]
    if not candidates:
        return primary
    return min(candidates, key=lambda m: (_aspect_score(m), -_monitor_area(m)))

def get_screen_info():
    """Kigyűjti a képernyők információit"""
    monitors = get_monitors()
    info = f"Talált monitorok száma: {len(monitors)}\n\n"

    for i, m in enumerate(monitors):
        info += f"Monitor {i}:\n"
        info += f"  Név: {m.name}\n"
        info += f"  Pozíció: x={m.x}, y={m.y}\n"
        info += f"  Méret: {m.width}x{m.height}\n"
        info += f"  Primary: {m.is_primary}\n\n"

    return info

def test_config_1():
    """Konfig 1: attributes('-fullscreen', True) + geometry("+x+y")"""
    print("\n" + "="*60)
    print("KONFIGURÁCIÓ 1")
    print("Módszer: attributes('-fullscreen', True) + geometry('+x+y')")
    print("="*60)

    monitors = get_monitors()
    print(get_screen_info())

    if len(monitors) < 2:
        print("✗ Nincs 2. monitor!")
        return

    root = tk.Tk()
    root.title("Teszt 1 - Fullscreen + Geometry")
    root.configure(bg='black')

    # 2. monitor koordinátái
    second = pick_output_monitor(monitors)
    if second is None:
        print("✗ Nincs kimeneti monitor!")
        return

    # Beállítások
    root.attributes('-fullscreen', True)
    root.geometry(f"+{second.x}+{second.y}")

    label = tk.Label(
        root,
        text=f"KONFIG 1\nFullscreen + Geometry\nMonitor: {second.name}\nPozíció: {second.x}, {second.y}",
        fg='white',
        bg='black',
        font=('Helvetica', 24)
    )
    label.place(relx=0.5, rely=0.5, anchor='center')

    root.bind('<Escape>', lambda e: root.destroy())

    print(f"✓ Ablak létrehozva: geometry='+{second.x}+{second.y}'")
    print("→ Nyomd meg ESC-et a bezáráshoz\n")

    root.mainloop()

def test_config_2():
    """Konfig 2: geometry("WIDTHxHEIGHT+X+Y") + attributes('-fullscreen', True)"""
    print("\n" + "="*60)
    print("KONFIGURÁCIÓ 2")
    print("Módszer: geometry('WxH+X+Y') -> attributes('-fullscreen', True)")
    print("="*60)

    monitors = get_monitors()
    print(get_screen_info())

    if len(monitors) < 2:
        print("✗ Nincs 2. monitor!")
        return

    root = tk.Tk()
    root.title("Teszt 2 - Full Geometry + Fullscreen")
    root.configure(bg='blue')

    # 2. monitor koordinátái
    second = pick_output_monitor(monitors)
    if second is None:
        print("✗ Nincs kimeneti monitor!")
        return

    # Először a teljes geometria, aztán egy rövid késleltetéssel fullscreen.
    root.geometry(f"{second.width}x{second.height}+{second.x}+{second.y}")
    root.update_idletasks()
    root.after(80, lambda: root.attributes('-fullscreen', True))

    label = tk.Label(
        root,
        text=f"KONFIG 2\nFull Geometry + Fullscreen\nMonitor: {second.name}\n{second.width}x{second.height}+{second.x}+{second.y}",
        fg='white',
        bg='blue',
        font=('Helvetica', 24)
    )
    label.place(relx=0.5, rely=0.5, anchor='center')

    root.bind('<Escape>', lambda e: root.destroy())

    print(f"✓ Ablak létrehozva: geometry='{second.width}x{second.height}+{second.x}+{second.y}'")
    print("→ Nyomd meg ESC-et a bezáráshoz\n")

    root.mainloop()

def test_config_3():
    """Konfig 3: overrideredirect(True) + geometry + attributes"""
    print("\n" + "="*60)
    print("KONFIGURÁCIÓ 3")
    print("Módszer: overrideredirect + geometry + fullscreen")
    print("="*60)

    monitors = get_monitors()
    print(get_screen_info())

    if len(monitors) < 2:
        print("✗ Nincs 2. monitor!")
        return

    root = tk.Tk()
    root.title("Teszt 3 - Override + Fullscreen")
    root.configure(bg='green')

    # 2. monitor koordinátái
    second = pick_output_monitor(monitors)
    if second is None:
        print("✗ Nincs kimeneti monitor!")
        return

    # Sorrend fontos!
    root.overrideredirect(True)
    root.attributes('-fullscreen', True)
    root.geometry(f"+{second.x}+{second.y}")

    label = tk.Label(
        root,
        text=f"KONFIG 3\nOverrideredirect + Fullscreen\nMonitor: {second.name}\nPozíció: {second.x}, {second.y}",
        fg='white',
        bg='green',
        font=('Helvetica', 24)
    )
    label.place(relx=0.5, rely=0.5, anchor='center')

    root.bind('<Escape>', lambda e: root.destroy())

    print(f"✓ Ablak létrehozva: overrideredirect + fullscreen + geometry")
    print("→ Nyomd meg ESC-et a bezáráshoz\n")

    root.mainloop()

def test_config_4():
    """Konfig 4: geometry teljes -> overrideredirect -> fullscreen"""
    print("\n" + "="*60)
    print("KONFIGURÁCIÓ 4")
    print("Módszer: geometry(full) -> overrideredirect -> fullscreen")
    print("="*60)

    monitors = get_monitors()
    print(get_screen_info())

    if len(monitors) < 2:
        print("✗ Nincs 2. monitor!")
        return

    root = tk.Tk()
    root.title("Teszt 4 - Full Geo + Override + Fullscreen")
    root.configure(bg='red')

    # 2. monitor koordinátái
    second = pick_output_monitor(monitors)
    if second is None:
        print("✗ Nincs kimeneti monitor!")
        return

    # Sorrend: geometry -> override -> fullscreen
    root.geometry(f"{second.width}x{second.height}+{second.x}+{second.y}")
    root.overrideredirect(True)
    root.attributes('-fullscreen', True)

    label = tk.Label(
        root,
        text=f"KONFIG 4\nGeometry -> Override -> Fullscreen\n{second.width}x{second.height}+{second.x}+{second.y}",
        fg='white',
        bg='red',
        font=('Helvetica', 24)
    )
    label.place(relx=0.5, rely=0.5, anchor='center')

    root.bind('<Escape>', lambda e: root.destroy())

    print(f"✓ Ablak létrehozva")
    print("→ Nyomd meg ESC-et a bezáráshoz\n")

    root.mainloop()

def test_config_5():
    """Konfig 5: 'pseudo-fullscreen' (borderless) a 2. kijelzőn, NEM macOS native fullscreen."""
    print("\n" + "="*60)
    print("KONFIGURÁCIÓ 5")
    print("Módszer: geometry(full) + overrideredirect(True) (pseudo-fullscreen)")
    print("="*60)

    monitors = get_monitors()
    print(get_screen_info())

    if len(monitors) < 2:
        print("✗ Nincs 2. monitor!")
        return

    second = pick_output_monitor(monitors)
    if second is None:
        print("✗ Nincs kimeneti monitor!")
        return

    root = tk.Tk()
    root.title("Teszt 5 - Pseudo Fullscreen (borderless)")
    root.configure(bg='black')

    root.overrideredirect(True)
    root.geometry(f"{second.width}x{second.height}+{second.x}+{second.y}")
    root.update_idletasks()

    label = tk.Label(
        root,
        text=f"KONFIG 5\nPseudo-fullscreen (no native fullscreen)\nMonitor: {second.name}\n{second.width}x{second.height}+{second.x}+{second.y}",
        fg='white',
        bg='black',
        font=('Helvetica', 24)
    )
    label.place(relx=0.5, rely=0.5, anchor='center')

    root.bind('<Escape>', lambda e: root.destroy())

    print("✓ Ablak létrehozva (pseudo-fullscreen)")
    print("→ Nyomd meg ESC-et a bezáráshoz\n")

    root.mainloop()

def main():
    import sys

    print("\n" + "="*60)
    print("TKINTER MONITOR POZICIONÁLÁSI TESZT")
    print("="*60)
    print("\nHasználat:")
    print("  python3 test_tkinter_monitor.py [konfig_szám]")
    print("\nKonfigurációk:")
    print("  1: attributes('-fullscreen') + geometry('+x+y')")
    print("  2: geometry('WxH+X+Y') + attributes('-fullscreen')")
    print("  3: overrideredirect + fullscreen + geometry('+x+y')")
    print("  4: geometry(full) + overrideredirect + fullscreen")
    print("  5: pseudo-fullscreen (borderless) a 2. kijelzőn")
    print("="*60 + "\n")

    config = 1
    if len(sys.argv) > 1:
        try:
            config = int(sys.argv[1])
            if config < 1 or config > 5:
                print("Hibás konfig szám! 1-5 közötti értéket adj meg.")
                return
        except ValueError:
            print("Hibás konfig szám!")
            return

    tests = {
        1: test_config_1,
        2: test_config_2,
        3: test_config_3,
        4: test_config_4,
        5: test_config_5,
    }

    tests[config]()

if __name__ == "__main__":
    main()
