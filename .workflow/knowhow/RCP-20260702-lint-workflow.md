---
title: "MuMuAINovel — Lint workflow"
type: recipe
tags: [workflow, lint-workflow, eslint, react, auto-generated]
created: 2026-07-02T09:51:43.000Z
source: spec-setup
---

# MuMuAINovel — Lint workflow

## Goal
Run the ESLint linter for the frontend codebase.

## Prerequisites
- Node.js >= 18
- Frontend dependencies installed: `cd frontend && npm install`

## Steps
1. `cd frontend`
2. `npm run lint` (runs `eslint .`)

## Expected Outcome
- ESLint reports any code quality or style issues
- Exit code 0 when no issues found

## Common Pitfalls
- ESLint may report errors from generated or vendored files — check `.eslintignore` if needed

## Related
- [[coding-conventions]]
