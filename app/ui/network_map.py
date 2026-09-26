"""Desenho interativo dos trechos MT/BT da subestação selecionada."""

from PySide6.QtCore import Qt, QPointF
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QTransform
from PySide6.QtWidgets import QWidget


class NetworkMap(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(420, 360)
        self.setMouseTracking(True)
        self._paths = {}
        self._bounds = None
        self._zoom = 1.0
        self._offset = QPointF()
        self._last_mouse = None
        self.show_mt = True
        self.show_bt = True
        self.show_trafos = True
        self._transformers = []

    def set_data(self, result):
        self._bounds = result["limites"]
        self._transformers = result.get("trafos", [])
        self._paths = {}
        for key in ("mt", "bt"):
            path = QPainterPath()
            for points in result["linhas_" + key]:
                path.moveTo(*points[0])
                for point in points[1:]:
                    path.lineTo(*point)
            self._paths[key] = path
        self.fit_all()

    def clear(self):
        self._paths = {}
        self._bounds = None
        self._transformers = []
        self.fit_all()

    def fit_all(self):
        self._zoom = 1.0
        self._offset = QPointF()
        self.update()

    def _transform(self):
        left, bottom, right, top = self._bounds
        width = max(right - left, 1e-8)
        height = max(top - bottom, 1e-8)
        scale = min(max(self.width() - 32, 1) / width,
                    max(self.height() - 32, 1) / height) * self._zoom
        cx, cy = (left + right) / 2, (bottom + top) / 2
        return QTransform(scale, 0, 0, -scale,
                          self.width() / 2 + self._offset.x() - cx * scale,
                          self.height() / 2 + self._offset.y() + cy * scale)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#ffffff"))
        if self._bounds is None:
            painter.setPen(QColor("#555555"))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                             "Selecione a geodatabase e uma subestação para desenhar a rede.")
            return
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setTransform(self._transform())
        for key, color, width, visible in (("bt", "#9E9E9E", 1.0, self.show_bt),
                                            ("mt", "#000000", 1.45, self.show_mt)):
            if visible:
                pen = QPen(QColor(color), width)
                pen.setCosmetic(True)
                painter.setPen(pen)
                painter.drawPath(self._paths.get(key, QPainterPath()))
        if self.show_trafos and self._transformers:
            transform = self._transform()
            painter.resetTransform()
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#4D4D4D"))
            for _, x, y in self._transformers:
                painter.drawEllipse(transform.map(QPointF(x, y)), 2.0, 2.0)

    def wheelEvent(self, event):
        if self._bounds is None:
            return
        factor = 1.25 if event.angleDelta().y() > 0 else 0.8
        before = self._transform().inverted()[0].map(event.position())
        self._zoom = max(0.5, min(100.0, self._zoom * factor))
        after = self._transform().map(before)
        self._offset += event.position() - after
        self.update()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._last_mouse = event.position()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)

    def mouseMoveEvent(self, event):
        if self._last_mouse is not None:
            self._offset += event.position() - self._last_mouse
            self._last_mouse = event.position()
            self.update()

    def mouseReleaseEvent(self, event):
        self._last_mouse = None
        self.unsetCursor()

    def mouseDoubleClickEvent(self, event):
        self.fit_all()
