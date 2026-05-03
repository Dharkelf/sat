# SAT — Satellite Change Detection

Desktop viewer for Sentinel-1/2 imagery over Vienna (Austria).  Downloads the two most recent
scenes from ESA's free Copernicus Data Space Ecosystem, displays RGB, NIR false-colour, and
change-detection overlays with zoom/pan.

---

## Architecture

```
┌───────────────────────────────────────────────────────┐
│  main.py  (CLI entry — loads config, calls app.run)   │
└───────────────────────┬───────────────────────────────┘
                        │
          ┌─────────────┼─────────────┐
          ▼             ▼             ▼
   ┌────────────┐ ┌──────────┐ ┌───────────┐
   │  ingest/   │ │ raster/  │ │  viewer/  │
   │────────────│ │──────────│ │───────────│
   │ CDSEAuth   │ │BandLoader│ │MainWindow │
   │ SceneSearch│ │  change  │ │MapCanvas  │
   │ SceneDl    │ │  loader  │ │LayerPanel │
   │ SceneCatal.│ └──────────┘ └───────────┘
   └────────────┘
          │
          ▼
   ┌────────────┐
   │   data/    │
   │  raw/s2/   │  JP2 band files (max 2 scenes, ~120 MB each)
   │  catalog   │  Parquet scene index
   └────────────┘
```

### Data Flow

```
CDSE STAC API ──► SceneSearcher ──► [cloud filter] ──► scene list
                                                            │
                                                            ▼
                                                    SceneDownloader
                                                    (OData Node API)
                                                    Band JP2 files
                                                            │
                                                            ▼
                                                     SceneCatalog
                                                    (Parquet index)
                                                            │
                                                            ▼
                                                      BandLoader
                                                  (crop to AOI bbox)
                                                            │
                                          ┌─────────────────┼──────────────┐
                                          ▼                 ▼              ▼
                                    RGB composite   NIR composite   Change diff
                                          │                 │              │
                                          └─────────────────┼──────────────┘
                                                            ▼
                                                    MapCanvas (PyQt6)
                                                     zoom / pan / overlay
```

---

## Setup

### Prerequisites

- Python 3.11+
- macOS with homebrew GDAL (required for JP2 support):
  ```bash
  brew install gdal
  ```
- Free account at https://dataspace.copernicus.eu (registration takes ~2 minutes)

### Installation

```bash
cd /path/to/sat
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip freeze > requirements.txt   # pin exact versions
pre-commit install
```

### Credentials

Copy `.env.example` to `.env` and fill in your CDSE credentials:
```
CDSE_USERNAME=your.email@example.com
CDSE_PASSWORD=your_password
```

`.env` is gitignored and never committed.

---

## Configuration

All parameters in `config/settings.yaml`:

| Key | Default | Description |
|---|---|---|
| `aoi.bbox` | Vienna WGS84 | West, South, East, North |
| `sentinel2.cloud_cover_max` | 30 | Max cloud cover % to accept |
| `sentinel2.search_days_back` | 45 | Look-back window for scene search |
| `sentinel2.max_scenes` | 2 | Scenes to keep (current + previous) |
| `storage.max_size_gb` | 2.0 | Hard cap on `data/` directory size |
| `storage.keep_scenes` | 2 | Scenes retained per sensor |
| `display.change_threshold` | 0.03 | Min normalised diff to show as change |
| `display.change_amplify` | 5.0 | Change colour amplification factor |

---

## Usage

```bash
source .venv/bin/activate
python main.py
```

The viewer opens.  Click **"Download Latest S2"** to fetch the two most recent
Sentinel-2 scenes for Vienna.  Use the right-side panel to switch display modes:

| Mode | Description |
|---|---|
| RGB (True Color) | Bands B04/B03/B02 with percentile stretch |
| False Color NIR | Bands B08/B04/B03 — vegetation appears bright red |
| Change Overlay | RGB base + red/green overlay: green = new bright areas, red = darker |

Zoom with the mouse wheel; pan by dragging.

---

## Data

### Local scene catalog (`data/catalog.parquet`)

| Column | Type | Notes |
|---|---|---|
| `scene_id` | str | ESA product name |
| `sensor` | str | `sentinel2` or `sentinel1` |
| `sensing_dt` | datetime64[ns, UTC] | Scene acquisition time |
| `cloud_cover` | float64 | Percent; NaN for S1 |
| `downloaded_at` | datetime64[ns, UTC] | Local download timestamp |
| `band_paths` | str | JSON dict band→relative path |
| `size_mb` | float64 | Total size of band files |

### Band files

`data/raw/sentinel2/<scene_id>/{B02,B03,B04,B08}.jp2`

- Format: JPEG2000, uint16, scale factor 10000 (divide by 10000 for reflectance 0–1)
- Resolution: 10 m/pixel
- CRS: UTM zone 33N (EPSG:32633) for Vienna scenes
- Typical size: 20–40 MB per band, ~120 MB per scene

---

## Development

```bash
pytest tests/ -v              # run test suite
pre-commit run --all-files    # lint + type-check + tests
```

Add a new module under `src/<name>/` and register it in `AGENTS.md`.

---

## Known Limitations

- Requires GDAL with OpenJPEG support (via `brew install gdal` on macOS).
- Sentinel-1 download is implemented in `SceneDownloader` but the viewer only shows S2 in Phase 1.
- Change detection uses simple pixel difference; seasonal illumination shifts can produce false positives.
- No GPU acceleration for compositing.

## Future Improvements

1. Sentinel-1 SAR visualisation in the viewer.
2. YOLOv8-based construction detection with bounding-box training UI (Phase 2).
3. Cloud-Optimized GeoTIFF pyramid loading for smoother high-resolution zoom.
4. Tile-based rendering for full-tile display without AOI crop.

## References

- Copernicus Data Space Ecosystem: https://dataspace.copernicus.eu
- Sentinel-2 User Guide: https://sentinels.copernicus.eu/web/sentinel/user-guides/sentinel-2-msi
- YOLOv8 (Phase 2): https://docs.ultralytics.com
