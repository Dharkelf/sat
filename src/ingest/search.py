"""Sentinel scene discovery via CDSE OData API.

CDSE retired the STAC /collections/SENTINEL-2 endpoint; OData v1 is the
stable alternative and supports server-side filtering including cloud cover.

Returned items are normalised to a STAC-like dict so all downstream modules
(catalog, download, viewer) need no changes:
  {
    "id":         product name without .SAFE suffix,
    "properties": {"datetime": ISO-str, "eo:cloud_cover": float, "productType": str},
    "assets":     {"PRODUCT": {"href": "<download-url>"}},
    "links":      [],
  }
"""
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from src.ingest.auth import CDSEAuth

logger = logging.getLogger(__name__)

_ODATA_BASE = "https://catalogue.dataspace.copernicus.eu/odata/v1"
_DOWNLOAD_BASE = "https://download.dataspace.copernicus.eu/odata/v1"

# AOI as WKT polygon — derived from bbox at search time
_AOI_WKT = "POLYGON(({w} {s},{e} {s},{e} {n},{w} {n},{w} {s}))"


class SceneSearcher:
    """Queries CDSE OData for the most recent Sentinel scenes covering the AOI.

    Results are normalised to a STAC-compatible dict so downstream code
    (catalog.record, download.extract_uuid, viewer) is unchanged.
    """

    def __init__(self, auth: CDSEAuth, catalog_base: str) -> None:
        # catalog_base kept for API compatibility; OData URL is fixed
        self._auth = auth

    def search_sentinel2(
        self,
        bbox: list[float],
        max_scenes: int,
        cloud_max: float,
        days_back: int,
    ) -> list[dict[str, Any]]:
        """Return up to max_scenes S2 L2A items, newest first, under cloud_max%."""
        w, s, e, n = bbox
        aoi_wkt = _AOI_WKT.format(w=w, s=s, e=e, n=n)
        start_dt = _iso(datetime.now(tz=UTC) - timedelta(days=days_back))

        filter_str = (
            f"Collection/Name eq 'SENTINEL-2' "
            f"and OData.CSC.Intersects(area=geography'SRID=4326;{aoi_wkt}') "
            f"and Attributes/OData.CSC.StringAttribute/any("
            f"  att:att/Name eq 'productType' "
            f"  and att/OData.CSC.StringAttribute/Value eq 'S2MSI2A') "
            f"and Attributes/OData.CSC.DoubleAttribute/any("
            f"  att:att/Name eq 'cloudCover' "
            f"  and att/OData.CSC.DoubleAttribute/Value lt {cloud_max}) "
            f"and ContentDate/Start gt {start_dt}"
        )
        raw = self._odata_search(filter_str, top=max_scenes * 3)
        items = [_normalise(r) for r in raw]
        logger.info(
            "S2 OData search: %d results ≤%.0f%% cloud → keeping %d",
            len(items), cloud_max, min(max_scenes, len(items)),
        )
        return items[:max_scenes]

    def search_sentinel1(
        self,
        bbox: list[float],
        max_scenes: int,
        days_back: int,
    ) -> list[dict[str, Any]]:
        """Return up to max_scenes S1 GRD items, newest first."""
        w, s, e, n = bbox
        aoi_wkt = _AOI_WKT.format(w=w, s=s, e=e, n=n)
        start_dt = _iso(datetime.now(tz=UTC) - timedelta(days=days_back))

        filter_str = (
            f"Collection/Name eq 'SENTINEL-1' "
            f"and OData.CSC.Intersects(area=geography'SRID=4326;{aoi_wkt}') "
            f"and Attributes/OData.CSC.StringAttribute/any("
            f"  att:att/Name eq 'productType' "
            f"  and att/OData.CSC.StringAttribute/Value eq 'GRD') "
            f"and ContentDate/Start gt {start_dt}"
        )
        raw = self._odata_search(filter_str, top=max_scenes * 2)
        items = [_normalise(r) for r in raw]
        logger.info("S1 OData search: %d results → keeping %d", len(items), min(max_scenes, len(items)))
        return items[:max_scenes]

    def _odata_search(self, filter_str: str, top: int) -> list[dict[str, Any]]:
        params: dict[str, Any] = {
            "$filter": filter_str,
            "$orderby": "ContentDate/Start desc",
            "$top": top,
            "$expand": "Attributes",
        }
        resp = httpx.get(
            f"{_ODATA_BASE}/Products",
            params=params,
            headers=self._auth.auth_header(),
            timeout=30.0,
        )
        resp.raise_for_status()
        return resp.json().get("value", [])


# ---------------------------------------------------------------------------
# Normalisation helpers
# ---------------------------------------------------------------------------

def _normalise(raw: dict[str, Any]) -> dict[str, Any]:
    """Convert an OData Products item to a STAC-compatible dict."""
    uuid: str = raw["Id"]
    name: str = raw["Name"]
    scene_id = name[:-5] if name.endswith(".SAFE") else name

    attrs = {a["Name"]: a.get("Value") for a in raw.get("Attributes", [])}
    cloud = float(attrs.get("cloudCover", 100.0))
    product_type = str(attrs.get("processingLevel", attrs.get("productType", "")))

    # Derive product type from name if attribute missing (S2MSI2A → productType)
    if "MSI" in name:
        product_type = "S2MSI" + name.split("_")[1][3:]  # e.g. S2MSI2A

    return {
        "id": scene_id,
        "properties": {
            "datetime": raw["ContentDate"]["Start"],
            "eo:cloud_cover": cloud,
            "productType": product_type,
        },
        "assets": {
            "PRODUCT": {
                "href": f"{_DOWNLOAD_BASE}/Products('{uuid}')/$value",
            }
        },
        "links": [
            {"rel": "derived_from", "href": f"{_ODATA_BASE}/Products('{uuid}')"}
        ],
    }


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _cloud(item: dict[str, Any]) -> float:
    return float(item.get("properties", {}).get("eo:cloud_cover", 100.0))


def _product_type(item: dict[str, Any]) -> str:
    return str(item.get("properties", {}).get("productType", ""))
