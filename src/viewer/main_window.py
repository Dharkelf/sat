"""Main application window.

Layout:
  ┌──────────────────────────────────────────┐
  │  Toolbar: [Download S2]  [Download S1]   │
  ├────────────────────────┬─────────────────┤
  │                        │                 │
  │     MapCanvas          │   LayerPanel    │
  │   (zoom / pan)         │  (RGB/NIR/Chg)  │
  │                        │                 │
  ├────────────────────────┴─────────────────┤
  │  Status bar  ▓▓▓▓░░░░░  "Downloading…"  │
  └──────────────────────────────────────────┘
"""
import json
import logging
from datetime import timezone
from pathlib import Path
from typing import Any, Optional

import numpy as np
from PyQt6.QtCore import QThread, Qt, pyqtSignal
from PyQt6.QtGui import QImage
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QProgressBar,
    QPushButton,
    QToolBar,
    QWidget,
)

from src.ingest.auth import CDSEAuth
from src.ingest.catalog import SceneCatalog
from src.ingest.download import SceneDownloader
from src.ingest.search import SceneSearcher
from src.raster.change import compute_change
from src.raster.loader import BandLoader, change_to_qimage, ndarray_to_qimage
from src.viewer.layer_panel import LayerPanel
from src.viewer.map_canvas import MapCanvas

logger = logging.getLogger(__name__)


class _DownloadWorker(QThread):
    """Runs scene discovery + download in a background thread."""

    progress = pyqtSignal(int, int, str)   # current, total, message
    finished = pyqtSignal(list)            # list[dict]  scene records from catalog
    error = pyqtSignal(str)

    def __init__(
        self,
        searcher: SceneSearcher,
        downloader: SceneDownloader,
        catalog: SceneCatalog,
        settings: dict[str, Any],
    ) -> None:
        super().__init__()
        self._searcher = searcher
        self._downloader = downloader
        self._catalog = catalog
        self._settings = settings

    def run(self) -> None:
        try:
            s2 = self._settings["sentinel2"]
            aoi = self._settings["aoi"]["bbox"]
            bands_cfg = s2["bands"]
            bands = [bands_cfg["blue"], bands_cfg["green"], bands_cfg["red"], bands_cfg["nir"]]

            self.progress.emit(0, 1, "Searching for Sentinel-2 scenes…")
            scenes = self._searcher.search_sentinel2(
                bbox=aoi,
                max_scenes=s2["max_scenes"],
                cloud_max=s2["cloud_cover_max"],
                days_back=s2["search_days_back"],
            )

            if not scenes:
                self.error.emit("No Sentinel-2 scenes found for the AOI and cloud filter.")
                return

            # Only download scenes that are newer than what we already have
            latest_local = self._catalog.latest_sensing_dt("sentinel2")
            new_scenes = []
            for sc in scenes:
                dt_str = sc.get("properties", {}).get("datetime", "")
                if dt_str:
                    import pandas as pd
                    sc_dt = pd.Timestamp(dt_str, tz="UTC").to_pydatetime()
                    if latest_local is None or sc_dt > latest_local:
                        new_scenes.append(sc)
                    elif not self._catalog.is_downloaded(sc["id"]):
                        new_scenes.append(sc)

            if not new_scenes:
                logger.info("All found scenes already downloaded and current — skipping")
                self.progress.emit(1, 1, "Already up to date.")
                existing = self._catalog.list_scenes("sentinel2")
                self.finished.emit(existing.to_dict("records"))
                return

            raw_dir = Path(self._settings["storage"]["raw_dir"])
            for i, scene in enumerate(new_scenes):
                self.progress.emit(i, len(new_scenes), f"Downloading scene {i+1}/{len(new_scenes)}…")

                def _cb(cur: int, total: int, msg: str) -> None:
                    self.progress.emit(i * 10 + cur, len(new_scenes) * 10, msg)

                paths = self._downloader.download_s2_bands(scene, bands, progress_cb=_cb)
                self._catalog.record(scene, "sentinel2", paths)

            self.progress.emit(1, 1, "Download complete.")
            self.finished.emit(self._catalog.list_scenes("sentinel2").to_dict("records"))
        except Exception as exc:
            logger.exception("Download worker failed")
            self.error.emit(str(exc))


