param(
  [ValidateSet("both", "web", "worker")]
  [string]$Target = "both",

  [int]$Limit = 100,

  [string]$Level = "",

  [string]$Text = "",

  [switch]$Tail
)

$ErrorActionPreference = "Stop"

$render = "C:\Users\User\.codex\bin\render.exe"
if (-not (Test-Path -LiteralPath $render)) {
  $render = "render"
}

$apiKey = [Environment]::GetEnvironmentVariable("RENDER_API_KEY", "User")
if (-not $apiKey) {
  throw "RENDER_API_KEY is not set in the user environment."
}
$env:RENDER_API_KEY = $apiKey

$resourceMap = @{
  web = "srv-d4nas8uuk2gs739mogv0"
  worker = "srv-d4ok9q2li9vc7384c0kg"
}

if ($Target -eq "both") {
  $resources = "$($resourceMap.web),$($resourceMap.worker)"
} else {
  $resources = $resourceMap[$Target]
}

$args = @("logs", "--resources", $resources)

if ($Tail) {
  $args += "--tail"
} else {
  $args += @("--limit", "$Limit", "--output", "json")
}

if ($Level) {
  $args += @("--level", $Level)
}

if ($Text) {
  $args += @("--text", $Text)
}

& $render @args
