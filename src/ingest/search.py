"""Sentinel scene discovery via CDSE STAC API."""
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from src.ingest.auth import CDSEAuth

logger = logging.getLogger(__name__)


class SceneSearcher:
    """Queries CDSE STAC for the most recent Sentinel scenes covering the AOI."""

    def __init__(self, auth: CDSEAuth, catalog_base: str) -> None:
        self._auth = auth
        self._base = catalog_base.rstrip("/")

    def search_sentinel2(
        self,
        bbox: list[float],
        max_scenes: int,
        cloud_max: float,
        days_back: int,
    ) -> list[dict[str, Any]]:
        """Return up to max_scenes S2 L2A items, newest first, under cloud_max%."""
        items = self._fetch(
            collection="SENTINEL-2",
            bbox=bbox,
            days_back=days_back,
            limit=50,
        )
        filtered = [
            it for it in items
            if _cloud(it) <= cloud_max and _product_type(it) == "S2MSI2A"
        ]
        logger.info(
            "S2 search: %d total, %d ≤%.0f%% cloud → keeping %d",
            len(items), len(filtered), cloud_max, min(max_scenes, len(filtered)),
        )
        return filtered[:max_scenes]

    def search_sentinel1(
        self,
        bbox: list[float],
        max_scenes: int,
        days_back: int,
    ) -> list[dict[str, Any]]:
        """Return up to max_scenes S1 GRD items, newest first."""
        items = self._fetch(
            collection="SENTINEL-1",
            bbox=bbox,
            days_back=days_back,
            limit=20,
        )
        filtered = [it for it in items if "GRD" in _product_type(it)]
        logger.info("S1 search: %d total, %d GRD → keeping %d", len(items), len(filtered), min(max_scenes, len(filtered)))
        return filtered[:max_scenes]

    def _fetch(
        self,
        collection: str,
        bbox: list[float],
        days_back: int,
        limit: int,
    ) -> list[dict[str, Any]]:
        end = datetime.now(tz=timezone.utc)
        start = end - timedelta(days=days_back)
        params: dict[str, Any] = {
            "bbox": ",".join(str(v) for v in bbox),
            "datetime": f"{_iso(start)}/{_iso(end)}",
            "limit": limit,
            "sortby": "-properties.datetime",
        }
        url = f"{self._base}/collections/{collection}/items"
        resp = httpx.get(url, params=params, headers=self._auth.auth_header(), timeout=30.0)
        resp.raise_for_status()
        return resp.json().get("features", [])


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _cloud(item: dict[str, Any]) -> float:
    return float(item.get("properties", {}).get("eo:cloud_cover", 100.0))


def _product_type(item: dict[str, Any]) -> str:
    return str(item.get("properties", {}).get("productType", ""))
