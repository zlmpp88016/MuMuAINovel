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
