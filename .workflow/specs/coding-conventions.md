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
