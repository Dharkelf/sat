"""Stores positive training examples and builds a YOLO-format dataset.

Workflow:
  1. User draws a bounding box in the viewer → save_crop() is called.
  2. The crop image (PNG) and a YOLO annotation file are written to disk.
  3. build_dataset() assembles images + labels into a YOLO dataset directory
     with a data.yaml that YOLOv8 can consume directly.
"""
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import NamedTuple

import numpy as np
from PIL import Image

logger = logging.getLogger(__name__)


class BBox(NamedTuple):
    x1: int
    y1: int
    x2: int
    y2: int


class CropStore:
    """Saves annotated positive examples and produces a YOLO dataset.

    Images go to:  <crops_dir>/images/<timestamp>.png
    Labels go to:  <crops_dir>/labels/<timestamp>.txt
    """

    CLASS_ID = 0
    CLASS_NAME = "construction"

    def __init__(self, crops_dir: Path) -> None:
        self._dir = crops_dir
        (self._dir / "images").mkdir(parents=True, exist_ok=True)
        (self._dir / "labels").mkdir(parents=True, exist_ok=True)

    def save_crop(
        self,
        rgb_array: np.ndarray,
        bbox: BBox,
        scene_id: str = "",
    ) -> Path:
        """Save the cropped region and a full-image YOLO annotation.

        Annotation approach: the full-scene RGB image is the training image;
        the drawn bounding box is recorded as a YOLO label relative to it.
        The crop PNG is also saved for user review.
        """
        h, w = rgb_array.shape[:2]
        x1, y1, x2, y2 = (
            max(0, bbox.x1), max(0, bbox.y1),
            min(w, bbox.x2), min(h, bbox.y2),
        )
        if x2 <= x1 or y2 <= y1:
            raise ValueError(f"Degenerate bbox after clipping: {bbox}")

        ts = datetime.now(tz=UTC).strftime("%Y%m%d_%H%M%S_%f")[:20]
        stem = f"{ts}_{scene_id[:12]}" if scene_id else ts

        # Save crop PNG for user review
        crop = rgb_array[y1:y2, x1:x2]
        crop_path = self._dir / "images" / f"{stem}.png"
        Image.fromarray(crop).save(crop_path)

        # YOLO label: class cx cy width height  (all normalised 0–1)
        cx = ((x1 + x2) / 2) / w
        cy = ((y1 + y2) / 2) / h
        bw = (x2 - x1) / w
        bh = (y2 - y1) / h
        label_path = self._dir / "labels" / f"{stem}.txt"
        label_path.write_text(f"{self.CLASS_ID} {cx:.6f} {cy:.6f} {bw:.6f} {bh:.6f}\n")

        logger.info("Saved training crop %s  (%.0f×%.0f px)", stem, x2 - x1, y2 - y1)
        return crop_path

    def count(self) -> int:
        return len(list((self._dir / "images").glob("*.png")))

    def build_dataset(self, full_scene_rgb: np.ndarray, dataset_dir: Path) -> Path:
        """Write a YOLO dataset folder from all saved crops + their labels.

        Returns the path to data.yaml consumed by model.train(data=...).

        The training set uses one copy of the full scene image per annotation,
        with the label referencing the drawn bounding box in normalised coords.
        Only labels that have a matching image (scene_rgb) are included.
        """
        (dataset_dir / "images" / "train").mkdir(parents=True, exist_ok=True)
        (dataset_dir / "labels" / "train").mkdir(parents=True, exist_ok=True)

        h, w = full_scene_rgb.shape[:2]
        pil_scene = Image.fromarray(full_scene_rgb)

        label_files = sorted((self._dir / "labels").glob("*.txt"))
        if not label_files:
            raise RuntimeError("No training examples saved yet")

        for lf in label_files:
            stem = lf.stem
            # Copy label
            dest_label = dataset_dir / "labels" / "train" / lf.name
            dest_label.write_text(lf.read_text())
            # Write full-scene image with unique name
            dest_img = dataset_dir / "images" / "train" / f"{stem}.png"
            if not dest_img.exists():
                pil_scene.save(dest_img)

        data_yaml = dataset_dir / "data.yaml"
        data_yaml.write_text(
            f"path: {dataset_dir.resolve()}\n"
            "train: images/train\n"
            "val: images/train\n"
            f"nc: 1\n"
            f"names: ['{self.CLASS_NAME}']\n"
        )
        logger.info("YOLO dataset written: %d samples → %s", len(label_files), dataset_dir)
        return data_yaml
