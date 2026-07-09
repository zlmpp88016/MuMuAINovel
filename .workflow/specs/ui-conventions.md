---
title: "UI Conventions"
category: ui
---
# UI Conventions

Auto-generated from project analysis. Update manually as patterns evolve.

## Framework
- React 18 with TypeScript
- Build tool: Vite 7
- Routing: react-router-dom 6
- State management: Zustand 5
- UI library: Ant Design 5 (antd) + @ant-design/icons
- HTTP client: axios
- Drag and drop: react-beautiful-dnd

## Structure
- `src/pages/` — Page-level components
- `src/components/` — Reusable UI components
- `src/services/` — API service layer
- `src/store/` — Zustand state stores
- `src/types/` — TypeScript type definitions
- `src/utils/` — Utility functions

## Build Output
- Build output: `backend/static/` (served by FastAPI)

## Development
- Dev server with proxy: `/api` -> `http://localhost:8000`
- ESLint for linting

## Entries

{empty section for spec-add entries}
