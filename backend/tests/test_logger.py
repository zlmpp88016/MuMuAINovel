from __future__ import annotations

import logging
import re

from app.logger import UvicornFormatter


def test_uvicorn_formatter_includes_timestamp() -> None:
    record = logging.LogRecord(
        name="test.logger",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="测试日志",
        args=(),
        exc_info=None,
    )

    formatted = UvicornFormatter(use_colors=False).format(record)

    assert re.match(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} INFO:", formatted)
    assert formatted.endswith("test.logger - 测试日志")
