---
title: "Coding Conventions"
category: coding
---
# Coding Conventions

Auto-generated from project analysis. Update manually as patterns evolve.

## Formatting
- Indentation: 4 spaces (Python)
- Line length: not configured
- Trailing commas: no (Python)
- Semicolons: no (Python)

## Naming
- Variables/functions: snake_case (Python), camelCase (TypeScript)
- Classes/types: PascalCase (Python + TypeScript)
- Constants: UPPER_SNAKE_CASE
- Files: snake_case (Python), kebab-case (TypeScript)

## Imports
- Style: named imports
- Order: standard library, third-party, local modules (Python)
- Type hints: always used (Python 3.10+)

## Patterns
- FastAPI route handlers with APIRouter
- SQLAlchemy async session pattern
- Pydantic models for request/response validation
- Service layer pattern (api -> service -> model)
- BackgroundTasks for async operations

## Entries

{empty section for spec-add entries}


<spec-entry category="coding" keywords="corpus trace logging redaction" date="2026-07-27" sid="S-20260727-5ywl" title="章节语料 Trace 必须使用 allowlist" description="章节语料编排日志的内容隔离规则" source="upgrade@d81a073">

### 章节语料 Trace 必须使用 allowlist

章节生成的语料 trace 只能记录目录状态、选中标签、MCP 工具名、回退阶段、命中数、模板注入、上下文长度和耗时。不得记录章节大纲、Prompt、参考正文、生成正文、凭据或原始异常文本。单章和批量生成必须调用同一 trace API。

</spec-entry>