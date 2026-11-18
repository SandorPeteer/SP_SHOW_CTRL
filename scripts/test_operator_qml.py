"""Exercise the real Qt modal popup without starting the media controller."""
import os
from pathlib import Path
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

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
            self.models = {}
            self.preview_urls = {}

        @Property("QVariantMap", notify=stateChanged)
        def state(self):
            return self.current

        @Property("QVariantMap", notify=listsChanged)
        def lists(self):
            return self.models

        @Property("QVariantMap", notify=previewsChanged)
        def previews(self):
            return self.preview_urls

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

    class VideoImages(EmptyImages):
        def requestImage(self, image_id, size, requested_size):
            image = QImage(1600, 900, QImage.Format.Format_RGB32)
            image.fill(Qt.GlobalColor.black)
            return image


@unittest.skipIf(QGuiApplication is None, "PySide6 is required for the Qt UI test")
class OperatorQmlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QGuiApplication.instance() or QGuiApplication([])
        from waveform_item import register_waveform_item
        register_waveform_item()

    def test_dense_waveform_follows_signed_pcm_and_preserves_silence(self):
        from waveform_item import AudioWaveform
        samples = [{"minimum": 0, "maximum": 0, "color": "#59d8ff"} for _ in range(2048)]
        for i in range(700, 1300):
            samples[i] = {"minimum": -0.25, "maximum": 0.75, "color": "#59d8ff"}
        image = AudioWaveform.render_envelope(samples)
        self.assertEqual(image.width(), 2048)
        self.assertEqual(image.pixelColor(200, 160).alpha(), 0)
        self.assertGreater(image.pixelColor(1000, 60).alpha(), 0)
        self.assertEqual(image.pixelColor(1000, 230).alpha(), 0)
        # Dense adjacent PCM bins form an unbroken envelope, not spaced sticks.
        self.assertTrue(all(image.pixelColor(x, 160).alpha() > 0 for x in range(701, 1299)))

    def test_preview_images_have_equal_usable_width_and_headers_fit_at_900(self):
        from shiboken6 import delete
        bridge = Bridge()
        bridge.preview_urls = {"live": "image://preview/live", "next": "image://preview/next"}
        bridge.current = {"selectedIsVideo": True, "audioOnly": True, "time": "−1:23:45.67",
                          "duration": "02:00:00", "slides": True, "slidePosition": "01 / 03",
                          "projectionClock": {"label": "● 0:23.45"}}
        engine = QQmlApplicationEngine()
        engine.addImageProvider("icon", EmptyImages())
        engine.addImageProvider("preview", VideoImages())
        engine.rootContext().setContextProperty("operatorBridge", bridge)
        engine.load(QUrl.fromLocalFile(str(Path(__file__).resolve().parents[1] / "operator.qml")))
        try:
            window = engine.rootObjects()[0]
            window.resize(900, 550)
            QTest.qWait(100)
            live = window.findChild(QObject, "livePreview")
            following = window.findChild(QObject, "nextPreview")
            self.assertGreater(live.width(), 280)
            self.assertAlmostEqual(live.width(), following.width(), delta=0.01)
            self.assertAlmostEqual(live.property("paintedWidth"), following.property("paintedWidth"), delta=0.01)
            self.assertAlmostEqual(live.property("paintedWidth") / live.property("paintedHeight"), 16 / 9, delta=0.001)
            for name in ("operatorHeader", "waveformHeader", "livePreviewHeader", "nextPreviewHeader", "cueHeader_a"):
                header = window.findChild(QObject, name)
                self.assertIsNotNone(header, name)
                for item in header.childItems():
                    if item.isVisible():
                        self.assertGreaterEqual(item.x(), -0.5, name)
                        self.assertLessEqual(item.x() + item.width(), header.width() + 0.5, name)
        finally:
            delete(engine)

    def test_long_list_browsing_does_not_jump_on_selection_or_playback_updates(self):
        from operator_models import OperatorRows
        from PySide6.QtCore import QPointF
        from shiboken6 import delete
        bridge = Bridge()
        model = OperatorRows(bridge)
        rows = [dict(id=str(i), name=f"Cue {i}", kind="audio", selected=i == 0,
                     role="playing" if i == 0 else "", detail="0:30") for i in range(80)]
        model.set_rows(rows)
        bridge.models = {"audio": model}
        engine = QQmlApplicationEngine()
        engine.addImageProvider("icon", EmptyImages())
        engine.rootContext().setContextProperty("operatorBridge", bridge)
        engine.load(QUrl.fromLocalFile(str(Path(__file__).resolve().parents[1] / "operator.qml")))
        try:
            window = engine.rootObjects()[0]
            view = window.findChild(QObject, "cueList_a")
            QTest.qWait(100)
            self.assertEqual(view.property("count"), 80)
            view.setProperty("contentY", 1800.0)
            QTest.qWait(50)
            before = view.property("contentY")
            self.assertGreater(before, 1700)
            point = view.mapToScene(QPointF(100, 80)).toPoint()
            QTest.mouseClick(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, point)
            self.assertEqual(bridge.commands[-1][0], "a_select")
            self.assertGreater(bridge.commands[-1][1], 40)
            for i in (60, 62, 69):
                model.set_rows([dict(row, selected=n == i, role="next" if n == i else row["role"])
                                for n, row in enumerate(rows)])
                QTest.qWait(50)
                self.assertAlmostEqual(view.property("contentY"), before, delta=0.5)
        finally:
            delete(engine)

    def test_meter_channels_fit_without_overlap_for_stereo_51_and_71(self):
        from PySide6.QtCore import QPointF
        from shiboken6 import delete
        bridge = Bridge()
        engine = QQmlApplicationEngine()
        engine.addImageProvider("icon", EmptyImages())
        engine.rootContext().setContextProperty("operatorBridge", bridge)
        engine.load(QUrl.fromLocalFile(str(Path(__file__).resolve().parents[1] / "operator.qml")))
        try:
            window = engine.rootObjects()[0]
            window.resize(900, 550)
            for count in (2, 6, 8):
                with self.subTest(channels=count):
                    channel = {"level": 0.65, "peak": 0.8}
                    bridge.current = {"vu": {"decks": [dict(name=name, active=True, available=True,
                                        channels=[dict(channel) for _ in range(count)]) for name in ("A", "B")]}}
                    bridge.stateChanged.emit()
                    QTest.qWait(30)
                    items = []
                    pending = list(window.contentItem().childItems())
                    while pending:
                        item = pending.pop()
                        items.append(item)
                        pending.extend(item.childItems())
                    for name in ("A", "B"):
                        deck = next(item for item in items if item.objectName() == "vuDeck_live_" + name)
                        bars = []
                        for item in items:
                            if not item.objectName().startswith("vuChannel_"):
                                continue
                            if item.parentItem().parentItem() is deck:
                                bars.append(item)
                        self.assertEqual(len(bars), count)
                        right_edge = 0.0
                        for bar in sorted(bars, key=lambda item: item.x()):
                            x = bar.mapToItem(deck, QPointF()).x()
                            self.assertGreaterEqual(x, right_edge - 0.01)
                            self.assertLessEqual(x + bar.width(), deck.width() + 0.01)
                            self.assertGreater(bar.width(), 0)
                            if count == 2:
                                self.assertEqual(bar.width(), 8)
                            peak = next(item for item in bar.childItems() if item.objectName() == "vuPeak")
                            self.assertGreaterEqual(peak.x(), 0)
                            self.assertLessEqual(peak.x() + peak.width(), bar.width())
                            right_edge = x + bar.width()
        finally:
            delete(engine)

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
