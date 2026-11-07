# Owner's one-command Vercel deploy of the theater, /talk and /ceo
# (crew/reports/frontend.md, "Deploying the frontend on Vercel").
#   powershell -ExecutionPolicy Bypass -File .\crew\scripts\deploy-frontend.ps1
# Reads VERCEL_TOKEN, THEATER_TOKEN and UTAX_PUBLIC_URL from backend/.env. Nothing is printed.
# NEXT_PUBLIC_* values are baked into the build: rerun this after every tunnel rotation.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$envFile = Join-Path $root "backend\.env"
$vals = @{}
foreach ($line in Get-Content $envFile) { if ($line -match "^([A-Z0-9_]+)=(.*)$") { $vals[$Matches[1]] = $Matches[2].Trim() } }
foreach ($k in "VERCEL_TOKEN", "THEATER_TOKEN", "UTAX_PUBLIC_URL") { if (-not $vals[$k]) { throw "backend/.env has no $k" } }
$api = $vals["UTAX_PUBLIC_URL"].TrimEnd("/")
if ($api -notmatch "^https://([^/]+)$") { throw "UTAX_PUBLIC_URL must be https://<host> (got $api)" }
$apiHost = $Matches[1]
$pairs = @("NEXT_PUBLIC_API_URL=$api", "NEXT_PUBLIC_THEATER_TOKEN=$($vals['THEATER_TOKEN'])", "NEXT_PUBLIC_ASSET_HOSTS=$apiHost")
$flags = @()
foreach ($p in $pairs) { $flags += "--build-env"; $flags += $p; $flags += "--env"; $flags += $p }
Push-Location (Join-Path $root "frontend")
try {
    # Windows PowerShell 5.1 turns any native stderr line (npm notices) into an error under "Stop".
    $ErrorActionPreference = "Continue"
    $out = & npx -y vercel@latest deploy --prod --yes --token $vals["VERCEL_TOKEN"] --name u-tax @flags 2>&1 | ForEach-Object { "$_" } | Out-String
    $out = $out.Replace($vals["VERCEL_TOKEN"], "***").Replace($vals["THEATER_TOKEN"], "***")
    Write-Host $out
    if ($LASTEXITCODE -ne 0) { throw "vercel deploy exited $LASTEXITCODE" }
} finally { Pop-Location }
