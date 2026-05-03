"""Test download URL format variants against CDSE."""
import os, sys
sys.path.insert(0, ".")
from dotenv import load_dotenv
load_dotenv()
import httpx, yaml
from src.ingest.auth import CDSEAuth

with open("config/settings.yaml") as f:
    cfg = yaml.safe_load(f)
auth = CDSEAuth(os.environ["CDSE_USERNAME"], os.environ["CDSE_PASSWORD"], cfg["cdse"]["token_url"])
uuid = "b5986868-19cc-44af-aa4c-c0b84f784b48"
base = "https://download.dataspace.copernicus.eu/odata/v1"

variants = [
    ("single-q",  base + "/Products('" + uuid + "')/$value"),
    ("no-q",      base + "/Products(" + uuid + ")/$value"),
    ("dbl-q",     base + '/Products("' + uuid + '")/$value'),
    ("catalogue", "https://catalogue.dataspace.copernicus.eu/odata/v1/Products('" + uuid + "')/$value"),
]
for label, url in variants:
    try:
        r = httpx.get(url, headers=auth.auth_header(), timeout=20, follow_redirects=False)
        print(label, r.status_code, r.text[:120])
    except Exception as e:
        print(label, "ERROR", str(e)[:80])
