#!/usr/bin/env python3
"""
Teszt script különböző ablak-pozicionálási módszerek kipróbálására.
Minden konfiguráció után vársz, hogy a felhasználó visszajelezzen.
"""

import sys
import time
from PyQt6.QtWidgets import QApplication, QMainWindow, QLabel, QVBoxLayout, QWidget, QPushButton
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QScreen

class TestWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Teszt Ablak - Monitor Pozíció")
        self.resize(800, 600)

        # Central widget
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)

        self.info_label = QLabel("Inicializálás...")
        self.info_label.setWordWrap(True)
        self.info_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.info_label)

    def show_info(self, text):
        self.info_label.setText(text)

def get_screen_info(app):
    """Kigyűjti a képernyők információit"""
    screens = app.screens()
    info = f"Talált képernyők száma: {len(screens)}\n\n"

    for i, screen in enumerate(screens):
        geom = screen.geometry()
        info += f"Képernyő {i}:\n"
        info += f"  Név: {screen.name()}\n"
        info += f"  Pozíció: x={geom.x()}, y={geom.y()}\n"
        info += f"  Méret: {geom.width()}x{geom.height()}\n"
        info += f"  DPI: {screen.logicalDotsPerInch()}\n\n"

    return info

def test_config_1(app, window):
    """Konfig 1: setScreen + showFullScreen"""
    info = "=== KONFIGURÁCIÓ 1 ===\n"
    info += "Módszer: window.setScreen(screens[1]) + showFullScreen()\n\n"
    info += get_screen_info(app)

    screens = app.screens()
    if len(screens) > 1:
        window.setScreen(screens[1])
        window.showFullScreen()
        info += "✓ Beállítva a 2. képernyőre fullscreen módban"
    else:
        info += "✗ Nincs 2. képernyő!"

    window.show_info(info)
    print(info)

def test_config_2(app, window):
    """Konfig 2: setGeometry a 2. monitor geometriájával + showFullScreen"""
    info = "=== KONFIGURÁCIÓ 2 ===\n"
    info += "Módszer: setGeometry(screen.geometry()) + showFullScreen()\n\n"
    info += get_screen_info(app)

    screens = app.screens()
    if len(screens) > 1:
        window.setGeometry(screens[1].geometry())
        window.showFullScreen()
        info += "✓ Beállítva a 2. képernyő geometriájával + fullscreen"
    else:
        info += "✗ Nincs 2. képernyő!"

    window.show_info(info)
    print(info)

def test_config_3(app, window):
    """Konfig 3: windowHandle + setScreen + showFullScreen"""
    info = "=== KONFIGURÁCIÓ 3 ===\n"
    info += "Módszer: windowHandle().setScreen() + showFullScreen()\n\n"
    info += get_screen_info(app)

    screens = app.screens()
    if len(screens) > 1:
        window.windowHandle().setScreen(screens[1])
        window.showFullScreen()
        info += "✓ Beállítva windowHandle-n keresztül"
    else:
        info += "✗ Nincs 2. képernyő!"

    window.show_info(info)
    print(info)

def test_config_4(app, window):
    """Konfig 4: move a 2. monitor pozíciójára + showFullScreen"""
    info = "=== KONFIGURÁCIÓ 4 ===\n"
    info += "Módszer: move(screen.geometry().topLeft()) + showFullScreen()\n\n"
    info += get_screen_info(app)

    screens = app.screens()
    if len(screens) > 1:
        geom = screens[1].geometry()
        window.move(geom.topLeft())
        window.showFullScreen()
        info += f"✓ Mozgatva a pozícióra: {geom.x()}, {geom.y()}"
    else:
        info += "✗ Nincs 2. képernyő!"

    window.show_info(info)
    print(info)

def test_config_5(app, window):
    """Konfig 5: move + setGeometry + showFullScreen"""
    info = "=== KONFIGURÁCIÓ 5 ===\n"
    info += "Módszer: move() + setGeometry() + showFullScreen()\n\n"
    info += get_screen_info(app)

    screens = app.screens()
    if len(screens) > 1:
        geom = screens[1].geometry()
        window.move(geom.x(), geom.y())
        window.setGeometry(geom)
        window.showFullScreen()
        info += f"✓ Move + Geometry + Fullscreen"
    else:
        info += "✗ Nincs 2. képernyő!"

    window.show_info(info)
    print(info)

def test_config_6(app, window):
    """Konfig 6: showNormal() először, aztán setScreen + showFullScreen"""
    info = "=== KONFIGURÁCIÓ 6 ===\n"
    info += "Módszer: showNormal() -> setScreen() -> showFullScreen()\n\n"
    info += get_screen_info(app)

    screens = app.screens()
    if len(screens) > 1:
        window.showNormal()
        QApplication.processEvents()
        window.setScreen(screens[1])
        QApplication.processEvents()
        window.showFullScreen()
        info += "✓ Normal -> SetScreen -> Fullscreen"
    else:
        info += "✗ Nincs 2. képernyő!"

    window.show_info(info)
    print(info)

def main():
    print("\n" + "="*60)
    print("ABLAK POZICIONÁLÁSI TESZT")
    print("="*60)
    print("\nHasználat:")
    print("  python3 test_window_positions.py [konfig_szám]")
    print("\nKonfigurációk:")
    print("  1: setScreen + showFullScreen")
    print("  2: setGeometry + showFullScreen")
    print("  3: windowHandle + setScreen + showFullScreen")
    print("  4: move + showFullScreen")
    print("  5: move + setGeometry + showFullScreen")
    print("  6: showNormal + setScreen + showFullScreen")
    print("="*60 + "\n")

    config = 1
    if len(sys.argv) > 1:
        try:
            config = int(sys.argv[1])
            if config < 1 or config > 6:
                print("Hibás konfig szám! 1-6 közötti értéket adj meg.")
                return
        except ValueError:
            print("Hibás konfig szám!")
            return

    app = QApplication(sys.argv)
    window = TestWindow()

    # Teszt funkciók
    tests = {
        1: test_config_1,
        2: test_config_2,
        3: test_config_3,
        4: test_config_4,
        5: test_config_5,
        6: test_config_6,
    }

    # Kiválasztott teszt futtatása
    tests[config](app, window)

    print(f"\n→ Konfiguráció {config} aktív.")
    print("→ Ellenőrizd, hogy a 2. monitoron jelenik-e meg fullscreen!")
    print("→ Nyomd meg Ctrl+C a kilépéshez.\n")

    sys.exit(app.exec())

if __name__ == "__main__":
    main()
