"""Quick CLI smoke-test for Phase 1 pipeline (no GUI required).

Runs: auth → search → node-discovery (no actual file download).
Prints the raw API responses so any structural surprises are visible.

Usage:
    python scripts/test_pipeline.py
"""
import json
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import yaml
from dotenv import load_dotenv

logging.basicConfig(level=logging.INFO, format="%(levelname)-8s %(message)s")
log = logging.getLogger("smoke")

load_dotenv()
username = os.environ.get("CDSE_USERNAME", "")
password = os.environ.get("CDSE_PASSWORD", "")
if not username or not password:
    sys.exit("ERROR: set CDSE_USERNAME and CDSE_PASSWORD in .env")

with open("config/settings.yaml") as fh:
    cfg = yaml.safe_load(fh)

cdse = cfg["cdse"]
aoi  = cfg["aoi"]["bbox"]
s2   = cfg["sentinel2"]


# ── Step 1: Auth ─────────────────────────────────────────────────────────────
log.info("=== Step 1: Acquire CDSE token ===")
from src.ingest.auth import CDSEAuth
auth = CDSEAuth(username, password, cdse["token_url"])
try:
    token = auth.get_token()
    log.info("OK — token starts with: %s…", token[:20])
except Exception as e:
    sys.exit(f"FAIL auth: {e}")


# ── Step 2: STAC Search ───────────────────────────────────────────────────────
log.info("\n=== Step 2: Search Sentinel-2 scenes ===")
from src.ingest.search import SceneSearcher
searcher = SceneSearcher(auth=auth, catalog_base=cdse["catalog_base"])
try:
    scenes = searcher.search_sentinel2(
        bbox=aoi,
        max_scenes=s2["max_scenes"],
        cloud_max=s2["cloud_cover_max"],
        days_back=s2["search_days_back"],
    )
    log.info("OK — found %d scene(s)", len(scenes))
    for sc in scenes:
        props = sc.get("properties", {})
        log.info("  id=%-60s  date=%s  cloud=%.1f%%",
                 sc["id"][:60], props.get("datetime", "?")[:10],
                 props.get("eo:cloud_cover", float("nan")))
except Exception as e:
    sys.exit(f"FAIL search: {e}")

if not scenes:
    sys.exit("No scenes found — try increasing cloud_cover_max or search_days_back in settings.yaml")


# ── Step 3: Extract UUID ──────────────────────────────────────────────────────
log.info("\n=== Step 3: Extract UUID from STAC item ===")
from src.ingest.download import extract_uuid
scene = scenes[0]
try:
    uuid = extract_uuid(scene)
    log.info("OK — UUID: %s", uuid)
except Exception as e:
    log.warning("FAIL extract_uuid: %s", e)
    log.info("Raw assets: %s", json.dumps(scene.get("assets", {}), indent=2)[:400])
    log.info("Raw links:  %s", json.dumps(scene.get("links", []), indent=2)[:400])
    sys.exit("Cannot proceed without UUID")


# ── Step 4: OData Node listing (top level) ───────────────────────────────────
log.info("\n=== Step 4: OData Node listing — top level ===")
import httpx
node_url = f"{cdse['odata_base']}/Products('{uuid}')/Nodes"
log.info("GET %s", node_url)
try:
    resp = httpx.get(node_url, headers=auth.auth_header(), timeout=30)
    resp.raise_for_status()
    data = resp.json()
    nodes = data.get("value", data.get("result", []))
    log.info("OK — %d top-level nodes", len(nodes))
    for n in nodes[:8]:
        log.info("  %s", n.get("Name", n))
except Exception as e:
    log.warning("FAIL node listing: %s", e)
    log.info("Raw response: %s", resp.text[:600] if "resp" in dir() else "no response")
    sys.exit("Node API failed")


# ── Step 5: Find .SAFE and first granule ─────────────────────────────────────
log.info("\n=== Step 5: Navigate to GRANULE ===")
safe_node = next((n for n in nodes if str(n.get("Name","")).endswith(".SAFE")), None)
if not safe_node:
    log.warning("No .SAFE node found. Node names: %s", [n.get("Name") for n in nodes])
    sys.exit("Cannot find .SAFE folder")

safe = safe_node["Name"]
log.info("SAFE folder: %s", safe)

granule_url = f"{cdse['odata_base']}/Products('{uuid}')/Nodes('{safe}')/Nodes('GRANULE')/Nodes"
resp2 = httpx.get(granule_url, headers=auth.auth_header(), timeout=30)
resp2.raise_for_status()
granules = resp2.json().get("value", resp2.json().get("result", []))
log.info("OK — %d granule(s): %s", len(granules), [g.get("Name") for g in granules])


# ── Step 6: List 10m band files ───────────────────────────────────────────────
if granules:
    granule = granules[0]["Name"]
    log.info("\n=== Step 6: List R10m band files ===")
    r10_url = (f"{cdse['odata_base']}/Products('{uuid}')/Nodes('{safe}')"
               f"/Nodes('GRANULE')/Nodes('{granule}')/Nodes('IMG_DATA')/Nodes('R10m')/Nodes")
    resp3 = httpx.get(r10_url, headers=auth.auth_header(), timeout=30)
    resp3.raise_for_status()
    band_files = resp3.json().get("value", resp3.json().get("result", []))
    log.info("OK — %d files in R10m:", len(band_files))
    for f in band_files:
        log.info("  %s", f.get("Name"))

log.info("\n✓ All Phase 1 API steps passed — pipeline is ready for real download")
