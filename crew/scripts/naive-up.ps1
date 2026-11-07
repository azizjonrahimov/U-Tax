# Owner's helper for `naive up` (crew/reports/agents.md, "What the owner must do").
# Loads the seven seat tokens from backend/.env into this shell only (never printed),
# points every agent at the public gateway, runs the offline checks, then naive up.
#
#   .\crew\scripts\naive-up.ps1 -Url https://<words>.trycloudflare.com           # dry run
#   .\crew\scripts\naive-up.ps1 -Url https://<words>.trycloudflare.com -Apply    # for real
#
# Copy your full-access key (sk_live_...) to the clipboard just before running: `naive up`
# reads NAIVE_API_KEY only. It never uses NAIVE_API_KEY from backend/.env, which is the
# gateway's narrow key.
param(
    [Parameter(Mandatory = $true)][string]$Url,
    [switch]$Apply
)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$envFile = Join-Path $root "backend\.env"
$base = $Url.TrimEnd("/")
if ($base -notmatch "^https://[^/]+$") { throw "Url must be https://<host> with no path (got '$Url')" }

$health = Invoke-RestMethod -Uri "$base/api/health" -TimeoutSec 15
if ($health.status -ne "ok") { throw "the gateway at $base is not healthy" }
Write-Host "gateway ok at $base"

$seats = "COS", "INTAKE", "TAX", "QA", "ACCT", "MKT", "HR"
$found = @{}
foreach ($line in Get-Content $envFile) {
    if ($line -match "^UTAX_TOOL_TOKEN_([A-Z]+)=(.+)$" -and $seats -contains $Matches[1]) {
        Set-Item -Path "Env:UTAX_TOOL_TOKEN_$($Matches[1])" -Value $Matches[2].Trim()
        $found[$Matches[1]] = $true
    }
}
$missing = $seats | Where-Object { -not $found[$_] }
if ($missing) { throw "backend/.env has no token for: $($missing -join ', ')" }
# `naive up` reads NAIVE_API_KEY, not the CLI profile. Take the owner's full-access key from
# the clipboard (copy it just before running), for this process only, then clear the clipboard.
$key = ""
try { $key = (Get-Clipboard -Raw).Trim() } catch {}
if ($key -notmatch "^sk_live_[A-Za-z0-9]{20,}$") {
    throw "copy your full-access Naive key (sk_live_...) to the clipboard first, then run this again"
}
$env:NAIVE_API_KEY = $key
Remove-Variable key
Set-Clipboard -Value " "
Write-Host "Naive key taken from the clipboard (not shown); clipboard cleared"
$env:UTAX_MCP_URL = "$base/mcp"
Write-Host "7 seat tokens loaded; UTAX_MCP_URL=$env:UTAX_MCP_URL"

Push-Location (Join-Path $root "naive")
try {
    npm run -s check
    if ($LASTEXITCODE -ne 0) { throw "npm run check failed" }
    if ($Apply) { naive.cmd up } else { naive.cmd up --dry-run }
    if ($LASTEXITCODE -ne 0) { throw "naive up exited $LASTEXITCODE" }
} finally {
    Pop-Location
}
