"""Drive the presentation opened by a Show Control cue using PowerPoint's COM API."""
from pathlib import Path

from pywintypes import com_error
from win32com.client import Dispatch


class PowerPointShow:
    def __init__(self):
        self.presentation = None
        self.window = None

    def open(self, path):
        path = Path(path).resolve(strict=True)
        try:
            app = Dispatch("PowerPoint.Application")
        except com_error as exc:
            raise RuntimeError("A PowerPoint cue requires Microsoft PowerPoint desktop on this PC.") from exc
        # Reuse an already open document, keeping any unsaved edits.
        presentation = next(
            (p for p in app.Presentations if Path(p.FullName) == path),
            None,
        )
        if presentation is None:
            presentation = app.Presentations.Open(str(path), -1, 0, -1)  # read-only, titled, visible
        self.end()
        self.presentation = presentation
        self.start()

    def start(self):
        if self.presentation is None:
            raise RuntimeError("Select and start a PowerPoint cue first.")
        if not self.active():
            # PowerPoint uses its configured slideshow monitor.
            self.window = self.presentation.SlideShowSettings.Run()

    def active(self):
        if self.window is None:
            return False
        try:
            # Query the live view. PowerPoint invalidates this object when the show ends.
            return bool(self.window.View.State)
        except com_error:
            self.window = None
            return False

    def move(self, forward):
        if self.active():
            if forward:
                self.window.View.Next()
            else:
                self.window.View.Previous()

    def end(self):
        if self.active():
            self.window.View.Exit()
        self.window = None


show = PowerPointShow()
