$ErrorActionPreference = 'Stop'
Set-Location (Split-Path -Parent $PSScriptRoot)

if (Test-Path -LiteralPath '.venv\Scripts\python.exe') {
    $lifeFlowPython = '.\.venv\Scripts\python.exe'
} elseif (Test-Path -LiteralPath '.tools\python\python.exe') {
    $lifeFlowPython = '.\.tools\python\python.exe'
} else {
    throw 'Python local absent. Suivez les instructions Python ou Docker du README.'
}

Write-Host 'LifeFlow : http://localhost:8000 — Ctrl+C pour arrêter.'
& $lifeFlowPython -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
