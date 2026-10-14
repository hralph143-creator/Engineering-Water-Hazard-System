$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$compilerCandidates = @(
    (Join-Path ${env:ProgramFiles(x86)} 'Inno Setup 6\ISCC.exe'),
    (Join-Path $env:ProgramFiles 'Inno Setup 6\ISCC.exe'),
    (Join-Path $env:LOCALAPPDATA 'Programs\Inno Setup 6\ISCC.exe')
)
$compiler = $compilerCandidates | Where-Object { $_ -and (Test-Path -LiteralPath $_) } | Select-Object -First 1
if (-not $compiler) { throw 'Inno Setup 6 compiler was not found.' }

$desktopExe = Join-Path $projectRoot 'release\ZhiyuShield\ZhiyuShield.exe'
if (-not (Test-Path -LiteralPath $desktopExe)) {
    throw 'Desktop package is missing. Run packaging\build_windows.ps1 first.'
}

Set-Location -LiteralPath $PSScriptRoot
& $compiler 'FloodRiskSystem.iss'
if ($LASTEXITCODE -ne 0) { throw "Installer build failed with exit code $LASTEXITCODE" }
