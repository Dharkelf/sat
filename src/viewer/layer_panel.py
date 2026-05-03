"""Right-side panel with layer toggle buttons and scene info display."""
import logging

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QLabel,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

logger = logging.getLogger(__name__)

LayerMode = str  # "rgb" | "nir" | "change"


class LayerPanel(QWidget):
    """Emits layer_changed(mode) when the user switches display layers."""

    layer_changed = pyqtSignal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedWidth(180)

        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        layout.setSpacing(6)

        layout.addWidget(_section_label("Display Mode"))

        self._btn_rgb = QRadioButton("RGB (True Color)")
        self._btn_nir = QRadioButton("False Color NIR")
        self._btn_change = QRadioButton("Change Overlay")
        self._btn_rgb.setChecked(True)

        group = QButtonGroup(self)
        for btn in (self._btn_rgb, self._btn_nir, self._btn_change):
            group.addButton(btn)
            layout.addWidget(btn)

        group.buttonClicked.connect(self._on_mode_changed)

        layout.addWidget(_divider())
        layout.addWidget(_section_label("Scene Info"))

        self._info_label = QLabel("No data loaded")
        self._info_label.setWordWrap(True)
        self._info_label.setAlignment(Qt.AlignmentFlag.AlignTop)
        layout.addWidget(self._info_label)

        layout.addStretch()

    def update_scene_info(self, text: str) -> None:
        self._info_label.setText(text)

    def set_mode(self, mode: LayerMode) -> None:
        mapping = {"rgb": self._btn_rgb, "nir": self._btn_nir, "change": self._btn_change}
        btn = mapping.get(mode)
        if btn:
            btn.setChecked(True)

    def _on_mode_changed(self, btn: QRadioButton) -> None:
        if btn is self._btn_rgb:
            self.layer_changed.emit("rgb")
        elif btn is self._btn_nir:
            self.layer_changed.emit("nir")
        else:
            self.layer_changed.emit("change")


def _section_label(text: str) -> QLabel:
    lbl = QLabel(f"<b>{text}</b>")
    lbl.setAlignment(Qt.AlignmentFlag.AlignLeft)
    return lbl


def _divider() -> QFrame:
    line = QFrame()
    line.setFrameShape(QFrame.Shape.HLine)
    line.setFrameShadow(QFrame.Shadow.Sunken)
    return line
