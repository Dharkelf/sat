"""Tests for raster loading and change detection."""
import numpy as np
import pytest

from src.raster.change import compute_change, multi_band_change
from src.raster.loader import BandLoader, _stretch, _to_uint8

# ---------------------------------------------------------------------------
# Stretch / uint8 conversion
# ---------------------------------------------------------------------------

class TestStretch:
    def test_output_range(self):
        arr = np.linspace(0.0, 1.0, 1000, dtype=np.float32)
        out = _stretch(arr)
        assert out.min() >= 0.0
        assert out.max() <= 1.0

    def test_all_zeros_returns_zeros(self):
        arr = np.zeros((10, 10), dtype=np.float32)
        out = _stretch(arr)
        np.testing.assert_array_equal(out, np.zeros_like(arr))

    def test_constant_nonzero_returns_zeros(self):
        arr = np.full((5, 5), 0.5, dtype=np.float32)
        out = _stretch(arr)
        np.testing.assert_array_equal(out, np.zeros_like(arr))


class TestToUint8:
    def test_values_clipped_to_byte(self):
        arr = np.array([[0.0, 0.5, 1.0, 1.5]], dtype=np.float32)
        # dstack needs (H,W,3) — just test the utility directly
        out = _to_uint8(arr)
        assert out.max() <= 255
        assert out.min() >= 0
        assert out.dtype == np.uint8


# ---------------------------------------------------------------------------
# BandLoader composite
# ---------------------------------------------------------------------------

class TestBandLoaderComposites:
    def _loader(self) -> BandLoader:
        return BandLoader(aoi_bbox_wgs84=[16.18, 48.11, 16.58, 48.32])

    def test_rgb_composite_shape(self, synthetic_bands):
        loader = self._loader()
        rgb = loader.rgb_composite(synthetic_bands)
        assert rgb.ndim == 3
        assert rgb.shape[2] == 3
        assert rgb.dtype == np.uint8

    def test_nir_composite_shape(self, synthetic_bands):
        loader = self._loader()
        nir = loader.nir_composite(synthetic_bands)
        assert nir.shape == synthetic_bands["B04"].shape + (3,)
        assert nir.dtype == np.uint8

    def test_rgb_and_nir_differ(self, synthetic_bands):
        loader = self._loader()
        rgb = loader.rgb_composite(synthetic_bands)
        nir = loader.nir_composite(synthetic_bands)
        assert not np.array_equal(rgb, nir)


# ---------------------------------------------------------------------------
# Change detection
# ---------------------------------------------------------------------------

class TestComputeChange:
    def test_zero_change_for_identical_scenes(self, synthetic_bands):
        diff = compute_change(synthetic_bands, synthetic_bands, band="B04")
        np.testing.assert_array_almost_equal(diff, np.zeros_like(diff), decimal=5)

    def test_positive_change_when_new_brighter(self):
        old = {"B04": np.full((10, 10), 0.1, dtype=np.float32)}
        new = {"B04": np.full((10, 10), 0.5, dtype=np.float32)}
        diff = compute_change(new, old, band="B04")
        assert diff.mean() > 0

    def test_output_clipped_to_minus_one_one(self):
        old = {"B04": np.zeros((10, 10), dtype=np.float32)}
        new = {"B04": np.ones((10, 10), dtype=np.float32)}
        diff = compute_change(new, old, band="B04")
        assert diff.max() <= 1.0
        assert diff.min() >= -1.0

    def test_missing_band_raises(self, synthetic_bands):
        with pytest.raises(KeyError):
            compute_change(synthetic_bands, synthetic_bands, band="B99")

    def test_shape_mismatch_handled(self):
        new = {"B04": np.ones((20, 20), dtype=np.float32) * 0.5}
        old = {"B04": np.ones((10, 10), dtype=np.float32) * 0.3}
        diff = compute_change(new, old, band="B04")
        assert diff.shape == (20, 20)


class TestMultiBandChange:
    def test_multi_band_output_nonnegative(self, synthetic_bands):
        bands_shifted = {k: np.clip(v + 0.1, 0, 1) for k, v in synthetic_bands.items()}
        result = multi_band_change(bands_shifted, synthetic_bands)
        assert (result >= 0).all()

    def test_uses_intersection_of_available_bands(self, synthetic_bands):
        partial = {k: v for k, v in synthetic_bands.items() if k != "B08"}
        result = multi_band_change(synthetic_bands, partial)
        assert result is not None
