"""Application entry point — creates QApplication and wires all components."""
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from PyQt6.QtWidgets import QApplication

from src.ingest.auth import CDSEAuth
from src.ingest.catalog import SceneCatalog
from src.ingest.download import SceneDownloader
from src.ingest.search import SceneSearcher
from src.viewer.main_window import MainWindow

logger = logging.getLogger(__name__)


def run(settings: dict) -> None:
    """Build all components and launch the viewer."""
    load_dotenv()
    username = os.environ.get("CDSE_USERNAME", "")
    password = os.environ.get("CDSE_PASSWORD", "")
    if not username or not password:
        raise RuntimeError("CDSE_USERNAME and CDSE_PASSWORD must be set in .env")

    cdse_cfg = settings["cdse"]
    storage_cfg = settings["storage"]

    auth = CDSEAuth(
        username=username,
        password=password,
        token_url=cdse_cfg["token_url"],
        refresh_margin_s=cdse_cfg["token_refresh_margin_s"],
    )
    searcher = SceneSearcher(auth=auth, catalog_base=cdse_cfg["catalog_base"])

    raw_dir = Path(storage_cfg["raw_dir"])
    raw_dir.mkdir(parents=True, exist_ok=True)

    downloader = SceneDownloader(
        auth=auth,
        odata_base=cdse_cfg["odata_base"],
        download_base=cdse_cfg["download_base"],
        raw_dir=raw_dir,
    )
    catalog = SceneCatalog(
        catalog_path=Path(storage_cfg["catalog_file"]),
        raw_dir=raw_dir,
        keep_scenes=storage_cfg["keep_scenes"],
        max_size_gb=storage_cfg["max_size_gb"],
    )

    app = QApplication(sys.argv)
    app.setApplicationName(settings["app"]["name"])

    window = MainWindow(
        settings=settings,
        auth=auth,
        searcher=searcher,
        downloader=downloader,
        catalog=catalog,
    )
    window.show()

    sys.exit(app.exec())
