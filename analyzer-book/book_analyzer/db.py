"""数据库初始化和会话工具。

book-analyzer 目前默认使用 SQLite，但这里保留 SQLAlchemy Engine/Session
抽象，后续切 PostgreSQL 时可以复用服务层代码。
"""

from __future__ import annotations

import json
from collections.abc import Iterator

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker, Session


def create_sqlalchemy_engine(database_url: str) -> Engine:
    """创建 SQLAlchemy engine。

    参数：
        database_url: SQLAlchemy 数据库 URL，例如 ``sqlite:///...``。

    返回：
        已配置好的同步 SQLAlchemy engine。
    """
    connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {}
    # JSON 列默认用 json.dumps(ensure_ascii=True) 序列化，会把中文存成 \uXXXX 转义。
    # 显式传入 json_serializer 让 SQLite/PostgreSQL/MySQL 的 JSON 列都以 UTF-8 中文原样落库。
    return create_engine(
        database_url,
        future=True,
        connect_args=connect_args,
        json_serializer=lambda obj: json.dumps(obj, ensure_ascii=False),
    )


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    """基于 engine 创建同步 Session 工厂。

    参数：
        engine: ``create_sqlalchemy_engine`` 返回的 engine。

    返回：
        配置了 ``expire_on_commit=False`` 的 sessionmaker。
    """
    return sessionmaker(bind=engine, expire_on_commit=False, class_=Session)


def ensure_schema_compatibility(engine: Engine) -> None:
    """执行轻量向前兼容迁移。

    参数：
        engine: 已初始化的 SQLAlchemy engine。

    说明：
        V1 没有引入 Alembic。这里仅处理早期本地库缺少新增列的场景，
        不承载复杂 schema 演进。
    """
    inspector = inspect(engine)
    table_names = inspector.get_table_names()
    if "books" not in table_names and "book_chunks" not in table_names:
        return

    with engine.begin() as connection:
        if "books" in table_names:
            book_columns = {column["name"] for column in inspector.get_columns("books")}
            if "style_profile_json" not in book_columns:
                connection.execute(
                    text("ALTER TABLE books ADD COLUMN style_profile_json JSON NOT NULL DEFAULT '{}'")
                )

        if "book_chunks" in table_names:
            chunk_columns = {column["name"] for column in inspector.get_columns("book_chunks")}
            if "tag_metadata" not in chunk_columns:
                connection.execute(
                    text("ALTER TABLE book_chunks ADD COLUMN tag_metadata JSON NOT NULL DEFAULT '{}'")
                )
            if "characters" not in chunk_columns:
                connection.execute(
                    text("ALTER TABLE book_chunks ADD COLUMN characters JSON NOT NULL DEFAULT '[]'")
                )


def session_scope(session_factory: sessionmaker[Session]) -> Iterator[Session]:
    """提供一个事务性 session 上下文。

    参数：
        session_factory: 同步 SQLAlchemy sessionmaker。

    生成：
        一个会在成功时提交、异常时回滚的 ``Session``。
    """
    session = session_factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
