"""YOLOv8 training pipeline running in a background QThread.

Signals:
  progress(epoch, total_epochs, metrics_str)  — emitted after each epoch
  finished(model_path_str)                    — emitted on success
  error(message)                              — emitted on failure
"""
import logging
from pathlib import Path

from PyQt6.QtCore import QThread, pyqtSignal

logger = logging.getLogger(__name__)


class TrainingWorker(QThread):
    """Runs YOLO fine-tuning in a background thread.

    Does NOT block the Qt event loop; progress is reported via signals.
    """

    progress = pyqtSignal(int, int, str)   # epoch, total, metrics
    finished = pyqtSignal(str)             # path to best.pt
    error = pyqtSignal(str)

    def __init__(
        self,
        data_yaml: Path,
        output_dir: Path,
        base_model: str = "yolov8n.pt",
        epochs: int = 50,
        imgsz: int = 640,
        batch: int = 8,
        patience: int = 10,
    ) -> None:
        super().__init__()
        self._data_yaml = data_yaml
        self._output_dir = output_dir
        self._base_model = base_model
        self._epochs = epochs
        self._imgsz = imgsz
        self._batch = batch
        self._patience = patience

    def run(self) -> None:
        try:
            from ultralytics import YOLO

            self.progress.emit(0, self._epochs, "Loading base model …")
            model = YOLO(self._base_model)

            self.progress.emit(0, self._epochs, "Training started …")
            results = model.train(
                data=str(self._data_yaml),
                epochs=self._epochs,
                imgsz=self._imgsz,
                batch=self._batch,
                patience=self._patience,
                project=str(self._output_dir),
                name="train",
                exist_ok=True,
                verbose=False,
            )

            best = Path(results.save_dir) / "weights" / "best.pt"
            if not best.exists():
                self.error.emit(f"Training finished but best.pt not found at {best}")
                return

            logger.info("Training complete, best weights: %s", best)
            self.finished.emit(str(best))

        except Exception as exc:
            logger.exception("Training failed")
            self.error.emit(str(exc))
