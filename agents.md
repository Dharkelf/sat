# AGENTS.md — SAT: Satellite Change Detection

This file governs how AI agents (Claude Code, Codex, etc.) work in this repository.
Read it before making any structural or architectural decisions.

---

## Project Purpose

Desktop viewer for Sentinel-1 and Sentinel-2 satellite imagery over a configurable AOI
(default: Vienna, Austria).  Downloads the two most recent scenes from ESA's Copernicus
Data Space Ecosystem (CDSE), displays RGB, NIR false-colour, and change-detection overlays
with zoom/pan.

**Phase 1 (current):** Download pipeline + PyQt6 viewer (RGB / NIR false-colour / change diff).
**Phase 2 (planned):** YOLOv8-based construction-change detection with interactive bounding-box
training UI and confidence-coloured overlays.

---

## Standard Directory Layout

```
<project-root>/
├── agents.md               # this file
├── README.md
├── REPORT.md               # observed runtime behaviour
├── requirements.txt        # pinned deps (pip freeze after every install)
├── .gitignore
├── .env                    # CDSE credentials — NEVER commit
├── .env.example
├── .pre-commit-config.yaml
│
├── config/
│   └── settings.yaml       # single source of truth for all parameters
│
├── data/                   # gitignored — max 2 GB total
│   ├── raw/
│   │   ├── sentinel2/      # one subfolder per scene_id; band JP2 files
│   │   └── sentinel1/      # GRD TIFF files
│   └── processed/
│
├── src/
│   ├── ingest/             # CDSE auth, search, download, local catalog
│   ├── raster/             # band loading, compositing, change detection
│   └── viewer/             # PyQt6 application (Phase 1)
│
├── tests/
│   ├── conftest.py
│   ├── test_ingest.py
│   └── test_raster.py
│
└── main.py                 # CLI entry — loads config, calls src/viewer/app.run()
```

---

## Modules

| Module | Path | Responsibility |
|---|---|---|
| ingest | `src/ingest/` | CDSE OAuth, STAC search, OData Node download, Parquet catalog |
| raster | `src/raster/` | Band loading + AOI crop, RGB/NIR compositing, change detection |
| viewer | `src/viewer/` | PyQt6 app: MainWindow, MapCanvas (zoom/pan), LayerPanel |

---

## Storage Rules

- `data/` is **gitignored**.  Max 2 GB total (`storage.max_size_gb` in `settings.yaml`).
- Keep **2 scenes per sensor** (`storage.keep_scenes`): current + previous.  Older scenes are automatically deleted by `SceneCatalog._evict()` after every successful download.
- Band JP2 files land in `data/raw/sentinel2/<scene_id>/{B02,B03,B04,B08}.jp2`.
- Training crops (Phase 2) land in `data/processed/crops/` and are **not** evicted.
- The local catalog is `data/catalog.parquet` — do not hand-edit it.

---

## Download Policy

- `SceneCatalog.latest_sensing_dt()` is checked before every download.
- A remote scene is downloaded only if its sensing datetime is **newer** than the locally stored newest scene, or if the scene is not yet recorded in the catalog.
- If local data is already current the download is skipped and the status bar shows "Already up to date."

---

## Key Design Patterns

| Pattern | Location |
|---|---|
| **Repository** | `SceneCatalog` — single access point for local scene metadata |
| **Strategy** | `BandLoader` — compositing strategy selectable per call (rgb / nir) |
| **Observer** | `_DownloadWorker` (QThread) emits `progress`, `finished`, `error` signals |
| **Template Method** | `MainWindow._update_display()` dispatches to per-mode rendering |

---

## Pre-commit Hooks

```bash
pip install pre-commit
pre-commit install
```

Hooks run in order: **ruff** (lint + format) → **mypy** (type check) → **pytest** (tests).
No commit may pass with errors in any hook.

---

## Testing

- Unit tests mock HTTP (no real CDSE calls) and use `tmp_path` for file I/O.
- Integration tests use synthetic numpy arrays; no real rasterio files required.
- Run before every commit: `pytest tests/ -v`

---

## Coding Conventions

- Python 3.11+.  Type hints on all public functions.
- No `print()` in library code — use `logging` only.  Level configured in `settings.yaml`.
- No hard-coded values — all parameters in `config/settings.yaml`.
- Parquet for the scene catalog; JP2 for raw bands (rasterio reads them natively).
- Timestamps: always UTC, stored as `datetime64[ns, UTC]`.

---

## Git Rules

- **Never push automatically.** `git push` only on explicit user request.
- Commit messages follow Conventional Commits: `<type>(<scope>): <subject>`
- Never commit: `.env`, `data/`, `.venv/`, `__pycache__/`, `*.pyc`.
- Always commit: `agents.md`, `README.md`, `REPORT.md`, `config/settings.yaml`, `requirements.txt`.

---

## Failure Conditions — Agents Must NOT

- Push to remote without an explicit user request.
- Commit `.env` (contains CDSE credentials).
- Overwrite or delete files in `data/processed/crops/` (Phase 2 training data).
- Hard-code numeric values that belong in `config/settings.yaml`.
- Use `print()` in library code.
- Leave business logic in `notebooks/`.
- Introduce a dependency without pinning it in `requirements.txt` via `pip freeze`.

---

## External References

- CDSE portal: `https://dataspace.copernicus.eu`
- CDSE STAC catalog: `https://catalogue.dataspace.copernicus.eu/stac`
- CDSE OData API: `https://catalogue.dataspace.copernicus.eu/odata/v1`
- CDSE download: `https://download.dataspace.copernicus.eu/odata/v1`
- Sentinel-2 product guide: `https://sentinels.copernicus.eu/web/sentinel/user-guides/sentinel-2-msi`
- YOLOv8 (Phase 2): `https://docs.ultralytics.com`
