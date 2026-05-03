"""Local scene catalog and storage manager.

Maintains a Parquet index of downloaded scenes and enforces:
  - keep_scenes: retain only the N most recent scenes per sensor
  - max_size_gb:  hard cap on total data/ directory size
"""
import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import pandas as pd

logger = logging.getLogger(__name__)


class SceneCatalog:
    """Repository for local scene metadata.  Parquet-backed, append-only index."""

    SCHEMA = {
        "scene_id": "str",
        "sensor": "str",          # sentinel2 | sentinel1
        "sensing_dt": "datetime64[ns, UTC]",
        "cloud_cover": "float64",
        "downloaded_at": "datetime64[ns, UTC]",
        "band_paths": "str",      # JSON-encoded dict  band→relative_path
        "size_mb": "float64",
    }

    def __init__(self, catalog_path: Path, raw_dir: Path, keep_scenes: int, max_size_gb: float) -> None:
        self._path = catalog_path
        self._raw = raw_dir
        self._keep = keep_scenes
        self._max_bytes = int(max_size_gb * 1024**3)
        catalog_path.parent.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def list_scenes(self, sensor: str) -> pd.DataFrame:
        """Return all catalog entries for a sensor, sorted newest first."""
        df = self._load()
        result = df[df["sensor"] == sensor].sort_values("sensing_dt", ascending=False)
        return result.reset_index(drop=True)

    def latest_sensing_dt(self, sensor: str) -> Optional[datetime]:
        """Return the most recent sensing datetime on disk, or None."""
        df = self.list_scenes(sensor)
        if df.empty:
            return None
        ts = df["sensing_dt"].iloc[0]
        return ts.to_pydatetime()

    def is_downloaded(self, scene_id: str) -> bool:
        df = self._load()
        return bool((df["scene_id"] == scene_id).any())

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def record(
        self,
        scene: dict[str, Any],
        sensor: str,
        band_paths: dict[str, Path],
    ) -> None:
        """Add a scene entry; evict excess old scenes afterwards."""
        import json

        sensing_dt = pd.Timestamp(scene["properties"]["datetime"], tz="UTC")
        size_mb = sum(p.stat().st_size for p in band_paths.values()) / 1e6

        rel_paths = {b: str(p.relative_to(self._raw)) for b, p in band_paths.items()}
        row = pd.DataFrame([{
            "scene_id": scene["id"],
            "sensor": sensor,
            "sensing_dt": sensing_dt,
            "cloud_cover": scene.get("properties", {}).get("eo:cloud_cover", float("nan")),
            "downloaded_at": pd.Timestamp(datetime.now(tz=timezone.utc)),
            "band_paths": json.dumps(rel_paths),
            "size_mb": round(size_mb, 1),
        }])

        df = self._load()
        df = pd.concat([df, row], ignore_index=True).drop_duplicates("scene_id", keep="last")
        self._save(df)
        logger.info("Catalog: recorded %s (%.1f MB)", scene["id"][:32], size_mb)

        self._evict(sensor)
        self._enforce_size_cap()

    # ------------------------------------------------------------------
    # Eviction
    # ------------------------------------------------------------------

    def _evict(self, sensor: str) -> None:
        """Delete scenes older than keep_scenes for the given sensor."""
        df = self._load()
        sensor_df = df[df["sensor"] == sensor].sort_values("sensing_dt", ascending=False)
        to_delete = sensor_df.iloc[self._keep:]  # everything beyond the N newest

        if to_delete.empty:
            return

        import json
        for _, row in to_delete.iterrows():
            scene_dir = self._raw / sensor / str(row["scene_id"])
            if scene_dir.exists():
                shutil.rmtree(scene_dir)
                logger.info("Evicted old scene: %s", row["scene_id"][:32])
            try:
                paths = json.loads(row["band_paths"])
                for rel in paths.values():
                    p = self._raw / rel
                    if p.exists():
                        p.unlink()
            except Exception:
                pass

        remaining = df[~df["scene_id"].isin(to_delete["scene_id"])]
        self._save(remaining.reset_index(drop=True))

    def _enforce_size_cap(self) -> None:
        """Remove oldest scene globally if data/ exceeds max_size_gb."""
        total = _dir_bytes(self._raw)
        if total <= self._max_bytes:
            return
        logger.warning(
            "Storage %.1f GB exceeds cap %.1f GB — evicting oldest scene",
            total / 1024**3, self._max_bytes / 1024**3,
        )
        df = self._load().sort_values("sensing_dt", ascending=True)
        if df.empty:
            return
        oldest = df.iloc[0]
        # Remove from catalog and disk
        scene_dir = self._raw / oldest["sensor"] / oldest["scene_id"]
        if scene_dir.exists():
            shutil.rmtree(scene_dir)
        self._save(df.iloc[1:].reset_index(drop=True))

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _load(self) -> pd.DataFrame:
        if not self._path.exists():
            return pd.DataFrame(columns=list(self.SCHEMA.keys()))
        return pd.read_parquet(self._path)

    def _save(self, df: pd.DataFrame) -> None:
        df.to_parquet(self._path, index=False)


def _dir_bytes(path: Path) -> int:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
