param([Parameter(ValueFromRemainingArguments=$true)][string[]]$CommandArgs)
$ErrorActionPreference = 'Stop'
if (-not $CommandArgs) { $CommandArgs = @('status') }
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) { throw 'Create the repo .venv and install industrial_station/requirements.txt first.' }
Push-Location $root
try {
    & $python -m industrial_station.cli @CommandArgs
    if ($LASTEXITCODE -ne 0) { throw "Industrial CLI failed: $LASTEXITCODE" }
} finally { Pop-Location }
