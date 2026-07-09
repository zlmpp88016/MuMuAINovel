"""TXT 解码、章节解析和文本切块工具。

本模块只处理 TXT 的最小可行流程：解码原始字节、识别书名、
按章节标题拆分正文，再切成带重叠的分析块。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
import re
from pathlib import Path


logger = logging.getLogger(__name__)

CHAPTER_HEADING_RE = re.compile(
    r"^\s*(第[0-9一二三四五六七八九十百千零〇两]+[章节回卷篇幕集].*|chapter\s+\d+.*)$",
    re.IGNORECASE,
)


@dataclass(slots=True)
class Chapter:
    """解析后的章节。

    Args:
        number: 章节序号，从 1 开始递增。
        title: 章节标题，未识别到标题时使用默认正文标题。
        content: 章节正文内容。
    """

    number: int
    title: str
    content: str


@dataclass(slots=True)
class ParsedBook:
    """完成基础解析后的书籍结构。"""

    title: str
    chapters: list[Chapter]
    full_text: str


@dataclass(slots=True)
class Chunk:
    """用于分析和向量索引的章节文本片段。"""

    chapter_no: int
    chapter_title: str
    chunk_index: int
    content: str


def decode_text_file(raw_bytes: bytes) -> str:
    """按常见中文小说编码顺序解码 TXT 字节。

    Args:
        raw_bytes: 上传文件的原始字节。

    Returns:
        解码后的文本。若常见编码都失败，则使用 UTF-8 替换非法字符。
    """
    for encoding in ("utf-8-sig", "utf-8", "gb18030", "gbk", "big5"):
        try:
            decoded = raw_bytes.decode(encoding)
            logger.info("文本解码成功: encoding=%s, 长度=%d 字符", encoding, len(decoded))
            return decoded
        except UnicodeDecodeError:
            continue
    logger.warning("常见编码均解码失败, 使用 utf-8 replace fallback")
    return raw_bytes.decode("utf-8", errors="replace")


def parse_book_text(text: str, title_hint: str | None = None) -> ParsedBook:
    """将原始文本解析成书名和章节列表。

    Args:
        text: 已解码的 TXT 文本。
        title_hint: 可选标题来源，通常是上传文件名。

    Returns:
        包含书名、章节和全文正文的 ``ParsedBook``。
    """
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "")
    lines = [line.rstrip() for line in normalized.split("\n")]
    non_empty_lines = [line for line in lines if line.strip()]

    title = _detect_title(non_empty_lines, title_hint)
    body_lines = list(lines)
    if non_empty_lines and non_empty_lines[0].strip() == title.strip():
        first_title_index = body_lines.index(non_empty_lines[0])
        body_lines = body_lines[first_title_index + 1 :]

    chapters = _split_chapters(body_lines)
    logger.info("章节解析完成: 书名=%s, 章节数=%d", title, len(chapters))
    return ParsedBook(title=title, chapters=chapters, full_text="\n".join(body_lines).strip())


def chunk_chapters(
    chapters: list[Chapter],
    chunk_size: int = 2000,
    overlap: int = 200,
) -> list[Chunk]:
    """按固定长度和重叠区间切分章节内容。

    Args:
        chapters: 待切分的章节列表。
        chunk_size: 单个 chunk 的最大字符数。
        overlap: 相邻 chunk 之间重复保留的字符数，用于减少断点信息丢失。

    Returns:
        保留章节编号、标题和片段序号的 chunk 列表。

    Raises:
        ValueError: ``chunk_size`` 或 ``overlap`` 不合法。
    """
    if chunk_size <= 0:
        raise ValueError("chunk_size 必须大于 0")
    if overlap < 0 or overlap >= chunk_size:
        raise ValueError("overlap 必须大于等于 0 且小于 chunk_size")

    chunks: list[Chunk] = []
    step = chunk_size - overlap

    for chapter in chapters:
        content = chapter.content.strip()
        if not content:
            continue

        if len(content) <= chunk_size:
            chunks.append(
                Chunk(
                    chapter_no=chapter.number,
                    chapter_title=chapter.title,
                    chunk_index=0,
                    content=content,
                )
            )
            continue

        start = 0
        chunk_index = 0
        while start < len(content):
            end = min(len(content), start + chunk_size)
            chunk_text = content[start:end].strip()
            if chunk_text:
                chunks.append(
                    Chunk(
                        chapter_no=chapter.number,
                        chapter_title=chapter.title,
                        chunk_index=chunk_index,
                        content=chunk_text,
                    )
                )
                chunk_index += 1
            if end >= len(content):
                break
            start += step

    logger.info("文本切块完成: 输入章节=%d, 输出chunks=%d, chunk_size=%d, overlap=%d",
                 len(chapters), len(chunks), chunk_size, overlap)
    return chunks


def filename_to_title(filename: str) -> str:
    """Turn a filename into a clean display title."""
    return Path(filename).stem.strip() or "未命名书籍"


def _detect_title(non_empty_lines: list[str], title_hint: str | None) -> str:
    """从文件名或首个非空行推断书名。"""
    if title_hint:
        return filename_to_title(title_hint)
    if not non_empty_lines:
        return "未命名书籍"
    first_line = non_empty_lines[0].strip()
    if len(first_line) <= 40 and not CHAPTER_HEADING_RE.match(first_line):
        return first_line
    return "未命名书籍"


def _split_chapters(lines: list[str]) -> list[Chapter]:
    """根据轻量章节标题规则拆分正文行。"""
    chapters: list[Chapter] = []
    current_title = "正文"
    current_lines: list[str] = []
    chapter_number = 1

    for raw_line in lines:
        line = raw_line.strip()
        if CHAPTER_HEADING_RE.match(line):
            if current_lines:
                chapters.append(
                    Chapter(
                        number=chapter_number,
                        title=current_title,
                        content="\n".join(current_lines).strip(),
                    )
                )
                chapter_number += 1
                current_lines = []
            current_title = line
            continue
        current_lines.append(raw_line)

    if current_lines or not chapters:
        chapters.append(
            Chapter(
                number=chapter_number,
                title=current_title,
                content="\n".join(current_lines).strip(),
            )
        )

    return [chapter for chapter in chapters if chapter.content]
