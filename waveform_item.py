"""Dense PCM envelope rendering, cached until the selected source changes."""
from PySide6.QtCore import QPointF, Property, QRectF, Signal, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPolygonF
from PySide6.QtQml import qmlRegisterType
from PySide6.QtQuick import QQuickPaintedItem


class AudioWaveform(QQuickPaintedItem):
    samplesChanged = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._samples = []
        self._image = QImage()

    @Property("QVariantList", notify=samplesChanged)
    def samples(self):
        return self._samples

    @samples.setter
    def samples(self, value):
        if self._samples == value:
            return
        self._samples = value
        self._image = self.render_envelope(value)
        self.samplesChanged.emit()
        self.update()

    @staticmethod
    def render_envelope(samples):
        if not samples:
            return QImage()
        height = 320
        image = QImage(max(2, len(samples)), height, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        painter.setPen(Qt.PenStyle.NoPen)
        def edges(sample):
            return ((1 - max(-1, min(1, sample.get("maximum", 0)))) * (height - 1) / 2,
                    (1 - max(-1, min(1, sample.get("minimum", 0)))) * (height - 1) / 2)
        for index, sample in enumerate(samples):
            upper, lower = edges(sample)
            following_upper, following_lower = edges(samples[min(index + 1, len(samples) - 1)])
            painter.setBrush(QColor(sample.get("color", "#59d8ff")))
            painter.drawPolygon(QPolygonF([QPointF(index, upper), QPointF(index + 1, following_upper),
                                          QPointF(index + 1, following_lower), QPointF(index, lower)]))
        painter.end()
        return image

    def paint(self, painter):
        if not self._image.isNull():
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            painter.drawImage(self.boundingRect(), self._image, QRectF(self._image.rect()))


def register_waveform_item():
    qmlRegisterType(AudioWaveform, "ShowControl", 1, 0, "AudioWaveform")
