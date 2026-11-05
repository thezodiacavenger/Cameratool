#!/bin/sh
cd "$(dirname "$0")"
if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3 is required. Install from https://www.python.org/downloads/ (or: xcode-select --install)"
  open https://www.python.org/downloads/
  read -r _; exit 1
fi
if [ ! -x tools/exiftool/exiftool ] && ! command -v exiftool >/dev/null 2>&1; then
  echo "ExifTool not found - downloading it once from exiftool.org ..."
  sh tools/install_exiftool_mac_linux.sh
fi
exec python3 CameraCheck.py
