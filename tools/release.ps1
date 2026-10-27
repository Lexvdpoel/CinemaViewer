# CinemaViewer - publishes a new version as a GitHub release (developer).
# 1. Raise __version__ in cinemaviewer\__init__.py and commit your changes.
# 2. Run:  powershell -ExecutionPolicy Bypass -File tools\release.ps1 [-Notes "wat er nieuw is"]
# The script tags the commit, pushes it and creates the release with the vendor folder attached as
# CinemaViewer-vendor-<versie>.zip. Installed CinemaViewers then offer the update (Instellingen -> Systeem).
# Needs the GitHub CLI (gh), logged in with 'gh auth login'.

param([string]$Notes = "")

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$init = Get-Content (Join-Path $Root 'cinemaviewer\__init__.py') -Raw
if ($init -notmatch '__version__\s*=\s*"([^"]+)"') { throw "Geen __version__ gevonden in cinemaviewer\__init__.py" }
$Version = $Matches[1]
$Tag = "v$Version"

if (git status --porcelain) { throw "Er zijn nog niet-gecommitte wijzigingen. Commit eerst." }
if (git tag --list $Tag) { throw "Versie $Version bestaat al (tag $Tag). Verhoog __version__ eerst." }

if (-not $Notes) {
    $previous = git describe --tags --abbrev=0 2>$null
    $range = if ($previous) { "$previous..HEAD" } else { "HEAD" }
    $Notes = (git log $range --pretty=format:'- %s') -join "`n"
}

$zip = Join-Path ([System.IO.Path]::GetTempPath()) "CinemaViewer-vendor-$Version.zip"
if (Test-Path $zip) { Remove-Item $zip }
Write-Host "Vendor-map inpakken: $zip"
Compress-Archive -Path (Join-Path $Root 'vendor') -DestinationPath $zip -CompressionLevel Optimal

git tag -a $Tag -m "CinemaViewer $Version"
git push origin HEAD
git push origin $Tag
gh release create $Tag $zip --title "CinemaViewer $Version" --notes $Notes
Remove-Item $zip
Write-Host "Release $Tag gepubliceerd."
