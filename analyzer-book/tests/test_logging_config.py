from __future__ import annotations

import logging

from book_analyzer.logging_config import DATE_FORMAT, LOG_FORMAT, configure_logging


def test_configure_logging_applies_timestamp_format_to_root_handlers() -> None:
    root_logger = logging.getLogger()
    original_handlers = root_logger.handlers[:]
    original_level = root_logger.level
    try:
        root_logger.handlers.clear()
        configure_logging()

        assert root_logger.handlers
        assert all(
            handler.formatter is not None
            and handler.formatter._fmt == LOG_FORMAT
            and handler.formatter.datefmt == DATE_FORMAT
            for handler in root_logger.handlers
        )
    finally:
        root_logger.handlers.clear()
        root_logger.handlers.extend(original_handlers)
        root_logger.setLevel(original_level)
