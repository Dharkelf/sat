"""CLI entry point — loads config and launches the viewer."""
import logging
import sys
from pathlib import Path

import yaml
from dotenv import load_dotenv


def _setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
        datefmt="%H:%M:%S",
    )


def main() -> None:
    load_dotenv()
    config_path = Path("config/settings.yaml")
    if not config_path.exists():
        print(f"ERROR: config not found at {config_path}", file=sys.stderr)
        sys.exit(1)

    with open(config_path) as fh:
        settings = yaml.safe_load(fh)

    _setup_logging(settings.get("app", {}).get("log_level", "INFO"))

    from src.viewer.app import run
    run(settings)


if __name__ == "__main__":
    main()
