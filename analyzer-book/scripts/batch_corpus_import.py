"""Batch import TXT novels into the analyzer corpus."""

from __future__ import annotations

import argparse
import asyncio
import logging
from pathlib import Path
import sys

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))

from book_analyzer.analysis import create_analyzer
from book_analyzer.config import Settings
from book_analyzer.db import (
    create_session_factory,
    create_sqlalchemy_engine,
    ensure_schema_compatibility,
)
from book_analyzer.llm_service import AIService
from book_analyzer.models import Base
from book_analyzer.service import BookAnalysisService
from book_analyzer.vector_store import create_vector_store


logger = logging.getLogger(__name__)


def build_service(settings: Settings) -> tuple[BookAnalysisService, AIService, AIService]:
    """Create the same service stack used by the HTTP app."""
    settings.ensure_directories()
    engine = create_sqlalchemy_engine(settings.database_url)
    Base.metadata.create_all(engine)
    ensure_schema_compatibility(engine)

    ai_service = AIService(settings)
    tagging_ai_service = AIService(
        settings,
        api_provider=settings.tagging_ai_provider,
        api_key=settings.tagging_api_key,
        api_base_url=settings.tagging_base_url,
        default_model=settings.tagging_model,
        default_temperature=settings.tagging_temperature,
        default_max_tokens=settings.tagging_max_tokens,
    )
    analyzer = create_analyzer(settings, ai_service, tagging_ai_service=tagging_ai_service)
    service = BookAnalysisService(
        settings=settings,
        session_factory=create_session_factory(engine),
        vector_store=create_vector_store(settings),
        analyzer=analyzer,
    )
    return service, ai_service, tagging_ai_service


async def import_file(
    service: BookAnalysisService,
    path: Path,
    semaphore: asyncio.Semaphore,
) -> bool:
    """Import one TXT file with concurrency control."""
    async with semaphore:
        try:
            raw_bytes = path.read_bytes()
            book = await service.ingest_upload(path.name, raw_bytes)
            logger.info("Imported %s -> %s (%s)", path, book.title, book.id)
            return True
        except Exception:
            logger.exception("Failed to import %s", path)
            return False


async def run(args: argparse.Namespace) -> int:
    """Run batch import and return process exit code."""
    settings = Settings.from_env()
    if args.limit and args.limit <= 0:
        raise ValueError("--limit must be greater than 0")
    if args.concurrency:
        settings.batch_import_concurrency = args.concurrency

    service, ai_service, tagging_ai_service = build_service(settings)
    source_dir = Path(args.source).expanduser().resolve()
    files = sorted(source_dir.rglob("*.txt"))
    if args.limit:
        files = files[:args.limit]
    if not files:
        logger.warning("No TXT files found under %s", source_dir)
        return 0

    semaphore = asyncio.Semaphore(settings.batch_import_concurrency)
    results = await asyncio.gather(
        *(import_file(service, path, semaphore) for path in files)
    )
    await tagging_ai_service.close()
    await ai_service.close()

    success_count = sum(1 for result in results if result)
    failed_count = len(results) - success_count
    logger.info("Batch import done: success=%d failed=%d", success_count, failed_count)
    return 0 if failed_count == 0 else 1


def parse_args() -> argparse.Namespace:
    """Parse CLI args."""
    parser = argparse.ArgumentParser(description="Batch import TXT novels into analyzer corpus.")
    parser.add_argument("source", help="Directory containing TXT files.")
    parser.add_argument("--limit", type=int, default=50, help="Max files to import. Defaults to 50.")
    parser.add_argument("--concurrency", type=int, default=None, help="Override import concurrency.")
    return parser.parse_args()


def main() -> None:
    """CLI entrypoint."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )
    raise SystemExit(asyncio.run(run(parse_args())))


if __name__ == "__main__":
    main()
