"""固定查询语料检索评估指标。"""

from __future__ import annotations

import math
from typing import Any


def percentile(values: list[float], percentile_value: float) -> float:
    """在不依赖可选数值库的情况下返回最近秩百分位数。"""
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(1, math.ceil(percentile_value * len(ordered)))
    return ordered[min(rank - 1, len(ordered) - 1)]


def evaluate_records(
    records: list[dict[str, Any]],
    minimum_corpus_books: int,
    corpus_book_count: int | None = None,
) -> dict[str, Any]:
    """评估延迟、标注相关性、标签命中率和来源多样性。"""
    latencies = [float(record.get("latency_ms", 0)) for record in records]
    diversity_scores: list[float] = []
    recall_scores: list[float] = []
    precision_scores: list[float] = []
    tag_scores: list[float] = []

    for record in records:
        passages = record.get("passages") or []
        retrieved_ids = [
            str(passage.get("book_id") or "")
            for passage in passages
            if passage.get("book_id")
        ]
        if passages:
            diversity_scores.append(len(set(retrieved_ids)) / len(passages))

        relevant_ids = {
            str(book_id)
            for book_id in record.get("relevant_book_ids") or []
            if str(book_id)
        }
        if relevant_ids:
            retrieved_set = set(retrieved_ids)
            relevant_hits = len(retrieved_set & relevant_ids)
            recall_scores.append(relevant_hits / len(relevant_ids))
            precision_scores.append(relevant_hits / max(1, len(retrieved_set)))

        expected_tags = {
            str(tag)
            for tag in record.get("expected_tags") or []
            if str(tag)
        }
        if expected_tags:
            retrieved_tags = {
                str(tag)
                for passage in passages
                for tag in passage.get("tags") or []
            }
            tag_scores.append(len(expected_tags & retrieved_tags) / len(expected_tags))

    scale_gate = "not_evaluated"
    if corpus_book_count is not None:
        scale_gate = (
            "passed" if corpus_book_count >= minimum_corpus_books else "blocked"
        )

    return {
        "query_count": len(records),
        "p95_latency_ms": round(percentile(latencies, 0.95), 2),
        "mean_cross_book_diversity": _mean(diversity_scores),
        "recall_at_k": _mean(recall_scores) if recall_scores else None,
        "manual_precision_at_k": _mean(precision_scores) if precision_scores else None,
        "expected_tag_hit_rate": _mean(tag_scores) if tag_scores else None,
        "labeled_query_count": len(recall_scores),
        "corpus_book_count": corpus_book_count,
        "minimum_corpus_books": minimum_corpus_books,
        "scale_gate": scale_gate,
    }


def _mean(values: list[float]) -> float:
    """返回经过舍入的算术平均值，保证报告结果稳定。"""
    return round(sum(values) / len(values), 4) if values else 0.0
