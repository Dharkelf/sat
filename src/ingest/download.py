"""Download Sentinel-2 bands via Sentinel Hub Processing API.

Replaces the broken CDSE OData Node API / Product ZIP endpoints (DAT-ZIP-104,
DAT-ZIP-111) with the Sentinel Hub Processing API which uses the same CDSE
OAuth token and returns an AOI-clipped multi-band GeoTIFF directly.

Returned band files are single-band UINT16 GeoTIFFs where value ÷ 10000 =
surface reflectance — matching the scale factor in BandLoader.
"""
import logging
import math
import re
import tempfile
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import rasterio

from src.ingest.auth import CDSEAuth

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[int, int, str], None]

_SH_URL = "https://sh.dataspace.copernicus.eu/api/v1/process"

# Order of bands in the GeoTIFF returned by _EVALSCRIPT (rasterio band index = position + 1)
_BAND_ORDER = ["B04", "B03", "B02", "B08"]

_EVALSCRIPT = """//VERSION=3
function setup() {
  return {
    input: [{ bands: ["B02","B03","B04","B08"], units: "REFLECTANCE" }],
    output: { bands: 4, sampleType: "UINT16" }
  };
}
function evaluatePixel(sample) {
  return [
    Math.round(sample.B04 * 10000),
    Math.round(sample.B03 * 10000),
    Math.round(sample.B02 * 10000),
    Math.round(sample.B08 * 10000)
  ];
}"""


def extract_uuid(scene: dict[str, Any]) -> str:
    """Extract CDSE product UUID from a STAC item's assets or links."""
    for asset in scene.get("assets", {}).values():
        m = re.search(r"Products\('([0-9a-f\-]{36})'\)", asset.get("href", ""), re.I)
        if m:
            return m.group(1)
    for link in scene.get("links", []):
        m = re.search(r"Products\('([0-9a-f\-]{36})'\)", link.get("href", ""), re.I)
        if m:
            return m.group(1)
    item_id = scene.get("id", "")
    if re.fullmatch(r"[0-9a-f\-]{36}", item_id, re.I):
        return item_id
    raise ValueError(f"Cannot extract UUID from scene '{item_id}'")


