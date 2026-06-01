# 本機品質關卡彙整（本專案不使用 GitHub CI）。
# Windows 無 make 時用已安裝的 mingw32-make：`mingw32-make check`
# 也可直接跑 ./check.ps1 (PowerShell) 或 ./check.sh (bash)。
#
# UV 預設用 `python -m uv` 以免 uv 未在 PATH；可覆寫：make check UV=uv
UV ?= python -m uv
PNPM ?= pnpm

.PHONY: check backend-check contract-check frontend-check install

check: backend-check contract-check frontend-check
	@echo "==> ALL CHECKS PASSED"

# 1) 後端四道門：lint / format / type / test+coverage
backend-check:
	cd backend && $(UV) run ruff check .
	cd backend && $(UV) run ruff format --check .
	cd backend && $(UV) run mypy .
	cd backend && $(UV) run pytest

# 2) API 合約漂移：重生 openapi.json + api-types.ts，與版控比對，有差異即失敗
contract-check:
	cd backend && $(UV) run python -m app.scripts.export_openapi
	cd frontend && $(PNPM) run gen:api
	git diff --exit-code frontend/openapi.json frontend/lib/api-types.ts

# 3) 前端關卡：eslint / tsc / 測試（需先跑 contract-check 生成 api-types.ts）
frontend-check:
	cd frontend && $(PNPM) run lint
	cd frontend && $(PNPM) run typecheck
	cd frontend && $(PNPM) run test

install:
	cd backend && $(UV) sync
	cd frontend && $(PNPM) install
