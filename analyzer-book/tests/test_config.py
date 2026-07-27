from __future__ import annotations

from pathlib import Path

import book_analyzer.config as config_module
from book_analyzer.config import Settings


def test_from_env_loads_standalone_dotenv(monkeypatch, tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "BOOK_ANALYZER_VECTOR_BACKEND=simple",
                "BOOK_ANALYZER_DATABASE_URL=sqlite:///custom/book.db",
                "BOOK_ANALYZER_DATA_DIR=runtime",
                "BOOK_ANALYZER_CHROMA_DIR=runtime/chroma-custom",
                "BOOK_ANALYZER_CHROMA_COLLECTION_NAME=novel_chunks",
                "BOOK_ANALYZER_EMBEDDING_CACHE_DIR=model-cache",
                "BOOK_ANALYZER_DISABLE_RULE_ANALYSIS=true",
            ]
        ),
        encoding="utf-8",
    )
    for name in [
        "BOOK_ANALYZER_VECTOR_BACKEND",
        "BOOK_ANALYZER_DATABASE_URL",
        "BOOK_ANALYZER_DATA_DIR",
        "BOOK_ANALYZER_CHROMA_DIR",
        "BOOK_ANALYZER_CHROMA_COLLECTION_NAME",
        "BOOK_ANALYZER_EMBEDDING_CACHE_DIR",
        "BOOK_ANALYZER_DISABLE_RULE_ANALYSIS",
    ]:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(config_module, "_project_root", lambda: tmp_path)

    settings = Settings.from_env()

    assert settings.vector_backend == "simple"
    assert settings.database_url == f"sqlite:///{(tmp_path / 'custom/book.db').as_posix()}"
    assert settings.data_dir == tmp_path / "runtime"
    assert settings.chroma_dir == tmp_path / "runtime/chroma-custom"
    assert settings.chroma_collection_name == "novel_chunks"
    assert settings.embedding_cache_dir == tmp_path / "model-cache"
    assert settings.disable_rule_analysis is True


def test_from_env_keeps_existing_system_env_over_dotenv(
    monkeypatch,
    tmp_path: Path,
) -> None:
    (tmp_path / ".env").write_text(
        "BOOK_ANALYZER_VECTOR_BACKEND=chroma\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("BOOK_ANALYZER_VECTOR_BACKEND", "simple")
    monkeypatch.setattr(config_module, "_project_root", lambda: tmp_path)

    settings = Settings.from_env()

    assert settings.vector_backend == "simple"


def test_mcp_defaults_to_localhost(tmp_path: Path) -> None:
    settings = Settings(project_root=tmp_path)

    assert settings.mcp_host == "127.0.0.1"
    assert settings.mcp_port == 8765
