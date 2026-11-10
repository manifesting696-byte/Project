$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
docker compose up -d --wait postgres
if ($LASTEXITCODE -ne 0) { throw 'PostgreSQL startup failed' }
docker compose build app init
if ($LASTEXITCODE -ne 0) { throw 'Application build failed' }
docker compose run --rm --no-deps init
if ($LASTEXITCODE -ne 0) { throw 'Database initialization failed' }
docker compose up -d --no-deps --wait app
if ($LASTEXITCODE -ne 0) { throw 'Application startup failed' }
Write-Host 'Workspace: http://127.0.0.1:8000'
