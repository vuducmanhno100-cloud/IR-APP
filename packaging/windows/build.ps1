[CmdletBinding()]
param(
    # Folder with pg_ctl.exe of PostgreSQL 18 x64; its lib and share folders are taken from the same install.
    [Parameter(Mandatory = $false)]
    [string]$PostgreSqlBin,
    # Embed the full WebView2 runtime (about 130 MB) for lab machines without internet access.
    [switch]$OfflineWebView2,
    # Build from a working tree with uncommitted changes (local tests only, never for a release).
    [switch]$AllowDirty
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$engineDir = Join-Path $repoRoot "engine"
$desktopDir = Join-Path $repoRoot "apps\desktop"
$distEngine = Join-Path $PSScriptRoot "dist\engine"
$distPgsql = Join-Path $PSScriptRoot "dist\pgsql"
$work = Join-Path $PSScriptRoot "build"
$venv = Join-Path $PSScriptRoot ".build-venv"

function Clear-Dist([string]$folder) {
    # Keep the README placeholder; anything left from an older build would end up in the installer.
    Get-ChildItem $folder -Force | Where-Object { $_.Name -ne "README.txt" } | Remove-Item -Recurse -Force
}

function Invoke-Checked([string]$what, [scriptblock]$command) {
    & $command
    if ($LASTEXITCODE -ne 0) { throw "$what failed (exit code $LASTEXITCODE)." }
}

# 1. Release from a known commit, with one version for app and engine.
$commit = (git -C $repoRoot rev-parse --short HEAD).Trim()
$changes = git -C $repoRoot status --porcelain --untracked-files=no
if ($changes -and -not $AllowDirty) {
    throw "Uncommitted changes in tracked files; commit them first (or pass -AllowDirty for a local test build).`n$changes"
}
$appVersion = (Get-Content (Join-Path $desktopDir "src-tauri\tauri.conf.json") -Raw | ConvertFrom-Json).version
$engineVersion = ((Select-String -Path (Join-Path $engineDir "pyproject.toml") -Pattern '^version\s*=\s*"([^"]+)"').Matches[0].Groups[1].Value)
if ($appVersion -ne $engineVersion) {
    throw "Version mismatch: tauri.conf.json has $appVersion, engine/pyproject.toml has $engineVersion."
}

# 2. Engine in its own Python 3.13 x64 environment, so nothing from the developer's Python leaks into the build.
if (-not (Test-Path (Join-Path $venv "Scripts\python.exe"))) {
    if (Get-Command py -ErrorAction SilentlyContinue) {
        Invoke-Checked "Creating the build venv" { py -3.13 -m venv $venv }
    } else {
        Invoke-Checked "Creating the build venv" { python -m venv $venv }
    }
}
$python = Join-Path $venv "Scripts\python.exe"
$pyInfo = (& $python -c "import sys, struct; print(sys.version_info[0], sys.version_info[1], struct.calcsize('P') * 8)").Trim()
if ($pyInfo -ne "3 13 64") {
    throw "The build venv must be Python 3.13 64-bit (found $pyInfo). Delete $venv and install Python 3.13 x64."
}
Invoke-Checked "Installing engine build dependencies" { & $python -m pip install --quiet -e "$engineDir[build]" }

Push-Location $engineDir
try {
    Invoke-Checked "Engine packaging" {
        & $python -m PyInstaller -n ir-engine --onedir --noconsole --noconfirm --clean `
            --distpath (Join-Path $work "dist") --workpath (Join-Path $work "work") --specpath $work `
            --collect-data ir_engine `
            --collect-submodules keyring.backends `
            --collect-submodules uvicorn `
            (Join-Path $engineDir "ir_engine\__main__.py")
    }
}
finally {
    Pop-Location
}
$builtEngine = Join-Path $work "dist\ir-engine"
if (-not (Test-Path (Join-Path $builtEngine "ir-engine.exe"))) {
    throw "PyInstaller output does not contain ir-engine.exe."
}
Clear-Dist $distEngine
Copy-Item (Join-Path $builtEngine "*") $distEngine -Recurse -Force

# 3. PostgreSQL binaries and their license.
if ($PostgreSqlBin) {
    if (-not (Test-Path (Join-Path $PostgreSqlBin "pg_ctl.exe"))) {
        throw "PostgreSqlBin must contain pg_ctl.exe."
    }
    $postgresRoot = Split-Path (Resolve-Path $PostgreSqlBin) -Parent
    Clear-Dist $distPgsql
    Copy-Item $PostgreSqlBin (Join-Path $distPgsql "bin") -Recurse -Force
    foreach ($folder in @("lib", "share")) {
        $source = Join-Path $postgresRoot $folder
        if (-not (Test-Path $source)) { throw "Missing $source next to the PostgreSQL bin folder." }
        Copy-Item $source (Join-Path $distPgsql $folder) -Recurse -Force
    }
}
if (-not (Test-Path (Join-Path $distEngine "ir-engine.exe"))) {
    throw "Install an engine build in packaging/windows/dist/engine before building the installer."
}
if (-not (Test-Path (Join-Path $distPgsql "bin\pg_ctl.exe"))) {
    throw "Provide PostgreSQL 18 binaries with -PostgreSqlBin."
}
# pg_trgm backs "contains" search; pg_freespacemap lets the quota count space freed by retention.
foreach ($extension in @("pg_trgm", "pg_freespacemap")) {
    if (-not (Test-Path (Join-Path $distPgsql "share\extension\$extension.control"))) {
        throw "PostgreSQL extension $extension is missing from share\extension."
    }
}
Copy-Item (Join-Path $PSScriptRoot "licenses\PostgreSQL-COPYRIGHT.txt") (Join-Path $distPgsql "COPYRIGHT.txt") -Force

# 4. Desktop app and NSIS installer, from the locked npm dependencies.
Push-Location $desktopDir
try {
    Invoke-Checked "npm ci" { npm ci --no-audit --no-fund }
    $tauriArgs = @("build")
    if ($OfflineWebView2) {
        $override = Join-Path $work "tauri.offline-webview.json"
        New-Item -ItemType Directory -Force $work | Out-Null
        '{"bundle":{"windows":{"webviewInstallMode":{"type":"offlineInstaller"}}}}' | Set-Content -Encoding ascii $override
        $tauriArgs += @("--config", $override)
    }
    # Call the CLI shim directly: npm's PowerShell shim may drop the "--" needed to forward options.
    $tauri = Join-Path $desktopDir "node_modules\.bin\tauri.cmd"
    Invoke-Checked "NSIS installer build" { & $tauri @tauriArgs }
}
finally {
    Pop-Location
}

# 5. Checksum to publish next to the installer.
$installer = Get-ChildItem (Join-Path $desktopDir "src-tauri\target\release\bundle\nsis\*-setup.exe") |
    Sort-Object LastWriteTime -Descending | Select-Object -First 1
if (-not $installer) { throw "No installer found in src-tauri\target\release\bundle\nsis." }
$hash = (Get-FileHash $installer.FullName -Algorithm SHA256).Hash.ToLower()
"$hash  $($installer.Name)" | Set-Content -Encoding ascii "$($installer.FullName).sha256"
Write-Host ""
Write-Host "Installer : $($installer.FullName)"
Write-Host "Version   : $appVersion (commit $commit)"
Write-Host "SHA-256   : $hash"
