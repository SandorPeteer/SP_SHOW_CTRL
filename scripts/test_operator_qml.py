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
            self.dialog_events = []
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
            if name == "dialog_trace":
                self.dialog_events.append(value)
                return
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

    def test_presenter_page_keys_dispatch_only_while_slides_are_running(self):
        from shiboken6 import delete
        bridge = Bridge()
        bridge.current = {"slides": True}
        engine = QQmlApplicationEngine()
        engine.addImageProvider("icon", EmptyImages())
        engine.rootContext().setContextProperty("operatorBridge", bridge)
        engine.load(QUrl.fromLocalFile(str(Path(__file__).resolve().parents[1] / "operator.qml")))
        self.assertTrue(engine.rootObjects())
        window = engine.rootObjects()[0]
        try:
            window.requestActivate()
            self.assertTrue(QTest.qWaitForWindowActive(window, 1000))
            QTest.keyClick(window, Qt.Key.Key_PageDown)
            QTest.keyClick(window, Qt.Key.Key_PageUp)
            self.assertEqual(bridge.commands, [("slide_key", 1), ("slide_key", -1)])
            bridge.current = {"slides": False}
            bridge.stateChanged.emit()
            QTest.keyClick(window, Qt.Key.Key_PageDown)
            QTest.keyClick(window, Qt.Key.Key_PageUp)
            self.assertEqual(len(bridge.commands), 2)
        finally:
            delete(engine)

    def test_open_cancel_then_save_keeps_request_ids_and_shortcuts(self):
        from PySide6.QtCore import QMetaObject
        from shiboken6 import delete
        bridge = Bridge()
        engine = QQmlApplicationEngine()
        engine.addImageProvider("icon", EmptyImages())
        engine.rootContext().setContextProperty("operatorBridge", bridge)
        engine.load(QUrl.fromLocalFile(str(Path(__file__).resolve().parents[1] / "operator.qml")))
        window = engine.rootObjects()[0]
        try:
            dialog = window.findChild(QObject, "operatorFileDialog")
            self.assertIsNotNone(dialog)
            folder = QUrl.fromLocalFile(str(Path.home())).toString()
            for request_id, kind in enumerate(("open", "save", "open", "save"), start=1):
                bridge.current = {"dialog": dict(id=request_id, kind=kind, title=f"{kind.title()} show",
                    multiple=False, folder=folder, initial=folder + "/Show.json", filters=["Show (*.json)"], suffix="json")}
                bridge.stateChanged.emit()
                QTest.qWait(50)
                self.assertTrue(dialog.property("visible"), (kind, bridge.dialog_events))
                self.assertEqual(dialog.property("requestId"), request_id)
                # Rejection exercises the real Quick dialog signals. Native
                # HWND/input/owner behavior remains covered by the Windows gate.
                self.assertTrue(QMetaObject.invokeMethod(dialog, "reject"))
                QTest.qWait(20)
                self.assertFalse(dialog.property("visible"))
                self.assertEqual(bridge.commands[-1], ("dialog_result", {"id": request_id, "value": ""}))
                window.requestActivate()
                self.assertTrue(QTest.qWaitForWindowActive(window, 1000))
                QTest.keyClick(window, Qt.Key.Key_S, Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier)
                self.assertEqual(bridge.commands[-1], ("save_as", None))
            shown_modes = {event["mode"] for event in bridge.dialog_events if event["stage"] == "visible" and event["visible"]}
            self.assertEqual(shown_modes, {0, 2})
        finally:
            delete(engine)

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
            self.assertGreater(live.width(), 320)
            self.assertAlmostEqual(live.width(), following.width(), delta=0.01)
            self.assertAlmostEqual(live.height(), following.height(), delta=0.01)
            self.assertAlmostEqual(live.property("paintedWidth"), following.property("paintedWidth"), delta=0.01)
            self.assertAlmostEqual(live.property("paintedWidth") / live.property("paintedHeight"), 16 / 9, delta=0.001)
            live_panel = window.findChild(QObject, "livePreviewPanel")
            next_panel = window.findChild(QObject, "nextPreviewPanel")
            meter = window.findChild(QObject, "audioMeterStrip")
            self.assertAlmostEqual(live_panel.width(), next_panel.width(), delta=0.01)
            self.assertAlmostEqual(live_panel.height(), next_panel.height(), delta=0.01)
            from PySide6.QtCore import QPointF
            waveform = window.findChild(QObject, "waveformPanel")
            envelope = window.findChild(QObject, "waveformEnvelope")
            meter_position = meter.mapToItem(waveform, QPointF())
            envelope_position = envelope.mapToItem(waveform, QPointF())
            self.assertGreaterEqual(meter_position.x(), 0)
            self.assertLessEqual(meter_position.x() + meter.width(), waveform.width())
            self.assertGreaterEqual(meter_position.y(), envelope_position.y() + envelope.height())
            self.assertLessEqual(meter_position.y() + meter.height(), waveform.height())
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
            for source, label, mixed in (("audio", "Audio", False), ("presentation", "Presentation", False), ("mix", "MIX", True)):
                for count in (2, 6, 8):
                    with self.subTest(source=source, channels=count):
                        meter = dict(source=source, label=label, active=True, available=True,
                                     channels=[{"level": 0.65, "peak": 0.8} for _ in range(count)])
                        bridge.current = {"audioMix": mixed, "vu": dict(mixed=True, decks=[dict(meter, name=name) for name in ("A", "B")]) if mixed else meter}
                        bridge.stateChanged.emit()
                        QTest.qWait(30)
                        items = []
                        pending = list(window.contentItem().childItems())
                        while pending:
                            item = pending.pop()
                            items.append(item)
                            pending.extend(item.childItems())
                        area = next(item for item in items if item.objectName() == "activeAudioMeter")
                        if not mixed:
                            source_label = next(item for item in items if item.objectName() == "activeAudioSource")
                            self.assertEqual(source_label.property("text"), label)
                        groups = [item for item in items if item.objectName().startswith("meterGroup_live_")]
                        self.assertEqual(len(groups), 2 if mixed else 1)
                        for group in groups:
                            group_x = group.mapToItem(area, QPointF()).x()
                            self.assertGreaterEqual(group_x, 0)
                            self.assertLessEqual(group_x + group.width(), area.width() + 0.01)
                            bars = [item for item in items if item.objectName().startswith("vuChannel_")
                                    and item.parentItem().parentItem().parentItem() is group]
                            self.assertEqual(len(bars), count)
                            bottom_edge = 0.0
                            for bar in sorted(bars, key=lambda item: item.y()):
                                position = bar.mapToItem(group, QPointF())
                                self.assertGreaterEqual(position.x(), 0)
                                self.assertLessEqual(position.x() + bar.width(), group.width() + 0.01)
                                self.assertGreaterEqual(position.y(), bottom_edge - 0.01)
                                self.assertLessEqual(position.y() + bar.height(), group.height() + 0.01)
                                self.assertGreater(bar.height(), 0)
                                if count == 2:
                                    self.assertEqual(bar.height(), 6)
                                peak = next(item for item in bar.childItems() if item.objectName() == "vuPeak")
                                self.assertGreaterEqual(peak.x(), 0)
                                self.assertLessEqual(peak.x() + peak.width(), bar.width())
                                bottom_edge = position.y() + bar.height()
        finally:
            delete(engine)

    def test_meter_fill_keeps_measured_level_during_frequent_state_updates(self):
        from shiboken6 import delete
        bridge = Bridge()
        engine = QQmlApplicationEngine()
        engine.addImageProvider("icon", EmptyImages())
        engine.rootContext().setContextProperty("operatorBridge", bridge)
        engine.load(QUrl.fromLocalFile(str(Path(__file__).resolve().parents[1] / "operator.qml")))
        try:
            window = engine.rootObjects()[0]
            window.resize(900, 550)
            for mixed in (False, True):
                for channels in (1, 2):
                    with self.subTest(mixed=mixed, channels=channels):
                        measured_widths = []
                        for tick in range(10):
                            # RMS remains constant while the real sample peak
                            # updates in successive 33 ms engine snapshots.
                            meter = dict(source="audio", label="Audio", active=True, available=True,
                                         channels=[dict(level=0.6, peak=0.79 + 0.01 * (tick % 2)) for _ in range(channels)])
                            bridge.current = {"position": tick / 100, "audioMix": mixed,
                                "vu": dict(mixed=True, decks=[dict(meter, name=name) for name in ("A", "B")]) if mixed else meter}
                            bridge.stateChanged.emit()
                            QTest.qWait(33)
                            pending = list(window.contentItem().childItems())
                            fills = []
                            while pending:
                                item = pending.pop()
                                if item.objectName() == "vuFill":
                                    fills.append(item)
                                pending.extend(item.childItems())
                            self.assertEqual(len(fills), channels * (2 if mixed else 1))
                            for fill in fills:
                                self.assertGreater(fill.parentItem().width(), 0)
                            measured_widths.append(min(fill.width() / fill.parentItem().width() for fill in fills))
                        self.assertAlmostEqual(min(measured_widths[2:]), 0.6, delta=0.01,
                                               msg=f"RMS fill collapsed during updates: {measured_widths}")
        finally:
            delete(engine)

    def test_projection_still_context_menu_keeps_selection_and_dispatches_cue_id(self):
        from operator_models import OperatorRows
        from PySide6.QtCore import QPointF
        from shiboken6 import delete
        bridge = Bridge()
        model = OperatorRows(bridge)
        rows = [dict(id="playing", name="Playing video", kind="video", selected=True, role="playing", standby=False),
                dict(id="background", name="Background image", kind="image", selected=False, role="", standby=False)]
        model.set_rows(rows)
        bridge.models = {"projection": model}
        engine = QQmlApplicationEngine()
        engine.addImageProvider("icon", EmptyImages())
        engine.rootContext().setContextProperty("operatorBridge", bridge)
        engine.load(QUrl.fromLocalFile(str(Path(__file__).resolve().parents[1] / "operator.qml")))
        try:
            window = engine.rootObjects()[0]
            window.resize(900, 550)
            window.requestActivate()
            self.assertTrue(QTest.qWaitForWindowActive(window, 1000))
            QTest.qWait(30)
            def visual(name):
                pending = [window.contentItem()]
                while pending:
                    item = pending.pop()
                    if item.objectName() == name:
                        return item
                    pending.extend(item.childItems())
                self.fail("Missing visible item " + name)
            row = visual("cueRow_b_background")
            selected = visual("cueRow_b_playing")
            selected_color = selected.property("color")
            target_color = row.property("color")
            menu = window.findChild(QObject, "projectionStillMenu")
            self.assertEqual(len(window.findChildren(QObject, "projectionStillMenu")), 1)
            for marked in (False, True):
                with self.subTest(marked=marked):
                    model.set_rows([rows[0], dict(rows[1], standby=marked)])
                    QTest.qWait(20)
                    badge = visual("standbyBadge_b_background")
                    self.assertEqual(badge.isVisible(), marked)
                    if marked:
                        pos = badge.mapToItem(row, QPointF())
                        self.assertGreaterEqual(pos.x(), 0)
                        self.assertLessEqual(pos.x() + badge.width(), row.width())
                    bridge.commands.clear()
                    point = row.mapToScene(QPointF(120, row.height() / 2)).toPoint()
                    QTest.mouseClick(window, Qt.MouseButton.RightButton, Qt.KeyboardModifier.NoModifier, point)
                    QTest.qWait(20)
                    self.assertTrue(menu.property("visible"))
                    self.assertEqual(bridge.commands, [])
                    self.assertEqual(selected.property("color"), selected_color)
                    self.assertEqual(row.property("color"), target_color)
                    action = window.findChild(QObject, "projectionStillAction")
                    self.assertEqual(action.property("text"), "Clear Still" if marked else "Use as Still")
                    point = action.mapToScene(QPointF(action.width() / 2, action.height() / 2)).toPoint()
                    QTest.mouseClick(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, point)
                    self.assertEqual(bridge.commands, [("b_still", "background")])
                    self.assertEqual(badge.isVisible(), marked)  # Wait for backend acknowledgement.
        finally:
            delete(engine)

    def test_transport_highlights_require_confirmed_playback_and_do_not_steal_focus(self):
        from PySide6.QtCore import QPointF
        from shiboken6 import delete
        bridge = Bridge()
        bridge.models = {deck: [dict(name="Selected cue", kind="video", selected=True, role="playing")]
                         for deck in ("audio", "projection")}
        engine = QQmlApplicationEngine()
        engine.addImageProvider("icon", EmptyImages())
        engine.rootContext().setContextProperty("operatorBridge", bridge)
        engine.load(QUrl.fromLocalFile(str(Path(__file__).resolve().parents[1] / "operator.qml")))
        try:
            window = engine.rootObjects()[0]
            window.resize(900, 550)
            window.requestActivate()
            self.assertTrue(QTest.qWaitForWindowActive(window, 1000))
            QTest.qWait(30)
            play = window.findChild(QObject, "playA")
            show = window.findChild(QObject, "showB")
            focus_target = window.findChild(QObject, "mixAudioButton")
            for label, playing_a, playing_b, loading in (("Play", False, False, False),
                    ("Pause", False, False, True), ("Resume", False, False, False),
                    ("Pause", True, False, False), ("Play", False, True, False),
                    ("Pause", True, True, False), ("Play", False, False, False)):
                with self.subTest(label=label, a=playing_a, b=playing_b, loading=loading):
                    focus_target.forceActiveFocus(Qt.FocusReason.TabFocusReason)
                    before_focus = window.activeFocusItem()
                    bridge.current = dict(play=label, audioPlaying=playing_a, projectionPlaying=playing_b, busy=loading)
                    bridge.stateChanged.emit()
                    QTest.qWait(10)
                    self.assertIs(window.activeFocusItem(), before_focus)
                    self.assertEqual(play.property("label"), label)
                    self.assertEqual(show.property("label"), "Show")
                    for button, command, active, accent in ((play, "a_play", playing_a, "#59d8ff"),
                                                           (show, "b_play", playing_b, "#57f3b9")):
                        before = button.property("base")
                        self.assertEqual(before.name(), accent if active else "#1c2938")
                        self.assertEqual(button.property("tone").name(), "#07131d" if active else "#ecf5ff")
                        point = button.mapToScene(QPointF(button.width() / 2, button.height() / 2)).toPoint()
                        QTest.mouseClick(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, point)
                        self.assertEqual(bridge.commands[-1], (command, None))
                        self.assertEqual(button.property("base"), before)
        finally:
            delete(engine)

    def test_projection_timeline_click_drag_identity_and_compact_layout(self):
        from PySide6.QtCore import QPointF
        from shiboken6 import delete
        bridge = Bridge()
        timeline = dict(available=True, position=35, start=10, end=110, fraction=0.25,
                        elapsed="0:25", remaining="1:15", duration="1:40", cueId="video-1", path="/media/one.mp4")
        bridge.current = {"projectionTimeline": timeline}
        engine = QQmlApplicationEngine()
        engine.addImageProvider("icon", EmptyImages())
        engine.rootContext().setContextProperty("operatorBridge", bridge)
        engine.load(QUrl.fromLocalFile(str(Path(__file__).resolve().parents[1] / "operator.qml")))
        try:
            window = engine.rootObjects()[0]
            window.resize(900, 550)
            window.requestActivate()
            self.assertTrue(QTest.qWaitForWindowActive(window, 1000))
            QTest.qWait(30)
            strip = window.findChild(QObject, "projectionTimeline")
            slider = window.findChild(QObject, "projectionSeek")
            self.assertTrue(strip.isVisible())
            self.assertEqual(window.findChild(QObject, "projectionElapsed").property("text"), "0:25")
            self.assertEqual(window.findChild(QObject, "projectionRemaining").property("text"), "−1:15")
            transport = window.findChild(QObject, "cueTransport_b")
            self.assertLessEqual(strip.y() + strip.height(), transport.y())
            self.assertGreaterEqual(slider.x(), 0)
            self.assertLessEqual(slider.x() + slider.width(), strip.width())
            live = window.findChild(QObject, "livePreviewPanel")
            following = window.findChild(QObject, "nextPreviewPanel")
            original_geometry = (live.width(), live.height(), following.width(), following.height())
            self.assertEqual(original_geometry[:2], original_geometry[2:])
            def point(fraction):
                return slider.mapToScene(QPointF(5 + fraction * (slider.width() - 10), slider.height() / 2)).toPoint()
            QTest.mouseClick(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, point(0.75))
            self.assertEqual(len(bridge.commands), 1)
            command, payload = bridge.commands[-1]
            self.assertEqual(command, "b_seek")
            self.assertEqual((payload["cueId"], payload["path"]), ("video-1", "/media/one.mp4"))
            self.assertAlmostEqual(payload["fraction"], 0.75, delta=0.01)
            bridge.commands.clear()
            QTest.mousePress(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, point(0.25))
            QTest.mouseMove(window, point(0.80))
            self.assertEqual(bridge.commands, [])  # One seek on release, not an IPC flood.
            bridge.current = {"projectionTimeline": dict(timeline, cueId="video-2", path="/media/two.mp4", fraction=0.1)}
            bridge.stateChanged.emit()
            QTest.qWait(20)
            self.assertAlmostEqual(slider.property("value"), 0.80, delta=0.01)
            QTest.mouseRelease(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, point(0.80))
            self.assertEqual(len(bridge.commands), 1)
            payload = bridge.commands[0][1]
            self.assertEqual((payload["cueId"], payload["path"]), ("video-1", "/media/one.mp4"))
            self.assertAlmostEqual(payload["fraction"], 0.80, delta=0.01)
            bridge.current = {"projectionTimeline": {"available": False}}
            bridge.stateChanged.emit()
            QTest.qWait(20)
            self.assertFalse(strip.isVisible())
            self.assertEqual((live.width(), live.height(), following.width(), following.height()), original_geometry)
        finally:
            delete(engine)

    def test_projection_sound_click_waits_for_backend_and_header_fits_at_900(self):
        from PySide6.QtCore import QPointF
        from shiboken6 import delete
        bridge = Bridge()
        bridge.current = {"projectionSound": False, "audioMix": False}
        engine = QQmlApplicationEngine()
        engine.addImageProvider("icon", EmptyImages())
        engine.rootContext().setContextProperty("operatorBridge", bridge)
        engine.load(QUrl.fromLocalFile(str(Path(__file__).resolve().parents[1] / "operator.qml")))
        try:
            window = engine.rootObjects()[0]
            window.resize(900, 550)
            window.requestActivate()
            self.assertTrue(QTest.qWaitForWindowActive(window, 1000))
            QTest.qWait(30)
            button = window.findChild(QObject, "projectionSoundButton")
            self.assertIsNotNone(button)
            self.assertTrue(button.isVisible())
            self.assertTrue(button.isEnabled())  # Available before any cue is loaded.
            for enabled, mixed in ((False, False), (True, False), (True, True), (False, True)):
                bridge.current = {"projectionSound": enabled, "audioMix": mixed}
                bridge.stateChanged.emit()
                QTest.qWait(20)
                expected_label = "Sound on" if enabled else "Sound off"
                self.assertEqual(button.property("label"), expected_label)
                before = button.property("base")
                self.assertEqual(before.name(), "#c3a3ff" if enabled else "#1c2938")
                point = button.mapToScene(QPointF(button.width() / 2, button.height() / 2)).toPoint()
                QTest.mouseClick(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, point)
                self.assertEqual(bridge.commands[-1], ("b_sound", None))
                self.assertEqual(button.property("label"), expected_label)
                self.assertEqual(button.property("base"), before)
                header = window.findChild(QObject, "cueHeader_b")
                edge = 0
                for item in sorted((item for item in header.childItems() if item.isVisible() and item.width() > 0), key=lambda item: item.x()):
                    self.assertGreaterEqual(item.x(), edge - 0.5)
                    self.assertLessEqual(item.x() + item.width(), header.width() + 0.5)
                    edge = item.x() + item.width()
                title = window.findChild(QObject, "cueTitle_b")
                self.assertGreaterEqual(title.width(), title.implicitWidth() - 0.5)
        finally:
            delete(engine)

    def test_mix_button_waits_for_backend_and_fits_the_small_header(self):
        from PySide6.QtCore import QMetaObject
        from shiboken6 import delete
        bridge = Bridge()
        bridge.current = {"audioMix": False, "slides": True, "slidePosition": "01 / 99",
                          "projectionClock": {"label": "● 1:23:45.67"}}
        engine = QQmlApplicationEngine()
        engine.addImageProvider("icon", EmptyImages())
        engine.rootContext().setContextProperty("operatorBridge", bridge)
        engine.load(QUrl.fromLocalFile(str(Path(__file__).resolve().parents[1] / "operator.qml")))
        try:
            window = engine.rootObjects()[0]
            window.resize(900, 550)
            QTest.qWait(30)
            button = window.findChild(QObject, "mixAudioButton")
            before = button.property("base")
            self.assertTrue(QMetaObject.invokeMethod(button, "clicked"))
            self.assertEqual(bridge.commands[-1], ("mix_audio", None))
            self.assertEqual(button.property("base"), before)
            bridge.current = dict(bridge.current, audioMix=True)
            bridge.stateChanged.emit()
            QTest.qWait(30)
            self.assertEqual(button.property("base").name(), "#ffd27c")
            header = window.findChild(QObject, "livePreviewHeader")
            for item in header.childItems():
                if item.isVisible():
                    self.assertGreaterEqual(item.x(), -0.5)
                    self.assertLessEqual(item.x() + item.width(), header.width() + 0.5)
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
