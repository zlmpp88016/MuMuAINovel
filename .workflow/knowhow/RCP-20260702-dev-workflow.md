---
title: "MuMuAINovel — Dev workflow"
type: recipe
tags: [workflow, dev-workflow, fastapi, vite, docker, auto-generated]
created: 2026-07-02T09:51:43.000Z
source: spec-setup
---

# MuMuAINovel — Dev workflow

## Goal
Start the full development environment with PostgreSQL, FastAPI backend, and React frontend.

## Prerequisites
- Node.js >= 18
- Python >= 3.10
- Docker (for PostgreSQL)
- Install backend deps: `pip install -r backend/requirements.txt`
- Install frontend deps: `cd frontend && npm install`

## Steps
1. Start PostgreSQL: `docker compose up -d postgres`
2. Start FastAPI backend: `cd backend && uvicorn app.main:app --reload --port 8000`
3. Start Vite frontend: `cd frontend && npm run dev` (serves on port 5173, proxies `/api` to `localhost:8000`)

## Expected Outcome
- Backend API reachable at http://localhost:8000
- Frontend reachable at http://localhost:5173 with HMR active
- API requests from frontend proxied to backend automatically

## Common Pitfalls
- PostgreSQL not ready: wait for health check, then start backend
- Port conflicts: check if port 8000 or 5173 is already in use
- Missing env vars: copy `backend/.env.example` to `backend/.env` and configure

## Related
- [[architecture-constraints]]
