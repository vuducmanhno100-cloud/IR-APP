# Build prerequisites of the Windows installer: required versions and how each one is detected.
# Dot-sourced by build.ps1 and install-prereqs.ps1. Keep this file ASCII: Windows PowerShell 5.1 reads scripts
# without a BOM in the ANSI code page.

$PackagingDir = $PSScriptRoot
$PythonVersion = "3.13"
# @stoplight/prism-cli in the desktop devDependencies refuses older Node versions.
$MinNode = [version]"24.18.0"
$PostgresMajor = 18
$ExtractedPostgres = Join-Path $PackagingDir ".tools\pgsql-$PostgresMajor"

function Update-SessionPath {
    # Installers write PATH to the registry; a shell opened before them keeps the old value.
    $parts = @($env:Path -split ";")
    foreach ($scope in @("Machine", "User")) {
        $value = [Environment]::GetEnvironmentVariable("Path", $scope)
        if ($value) { $parts += ($value -split ";") }
    }
    if ($env:USERPROFILE) {
        # rustup puts cargo here and only the next login picks it up.
        $cargo = Join-Path $env:USERPROFILE ".cargo\bin"
        if (Test-Path $cargo) { $parts = @($cargo) + $parts }
    }
    $env:Path = (($parts | Where-Object { $_ } | Select-Object -Unique) -join ";")
}

function Invoke-Probe([string]$command, [string[]]$arguments) {
    # Output of a version probe, or $null when the tool is missing or fails.
    if (-not (Get-Command $command -ErrorAction SilentlyContinue)) { return $null }
    $ErrorActionPreference = "Continue"
    try { $output = & $command @arguments 2>$null } catch { return $null }
    if ($LASTEXITCODE -ne 0) { return $null }
    return (($output | Out-String).Trim())
}

function Get-VersionIn([string]$text) {
    if ($text -and $text -match '(\d+)\.(\d+)(\.\d+)?') { return [version]$Matches[0] }
    return $null
}

function New-Check([string]$name, [string]$component, [string]$required, [string]$found, [bool]$ok, [string]$path) {
    if (-not $found) { $found = "not found" }
    [pscustomobject]@{ Name = $name; Component = $component; Required = $required; Found = $found; Ok = $ok; Path = $path }
}

function Test-Git {
    $found = Invoke-Probe "git" @("--version")
    New-Check "git" "Git" "any" $found ([bool]$found) ""
}

function Find-CrtDir([string]$vsPath) {
    # Microsoft.VC14x.CRT holds the Visual C++ runtime DLLs that may be redistributed next to an app.
    $redist = Join-Path $vsPath "VC\Redist\MSVC"
    if (-not (Test-Path $redist)) { return $null }
    # Newest numbered folder (VCToolsRedistDir) first; newer installs may also keep a v14x folder.
    $versions = @(Get-ChildItem $redist -Directory | Where-Object { $_.Name -match '^\d+\.\d+\.\d+$' } |
        Sort-Object { [version]$_.Name } -Descending)
    $versions += @(Get-ChildItem $redist -Directory -Filter "v14*" | Sort-Object Name -Descending)
    foreach ($version in $versions) {
        $crt = Get-ChildItem (Join-Path $version.FullName "x64") -Directory -Filter "Microsoft.VC*.CRT" -ErrorAction SilentlyContinue |
            Select-Object -First 1
        if ($crt) { return $crt.FullName }
    }
    return $null
}

function Test-Msvc {
    $required = "Build Tools: C++ x64, Windows SDK, VC++ redist"
    $programFiles86 = ${env:ProgramFiles(x86)}
    if (-not $programFiles86) { return New-Check "msvc" "Visual Studio C++" $required "" $false "" }
    $vswhere = Join-Path $programFiles86 "Microsoft Visual Studio\Installer\vswhere.exe"
    $vsPath = $null
    if (Test-Path $vswhere) {
        $vsPath = & $vswhere -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath |
            Select-Object -First 1
    }
    $sdk = Get-ChildItem (Join-Path $programFiles86 "Windows Kits\10\Lib\*\um\x64\kernel32.lib") -ErrorAction SilentlyContinue |
        Select-Object -First 1
    $crt = $null
    if ($vsPath) { $crt = Find-CrtDir $vsPath }
    $found = @()
    if ($vsPath) { $found += "C++ tools" } else { $found += "no C++ tools" }
    if ($sdk) { $found += "SDK" } else { $found += "no SDK" }
    if ($crt) { $found += "redist " + (Split-Path (Split-Path (Split-Path $crt -Parent) -Parent) -Leaf) } else { $found += "no redist" }
    New-Check "msvc" "Visual Studio C++" $required ($found -join ", ") ([bool]($vsPath -and $sdk -and $crt)) $crt
}

