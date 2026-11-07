# Put one secret from the clipboard into backend/.env without showing it, then clear the clipboard.
#   1. copy the key from the sponsor's dashboard
#   2. powershell -ExecutionPolicy Bypass -File .\crew\scripts\set-key.ps1 AGENTMAIL_API_KEY
param([Parameter(Mandatory = $true)][ValidatePattern("^[A-Z][A-Z0-9_]+$")][string]$Name)
$ErrorActionPreference = "Stop"
$f = Join-Path (Split-Path -Parent (Split-Path -Parent $PSScriptRoot)) "backend\.env"
$v = (Get-Clipboard -Raw).Trim()
if ($v.Length -lt 8 -or $v -match "\s") { throw "the clipboard doesn't look like a key; copy it again" }
$lines = @(Get-Content $f)
$hit = $false
$lines = $lines | ForEach-Object { if ($_ -match "^$Name=") { $hit = $true; "$Name=$v" } else { $_ } }
if (-not $hit) { $lines += "$Name=$v" }
[IO.File]::WriteAllLines($f, [string[]]$lines, (New-Object Text.UTF8Encoding($false)))
Remove-Variable v; Set-Clipboard -Value " "
Write-Host "$Name saved to backend/.env ($(if ($hit) {'replaced'} else {'added'})); clipboard cleared"
