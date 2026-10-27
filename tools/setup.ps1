# CinemaViewer - installs the bundled parts from .\vendor into .\runtime.
# Works offline on a clean 64-bit Windows 10/11 PC: no Python, VLC or internet needed.
# Started automatically by run.bat when runtime\ready.txt is missing.

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$Root    = Split-Path -Parent $PSScriptRoot
$Vendor  = Join-Path $Root 'vendor'
$Runtime = Join-Path $Root 'runtime'
$Py      = Join-Path $Runtime 'python'
$Site    = Join-Path $Py 'Lib\site-packages'
$VlcDir  = Join-Path $Runtime 'vlc'
$Ready   = Join-Path $Runtime 'ready.txt'

Add-Type -AssemblyName System.IO.Compression.FileSystem

function Step($text) { Write-Host "  - $text" }

function Find-Vendor($pattern) {
    $file = Get-ChildItem -Path $Vendor -Filter $pattern -File -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $file) { throw "Onderdeel ontbreekt in de map vendor: $pattern" }
    return $file.FullName
}

# Parts of the Qt wheel CinemaViewer never uses (QML, C++ headers, build objects, developer tools).
# Skipping them keeps the install small and avoids paths longer than Windows' 260-character limit.
$SkipPattern = '^PySide6/(qml|include|typesystems|glue|scripts|doc)/|\.obj$|^PySide6/[^/]+\.exe$'

function Expand-Zip($zip, $dest) {
    # .NET instead of Expand-Archive: also handles .whl files and is much faster
    New-Item -ItemType Directory -Force -Path $dest | Out-Null
    $archive = [System.IO.Compression.ZipFile]::OpenRead($zip)
    try {
        foreach ($entry in $archive.Entries) {
            if ($entry.FullName -match $SkipPattern) { continue }
            $target = [System.IO.Path]::GetFullPath((Join-Path $dest $entry.FullName))
            if (-not $target.StartsWith([System.IO.Path]::GetFullPath($dest))) { throw "Ongeldig pad in $zip" }
            if ($entry.FullName.EndsWith('/')) { New-Item -ItemType Directory -Force -Path $target | Out-Null; continue }
            New-Item -ItemType Directory -Force -Path (Split-Path -Parent $target) | Out-Null
            [System.IO.Compression.ZipFileExtensions]::ExtractToFile($entry, $target, $true)
        }
    } finally { $archive.Dispose() }
}

Write-Host ""
Write-Host "CinemaViewer installeren in $Runtime"

if (-not [Environment]::Is64BitOperatingSystem) { throw "CinemaViewer vereist 64-bit Windows." }
if ($Root.Length -gt 120) {
    Write-Host "  ! Let op: de map staat erg diep ($($Root.Length) tekens). Zet CinemaViewer bij voorkeur op een"
    Write-Host "    korter pad, bijvoorbeeld C:\CinemaViewer, om problemen met lange paden te voorkomen."
}

# Files copied from a download can carry the 'blocked' mark; clear it for our own files.
Get-ChildItem -Path $Vendor, (Join-Path $Root 'tools') -Recurse -File | Unblock-File -ErrorAction SilentlyContinue

if (Test-Path $Runtime) { Remove-Item -Recurse -Force $Runtime }   # start clean after a failed attempt

# 1. Python (embeddable distribution)
$pyZip = Find-Vendor 'python-*-embed-amd64.zip'
Step ("Python: " + (Split-Path -Leaf $pyZip))
Expand-Zip $pyZip $Py
$pth = Get-ChildItem -Path $Py -Filter 'python*._pth' | Select-Object -First 1
$lines = @(Get-Content $pth.FullName | Where-Object { $_ -and -not $_.StartsWith('#') })
$lines += 'Lib\site-packages'
$lines += '..\..'            # the CinemaViewer folder itself
$lines += 'import site'
[System.IO.File]::WriteAllLines($pth.FullName, $lines, (New-Object System.Text.ASCIIEncoding))

# 2. Python packages (PySide6, python-vlc) from the bundled wheels
foreach ($wheel in Get-ChildItem -Path (Join-Path $Vendor 'wheels') -Filter '*.whl') {
    Step ("Pakket: " + $wheel.Name)
    Expand-Zip $wheel.FullName $Site
}

# 3. VLC (libVLC + plugins for files, streams, DVD and Blu-ray)
$vlcZip = Find-Vendor 'vlc-*-win64.zip'
Step ("VLC: " + (Split-Path -Leaf $vlcZip))
Expand-Zip $vlcZip $VlcDir

# 4. Check that everything loads
Step "Controleren"
$check = & (Join-Path $Py 'python.exe') -c "import cinemaviewer.player as p, vlc, PySide6; print('PySide6', PySide6.__version__, '| VLC', vlc.libvlc_get_version().decode())" 2>&1
if ($LASTEXITCODE -ne 0) { throw "Controle mislukt: $check" }
Write-Host "    $check"

Set-Content -Path $Ready -Value ("Geinstalleerd op " + (Get-Date -Format 'yyyy-MM-dd HH:mm') + " | " + $check) -Encoding ASCII
Write-Host "Klaar."
exit 0
