"""Shared test fixtures."""

from __future__ import annotations

from pathlib import Path
import sys

from fastapi.testclient import TestClient
import pytest


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))

from book_analyzer.config import Settings
from book_analyzer.main import create_app


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    data_dir = tmp_path / "data"
    return Settings(
        project_root=tmp_path,
        database_url=f"sqlite:///{(tmp_path / 'book_analyzer.db').as_posix()}",
        data_dir=data_dir,
        uploads_dir=data_dir / "uploads",
        exports_dir=data_dir / "exports",
        chroma_dir=data_dir / "chroma",
        chunk_size=120,
        chunk_overlap=20,
        max_upload_bytes=1024 * 1024,
    )


@pytest.fixture
def client(settings: Settings) -> TestClient:
    app = create_app(settings)
    with TestClient(app) as test_client:
        yield test_client
