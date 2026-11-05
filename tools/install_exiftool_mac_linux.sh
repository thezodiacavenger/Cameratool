#!/bin/sh
# Downloads the official ExifTool Perl distribution (exiftool.org, by Phil Harvey)
# into tools/exiftool next to this script. macOS and Linux include Perl.
set -e
cd "$(dirname "$0")"
VER=$(curl -fsSL https://exiftool.org/ver.txt)
echo "Latest ExifTool: $VER"
curl -fL -o /tmp/exiftool.tgz "https://sourceforge.net/projects/exiftool/files/Image-ExifTool-$VER.tar.gz/download"
rm -rf exiftool /tmp/Image-ExifTool-$VER
tar -xzf /tmp/exiftool.tgz -C /tmp
mkdir -p exiftool
cp -R /tmp/Image-ExifTool-$VER/exiftool /tmp/Image-ExifTool-$VER/lib exiftool/
chmod +x exiftool/exiftool
perl exiftool/exiftool -ver
echo "ExifTool installed to $(pwd)/exiftool"
