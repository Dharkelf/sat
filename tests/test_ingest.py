"""Tests for the ingest module (auth, search, download helpers, catalog)."""
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from src.ingest.auth import CDSEAuth
from src.ingest.download import extract_uuid
from src.ingest.search import SceneSearcher, _cloud, _product_type

# ---------------------------------------------------------------------------
# auth
# ---------------------------------------------------------------------------

class TestCDSEAuth:
    def test_fetches_token_and_caches(self):
        with patch("src.ingest.auth.httpx.post") as mock_post:
            mock_post.return_value.json.return_value = {
                "access_token": "tok123",
                "expires_in": 600,
            }
            mock_post.return_value.raise_for_status = MagicMock()

            auth = CDSEAuth("user", "pass", "https://example.com/token")
            token = auth.get_token()
            assert token == "tok123"
            # Second call must not re-fetch (token still valid)
            _ = auth.get_token()
            assert mock_post.call_count == 1

    def test_refreshes_expired_token(self):
        with patch("src.ingest.auth.httpx.post") as mock_post:
            mock_post.return_value.json.return_value = {
                "access_token": "tok_fresh",
                "expires_in": 600,
            }
            mock_post.return_value.raise_for_status = MagicMock()

            auth = CDSEAuth("user", "pass", "https://example.com/token", refresh_margin_s=700)
            # With margin 700s > expires_in 600s the token is always considered expired
            auth.get_token()
            auth.get_token()
            assert mock_post.call_count == 2

    def test_auth_header_format(self):
        with patch("src.ingest.auth.httpx.post") as mock_post:
            mock_post.return_value.json.return_value = {"access_token": "abc", "expires_in": 600}
            mock_post.return_value.raise_for_status = MagicMock()
            auth = CDSEAuth("u", "p", "https://x.com")
            hdr = auth.auth_header()
            assert hdr == {"Authorization": "Bearer abc"}


# ---------------------------------------------------------------------------
# search helpers
# ---------------------------------------------------------------------------

class TestSearchHelpers:
    def test_cloud_returns_float(self, sample_stac_item):
        assert _cloud(sample_stac_item) == pytest.approx(12.5)

    def test_cloud_defaults_to_100_if_missing(self):
        assert _cloud({}) == pytest.approx(100.0)

    def test_product_type_extracted(self, sample_stac_item):
        assert _product_type(sample_stac_item) == "S2MSI2A"


class TestSceneSearcher:
    def _make_searcher(self):
        auth = MagicMock()
        auth.auth_header.return_value = {}
        return SceneSearcher(auth=auth, catalog_base="https://catalog.example.com")

    def _odata_item(self, i: int, cloud: float = 10.0) -> dict[str, Any]:
        """Minimal OData Products item as returned by catalogue.dataspace.copernicus.eu."""
        return {
            "Id": f"aaaaaaaa-0000-0000-0000-{i:012d}",
            "Name": f"S2A_MSIL2A_202501{i+1:02d}T100000_N0510_R122_T33UXP_20250101T130000.SAFE",
            "ContentDate": {"Start": f"2025-01-{i+1:02d}T10:00:00.000Z"},
            "Attributes": [
                {"Name": "cloudCover", "Value": cloud},
                {"Name": "productType", "Value": "S2MSI2A"},
            ],
        }

    def test_normalises_odata_to_stac(self):
        searcher = self._make_searcher()
        with patch("src.ingest.search.httpx.get") as mock_get:
            mock_get.return_value.json.return_value = {"value": [self._odata_item(0, 10.0)]}
            mock_get.return_value.raise_for_status = MagicMock()
            result = searcher.search_sentinel2(
                bbox=[16.0, 48.0, 17.0, 49.0], max_scenes=5, cloud_max=30.0, days_back=30
            )
        assert len(result) == 1
        assert "properties" in result[0]
        assert "assets" in result[0]
        assert _cloud(result[0]) == pytest.approx(10.0)

    def test_limits_to_max_scenes(self):
        searcher = self._make_searcher()
        raw_items = [self._odata_item(i, 5.0) for i in range(10)]
        with patch("src.ingest.search.httpx.get") as mock_get:
            mock_get.return_value.json.return_value = {"value": raw_items}
            mock_get.return_value.raise_for_status = MagicMock()
            result = searcher.search_sentinel2(
                bbox=[16.0, 48.0, 17.0, 49.0], max_scenes=2, cloud_max=30.0, days_back=30
            )
        assert len(result) == 2


