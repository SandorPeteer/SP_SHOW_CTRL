"""Drive the frozen operator with real Windows input; never call model handlers."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def exercise_dialogs(process, controller, folder, environment):
    import win32api
    import win32clipboard
    import win32con
    import win32gui
    import win32process
    from PIL import ImageChops, ImageGrab, ImageStat

    root = Path(__file__).resolve().parents[1]
    evidence = {"environment": "virtual Windows desktop, frozen EXE", "checks": [],
                "limitations": ["Controller HWND ownership and its virtual monitor are verified; "
                                "physical second-monitor/projector exclusion requires hardware.",
                                "Normal Close exits without a confirmation, matching existing behavior."]}
    report = root / "dist/windows-dialog-verification.json"
    app_path = root / "dist/SP Show Control/SP Show Control.exe"

    def wait(read, accepts=bool, timeout=12):
        deadline = time.monotonic() + timeout
        while True:
            value = read()
            if accepts(value):
                return value
            if time.monotonic() >= deadline:
                raise AssertionError(f"Dialog rehearsal timed out; last value: {value!r}")
            time.sleep(0.05)

    def key(code, *modifiers):
        for modifier in modifiers:
            win32api.keybd_event(modifier, 0, 0, 0)
        win32api.keybd_event(code, 0, 0, 0)
        win32api.keybd_event(code, 0, win32con.KEYEVENTF_KEYUP, 0)
        for modifier in reversed(modifiers):
            win32api.keybd_event(modifier, 0, win32con.KEYEVENTF_KEYUP, 0)

    def activate():
        win32gui.ShowWindow(controller, win32con.SW_RESTORE)
        # The short Alt press permits foreground transfer on unattended desktops.
        key(win32con.VK_MENU)
        win32gui.SetForegroundWindow(controller)
        wait(win32gui.GetForegroundWindow, lambda hwnd: hwnd == controller)

    def click(x, y):
        win32api.SetCursorPos((round(x), round(y)))
        win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
        win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)

    def capture(name):
        frame = ImageGrab.grab(bbox=win32gui.GetWindowRect(controller))
        frame.save(root / f"dist/windows-dialog-{name}.png")
        return frame

    def check(label, **details):
        evidence["checks"].append(dict(check=label, **details))
        report.write_text(json.dumps(evidence, indent=2), encoding="utf-8")
        print("PASS frozen UI:", label, flush=True)

    def native_dialog(title):
        def find():
            found = []
            def visit(hwnd, _):
                if (win32gui.IsWindowVisible(hwnd) and win32gui.GetWindowText(hwnd) == title
                        and win32process.GetWindowThreadProcessId(hwnd)[1] == process.pid):
                    found.append(hwnd)
            win32gui.EnumWindows(visit, None)
            return found[0] if found else None
        dialog = wait(find)
        wait(win32gui.GetForegroundWindow, lambda hwnd: hwnd == dialog)
        assert win32gui.GetAncestor(dialog, win32con.GA_ROOTOWNER) == controller
        dialog_monitor = win32api.MonitorFromWindow(dialog, 2)
        controller_monitor = win32api.MonitorFromWindow(controller, 2)
        assert dialog_monitor == controller_monitor
        assert not win32gui.IsWindowEnabled(controller), "Native dialog did not make its operator owner modal"
        check(title + " foreground/owner/monitor", owner=controller,
              dialog=dialog, monitor=str(controller_monitor))
        return dialog

    def cancel_file(code, title, *, shift=False):
        activate()
        key(code, win32con.VK_CONTROL, *([win32con.VK_SHIFT] if shift else []))
        dialog = native_dialog(title)
        capture(title.lower().replace(" ", "-") + "-cancel")
        key(win32con.VK_ESCAPE)
        wait(lambda: not win32gui.IsWindowVisible(dialog))
        wait(win32gui.GetForegroundWindow, lambda hwnd: hwnd == controller)
        assert win32gui.IsWindowEnabled(controller)
        check(title + " Cancel returned focus")

    def choose_file(code, title, destination, *, shift=False):
        activate()
        key(code, win32con.VK_CONTROL, *([win32con.VK_SHIFT] if shift else []))
        dialog = native_dialog(title)
        # The standard Windows file-name accelerator focuses the native field.
        key(ord("N"), win32con.VK_MENU)
        key(ord("A"), win32con.VK_CONTROL)
        win32clipboard.OpenClipboard()
        try:
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardText(str(destination), win32con.CF_UNICODETEXT)
        finally:
            win32clipboard.CloseClipboard()
        key(ord("V"), win32con.VK_CONTROL)
        key(win32con.VK_RETURN)
        wait(lambda: not win32gui.IsWindowVisible(dialog))
        wait(win32gui.GetForegroundWindow, lambda hwnd: hwnd == controller)
        time.sleep(0.3)

    def tool_window(code, title, *, shift=False):
        activate()
        key(code, win32con.VK_CONTROL, *([win32con.VK_SHIFT] if shift else []))
        def find():
            found = []
            def visit(hwnd, _):
                if (win32gui.IsWindowVisible(hwnd) and win32gui.GetWindowText(hwnd) == title
                        and win32gui.GetAncestor(hwnd, win32con.GA_ROOTOWNER) == controller):
                    found.append(hwnd)
            win32gui.EnumWindows(visit, None)
            return found[0] if found else None
        tool = wait(find)
        wait(win32gui.GetForegroundWindow, lambda hwnd: hwnd == tool)
        assert win32api.MonitorFromWindow(tool, 2) == win32api.MonitorFromWindow(controller, 2)
        work = win32api.GetMonitorInfo(win32api.MonitorFromWindow(controller, 2))["Work"]
        name = title.lower().replace(" ", "-").replace(":", "")
        measurements = []
        for enlarged in (False, True):
            if enlarged:
                left, top, right, bottom = win32gui.GetWindowRect(tool)
                width = min(right - left + 120, work[2] - work[0] - 40)
                height = min(bottom - top + 80, work[3] - work[1] - 40)
                win32gui.SetWindowPos(tool, 0, work[0] + 20, work[1] + 20, width, height,
                                      win32con.SWP_NOZORDER)
            time.sleep(0.25)
            bounds = win32gui.GetWindowRect(tool)
            assert bounds[0] >= work[0] and bounds[1] >= work[1]
            assert bounds[2] <= work[2] and bounds[3] <= work[3], (title, bounds, work)
            image = ImageGrab.grab(bbox=bounds).convert("RGB")
            # Sample the client area. Native titlebar rendering is OS-controlled.
            sample = image.crop((10, min(40, image.height // 5), image.width - 10, image.height - 10))
            dark = sum(count for count, color in sample.getcolors(sample.width * sample.height)
                       if max(color) < 140) / (sample.width * sample.height)
            assert dark > 0.6, (title, "Expected unified dark tool surface", dark)
            image.save(root / f"dist/windows-tool-{name}{'-resized' if enlarged else ''}.png")
            measurements.append(dict(bounds=bounds, dark_fraction=dark))
        key(win32con.VK_ESCAPE)
        wait(lambda: not win32gui.IsWindowVisible(tool))
        wait(win32gui.GetForegroundWindow, lambda hwnd: hwnd == controller)
        assert observer.get_property("path").endswith("logo.png")
        check(title + " owned/focused/dark/resizable; Escape returned focus with output intact",
              measurements=measurements)

    def save_to(path):
        choose_file(ord("S"), "Save show as", path, shift=True)
        wait(path.is_file, timeout=25)
        return json.loads(path.read_text(encoding="utf-8"))

    def new_dialog(name):
        activate()
        before = capture(name + "-before")
        key(ord("N"), win32con.VK_CONTROL)
        time.sleep(0.6)
        assert process.poll() is None
        assert win32gui.GetForegroundWindow() == controller
        after = capture(name)
        area = (before.width // 4, before.height // 3,
                before.width * 3 // 4, before.height * 3 // 4)
        delta = ImageStat.Stat(ImageChops.difference(before.crop(area), after.crop(area)))
        assert sum(delta.mean) > 12, ("Confirmation was not visible on the operator", delta.mean)
        return after

    observer = None
    restarted = None
    try:
        activate()
        # Scene activation selects A0 only. Select the visible first projection
        # row before Show; otherwise the production handler correctly does nothing.
        left, top, right, bottom = win32gui.GetWindowRect(controller)
        from ctypes import c_uint, c_void_p, windll
        get_dpi = windll.user32.GetDpiForWindow
        get_dpi.argtypes = (c_void_p,)
        get_dpi.restype = c_uint
        scale = get_dpi(controller) / 96
        first_row_y = top + 112 * scale + int((bottom - top - 66 * scale) * .42)
        click(left + (right - left) * .8, first_row_y)
        # Verify that a real row became selected before invoking transport.
        sample_x = round(left + (right - left) * .9)
        sample_y = round(first_row_y)
        selected_colors = {(25, 55, 71), (43, 37, 65)}  # selected / explicitly queued
        selected = wait(lambda: ImageGrab.grab(bbox=(sample_x, sample_y, sample_x + 1, sample_y + 1)).convert("RGB").getpixel((0, 0)),
                        lambda color: color in selected_colors)
        capture("projection-row-selected")
        check("Selected B0 through the actual projection row before Show", row_color=selected)
        click(left + (right - left - 36 * scale) * .58 + 65 * scale, bottom - 32 * scale)
        from mpv_ipc import MpvIpcSession
        observer = MpvIpcSession(str(app_path.parent / "_internal/tools/mpv/mpv.exe"),
            name="OUTPUT", second_screen_left=0, second_screen_top=0, fullscreen=False)
        observer._connect_ipc()
        wait(lambda: observer.get_property("path"), lambda value: value and value.endswith("logo.png"))
        wait(lambda: observer.get_property("video-params"))
        check("Dialog checks started with a decoded image in the real mpv output")

        new_dialog("new-show-keep")
        key(win32con.VK_ESCAPE)
        time.sleep(0.2)
        assert observer.get_property("path").endswith("logo.png")
        check("New show Escape kept the running output and operator focus")
        comma = win32api.VkKeyScan(",")
        assert comma != -1, "Active Windows keyboard layout cannot produce comma"
        for code, title, shift in ((comma & 0xff, "Setup", bool(comma & 0x100)),
                                   (ord("M"), "Media tools", True),
                                   (ord("P"), "Projection tools", True),
                                   (ord("E"), "Edit Scene: Rehearsal", False)):
            tool_window(code, title, shift=shift)
        for code, title, shift in ((ord("O"), "Open show", False),
                                   (ord("S"), "Save show as", True),
                                   (ord("A"), "Add Media", True),
                                   (ord("B"), "Add Visuals (Deck B)", True)):
            cancel_file(code, title, shift=shift)
            assert observer.get_property("path").endswith("logo.png")

        kept = folder / "kept-show.json"
        data = save_to(kept)
        assert len(data["cues_a"]) == len(data["cues_b"]) == len(data["scenes"]) == 1
        kept_digest = hashlib.sha256(kept.read_bytes()).hexdigest()
        check("New/Open/Add/Save-as Cancel preserved both cue lists and the scene")
        new_dialog("new-show-keep-button")
        key(win32con.VK_SPACE)  # Activate the focused Keep show button.
        time.sleep(0.3)
        after_keep = save_to(folder / "keep-button-show.json")
        assert len(after_keep["cues_a"]) == len(after_keep["cues_b"]) == len(after_keep["scenes"]) == 1
        check("Keep show button retained both lists and the scene")
        choose_file(ord("A"), "Add Media", folder / "árvíz teszt.mp4", shift=True)
        choose_file(ord("B"), "Add Visuals (Deck B)", app_path.parent / "_internal/assets/logo.png", shift=True)
        added = save_to(folder / "added-show.json")
        assert len(added["cues_a"]) == len(added["cues_b"]) == 2
        assert len({cue["path"] for cue in added["cues_a"]}) == 1
        assert len({cue["path"] for cue in added["cues_b"]}) == 1
        check("Actual Add media/visuals and portable Save as retained unique cues with shared media")

        new_dialog("new-show-confirm")
        key(win32con.VK_TAB)  # Keep show is focused by default; next is New show.
        key(win32con.VK_SPACE)
        time.sleep(0.4)
        empty = save_to(folder / "empty-show.json")
        assert not empty["cues_a"] and not empty["cues_b"] and len(empty["scenes"]) == 1
        assert hashlib.sha256(kept.read_bytes()).hexdigest() == kept_digest
        check("Confirmed New show cleared the active show and preserved the saved original")
        choose_file(ord("O"), "Open show", kept)
        opened = save_to(folder / "opened-show.json")
        assert len(opened["cues_a"]) == len(opened["cues_b"]) == 1
        key(ord("S"), win32con.VK_CONTROL)
        time.sleep(0.4)
        check("Actual Open and Save restored both lists")
        capture("completed")
        key(win32con.VK_F4, win32con.VK_MENU)
        assert process.wait(timeout=15) == 0
        check("Close exited normally with no hidden modal")
        observer.shutdown()
        observer = None

        restarted = subprocess.Popen([app_path], env=environment, cwd=folder)
        process = restarted
        def restored_window():
            hwnd = win32gui.FindWindow(None, "S.P. Show Control")
            return hwnd if hwnd and win32process.GetWindowThreadProcessId(hwnd)[1] == process.pid else None
        controller = wait(restored_window, timeout=35)
        time.sleep(3)
        restored = save_to(folder / "restored-show.json")
        assert len(restored["cues_a"]) == len(restored["cues_b"]) == 1
        assert restored["scenes"][0]["name"] == data["scenes"][0]["name"]
        check("Frozen EXE relaunch restored the last saved show")
        key(win32con.VK_F4, win32con.VK_MENU)
        assert process.wait(timeout=15) == 0
    except Exception as exc:
        evidence["error"] = repr(exc)
        if win32gui.IsWindow(controller):
            capture("failure")
        raise
    finally:
        if observer is not None:
            observer.shutdown()
        if restarted is not None and restarted.poll() is None:
            subprocess.run(["taskkill", "/PID", str(restarted.pid), "/T", "/F"], capture_output=True)
            restarted.wait(timeout=10)
        report.write_text(json.dumps(evidence, indent=2), encoding="utf-8")
