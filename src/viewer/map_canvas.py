"""Zoomable/pannable raster canvas built on QGraphicsView."""
import logging
from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QImage, QPixmap, QWheelEvent
from PyQt6.QtWidgets import QGraphicsPixmapItem, QGraphicsScene, QGraphicsView

logger = logging.getLogger(__name__)

_ZOOM_FACTOR = 1.15
_ZOOM_MIN = 0.05
_ZOOM_MAX = 50.0


class MapCanvas(QGraphicsView):
    """Displays a raster image with wheel-zoom and drag-pan.

    Two layers: base image (Z=0) and optional change overlay (Z=1).
    Both are QGraphicsPixmapItems so Qt handles compositing.
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)

        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setRenderHint(self.renderHints())  # keep default (no smooth scaling on large rasters)
        self.setBackgroundBrush(Qt.GlobalColor.darkGray)

        self._base_item: Optional[QGraphicsPixmapItem] = None
        self._overlay_item: Optional[QGraphicsPixmapItem] = None
        self._zoom_level: float = 1.0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_base_image(self, image: QImage) -> None:
        """Replace the base raster layer and reset zoom."""
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
        """Set or clear the semi-transparent overlay (change detection, etc.)."""
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

    # ------------------------------------------------------------------
    # Events
    # ------------------------------------------------------------------

    def wheelEvent(self, event: QWheelEvent) -> None:  # type: ignore[override]
        delta = event.angleDelta().y()
        factor = _ZOOM_FACTOR if delta > 0 else 1.0 / _ZOOM_FACTOR
        new_zoom = self._zoom_level * factor
        if _ZOOM_MIN <= new_zoom <= _ZOOM_MAX:
            self.scale(factor, factor)
            self._zoom_level = new_zoom
