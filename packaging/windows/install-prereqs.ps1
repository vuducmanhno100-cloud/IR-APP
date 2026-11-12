[CmdletBinding()]
param(
    # Names from the check table (git, msvc, rust, python, node, postgresql). Default: everything missing.
    [string[]]$Only,
    # Same as build.ps1 -PostgreSqlBin; when given, PostgreSQL is not downloaded.
    [string]$PostgreSqlBin
)

# Installs the build prerequisites that prereqs.ps1 reports missing or too old, with winget.
# Each installer may ask for administrator rights (UAC). Run it again after a reboot if an installer asks for one.

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "prereqs.ps1")

function Invoke-Winget([string[]]$arguments) {
    & winget @arguments
    # Non-zero also means "already installed, no upgrade" or "restart required"; the re-check below decides.
    if ($LASTEXITCODE -ne 0) { Write-Warning "winget $($arguments[0]) $($arguments[3]) exited with code $LASTEXITCODE" }
}

function Install-Package([string]$id, [string]$override) {
    $arguments = @("install", "--exact", "--id", $id, "--source", "winget", "--accept-package-agreements", "--accept-source-agreements")
    if ($override) { $arguments += @("--override", $override) } else { $arguments += "--silent" }
    Invoke-Winget $arguments
}

function Install-BuildTools {
    # The recommended set brings the Windows SDK and the VC++ redist folder the runtime is copied from.
    $components = "--add Microsoft.VisualStudio.Workload.VCTools --includeRecommended"
    $installer = Join-Path ${env:ProgramFiles(x86)} "Microsoft Visual Studio\Installer"
    $existing = $null
    if (Test-Path (Join-Path $installer "vswhere.exe")) {
        $existing = & (Join-Path $installer "vswhere.exe") -latest -products Microsoft.VisualStudio.Product.BuildTools -property installationPath |
            Select-Object -First 1
    }
    if ($existing) {
        # Build Tools without the C++ parts: add them to that installation instead of installing a second one.
        $arguments = "modify --installPath `"$existing`" $components --passive --wait --norestart"
        $process = Start-Process -FilePath (Join-Path $installer "setup.exe") -ArgumentList $arguments -Verb RunAs -Wait -PassThru
        if ($process.ExitCode -notin @(0, 3010)) { Write-Warning "Visual Studio Installer exited with code $($process.ExitCode)" }
    } else {
        Install-Package "Microsoft.VisualStudio.2022.BuildTools" "--wait --passive --norestart $components"
    }
}

function Install-Rust {
    if (-not (Get-Command rustup -ErrorAction SilentlyContinue)) {
        Install-Package "Rustlang.Rustup"
        Update-SessionPath
    }
    $rustup = Get-Command rustup -ErrorAction SilentlyContinue
    if (-not $rustup) { throw "rustup is not on PATH after installing it. Open a new PowerShell window and run the build again." }
    # Installs or updates the toolchain; Tauri builds need the MSVC one as default.
    & $rustup.Source toolchain install stable-x86_64-pc-windows-msvc --profile minimal
    if ($LASTEXITCODE -ne 0) { throw "rustup could not install the stable MSVC toolchain (exit code $LASTEXITCODE)." }
    & $rustup.Source default stable-x86_64-pc-windows-msvc
    if ($LASTEXITCODE -ne 0) { throw "rustup could not select the stable MSVC toolchain (exit code $LASTEXITCODE)." }
}

function Install-PostgreSqlCore {
    if ($PostgreSqlBin) {
        throw "-PostgreSqlBin $PostgreSqlBin has no PostgreSQL $PostgresMajor x64. Fix the path, or leave it out to download PostgreSQL."
    }
    # --extract-only unpacks the EnterpriseDB build into packaging\windows\.tools: no Windows service, no database,
    # no superuser password. build.ps1 later takes only the core from it.
    $downloads = Join-Path $PackagingDir ".tools\downloads"
    New-Item -ItemType Directory -Force $downloads | Out-Null
    Invoke-Winget @("download", "--exact", "--id", "PostgreSQL.PostgreSQL.$PostgresMajor", "--source", "winget",
        "--download-directory", $downloads, "--accept-package-agreements", "--accept-source-agreements")
    $setup = Get-ChildItem $downloads -Filter "*.exe" | Sort-Object LastWriteTime -Descending | Select-Object -First 1
    $manual = "Install it by hand: winget install -e --id PostgreSQL.PostgreSQL.$PostgresMajor --interactive (keep 'PostgreSQL Server' and 'Command Line Tools')."
    if (-not $setup) { throw "winget did not download the PostgreSQL $PostgresMajor installer. $manual" }
    $arguments = "--mode unattended --unattendedmodeui none --extract-only 1 --prefix `"$ExtractedPostgres`""
    $process = Start-Process -FilePath $setup.FullName -ArgumentList $arguments -Verb RunAs -Wait -PassThru
    if ($process.ExitCode -ne 0) { throw "Unpacking PostgreSQL failed (exit code $($process.ExitCode)). $manual" }
}

Update-SessionPath
$checks = Get-Prerequisites -PostgreSqlBin $PostgreSqlBin
$todo = @($checks | Where-Object { -not $_.Ok -and (-not $Only -or $Only -contains $_.Name) })
if ($todo.Count -eq 0) {
    Show-Prerequisites $checks
    Write-Host "All build prerequisites are installed."
    return
}
if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
    throw "winget is not available. Install 'App Installer' from the Microsoft Store, then run this again."
}
Write-Host "Installing: $(($todo | ForEach-Object Component) -join ', ')"
foreach ($item in $todo) {
    Write-Host ""
    Write-Host "==> $($item.Component): need $($item.Required), found $($item.Found)"
    switch ($item.Name) {
        "git" { Install-Package "Git.Git" }
        "msvc" { Install-BuildTools }
        "rust" { Install-Rust }
        "python" { Install-Package "Python.Python.$PythonVersion" }
        "node" { Install-Package "OpenJS.NodeJS.LTS" }
        "postgresql" { Install-PostgreSqlCore }
    }
    Update-SessionPath
}

$after = Get-Prerequisites -PostgreSqlBin $PostgreSqlBin
Show-Prerequisites $after
$still = @($after | Where-Object { -not $_.Ok -and (-not $Only -or $Only -contains $_.Name) })
if ($still.Count -gt 0) {
    throw ("Still missing: $(($still | ForEach-Object Component) -join ', '). Open a new PowerShell window (or restart " +
        "Windows if an installer asked for it) and run the build again; otherwise install it by hand, see packaging\windows\README.md.")
}
Write-Host "All build prerequisites are installed."
