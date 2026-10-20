$ErrorActionPreference = 'Stop'
Set-Location (Split-Path -Parent $PSScriptRoot)

$lifeFlowAdc = Join-Path $env:APPDATA 'gcloud\application_default_credentials.json'
if (-not (Test-Path -LiteralPath $lifeFlowAdc -PathType Leaf)) {
    throw 'ADC absent. Install Google Cloud CLI, then run: gcloud auth application-default login'
}
if (Test-Path -LiteralPath 'compose.override.yaml') {
    throw 'compose.override.yaml already exists. Merge the ADC mount manually rather than overwrite existing settings.'
}

# Only a path is written. Credentials stay in the user's Google Cloud directory.
$lifeFlowSource = $lifeFlowAdc.Replace('\', '/').Replace("'", "''")
@"
services:
  api:
    environment:
      GOOGLE_APPLICATION_CREDENTIALS: /var/run/google/adc.json
    volumes:
      - type: bind
        source: '$lifeFlowSource'
        target: /var/run/google/adc.json
        read_only: true
"@ | Set-Content -LiteralPath 'compose.override.yaml' -Encoding UTF8

docker compose config --quiet
if ($LASTEXITCODE -ne 0) { throw 'Docker Compose configuration validation failed.' }
docker compose up -d
if ($LASTEXITCODE -ne 0) { throw 'Docker Compose startup failed.' }
Write-Host 'ADC mount configured. Open http://localhost:8080 and select Retry recommendations.'
