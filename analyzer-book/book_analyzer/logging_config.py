"""图书分析服务的统一日志配置。"""

from __future__ import annotations

import logging


LOG_FORMAT = "%(asctime)s [%(name)s] %(levelname)s: %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def configure_logging(level: int = logging.INFO) -> None:
    """配置带日期和时间的应用日志，并兼容 Uvicorn 已创建的处理器。"""
    logging.basicConfig(level=level, format=LOG_FORMAT, datefmt=DATE_FORMAT)
    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    formatter = logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT)
    for handler in root_logger.handlers:
        handler.setFormatter(formatter)