class MainWindow(QMainWindow):
    """Top-level window wiring canvas, layer panel, toolbar and download logic."""

    def __init__(
        self,
        settings: dict[str, Any],
        auth: CDSEAuth,
        searcher: SceneSearcher,
        downloader: SceneDownloader,
        catalog: SceneCatalog,
    ) -> None:
        super().__init__()
        self._settings = settings
        self._auth = auth
        self._searcher = searcher
        self._downloader = downloader
        self._catalog = catalog

        self._bands_new: Optional[dict[str, np.ndarray]] = None
        self._bands_old: Optional[dict[str, np.ndarray]] = None
        self._loader = BandLoader(
            aoi_bbox_wgs84=settings["aoi"]["bbox"],
            max_display_pixels=settings["display"]["max_display_pixels"],
        )
        self._current_mode = "rgb"
        self._worker: Optional[_DownloadWorker] = None

        self._build_ui()
        self._try_load_cached()

    # ------------------------------------------------------------------
    # UI Construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        self.setWindowTitle(self._settings["app"]["name"])
        self.resize(1200, 800)

        # Central layout: canvas + side panel
        central = QWidget()
        layout = QHBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._canvas = MapCanvas()
        self._panel = LayerPanel()
        layout.addWidget(self._canvas, stretch=1)
        layout.addWidget(self._panel)
        self.setCentralWidget(central)

        # Toolbar
        tb = QToolBar("Controls")
        tb.setMovable(False)
        self.addToolBar(tb)

        self._btn_download = QPushButton("⬇  Download Latest S2")
        self._btn_download.clicked.connect(self._on_download_clicked)
        tb.addWidget(self._btn_download)

        # Status bar
        self._status_label = QLabel("Ready")
        self._progress_bar = QProgressBar()
        self._progress_bar.setFixedWidth(200)
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setValue(0)
        self._progress_bar.setVisible(False)
        self.statusBar().addWidget(self._status_label)
        self.statusBar().addPermanentWidget(self._progress_bar)

        # Layer panel signal
        self._panel.layer_changed.connect(self._on_layer_changed)

    # ------------------------------------------------------------------
    # Download
    # ------------------------------------------------------------------

    def _on_download_clicked(self) -> None:
        if self._worker and self._worker.isRunning():
            return
        self._btn_download.setEnabled(False)
        self._progress_bar.setVisible(True)
        self._progress_bar.setValue(0)

        self._worker = _DownloadWorker(
            searcher=self._searcher,
            downloader=self._downloader,
            catalog=self._catalog,
            settings=self._settings,
        )
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_download_finished)
        self._worker.error.connect(self._on_download_error)
        self._worker.start()

    def _on_progress(self, current: int, total: int, msg: str) -> None:
        pct = int(current / max(total, 1) * 100)
        self._progress_bar.setValue(pct)
        self._status_label.setText(msg)

    def _on_download_finished(self, records: list[dict[str, Any]]) -> None:
        self._btn_download.setEnabled(True)
        self._progress_bar.setVisible(False)
        self._status_label.setText(f"Loaded {len(records)} scene(s)")
        self._load_scenes(records)

    def _on_download_error(self, msg: str) -> None:
        self._btn_download.setEnabled(True)
        self._progress_bar.setVisible(False)
        self._status_label.setText(f"Error: {msg}")
        logger.error("Download error: %s", msg)

    # ------------------------------------------------------------------
    # Scene loading and display
    # ------------------------------------------------------------------

    def _try_load_cached(self) -> None:
        """Load already-downloaded scenes on startup if available."""
        records = self._catalog.list_scenes("sentinel2").to_dict("records")
        if records:
            self._load_scenes(records)

    def _load_scenes(self, records: list[dict[str, Any]]) -> None:
        raw_dir = Path(self._settings["storage"]["raw_dir"])
        s2_cfg = self._settings["sentinel2"]
        bands_cfg = s2_cfg["bands"]

        sorted_records = sorted(records, key=lambda r: r.get("sensing_dt", ""), reverse=True)
        if not sorted_records:
            return

        newest = sorted_records[0]
        self._bands_new = self._load_bands_from_record(newest, raw_dir)
        self._bands_old = None
        if len(sorted_records) >= 2:
            self._bands_old = self._load_bands_from_record(sorted_records[1], raw_dir)

        self._update_display()

        info_lines = []
        for i, rec in enumerate(sorted_records[:2]):
            dt = rec.get("sensing_dt", "?")
            cc = rec.get("cloud_cover", float("nan"))
            label = "NEW" if i == 0 else "PREV"
            info_lines.append(f"[{label}] {str(dt)[:10]}\nCloud: {cc:.1f}%\n")
        self._panel.update_scene_info("\n".join(info_lines))

    def _load_bands_from_record(self, record: dict[str, Any], raw_dir: Path) -> dict[str, np.ndarray]:
        paths_rel: dict[str, str] = json.loads(record.get("band_paths", "{}"))
        band_paths = {b: raw_dir / rel for b, rel in paths_rel.items()}
        return self._loader.load_bands(band_paths)

    # ------------------------------------------------------------------
    # Display
    # ------------------------------------------------------------------

    def _on_layer_changed(self, mode: str) -> None:
        self._current_mode = mode
        self._update_display()

    def _update_display(self) -> None:
        if self._bands_new is None:
            return

        s2 = self._settings["sentinel2"]["bands"]
        disp_cfg = self._settings["display"]

        if self._current_mode == "rgb":
            rgb = self._loader.rgb_composite(
                self._bands_new, red=s2["red"], green=s2["green"], blue=s2["blue"]
            )
            base_img: QImage = ndarray_to_qimage(rgb)
            self._canvas.set_base_image(base_img)
            self._canvas.set_overlay(None)

        elif self._current_mode == "nir":
            nir = self._loader.nir_composite(
                self._bands_new, nir=s2["nir"], red=s2["red"], green=s2["green"]
            )
            base_img = ndarray_to_qimage(nir)
            self._canvas.set_base_image(base_img)
            self._canvas.set_overlay(None)

        elif self._current_mode == "change":
            if self._bands_old is None:
                self._status_label.setText("Change mode requires 2 scenes — only 1 available")
                return
            rgb = self._loader.rgb_composite(
                self._bands_new, red=s2["red"], green=s2["green"], blue=s2["blue"]
            )
            base_img = ndarray_to_qimage(rgb)
            self._canvas.set_base_image(base_img)

            diff = compute_change(self._bands_new, self._bands_old, band=s2["red"])
            overlay = change_to_qimage(
                diff,
                amplify=disp_cfg["change_amplify"],
                threshold=disp_cfg["change_threshold"],
            )
            self._canvas.set_overlay(overlay)
