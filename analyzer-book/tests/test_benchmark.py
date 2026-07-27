from __future__ import annotations

from book_analyzer.benchmark import evaluate_records, percentile


def test_percentile_uses_nearest_rank() -> None:
    assert percentile([10, 20, 30, 40], 0.95) == 40
    assert percentile([], 0.95) == 0


def test_evaluate_records_reports_quality_and_scale_gate() -> None:
    records = [
        {
            "latency_ms": 20,
            "relevant_book_ids": ["book-1", "book-3"],
            "expected_tags": ["伏笔", "冲突"],
            "passages": [
                {"book_id": "book-1", "tags": ["伏笔"]},
                {"book_id": "book-2", "tags": ["冲突"]},
            ],
        },
        {
            "latency_ms": 80,
            "relevant_book_ids": [],
            "expected_tags": [],
            "passages": [
                {"book_id": "book-1", "tags": []},
                {"book_id": "book-1", "tags": []},
            ],
        },
    ]

    metrics = evaluate_records(
        records,
        minimum_corpus_books=50,
        corpus_book_count=2,
    )

    assert metrics["p95_latency_ms"] == 80
    assert metrics["mean_cross_book_diversity"] == 0.75
    assert metrics["recall_at_k"] == 0.5
    assert metrics["manual_precision_at_k"] == 0.5
    assert metrics["expected_tag_hit_rate"] == 1.0
    assert metrics["scale_gate"] == "blocked"
