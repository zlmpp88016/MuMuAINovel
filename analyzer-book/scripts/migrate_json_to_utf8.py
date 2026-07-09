"""把 SQLite 库里 JSON 列的 unicode 转义就地改写为 UTF-8 中文。

背景
----
早期 ``book_analyzer.db`` 的 SQLAlchemy ``JSON`` 列默认用
``json.dumps(ensure_ascii=True)`` 序列化，tags / tag_metadata /
characters / summary_json 等字段被存成 ``["\\u7cbe\\u795e\\u4e16\\u754c"]``
形式的转义字符串。``book_analyzer/db.py`` 已通过 ``json_serializer``
让新数据直接以 UTF-8 中文落库，本脚本一次性把存量数据也改回来。

用法
----
    python scripts/migrate_json_to_utf8.py
    python scripts/migrate_json_to_utf8.py --db ./data/book_analyzer.db
    python scripts/migrate_json_to_utf8.py --dry-run    # 只统计不写

脚本幂等：对已经是 UTF-8 中文原样的列不会再写，可反复重跑。
"""

from __future__ import annotations

import argparse
import ast
import json
import sqlite3
import sys
from pathlib import Path

# 列名 -> (表名, 列名, 默认值)
# 默认值用于当解析失败时退回原值，避免误删数据。
JSON_COLUMNS: list[tuple[str, str]] = [
    ("book_chunks", "tags"),
    ("book_chunks", "tag_metadata"),
    ("book_chunks", "characters"),
    ("books", "summary_json"),
]


def _safe_load(raw: str) -> object | None:
    """宽容解析 JSON 列原始值。

    SQLite 的 JSON 列既可能是 ``json.dumps`` 产物（含 ``\\uXXXX`` 转义），
    也可能已经是 UTF-8 中文原样（重跑场景）。优先用 ``json.loads``，
    解析失败时退回用 ``ast.literal_eval`` 兜一次 Python repr 形态。
    """
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        pass
    try:
        value = ast.literal_eval(raw)
        # 确认解析出的是 JSON 可序列化的纯容器类型，避免误判。
        json.dumps(value)
        return value
    except (ValueError, SyntaxError, TypeError):
        return None


def reencode(raw: str) -> tuple[str | None, bool]:
    """把单列原始值重新以 ``ensure_ascii=False`` 序列化。

    Returns:
        (new_text, changed)
        - new_text: 重写后的 JSON 字符串；解析失败时为 None
        - changed: 是否与原值不同（决定是否需要 UPDATE）
    """
    value = _safe_load(raw)
    if value is None:
        return None, False
    new_text = json.dumps(value, ensure_ascii=False)
    return new_text, new_text != raw


def migrate(db_path: Path, dry_run: bool = False) -> int:
    if not db_path.exists():
        print(f"[ERROR] 数据库文件不存在: {db_path}", file=sys.stderr)
        return 2

    con = sqlite3.connect(str(db_path))
    try:
        total_changed = 0
        for table, column in JSON_COLUMNS:
            cols = {row[1] for row in con.execute(f"PRAGMA table_info({table})")}
            if column not in cols:
                print(f"[SKIP] {table}.{column}: 列不存在，跳过")
                continue

            rows = con.execute(f"SELECT rowid, {column} FROM {table}").fetchall()
            changed = 0
            for rowid, raw in rows:
                if raw is None:
                    continue
                # JSON 列读出来可能是 str，也可能是 sqlite3 经过 JSON1 解析的值。
                text = raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False)
                new_text, changed_flag = reencode(text)
                if not changed_flag or new_text is None:
                    continue
                changed += 1
                if not dry_run:
                    con.execute(
                        f"UPDATE {table} SET {column} = ? WHERE rowid = ?",
                        (new_text, rowid),
                    )
            if changed and not dry_run:
                con.commit()
            label = "[DRY-RUN] " if dry_run else ""
            print(f"{label}{table}.{column}: 改写 {changed} 行 / 共 {len(rows)} 行")
            total_changed += changed
        action = "dry-run 完成" if dry_run else "迁移完成"
        print(f"\n{action}，共改写 {total_changed} 行 JSON 列。db={db_path}")
        return 0
    finally:
        con.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="把 book_analyzer SQLite 库的 JSON 列从 unicode 转义改回 UTF-8 中文")
    default_db = Path("data/book_analyzer.db")
    parser.add_argument("--db", type=Path, default=default_db, help=f"SQLite 数据库路径（默认 {default_db}）")
    parser.add_argument("--dry-run", action="store_true", help="只统计不改写")
    args = parser.parse_args()
    return migrate(args.db, dry_run=args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())