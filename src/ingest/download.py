"""Download individual S2 band JP2 files via CDSE OData Node API.

Uses band-selective download (not full product zip) to stay within the 2 GB
storage budget.  Only the 10m RGB+NIR bands are fetched (~20-40 MB per band).
"""
import logging
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

from src.ingest.auth import CDSEAuth

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[int, int, str], None]


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
    # Some items expose the UUID directly in the id field
    item_id = scene.get("id", "")
    if re.fullmatch(r"[0-9a-f\-]{36}", item_id, re.I):
        return item_id
    raise ValueError(f"Cannot extract UUID from scene '{item_id}'")


class SceneDownloader:
    """Downloads individual band files using the CDSE OData Node API.

    Navigates the product tree to locate specific JP2 files instead of
    downloading the full ~800 MB product zip.
    """

    def __init__(
        self,
        auth: CDSEAuth,
        odata_base: str,
        download_base: str,
        raw_dir: Path,
    ) -> None:
        self._auth = auth
        self._odata = odata_base.rstrip("/")
        self._dl = download_base.rstrip("/")
        self._raw = raw_dir

    def download_s2_bands(
        self,
        scene: dict[str, Any],
        bands: list[str],
        progress_cb: ProgressCallback | None = None,
    ) -> dict[str, Path]:
        """Download 10m JP2 files for the given bands.  Returns band→local_path."""
        uuid = extract_uuid(scene)
        scene_id: str = scene.get("id", uuid)
        out_dir = self._raw / "sentinel2" / scene_id
        out_dir.mkdir(parents=True, exist_ok=True)

        band_nodes = self._discover_s2_bands(uuid, bands)
        result: dict[str, Path] = {}

        for i, (band, node_url) in enumerate(band_nodes.items()):
            local = out_dir / f"{band}.jp2"
            if local.exists():
                logger.info("Cache hit: %s", local)
                result[band] = local
                continue
            if progress_cb:
                progress_cb(i, len(band_nodes), f"Downloading band {band} …")
            self._stream_file(node_url, local)
            result[band] = local

        return result

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _discover_s2_bands(self, uuid: str, bands: list[str]) -> dict[str, str]:
        """Return band → download URL by walking the product Node tree."""
        # Step 1: find the .SAFE root folder name
        top_nodes = self._list_nodes(f"Products('{uuid}')/Nodes")
        safe_node = next((n for n in top_nodes if n.get("Name", "").endswith(".SAFE")), None)
        if not safe_node:
            raise RuntimeError(f"No .SAFE folder found for product {uuid}")
        safe = safe_node["Name"]

        # Step 2: find the granule (L2A_T…) subfolder name
        granule_nodes = self._list_nodes(
            f"Products('{uuid}')/Nodes('{safe}')/Nodes('GRANULE')/Nodes"
        )
        if not granule_nodes:
            raise RuntimeError(f"No granule found under {safe}/GRANULE")
        granule = granule_nodes[0]["Name"]

        # Step 3: list 10m JP2 files and match requested bands
        r10_nodes = self._list_nodes(
            f"Products('{uuid}')/Nodes('{safe}')/Nodes('GRANULE')"
            f"/Nodes('{granule}')/Nodes('IMG_DATA')/Nodes('R10m')/Nodes"
        )

        band_urls: dict[str, str] = {}
        for node in r10_nodes:
            name: str = node.get("Name", "")
            for band in bands:
                if f"_{band}_" in name or name.endswith(f"_{band}.jp2"):
                    band_urls[band] = (
                        f"{self._dl}/Products('{uuid}')"
                        f"/Nodes('{safe}')/Nodes('GRANULE')"
                        f"/Nodes('{granule}')/Nodes('IMG_DATA')"
                        f"/Nodes('R10m')/Nodes('{name}')/$value"
                    )
                    break

        missing = set(bands) - set(band_urls)
        if missing:
            logger.warning("Bands not found in product %s: %s", uuid[:8], missing)
        return band_urls

    def _list_nodes(self, path: str) -> list[dict[str, Any]]:
        resp = httpx.get(
            f"{self._odata}/{path}",
            headers=self._auth.auth_header(),
            timeout=30.0,
        )
        resp.raise_for_status()
        data = resp.json()
        # OData v4 uses 'value'; some CDSE endpoints use 'result'
        return data.get("value", data.get("result", []))  # type: ignore[return-value]

    def _stream_file(self, url: str, dest: Path) -> None:
        with httpx.stream(
            "GET", url,
            headers=self._auth.auth_header(),
            timeout=600.0,
            follow_redirects=True,
        ) as resp:
            resp.raise_for_status()
            total = int(resp.headers.get("content-length", 0))
            written = 0
            with open(dest, "wb") as fh:
                for chunk in resp.iter_bytes(chunk_size=2 * 1024 * 1024):
                    fh.write(chunk)
                    written += len(chunk)
            mb = written // 1_000_000
            logger.info("Saved %s (%d MB%s)", dest.name, mb, f"/{total//1_000_000} MB" if total else "")
