"""Shared pytest fixtures.

Each test gets its own temp directory with a fresh config.yaml (copied from
the default) and a fresh SQLite DB, so tests never touch a real deployment's
data and can run in parallel.
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture()
def tmp_project(tmp_path, monkeypatch):
    """Sets up an isolated working directory with its own config + DB, and
    points PI_NVR_CONFIG at it for the duration of the test."""
    repo_root = Path(__file__).resolve().parent.parent
    config_src = repo_root / "config" / "default_config.yaml"
    config_dst = tmp_path / "config.yaml"
    shutil.copy(config_src, config_dst)

    # Rewrite paths in the copied config to live under tmp_path.
    content = config_dst.read_text()
    content = content.replace('path: "database/pi-nvr.db"', f'path: "{tmp_path / "db.sqlite"}"')
    content = content.replace('path: "recordings"', f'path: "{tmp_path / "recordings"}"')
    config_dst.write_text(content)

    monkeypatch.setenv("PI_NVR_CONFIG", str(config_dst))
    monkeypatch.setenv("PI_NVR_SESSION_SECRET", "test-secret-not-for-production")
    monkeypatch.setenv("PI_NVR_DB_SECRET", "test-db-secret-not-for-production")
    monkeypatch.chdir(tmp_path)

    # Reset process-wide singletons so each test gets a clean Config/engine.
    import app.config as config_module
    import app.database as database_module

    config_module._config_instance = None
    database_module._engine = None
    database_module._SessionLocal = None

    yield tmp_path

    config_module._config_instance = None
    database_module._engine = None
    database_module._SessionLocal = None


@pytest.fixture()
def db_session(tmp_project):
    from app.database import init_db, session_scope

    init_db()
    with session_scope() as db:
        yield db