function Get-MinRust {
    $cargoToml = Join-Path $PackagingDir "..\..\apps\desktop\src-tauri\Cargo.toml"
    $match = Select-String -Path $cargoToml -Pattern '^rust-version\s*=\s*"([^"]+)"' -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($match) { return [version]$match.Matches[0].Groups[1].Value }
    return [version]"1.77.2"
}

function Test-Rust {
    $minRust = Get-MinRust
    $rustc = Invoke-Probe "rustc" @("-vV")
    $cargo = Invoke-Probe "cargo" @("-V")
    $version = Get-VersionIn $rustc
    $triple = ""
    if ($rustc -and $rustc -match 'host:\s*(\S+)') { $triple = $Matches[1] }
    $found = ""
    if ($version) { $found = "rustc $version $triple" }
    if ($version -and -not $cargo) { $found += ", no cargo" }
    $ok = [bool]($version -and $cargo -and $version -ge $minRust -and $triple -like "*-pc-windows-msvc")
    New-Check "rust" "Rust (MSVC)" ">= $minRust, *-pc-windows-msvc" $found $ok ""
}

function Test-Python {
    $probe = "import sys, struct; print(sys.version.split()[0], struct.calcsize('P') * 8, sys.executable)"
    $output = Invoke-Probe "py" @("-$PythonVersion", "-c", $probe)
    if (-not $output) { $output = Invoke-Probe "python" @("-c", $probe) }
    $version, $bits, $exe = "", "", ""
    if ($output) { $version, $bits, $exe = $output -split " ", 3 }
    $found = ""
    if ($version) { $found = "$version ($bits-bit)" }
    $ok = [bool]($version -and $version.StartsWith("$PythonVersion.") -and $bits -eq "64")
    New-Check "python" "Python" "$PythonVersion x64" $found $ok $exe
}

function Test-Node {
    $found = Invoke-Probe "node" @("-v")
    $version = Get-VersionIn $found
    $npm = Get-Command npm -ErrorAction SilentlyContinue
    if ($version -and -not $npm) { $found += ", no npm" }
    New-Check "node" "Node.js" ">= $MinNode" $found ([bool]($version -and $npm -and $version -ge $MinNode)) ""
}

function Find-PostgresBin([string]$explicitBin) {
    if ($explicitBin) { return $explicitBin }
    $candidates = @(Join-Path $ExtractedPostgres "bin")
    $candidates += @(Get-ChildItem $ExtractedPostgres -Directory -ErrorAction SilentlyContinue | ForEach-Object { Join-Path $_.FullName "bin" })
    if ($env:ProgramFiles) { $candidates += (Join-Path $env:ProgramFiles "PostgreSQL\$PostgresMajor\bin") }
    foreach ($candidate in $candidates) {
        if (Test-Path (Join-Path $candidate "pg_ctl.exe")) { return (Resolve-Path $candidate).Path }
    }
    return $null
}

function Test-PostgreSql([string]$explicitBin) {
    $required = "$PostgresMajor.x x64 (bin, lib, share)"
    $bin = Find-PostgresBin $explicitBin
    $pgCtl = $null
    if ($bin) { $pgCtl = Join-Path $bin "pg_ctl.exe" }
    if (-not $pgCtl -or -not (Test-Path $pgCtl)) {
        $found = ""
        if ($explicitBin) { $found = "no pg_ctl.exe in $explicitBin" }
        return New-Check "postgresql" "PostgreSQL" $required $found $false ""
    }
    # The version resource needs no VC++ runtime; running pg_ctl is the fallback.
    $version = Get-VersionIn ((Get-Item $pgCtl).VersionInfo.ProductVersion)
    if (-not $version) { $version = Get-VersionIn (Invoke-Probe $pgCtl @("--version")) }
    $root = Split-Path $bin -Parent
    $complete = (Test-Path (Join-Path $root "lib")) -and (Test-Path (Join-Path $root "share\extension"))
    $found = "$version in $bin"
    if (-not $complete) { $found += " (lib or share missing)" }
    $ok = [bool]($version -and $version.Major -eq $PostgresMajor -and $complete)
    New-Check "postgresql" "PostgreSQL" $required $found $ok $bin
}

function Get-Prerequisites([string]$PostgreSqlBin) {
    # Install order matters: Rust needs the C++ build tools first.
    @(
        (Test-Git)
        (Test-Msvc)
        (Test-Rust)
        (Test-Python)
        (Test-Node)
        (Test-PostgreSql $PostgreSqlBin)
    )
}

function Show-Prerequisites($checks) {
    # Rendered to a fixed width: Git Bash and redirected output report no usable console width.
    $table = $checks | Format-Table -AutoSize -Wrap Component, Required, Found,
        @{ Label = "Status"; Expression = { if ($_.Ok) { "ok" } else { "MISSING" } } } | Out-String -Width 200
    Write-Host $table.TrimEnd()
    Write-Host ""
}
