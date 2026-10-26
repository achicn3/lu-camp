#!/usr/bin/env bash
# 本機四道門 + 合約漂移（bash 版，等同 Makefile 的 check）。
set -euo pipefail

UV="${UV:-python -m uv}"
PNPM="${PNPM:-pnpm}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

echo "==> backend: ruff / format / mypy / pytest"
( cd backend && $UV run ruff check . && $UV run ruff format --check . && $UV run mypy . && $UV run pytest )

echo "==> contract: regenerate openapi.json + api-types.ts, check drift"
( cd backend && $UV run python -m app.scripts.export_openapi )
( cd frontend && $PNPM run gen:api )
git diff --exit-code frontend/openapi.json frontend/lib/api-types.ts

echo "==> frontend: eslint / tsc / vitest"
( cd frontend && $PNPM run lint && $PNPM run typecheck && $PNPM run test )

echo "==> shell: 變數後緊接中文須加大括號（bash 3.2）"
scripts/check-shell-var-braces.sh

echo "==> ALL CHECKS PASSED"
