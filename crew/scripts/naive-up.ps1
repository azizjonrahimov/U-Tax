# Owner's helper for `naive up` (crew/reports/agents.md, "What the owner must do").
# Loads the seven seat tokens from backend/.env into this shell only (never printed),
# points every agent at the public gateway, runs the offline checks, then naive up.
#
#   .\crew\scripts\naive-up.ps1 -Url https://<words>.trycloudflare.com           # dry run
#   .\crew\scripts\naive-up.ps1 -Url https://<words>.trycloudflare.com -Apply    # for real
#
# Uses your own `naive login` profile (the full-access key). It never reads NAIVE_API_KEY
# from backend/.env: that is the gateway's narrow key, and it would override your profile.
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
Remove-Item Env:NAIVE_API_KEY -ErrorAction SilentlyContinue
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
