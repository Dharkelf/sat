"""Shared pytest fixtures for ingest and raster tests."""
import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest


@pytest.fixture()
def sample_stac_item() -> dict[str, Any]:
    """Minimal STAC item that mirrors a real CDSE Sentinel-2 response."""
    uuid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    return {
        "id": "S2B_MSIL2A_20250101T095259_N0510_R122_T33UWP_20250101T130135",
        "type": "Feature",
        "properties": {
            "datetime": "2025-01-01T09:52:59Z",
            "eo:cloud_cover": 12.5,
            "productType": "S2MSI2A",
        },
        "geometry": {"type": "Polygon", "coordinates": []},
        "assets": {
            "PRODUCT": {
                "href": f"https://download.dataspace.copernicus.eu/odata/v1/Products('{uuid}')/$value"
            }
        },
        "links": [
            {
                "rel": "derived_from",
                "href": f"https://catalogue.dataspace.copernicus.eu/odata/v1/Products('{uuid}')",
            }
        ],
    }


@pytest.fixture()
def synthetic_bands() -> dict[str, np.ndarray]:
    """4-band 100×80 float32 array resembling cropped S2 data."""
    rng = np.random.default_rng(42)
    shape = (80, 100)
    return {
        "B02": rng.uniform(0.0, 0.3, shape).astype(np.float32),
        "B03": rng.uniform(0.0, 0.35, shape).astype(np.float32),
        "B04": rng.uniform(0.0, 0.4, shape).astype(np.float32),
        "B08": rng.uniform(0.0, 0.6, shape).astype(np.float32),
    }


@pytest.fixture()
def tmp_catalog(tmp_path: Path) -> Any:
    """SceneCatalog backed by a temporary directory."""
    from src.ingest.catalog import SceneCatalog
    return SceneCatalog(
        catalog_path=tmp_path / "catalog.parquet",
        raw_dir=tmp_path / "raw",
        keep_scenes=2,
        max_size_gb=10.0,
    )
