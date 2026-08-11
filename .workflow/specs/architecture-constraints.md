---
title: "Architecture Constraints"
category: arch
---
# Architecture Constraints

Auto-generated from project structure. Update manually as architecture evolves.

## Module Structure
- Type: monorepo
- Key modules:
  - backend/ — Python FastAPI backend
  - frontend/ — React TypeScript frontend (Vite)

## Backend Layer Boundaries
- `app/api/` — Route handlers (presentation layer)
- `app/services/` — Business logic
- `app/models/` — SQLAlchemy ORM models (data layer)
- `app/middleware/` — FastAPI middleware (auth, request ID)
- `app/mcp/` — Model Context Protocol plugins
- `app/config.py` — Settings via pydantic-settings
- `app/database.py` — SQLAlchemy async engine + session factory

## Dependency Rules
- api -> services -> models (top-down only, no circular imports)
- services layer is the only layer that calls AI services (OpenAI/Anthropic)
- Database session injected via FastAPI Depends()

## Technology Constraints
- Runtime: Python >= 3.10 (backend), Node.js >= 18 (frontend)
- Web framework: FastAPI 0.121 (backend), React 18 + Vite (frontend)
- Database: PostgreSQL via SQLAlchemy 2.0 async
- AI SDKs: openai 2.7, anthropic 0.72
- Vector DB: ChromaDB 1.3.2
- MCP: Model Context Protocol Python SDK 1.21

## Entries

{empty section for spec-add entries}


<spec-entry category="arch" keywords="mcp tags corpus retrieval" date="2026-07-27" sid="S-20260727-ipur" title="MCP 标签检索必须非阻塞回退" description="语料标签检索的非阻塞回退和编排边界" source="upgrade@d81a073">

### MCP 标签检索必须非阻塞回退

章节样例检索以章节大纲的语义召回为基线。标签目录和目录内精确标签选择仅用于提升精度；目录为空、标签缺失、选择超时或筛选零命中时，backend 必须逐级放宽标签条件并继续生成。依赖目录再检索的多跳流程由业务服务显式编排，不能依赖只保证首轮 tools 的通用模型工具循环。

</spec-entry>