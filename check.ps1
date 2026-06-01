# 本機四道門 + 合約漂移（PowerShell 版，等同 Makefile 的 check）。
$ErrorActionPreference = "Stop"
$Uv  = if ($env:UV)  { $env:UV }  else { "python -m uv" }
$Pnpm = if ($env:PNPM) { $env:PNPM } else { "pnpm" }
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

function Step([string]$cmd) {
    Write-Host "==> $cmd" -ForegroundColor Cyan
    Invoke-Expression $cmd
    if ($LASTEXITCODE -ne 0) { throw "FAILED ($LASTEXITCODE): $cmd" }
}

# 1) 後端四道門
Push-Location backend
Step "$Uv run ruff check ."
Step "$Uv run ruff format --check ."
Step "$Uv run mypy ."
Step "$Uv run pytest"
# 2a) 重生 openapi.json
Step "$Uv run python -m app.scripts.export_openapi"
Pop-Location

# 2b) 重生 api-types.ts + 漂移檢查
Push-Location frontend
Step "$Pnpm run gen:api"
Pop-Location
Step "git diff --exit-code frontend/openapi.json frontend/lib/api-types.ts"

# 3) 前端關卡
Push-Location frontend
Step "$Pnpm run lint"
Step "$Pnpm run typecheck"
Step "$Pnpm run test"
Pop-Location

Write-Host "==> ALL CHECKS PASSED" -ForegroundColor Green
