$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$venvPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $venvPython)) {
    python -m venv .venv
}
& $venvPython -m pip install -e .
if ($LASTEXITCODE -ne 0) { throw 'error' }
& $venvPython -m enterprise_agent.application.bootstrap
if ($LASTEXITCODE -ne 0) { throw 'error' }
Write-Host 'Workspace: http://127.0.0.1:8000'
& $venvPython -m uvicorn enterprise_agent.api.app:app --host 127.0.0.1 --port 8000 --workers 1
