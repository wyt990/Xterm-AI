param(
    [switch]$NoBrowser
)

$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $scriptDir

function Read-EnvValue {
    param(
        [string]$FilePath,
        [string]$Key,
        [string]$DefaultValue
    )

    if (-not (Test-Path $FilePath)) {
        return $DefaultValue
    }

    $line = Get-Content -Path $FilePath | Where-Object { $_ -match "^\s*$Key\s*=" } | Select-Object -First 1
    if ([string]::IsNullOrWhiteSpace($line)) {
        return $DefaultValue
    }

    $value = ($line -replace "^\s*$Key\s*=\s*", "").Trim()
    if (($value.StartsWith('"') -and $value.EndsWith('"')) -or ($value.StartsWith("'") -and $value.EndsWith("'"))) {
        $value = $value.Substring(1, $value.Length - 2)
    }
    return $value
}

$envFile = Join-Path $scriptDir ".env"
$envExampleFile = Join-Path $scriptDir ".env.example"

if (-not (Test-Path $envFile)) {
    Write-Host ".env not found, copying from .env.example..."
    Copy-Item -Path $envExampleFile -Destination $envFile -Force
    Write-Host "Please edit .env before first run."
    exit 1
}

$serverPort = Read-EnvValue -FilePath $envFile -Key "SERVER_PORT" -DefaultValue "9000"
$dbPathRaw = Read-EnvValue -FilePath $envFile -Key "DB_PATH" -DefaultValue "../config/xterm.db"

$backendDir = Join-Path $scriptDir "backend"
$dbPath = [System.IO.Path]::GetFullPath((Join-Path $backendDir $dbPathRaw))
$dbTemplate = "$dbPath.example"
$dbDir = Split-Path -Parent $dbPath

if (-not (Test-Path $dbPath)) {
    Write-Host "Database not found at $dbPath, trying template..."
    if (Test-Path $dbTemplate) {
        New-Item -ItemType Directory -Path $dbDir -Force | Out-Null
        Copy-Item -Path $dbTemplate -Destination $dbPath -Force
        Write-Host "Database initialized from template: $dbPath"
    }
    else {
        Write-Host "Warning: template not found at $dbTemplate. Backend may create empty DB."
    }
}

Write-Host "Ensuring database directory exists: $dbPath"
New-Item -ItemType Directory -Path $dbDir -Force | Out-Null

if (-not $NoBrowser) {
    $frontendUrl = "http://127.0.0.1:$($serverPort)/frontend/index.html"
    Write-Host "Opening frontend page: $frontendUrl"
    Start-Process $frontendUrl | Out-Null
}

Write-Host "Starting backend service on port $serverPort..."
Set-Location $backendDir

$venvPython = Join-Path $scriptDir ".venv\Scripts\python.exe"
if (Test-Path $venvPython) {
    & $venvPython "main.py"
}
else {
    & python "main.py"
}
