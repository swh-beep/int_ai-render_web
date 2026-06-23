param(
  [string]$FrontRoot = "C:\Users\User\.codex\worktrees\intea-front\main-test",
  [string]$PatchPath = ""
)

$ErrorActionPreference = "Stop"

if (-not $PatchPath) {
  $ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
  $RepoRoot = Split-Path -Parent $ScriptRoot
  $PatchPath = Join-Path $RepoRoot "patches\intea-front-cart-simple-batch.patch"
}

if (-not (Test-Path -LiteralPath $FrontRoot)) {
  throw "Front repo not found: $FrontRoot"
}

if (-not (Test-Path -LiteralPath $PatchPath)) {
  throw "Patch file not found: $PatchPath"
}

$safeDirectory = ($FrontRoot -replace "\\", "/")
$mustExist = @(
  "src/components/ai-consultant/AIConsultantWidget.tsx",
  "src/lib/ai-consultant/render.ts"
)

foreach ($relativePath in $mustExist) {
  $target = Join-Path $FrontRoot ($relativePath -replace "/", "\")
  if (-not (Test-Path -LiteralPath $target)) {
    git -c "safe.directory=$safeDirectory" -C $FrontRoot restore --source=HEAD -- $relativePath
  }
}

git -c "safe.directory=$safeDirectory" -C $FrontRoot apply --check $PatchPath
git -c "safe.directory=$safeDirectory" -C $FrontRoot apply $PatchPath
git -c "safe.directory=$safeDirectory" -C $FrontRoot status --short
