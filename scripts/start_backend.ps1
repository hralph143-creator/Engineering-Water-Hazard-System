$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
& 'E:\Python3.11.0\python.exe' -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8001

