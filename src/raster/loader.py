"""Band loading, AOI cropping, and display composite generation.

All raster operations go through this module.  External callers receive
numpy arrays or QImages — never raw rasterio objects.
"""
import logging
from pathlib import Path
from typing import Optional

import numpy as np
import rasterio
from rasterio.crs import CRS
from rasterio.warp import transform_bounds
from rasterio.windows import from_bounds

try:
    from PyQt6.QtGui import QImage
    _QT_AVAILABLE = True
except ImportError:
    _QT_AVAILABLE = False

logger = logging.getLogger(__name__)

# Sentinel-2 L2A reflectance scale factor
_S2_SCALE = 10_000.0


class BandLoader:
    """Loads and crops Sentinel-2 JP2 bands to an AOI, returning float32 arrays."""

    def __init__(self, aoi_bbox_wgs84: list[float], max_display_pixels: int = 4096) -> None:
        # bbox = [west, south, east, north]
        self._bbox = aoi_bbox_wgs84
        self._max_px = max_display_pixels

    def load_bands(self, band_paths: dict[str, Path]) -> dict[str, np.ndarray]:
        """Load bands cropped to the AOI.  Values are 0–1 float32 reflectance."""
        result: dict[str, np.ndarray] = {}
        for band, path in band_paths.items():
            result[band] = self._load_one(path)
        return result

    def rgb_composite(
        self,
        bands: dict[str, np.ndarray],
        red: str = "B04",
        green: str = "B03",
        blue: str = "B02",
    ) -> np.ndarray:
        """Stack R/G/B bands into a (H, W, 3) uint8 array suitable for display."""
        r = _stretch(bands[red])
        g = _stretch(bands[green])
        b = _stretch(bands[blue])
        return _to_uint8(np.dstack([r, g, b]))

    def nir_composite(
        self,
        bands: dict[str, np.ndarray],
        nir: str = "B08",
        red: str = "B04",
        green: str = "B03",
    ) -> np.ndarray:
        """Standard NIR false-colour: NIR→R, Red→G, Green→B."""
        r = _stretch(bands[nir])
        g = _stretch(bands[red])
        b = _stretch(bands[green])
        return _to_uint8(np.dstack([r, g, b]))

    def _load_one(self, path: Path) -> np.ndarray:
        with rasterio.open(path) as src:
            # Reproject AOI bbox to raster CRS for windowed read
            dst_crs = CRS.from_epsg(4326)
            bbox_in_crs = transform_bounds(dst_crs, src.crs, *self._bbox)
            win = from_bounds(*bbox_in_crs, transform=src.transform)
            data = src.read(1, window=win, boundless=True, fill_value=0).astype(np.float32)
        data /= _S2_SCALE
        data = np.clip(data, 0.0, 1.0)
        logger.debug("Loaded %s: shape=%s", path.name, data.shape)
        return data


# ---------------------------------------------------------------------------
# Conversion helpers
# ---------------------------------------------------------------------------

def _stretch(arr: np.ndarray, lo_pct: float = 2.0, hi_pct: float = 98.0) -> np.ndarray:
    """Percentile contrast stretch to [0, 1]."""
    valid = arr[arr > 0]
    if valid.size == 0:
        return arr
    lo, hi = np.percentile(valid, [lo_pct, hi_pct])
    if hi == lo:
        return np.zeros_like(arr)
    return np.clip((arr - lo) / (hi - lo), 0.0, 1.0)


def _to_uint8(rgb: np.ndarray) -> np.ndarray:
    return (rgb * 255).clip(0, 255).astype(np.uint8)


def ndarray_to_qimage(rgb: np.ndarray) -> "QImage":
    """Convert (H, W, 3) uint8 array to QImage (RGB888)."""
    if not _QT_AVAILABLE:
        raise RuntimeError("PyQt6 not available")
    h, w, _ = rgb.shape
    contiguous = np.ascontiguousarray(rgb)
    return QImage(contiguous.data, w, h, w * 3, QImage.Format.Format_RGB888).copy()


def change_to_qimage(
    change: np.ndarray,
    amplify: float = 5.0,
    threshold: float = 0.03,
) -> Optional["QImage"]:
    """Convert a signed change array to a red/green RGBA overlay QImage."""
    if not _QT_AVAILABLE:
        raise RuntimeError("PyQt6 not available")
    mask = np.abs(change) >= threshold
    if not mask.any():
        return None

    h, w = change.shape
    rgba = np.zeros((h, w, 4), dtype=np.uint8)
    amp = np.clip(np.abs(change) * amplify, 0.0, 1.0)

    # Positive change (increase) → green; negative (decrease) → red
    pos = change > threshold
    neg = change < -threshold
    rgba[pos, 1] = (amp[pos] * 255).astype(np.uint8)   # green channel
    rgba[neg, 0] = (amp[neg] * 255).astype(np.uint8)   # red channel
    rgba[mask, 3] = 180                                  # alpha

    contiguous = np.ascontiguousarray(rgba)
    return QImage(contiguous.data, w, h, w * 4, QImage.Format.Format_RGBA8888).copy()
