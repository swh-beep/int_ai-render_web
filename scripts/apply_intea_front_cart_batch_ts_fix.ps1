param(
  [string]$FrontRoot = "C:\Users\User\.codex\worktrees\intea-front\main-test",
  [string]$PatchPath = ""
)

$ErrorActionPreference = "Stop"

if (-not $PatchPath) {
  $ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
  $RepoRoot = Split-Path -Parent $ScriptRoot
  $PatchPath = Join-Path $RepoRoot "patches\intea-front-cart-simple-batch-ts-fix.patch"
}

if (-not (Test-Path -LiteralPath $FrontRoot)) {
  throw "Front repo not found: $FrontRoot"
}

if (-not (Test-Path -LiteralPath $PatchPath)) {
  throw "Patch file not found: $PatchPath"
}

$safeDirectory = ($FrontRoot -replace "\\", "/")

git -c "safe.directory=$safeDirectory" -C $FrontRoot apply --check $PatchPath
git -c "safe.directory=$safeDirectory" -C $FrontRoot apply $PatchPath
git -c "safe.directory=$safeDirectory" -C $FrontRoot status --short -- `
  "src/components/ai-consultant/AIConsultantWidget.tsx" `
  "src/lib/ai-consultant/render.ts" `
  "src/pages/api/ai-consultant/render/cart-simple-batch.ts"
