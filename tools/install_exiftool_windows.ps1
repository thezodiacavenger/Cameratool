# Downloads the official Windows build of ExifTool (exiftool.org, by Phil Harvey)
# into tools\exiftool next to this script. Safe to re-run to update.
$ErrorActionPreference = "Stop"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$dest = Join-Path $PSScriptRoot "exiftool"
$ver  = (Invoke-WebRequest -UseBasicParsing "https://exiftool.org/ver.txt").Content.Trim()
Write-Host "Latest ExifTool: $ver"
$arch = if ([Environment]::Is64BitOperatingSystem) { "64" } else { "32" }
$url  = "https://sourceforge.net/projects/exiftool/files/exiftool-${ver}_${arch}.zip/download"
$zip  = Join-Path $env:TEMP "exiftool-$ver.zip"
$tmp  = Join-Path $env:TEMP "exiftool-$ver"
Write-Host "Downloading $url"
Invoke-WebRequest -UseBasicParsing -UserAgent "Wget" $url -OutFile $zip
if (Test-Path $tmp) { Remove-Item $tmp -Recurse -Force }
Expand-Archive $zip $tmp -Force
$inner = Get-ChildItem $tmp -Directory | Select-Object -First 1
if (Test-Path $dest) { Remove-Item $dest -Recurse -Force }
New-Item -ItemType Directory $dest | Out-Null
Copy-Item (Join-Path $inner.FullName "*") $dest -Recurse -Force
Rename-Item (Join-Path $dest "exiftool(-k).exe") "exiftool.exe"
& (Join-Path $dest "exiftool.exe") -ver
Write-Host "ExifTool installed to $dest"
