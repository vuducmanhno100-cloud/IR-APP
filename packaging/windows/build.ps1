[CmdletBinding()]
param(
    # bin folder of a PostgreSQL 18 x64 install (with pg_ctl.exe). Default: the copy install-prereqs.ps1 unpacks into
    # packaging\windows\.tools, then C:\Program Files\PostgreSQL\18\bin. Only its core goes into the installer.
    [Parameter(Mandatory = $false)]
    [string]$PostgreSqlBin,
    # Embed the full WebView2 runtime (about 130 MB) for lab machines without internet access.
    [switch]$OfflineWebView2,
    # Build from a working tree with uncommitted changes (local tests only, never for a release).
    [switch]$AllowDirty,
    # Stop when a build prerequisite is missing or too old instead of running install-prereqs.ps1.
    [switch]$NoInstall
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$engineDir = Join-Path $repoRoot "engine"
$desktopDir = Join-Path $repoRoot "apps\desktop"
$distEngine = Join-Path $PSScriptRoot "dist\engine"
$distPgsql = Join-Path $PSScriptRoot "dist\pgsql"
$work = Join-Path $PSScriptRoot "build"
$venv = Join-Path $PSScriptRoot ".build-venv"
. (Join-Path $PSScriptRoot "prereqs.ps1")

function Clear-Dist([string]$folder) {
    # Keep the README placeholder; anything left from an older build would end up in the installer.
    Get-ChildItem $folder -Force | Where-Object { $_.Name -ne "README.txt" } | Remove-Item -Recurse -Force
}

function Invoke-Checked([string]$what, [scriptblock]$command) {
    & $command
    if ($LASTEXITCODE -ne 0) { throw "$what failed (exit code $LASTEXITCODE)." }
}

# 0. Build prerequisites and their versions, before anything slow runs.
Update-SessionPath
$checks = Get-Prerequisites -PostgreSqlBin $PostgreSqlBin
Show-Prerequisites $checks
$missing = @($checks | Where-Object { -not $_.Ok } | ForEach-Object Name)
if ($missing.Count -gt 0) {
    if ($NoInstall) {
        throw "Missing or outdated build prerequisites: $($missing -join ', '). Run packaging\windows\install-prereqs.ps1, or build without -NoInstall."
    }
    & (Join-Path $PSScriptRoot "install-prereqs.ps1") -Only $missing -PostgreSqlBin $PostgreSqlBin
    $checks = Get-Prerequisites -PostgreSqlBin $PostgreSqlBin
}
$found = @{}
foreach ($check in $checks) { $found[$check.Name] = $check.Path }

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
    Invoke-Checked "Creating the build venv" { & $found["python"] -m venv $venv }
}
$python = Join-Path $venv "Scripts\python.exe"
$pyInfo = (& $python -c "import sys, struct; print(sys.version_info[0], sys.version_info[1], struct.calcsize('P') * 8)").Trim()
if ($pyInfo -ne "3 13 64") {
    throw "The build venv must be Python 3.13 64-bit (found $pyInfo). Delete $venv and run the build again."
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

# 3. PostgreSQL core: the programs the engine runs, the modules it loads, the DLLs they import and the Visual C++
#    runtime (a clean Windows has none). Add-ons of the source install (PostGIS, pgAdmin...) stay out.
Clear-Dist $distPgsql
$bundler = Join-Path $PSScriptRoot "bundle_pgsql.py"
Invoke-Checked "Collecting the PostgreSQL core" {
    & $python $bundler collect --source (Split-Path $found["postgresql"] -Parent) --dest $distPgsql --crt-dir $found["msvc"]
}
Copy-Item (Join-Path $PSScriptRoot "licenses\PostgreSQL-COPYRIGHT.txt") (Join-Path $distPgsql "COPYRIGHT.txt") -Force

# 4. Prove the bundle works on its own: no import a clean Windows cannot satisfy, then the engine starts the bundled
#    PostgreSQL with PATH reduced to Windows itself, migrates, answers /system/health and shuts down.
Invoke-Checked "DLL check" { & $python $bundler check $distEngine $distPgsql }
Invoke-Checked "Engine smoke test" { & $python (Join-Path $PSScriptRoot "smoke_engine.py") (Join-Path $distEngine "ir-engine.exe") }

# 5. Desktop app and NSIS installer, from the locked npm dependencies.
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

# 6. Checksum to publish next to the installer.
$installer = Get-ChildItem (Join-Path $desktopDir "src-tauri\target\release\bundle\nsis\*-setup.exe") |
    Sort-Object LastWriteTime -Descending | Select-Object -First 1
if (-not $installer) { throw "No installer found in src-tauri\target\release\bundle\nsis." }
$hash = (Get-FileHash $installer.FullName -Algorithm SHA256).Hash.ToLower()
"$hash  $($installer.Name)" | Set-Content -Encoding ascii "$($installer.FullName).sha256"
Write-Host ""
Write-Host "Installer : $($installer.FullName) ($([math]::Round($installer.Length / 1MB)) MB)"
Write-Host "Version   : $appVersion (commit $commit)"
Write-Host "SHA-256   : $hash"
