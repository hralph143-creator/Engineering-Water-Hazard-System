$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonExe = if ($env:FLOODRISK_PYTHON) { $env:FLOODRISK_PYTHON } else { 'python' }

Set-Location -LiteralPath $projectRoot
Push-Location -LiteralPath (Join-Path $projectRoot 'frontend')
try {
    if (-not (Test-Path -LiteralPath 'node_modules')) { npm.cmd ci }
    npm.cmd run build
    if ($LASTEXITCODE -ne 0) { throw "Frontend build failed with exit code $LASTEXITCODE" }
} finally {
    Pop-Location
}

& $pythonExe packaging\make_icon.py
if ($LASTEXITCODE -ne 0) { throw "Icon generation failed with exit code $LASTEXITCODE" }

$releaseTarget = Join-Path $projectRoot 'release\ZhiyuShield'
$workTarget = Join-Path $projectRoot 'build\desktop'
if (Test-Path -LiteralPath $releaseTarget) { Remove-Item -LiteralPath $releaseTarget -Recurse -Force }
if (Test-Path -LiteralPath $workTarget) { Remove-Item -LiteralPath $workTarget -Recurse -Force }

& $pythonExe -m PyInstaller --noconfirm --clean --distpath release --workpath build\desktop packaging\FloodRiskSystem.spec
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed with exit code $LASTEXITCODE" }
& $pythonExe packaging\prepare_release.py
if ($LASTEXITCODE -ne 0) { throw "Release preparation failed with exit code $LASTEXITCODE" }

Write-Host "Desktop package created: $releaseTarget\ZhiyuShield.exe"
