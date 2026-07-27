"""Run the fixed corpus retrieval benchmark through streamable HTTP MCP."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys
import time
from typing import Any

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))

from book_analyzer.benchmark import evaluate_records


DEFAULT_QUERIES = Path(__file__).resolve().parents[1] / "benchmarks" / "corpus_queries.json"


def _payload(result: Any) -> dict[str, Any]:
    structured = getattr(result, "structuredContent", None)
    if structured is None:
        structured = getattr(result, "structured_content", None)
    return structured if isinstance(structured, dict) else {}


async def run_benchmark(
    server_url: str,
    benchmark: dict[str, Any],
) -> list[dict[str, Any]]:
    """Execute every fixed query in one MCP session."""
    records: list[dict[str, Any]] = []
    async with streamablehttp_client(server_url) as (read_stream, write_stream, _):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            for case in benchmark.get("queries", []):
                arguments = {
                    key: value
                    for key, value in {
                        "query": case["query"],
                        "scene_type": case.get("scene_type"),
                        "mood": case.get("mood"),
                        "genre": case.get("genre"),
                        "style_tags": case.get("style_tags"),
                        "limit": case.get("limit", 5),
                    }.items()
                    if value is not None
                }
                started_at = time.perf_counter()
                result = await session.call_tool(
                    "corpus_search_reference_passages",
                    arguments,
                )
                latency_ms = (time.perf_counter() - started_at) * 1000
                payload = _payload(result)
                records.append(
                    {
                        "id": case.get("id", case["query"]),
                        "query": case["query"],
                        "latency_ms": round(latency_ms, 2),
                        "relevant_book_ids": case.get("relevant_book_ids", []),
                        "expected_tags": case.get("expected_tags", []),
                        "passages": payload.get("passages", []),
                    }
                )
    return records


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server-url", default="http://127.0.0.1:8765/mcp")
    parser.add_argument("--queries", type=Path, default=DEFAULT_QUERIES)
    parser.add_argument("--corpus-book-count", type=int)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--summary-only", action="store_true")
    args = parser.parse_args()

    benchmark = json.loads(args.queries.read_text(encoding="utf-8"))
    records = asyncio.run(run_benchmark(args.server_url, benchmark))
    report = {
        "server_url": args.server_url,
        "metrics": evaluate_records(
            records,
            minimum_corpus_books=int(benchmark.get("minimum_corpus_books", 50)),
            corpus_book_count=args.corpus_book_count,
        ),
        "records": records,
    }
    output = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(output, encoding="utf-8")
    printed = report["metrics"] if args.summary_only else report
    print(json.dumps(printed, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