class SceneDownloader:
    """Downloads Sentinel-2 bands via the Sentinel Hub Processing API.

    The SH Processing API clips to the AOI, returns a multi-band GeoTIFF,
    and authenticates with the same CDSE OAuth token — no Node API needed.
    Each band is saved as a separate single-band GeoTIFF so BandLoader is
    unchanged.
    """

    def __init__(
        self,
        auth: CDSEAuth,
        odata_base: str,    # unused; kept for call-site compatibility
        download_base: str,  # unused; kept for call-site compatibility
        raw_dir: Path,
        bbox: list[float] | None = None,
        sh_url: str = _SH_URL,
    ) -> None:
        self._auth = auth
        self._raw = raw_dir
        self._bbox = bbox      # AOI as [west, south, east, north] WGS84
        self._sh_url = sh_url

    def download_s2_bands(
        self,
        scene: dict[str, Any],
        bands: list[str],
        progress_cb: ProgressCallback | None = None,
        bbox: list[float] | None = None,
    ) -> dict[str, Path]:
        """Download 10 m bands for one S2 scene via the SH Processing API.

        Returns band → local single-band GeoTIFF path (UINT16, value ÷ 10000 = reflectance).
        """
        scene_id: str = scene.get("id", "unknown")
        out_dir = self._raw / "sentinel2" / scene_id
        out_dir.mkdir(parents=True, exist_ok=True)

        # Return cached files if all requested bands already exist
        cached = {b: out_dir / f"{b}.tif" for b in bands if (out_dir / f"{b}.tif").exists()}
        if set(cached) == set(bands):
            logger.info("Cache hit: all bands for %s", scene_id)
            return cached

        effective_bbox = bbox or self._bbox
        if effective_bbox is None:
            raise ValueError(
                "bbox must be provided either at SceneDownloader construction or per call"
            )

        if progress_cb:
            progress_cb(0, 3, "Requesting scene from Sentinel Hub…")

        dt_str = scene.get("properties", {}).get("datetime", "")
        t_from, t_to = _sensing_time_range(dt_str)

        logger.info(
            "SH request: scene=%s  window=%s → %s  bbox=%s",
            scene_id[:40], t_from[:10], t_to[:10], effective_bbox,
        )

        if progress_cb:
            progress_cb(1, 3, "Downloading GeoTIFF from Sentinel Hub…")

        tif_bytes = self._sh_fetch(t_from, t_to, effective_bbox)

        if progress_cb:
            progress_cb(2, 3, "Extracting bands…")

        result = _split_bands(tif_bytes, bands, out_dir)
        logger.info("Saved %d band file(s) for %s", len(result), scene_id)
        return result

    def _sh_fetch(self, t_from: str, t_to: str, bbox: list[float]) -> bytes:
        width, height = _bbox_pixels(bbox, resolution_m=10)
        payload: dict[str, Any] = {
            "input": {
                "bounds": {
                    "bbox": bbox,
                    "properties": {"crs": "http://www.opengis.net/def/crs/OGC/1.3/CRS84"},
                },
                "data": [{
                    "type": "sentinel-2-l2a",
                    "dataFilter": {
                        "timeRange": {"from": t_from, "to": t_to},
                        "maxCloudCoverage": 100,
                    },
                }],
            },
            "output": {
                "width": width,
                "height": height,
                "responses": [{
                    "identifier": "default",
                    "format": {
                        "type": "image/tiff",
                        "parameters": {"bits_per_sample": 16},
                    },
                }],
            },
            "evalscript": _EVALSCRIPT,
        }
        resp = httpx.post(
            self._sh_url,
            json=payload,
            headers=self._auth.auth_header(),
            timeout=180.0,
        )
        if not resp.is_success:
            raise RuntimeError(
                f"SH Processing API {resp.status_code}: {resp.text[:400]}"
            )
        return resp.content


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sensing_time_range(dt_str: str) -> tuple[str, str]:
    """Return ISO (from, to) strings for a ±12 h window around dt_str."""
    try:
        dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
    except ValueError:
        dt = datetime.now(tz=UTC) - timedelta(days=1)
    fmt = "%Y-%m-%dT%H:%M:%SZ"
    return (
        (dt - timedelta(hours=12)).strftime(fmt),
        (dt + timedelta(hours=12)).strftime(fmt),
    )


def _bbox_pixels(bbox: list[float], resolution_m: int = 10) -> tuple[int, int]:
    """Approximate pixel count for a WGS84 bbox at the given resolution."""
    w, s, e, n = bbox
    lat_mid = (s + n) / 2
    m_per_deg_lon = 111_320.0 * math.cos(math.radians(lat_mid))
    m_per_deg_lat = 110_540.0
    width = max(1, int((e - w) * m_per_deg_lon / resolution_m))
    height = max(1, int((n - s) * m_per_deg_lat / resolution_m))
    return width, height


def _split_bands(tif_bytes: bytes, bands: list[str], out_dir: Path) -> dict[str, Path]:
    """Write requested bands from a multi-band GeoTIFF as individual files."""
    result: dict[str, Path] = {}
    with tempfile.NamedTemporaryFile(suffix=".tif", delete=False) as tmp:
        tmp.write(tif_bytes)
        tmp_path = Path(tmp.name)
    try:
        with rasterio.open(tmp_path) as src:
            for raster_idx, band_name in enumerate(_BAND_ORDER, start=1):
                if band_name not in bands:
                    continue
                dest = out_dir / f"{band_name}.tif"
                profile = src.profile.copy()
                profile.update(count=1, compress="deflate", driver="GTiff")
                arr = src.read(raster_idx)
                with rasterio.open(dest, "w", **profile) as dst:
                    dst.write(arr, 1)
                result[band_name] = dest
    finally:
        tmp_path.unlink(missing_ok=True)
    return result
