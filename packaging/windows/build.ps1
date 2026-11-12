[CmdletBinding()]
param(
    [Parameter(Mandatory = $false)]
    [string]$PostgreSqlBin
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$engineDir = Join-Path $repoRoot "engine"
$desktopDir = Join-Path $repoRoot "apps\desktop"
$distEngine = Join-Path $PSScriptRoot "dist\engine"
$distPgsql = Join-Path $PSScriptRoot "dist\pgsql"

Push-Location $engineDir
try {
    python -m pip install -e ".[build]"
    if ($LASTEXITCODE -ne 0) { throw "Cannot install engine build dependencies." }

    python -m PyInstaller -n ir-engine --onedir --noconsole `
        --collect-data ir_engine `
        --collect-submodules keyring.backends `
        --collect-submodules uvicorn `
        ir_engine\__main__.py
    if ($LASTEXITCODE -ne 0) { throw "Engine packaging failed." }

    $builtEngine = Join-Path $engineDir "dist\ir-engine"
    if (-not (Test-Path (Join-Path $builtEngine "ir-engine.exe"))) {
        throw "PyInstaller output does not contain ir-engine.exe."
    }
    Copy-Item (Join-Path $builtEngine "*") $distEngine -Recurse -Force
}
finally {
    Pop-Location
}

if ($PostgreSqlBin) {
    $postgresRoot = Split-Path (Resolve-Path $PostgreSqlBin) -Parent
    if (-not (Test-Path (Join-Path $PostgreSqlBin "pg_ctl.exe"))) {
        throw "PostgreSqlBin must contain pg_ctl.exe."
    }
    if (Test-Path (Join-Path $distPgsql "bin")) {
        Remove-Item (Join-Path $distPgsql "bin") -Recurse -Force
    }
    Copy-Item $PostgreSqlBin (Join-Path $distPgsql "bin") -Recurse -Force

    foreach ($folder in @("lib", "share")) {
        $source = Join-Path $postgresRoot $folder
        if (Test-Path $source) {
            Copy-Item $source (Join-Path $distPgsql $folder) -Recurse -Force
        }
    }
}

if (-not (Test-Path (Join-Path $distEngine "ir-engine.exe"))) {
    throw "Install an engine build in packaging/windows/dist/engine before building the installer."
}
if (-not (Test-Path (Join-Path $distPgsql "bin\pg_ctl.exe"))) {
    throw "Provide PostgreSQL 18 binaries with -PostgreSqlBin."
}

Push-Location $desktopDir
try {
    npm run tauri:build
    if ($LASTEXITCODE -ne 0) { throw "NSIS installer build failed." }
}
finally {
    Pop-Location
}
