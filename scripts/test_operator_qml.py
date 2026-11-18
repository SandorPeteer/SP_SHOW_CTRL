"""Exercise the real Qt modal popup without starting the media controller."""
import os
from pathlib import Path
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
try:
    from PySide6.QtCore import QObject, Property, QUrl, Signal, Slot, Qt
    from PySide6.QtGui import QGuiApplication, QImage
    from PySide6.QtQml import QQmlApplicationEngine, QJSValue
    from PySide6.QtQuick import QQuickImageProvider
    from PySide6.QtTest import QTest
except ImportError:
    QGuiApplication = None


if QGuiApplication is not None:
    class Bridge(QObject):
        stateChanged = Signal()
        listsChanged = Signal()
        previewsChanged = Signal()
        waveformChanged = Signal()

        def __init__(self):
            super().__init__()
            self.current = {}
            self.commands = []

        @Property("QVariantMap", notify=stateChanged)
        def state(self):
            return self.current

        @Property("QVariantMap", notify=listsChanged)
        def lists(self):
            return {}

        @Property("QVariantMap", notify=previewsChanged)
        def previews(self):
            return {}

        @Property("QVariantList", notify=waveformChanged)
        def waveform(self):
            return []

        @Slot(str, "QVariant")
        def press(self, name, value):
            if isinstance(value, QJSValue):
                value = value.toVariant()
            self.commands.append((name, value))

    class EmptyImages(QQuickImageProvider):
        def __init__(self):
            super().__init__(QQuickImageProvider.ImageType.Image)

        def requestImage(self, image_id, size, requested_size):
            image = QImage(1, 1, QImage.Format.Format_ARGB32_Premultiplied)
            image.fill(Qt.GlobalColor.transparent)
            return image


@unittest.skipIf(QGuiApplication is None, "PySide6 is required for the Qt UI test")
class OperatorQmlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QGuiApplication.instance() or QGuiApplication([])

    def test_confirmation_is_owned_modal_and_escape_keeps_show(self):
        bridge = Bridge()
        engine = QQmlApplicationEngine()
        engine.addImageProvider("icon", EmptyImages())
        engine.rootContext().setContextProperty("operatorBridge", bridge)
        engine.load(QUrl.fromLocalFile(str(Path(__file__).resolve().parents[1] / "operator.qml")))
        self.assertTrue(engine.rootObjects())
        window = engine.rootObjects()[0]
        bridge.current = {"dialog": {"id": 1, "kind": "message", "title": "New show",
            "message": "Start a new show? Unsaved edits will be lost.", "cancel": "no",
            "buttons": [{"label": "Keep show", "value": "no"}, {"label": "New show", "value": "yes"}]}}
        bridge.stateChanged.emit()
        QTest.qWait(100)
        dialog = window.findChild(QObject, "operatorMessageDialog")
        self.assertTrue(dialog.property("visible"))
        self.assertTrue(dialog.property("modal"))
        self.assertEqual(dialog.property("title"), "New show")
        QTest.keyClick(window, Qt.Key.Key_Escape)
        QTest.qWait(100)
        self.assertEqual(bridge.commands, [("dialog_result", {"id": 1, "value": "no"})])
        self.assertFalse(dialog.property("visible"))
        # A subsequent request can open again; stale ids must not re-open it.
        bridge.current["dialog"]["id"] = 2
        bridge.stateChanged.emit()
        QTest.qWait(50)
        self.assertTrue(dialog.property("visible"))
        items = list(dialog.property("contentItem").childItems())
        button = None
        while items:
            item = items.pop()
            if item.objectName() == "dialogButton_yes":
                button = item
                break
            items.extend(item.childItems())
        self.assertIsNotNone(button)
        from PySide6.QtCore import QMetaObject
        self.assertTrue(QMetaObject.invokeMethod(button, "clicked"))
        QTest.qWait(50)
        self.assertEqual(bridge.commands[-1], ("dialog_result", {"id": 2, "value": "yes"}))
        self.assertFalse(dialog.property("visible"))
        window.hide()
        from shiboken6 import delete
        delete(engine)  # Destroy QML while its context bridge is still alive.


if __name__ == "__main__":
    unittest.main()
