---
title: "MuMuAINovel — Build workflow"
type: recipe
tags: [workflow, build-workflow, vite, react, auto-generated]
created: 2026-07-02T09:51:43.000Z
source: spec-setup
---

# MuMuAINovel — Build workflow

## Goal
Build the frontend for production deployment.

## Prerequisites
- Node.js >= 18
- Frontend dependencies installed: `cd frontend && npm install`

## Steps
1. `cd frontend`
2. `npm run build` (runs `tsc -b && vite build`)

## Expected Outcome
- Compiled frontend output in `backend/static/` directory
- TypeScript type-checking passes
- Vite produces optimized production bundle

## Related
- [[architecture-constraints]]
