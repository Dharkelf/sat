"""Change detection between two Sentinel-2 scenes.

Uses band-ratio normalized difference to reduce illumination artefacts.
"""
import logging

import numpy as np

logger = logging.getLogger(__name__)


def compute_change(
    bands_new: dict[str, np.ndarray],
    bands_old: dict[str, np.ndarray],
    band: str = "B04",
) -> np.ndarray:
    """Return a signed float32 change map in [-1, 1].

    Positive = reflectance increased (new brighter than old).
    Negative = reflectance decreased (new darker than old).
    Uses simple difference; caller decides threshold/visualisation.
    """
    new = bands_new.get(band)
    old = bands_old.get(band)
    if new is None or old is None:
        raise KeyError(f"Band {band!r} not found in one of the band dicts")

    if new.shape != old.shape:
        # Resize old to match new via nearest-neighbour
        from scipy.ndimage import zoom as nd_zoom  # type: ignore[import]
        fy = new.shape[0] / old.shape[0]
        fx = new.shape[1] / old.shape[1]
        old = nd_zoom(old, (fy, fx), order=0)
        logger.debug("Resampled old scene from %s to %s", old.shape, new.shape)

    diff = (new.astype(np.float32) - old.astype(np.float32))
    return np.clip(diff, -1.0, 1.0)


def multi_band_change(
    bands_new: dict[str, np.ndarray],
    bands_old: dict[str, np.ndarray],
    bands: list[str] | None = None,
) -> np.ndarray:
    """Mean absolute change across multiple bands for a composite change signal."""
    if bands is None:
        bands = sorted(set(bands_new) & set(bands_old))
    diffs = [np.abs(compute_change(bands_new, bands_old, b)) for b in bands]
    return np.mean(diffs, axis=0).astype(np.float32)
