"""YOLOv8 inference wrapper for construction-change detection.

Returns Detection objects; colour assignment (green/yellow/red) is
determined by confidence against thresholds in settings.yaml.
"""
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class Detection:
    x1: int
    y1: int
    x2: int
    y2: int
    confidence: float
    label: str

    @property
    def color(self) -> str:
        """Return display colour based on confidence: green / yellow / red."""
        if self.confidence >= 0.70:
            return "green"
        if self.confidence >= 0.40:
            return "yellow"
        return "red"


class ConstructionDetector:
    """Runs YOLOv8 inference on a numpy RGB image.

    Lazy-loads the model on first call so startup is not delayed.
    Falls back to the base pretrained model if no custom weights exist.
    """

    def __init__(self, model_path: Path, conf_threshold: float = 0.25) -> None:
        self._model_path = model_path
        self._conf = conf_threshold
        self._model: Optional[object] = None

    def is_trained(self) -> bool:
        return self._model_path.exists()

    def detect(self, rgb_array: np.ndarray) -> list[Detection]:
        """Run inference and return detections sorted by confidence descending."""
        from ultralytics import YOLO

        if self._model is None:
            path = str(self._model_path) if self.is_trained() else "yolov8n.pt"
            logger.info("Loading YOLO model from %s", path)
            self._model = YOLO(path)

        results = self._model.predict(
            source=rgb_array,
            conf=self._conf,
            verbose=False,
        )
        detections: list[Detection] = []
        for r in results:
            boxes = r.boxes
            if boxes is None:
                continue
            for box in boxes:
                x1, y1, x2, y2 = (int(v) for v in box.xyxy[0].tolist())
                conf = float(box.conf[0])
                cls_id = int(box.cls[0])
                label = r.names.get(cls_id, str(cls_id))
                detections.append(Detection(x1, y1, x2, y2, conf, label))

        detections.sort(key=lambda d: d.confidence, reverse=True)
        logger.info("Detected %d objects (conf≥%.2f)", len(detections), self._conf)
        return detections
