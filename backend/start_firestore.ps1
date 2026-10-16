$ErrorActionPreference = 'Stop'
$settingsPath = Join-Path $PSScriptRoot '.env'
if (-not (Test-Path -LiteralPath $settingsPath -PathType Leaf)) {
    throw "Missing $settingsPath. Create it from .env.example and configure Firebase credentials."
}

$settings = [System.IO.File]::ReadAllText($settingsPath) | ConvertFrom-StringData
foreach ($name in $settings.Keys) {
    [Environment]::SetEnvironmentVariable($name, $settings[$name], 'Process')
}
if ($env:SIMULATION_STORAGE_BACKEND -ne 'firestore') { throw 'SIMULATION_STORAGE_BACKEND must be firestore.' }
if (-not (Test-Path -LiteralPath $env:FIREBASE_SERVICE_ACCOUNT_JSON -PathType Leaf)) {
    throw 'The configured service-account file was not found.'
}

Push-Location $PSScriptRoot
try {
    & ..\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1
    if ($LASTEXITCODE -ne 0) { throw "Uvicorn exited with code $LASTEXITCODE." }
}
finally { Pop-Location }
