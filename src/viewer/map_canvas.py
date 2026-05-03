"""Zoomable/pannable raster canvas built on QGraphicsView.

Supports two interaction modes:
  - pan  (default): drag to scroll, wheel to zoom
  - draw (training): click top-left then bottom-right to define a ROI
"""
import logging
from typing import Optional

from PyQt6.QtCore import QPoint, QRect, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QImage, QMouseEvent, QPen, QPixmap, QWheelEvent
from PyQt6.QtWidgets import (
    QGraphicsPixmapItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsView,
)

from src.detection.detector import Detection

logger = logging.getLogger(__name__)

_ZOOM_FACTOR = 1.15
_ZOOM_MIN = 0.05
_ZOOM_MAX = 50.0

_CONF_COLORS = {
    "green":  QColor(0, 220, 0, 200),
    "yellow": QColor(220, 220, 0, 200),
    "red":    QColor(220, 0, 0, 200),
}
_BOX_THICKNESS = 3


class MapCanvas(QGraphicsView):
    """Displays a raster image with wheel-zoom and drag-pan.

    Layers (Z order):
      0 — base raster image
      1 — change/NIR overlay (semi-transparent)
      2 — detection bounding boxes
      3 — ROI rubber-band while drawing
    """

    roi_drawn = pyqtSignal(QRect)   # image-pixel coordinates

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)

        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setBackgroundBrush(Qt.GlobalColor.darkGray)

        self._base_item: Optional[QGraphicsPixmapItem] = None
        self._overlay_item: Optional[QGraphicsPixmapItem] = None
        self._detection_items: list[QGraphicsRectItem] = []
        self._rubber_band: Optional[QGraphicsRectItem] = None
        self._zoom_level: float = 1.0

        # draw-mode state
        self._draw_mode: bool = False
        self._draw_start: Optional[QPoint] = None   # scene coordinates

    # ------------------------------------------------------------------
    # Public API — image layers
    # ------------------------------------------------------------------

    def set_base_image(self, image: QImage) -> None:
        if self._base_item is not None:
            self._scene.removeItem(self._base_item)
        px = QPixmap.fromImage(image)
        self._base_item = self._scene.addPixmap(px)
        self._base_item.setZValue(0)
        self._scene.setSceneRect(self._base_item.boundingRect())
        self.fitInView(self._base_item, Qt.AspectRatioMode.KeepAspectRatio)
        self._zoom_level = 1.0
        logger.debug("Base image set: %dx%d", image.width(), image.height())

    def set_overlay(self, image: Optional[QImage], opacity: float = 0.55) -> None:
        if self._overlay_item is not None:
            self._scene.removeItem(self._overlay_item)
            self._overlay_item = None
        if image is None:
            return
        px = QPixmap.fromImage(image)
        self._overlay_item = self._scene.addPixmap(px)
        self._overlay_item.setZValue(1)
        self._overlay_item.setOpacity(opacity)

    def clear(self) -> None:
        self._scene.clear()
        self._base_item = None
        self._overlay_item = None
        self._detection_items = []
        self._rubber_band = None

    # ------------------------------------------------------------------
    # Public API — detection boxes
    # ------------------------------------------------------------------

    def set_detections(self, detections: list[Detection]) -> None:
        """Draw confidence-coloured rectangles for each detection."""
        for item in self._detection_items:
            self._scene.removeItem(item)
        self._detection_items = []

        for det in detections:
            color = _CONF_COLORS.get(det.color, _CONF_COLORS["red"])
            pen = QPen(color, _BOX_THICKNESS)
            rect_item = self._scene.addRect(
                det.x1, det.y1, det.x2 - det.x1, det.y2 - det.y1, pen
            )
            rect_item.setZValue(2)
            self._detection_items.append(rect_item)

    def clear_detections(self) -> None:
        for item in self._detection_items:
            self._scene.removeItem(item)
        self._detection_items = []

    # ------------------------------------------------------------------
    # Public API — draw mode
    # ------------------------------------------------------------------

    def set_draw_mode(self, enabled: bool) -> None:
        self._draw_mode = enabled
        self._draw_start = None
        self._clear_rubber_band()
        if enabled:
            self.setDragMode(QGraphicsView.DragMode.NoDrag)
            self.setCursor(Qt.CursorShape.CrossCursor)
        else:
            self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
            self.setCursor(Qt.CursorShape.ArrowCursor)

    # ------------------------------------------------------------------
    # Events
    # ------------------------------------------------------------------

    def wheelEvent(self, event: QWheelEvent) -> None:  # type: ignore[override]  # noqa: N802
        if self._draw_mode:
            return
        delta = event.angleDelta().y()
        factor = _ZOOM_FACTOR if delta > 0 else 1.0 / _ZOOM_FACTOR
        new_zoom = self._zoom_level * factor
        if _ZOOM_MIN <= new_zoom <= _ZOOM_MAX:
            self.scale(factor, factor)
            self._zoom_level = new_zoom

    def mousePressEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]  # noqa: N802
        if not self._draw_mode or event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return
        self._draw_start = self._to_image_point(event.pos())
        self._clear_rubber_band()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]  # noqa: N802
        if not self._draw_mode or self._draw_start is None:
            super().mouseMoveEvent(event)
            return
        end = self._to_image_point(event.pos())
        self._update_rubber_band(self._draw_start, end)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]  # noqa: N802
        if not self._draw_mode or event.button() != Qt.MouseButton.LeftButton:
            super().mouseReleaseEvent(event)
            return
        if self._draw_start is None:
            return
        end = self._to_image_point(event.pos())
        self._clear_rubber_band()
        rect = QRect(self._draw_start, end).normalized()
        if rect.width() > 5 and rect.height() > 5:
            self.roi_drawn.emit(rect)
        self._draw_start = None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _to_image_point(self, view_pos: QPoint) -> QPoint:
        scene_pos = self.mapToScene(view_pos)
        if self._base_item:
            item_pos = self._base_item.mapFromScene(scene_pos)
            return QPoint(int(item_pos.x()), int(item_pos.y()))
        return QPoint(int(scene_pos.x()), int(scene_pos.y()))

    def _update_rubber_band(self, start: QPoint, end: QPoint) -> None:
        self._clear_rubber_band()
        pen = QPen(QColor(255, 255, 0, 220), 2, Qt.PenStyle.DashLine)
        r = QRect(start, end).normalized()
        self._rubber_band = self._scene.addRect(r.x(), r.y(), r.width(), r.height(), pen)
        self._rubber_band.setZValue(3)

    def _clear_rubber_band(self) -> None:
        if self._rubber_band is not None:
            self._scene.removeItem(self._rubber_band)
            self._rubber_band = None
