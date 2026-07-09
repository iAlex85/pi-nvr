"""Rotating file + console logging, configured from config.yaml."""
from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from app.config import Config


def configure_logging(cfg: Config) -> None:
    level_name = cfg.get("logging.level", "INFO")
    log_dir = Path(cfg.get("logging.dir", "logs"))
    log_dir.mkdir(parents=True, exist_ok=True)
    max_bytes = cfg.get("logging.max_bytes", 5_000_000)
    backup_count = cfg.get("logging.backup_count", 5)

    root = logging.getLogger("pi_nvr")
    root.setLevel(getattr(logging, level_name, logging.INFO))
    root.handlers.clear()

    fmt = logging.Formatter(
        "%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    console = logging.StreamHandler()
    console.setFormatter(fmt)
    root.addHandler(console)

    file_handler = RotatingFileHandler(
        log_dir / "pi-nvr.log", maxBytes=max_bytes, backupCount=backup_count
    )
    file_handler.setFormatter(fmt)
    root.addHandler(file_handler)

    # Per-category loggers (recording/motion/auth) propagate up to this
    # handler set; EventLog DB rows are written separately by callers that
    # want them queryable from the UI (see app/dashboard/routes.py logs).