# ---------------------------------------------------------------------------
# download helpers
# ---------------------------------------------------------------------------

class TestExtractUuid:
    def test_from_assets(self, sample_stac_item):
        uuid = extract_uuid(sample_stac_item)
        assert uuid == "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"

    def test_from_links(self):
        item = {
            "id": "some_scene",
            "assets": {},
            "links": [
                {"rel": "derived_from", "href": "https://x.com/Products('12345678-1234-1234-1234-123456789abc')"}
            ],
        }
        assert extract_uuid(item) == "12345678-1234-1234-1234-123456789abc"

    def test_raises_if_not_found(self):
        with pytest.raises(ValueError):
            extract_uuid({"id": "no-uuid-here", "assets": {}, "links": []})


# ---------------------------------------------------------------------------
# catalog
# ---------------------------------------------------------------------------

class TestSceneCatalog:
    def test_empty_catalog_returns_empty_df(self, tmp_catalog):
        df = tmp_catalog.list_scenes("sentinel2")
        assert df.empty

    def test_record_and_retrieve(self, tmp_catalog, sample_stac_item, tmp_path):
        # Create fake band files so size calculation works
        band_dir = tmp_path / "raw" / "sentinel2" / sample_stac_item["id"]
        band_dir.mkdir(parents=True)
        paths = {}
        for band in ("B02", "B03", "B04", "B08"):
            p = band_dir / f"{band}.jp2"
            p.write_bytes(b"\x00" * 100)
            paths[band] = p

        tmp_catalog.record(sample_stac_item, "sentinel2", paths)
        df = tmp_catalog.list_scenes("sentinel2")
        assert len(df) == 1
        assert df.iloc[0]["scene_id"] == sample_stac_item["id"]

    def test_evicts_oldest_beyond_keep(self, tmp_catalog, sample_stac_item, tmp_path):
        """When keep_scenes=2, the 3rd scene triggers eviction of the oldest."""
        for i in range(3):
            sc = dict(sample_stac_item)
            sc["id"] = f"scene_{i:04d}"
            sc["properties"] = dict(sample_stac_item["properties"])
            sc["properties"]["datetime"] = f"2025-01-{i+1:02d}T10:00:00Z"

            band_dir = tmp_path / "raw" / "sentinel2" / sc["id"]
            band_dir.mkdir(parents=True)
            paths = {}
            for band in ("B02",):
                p = band_dir / f"{band}.jp2"
                p.write_bytes(b"\x00" * 50)
                paths[band] = p
            tmp_catalog.record(sc, "sentinel2", paths)

        df = tmp_catalog.list_scenes("sentinel2")
        assert len(df) == 2
        # Oldest scene_0000 should be gone
        assert "scene_0000" not in df["scene_id"].values

    def test_is_downloaded(self, tmp_catalog, sample_stac_item, tmp_path):
        assert not tmp_catalog.is_downloaded(sample_stac_item["id"])
        band_dir = tmp_path / "raw" / "sentinel2" / sample_stac_item["id"]
        band_dir.mkdir(parents=True)
        p = band_dir / "B04.jp2"
        p.write_bytes(b"\x00" * 10)
        tmp_catalog.record(sample_stac_item, "sentinel2", {"B04": p})
        assert tmp_catalog.is_downloaded(sample_stac_item["id"])
