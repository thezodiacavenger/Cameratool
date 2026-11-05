@echo off
title Camera Check
cd /d "%~dp0"
set PY=
where py >nul 2>nul && set PY=py -3
if not defined PY where python >nul 2>nul && set PY=python
if not defined PY (
  echo Python 3 is required. Install it from https://www.python.org/downloads/
  echo  ^(tick "Add python.exe to PATH" during setup^), then run this again.
  start https://www.python.org/downloads/
  pause
  exit /b 1
)
if not exist "tools\exiftool\exiftool.exe" (
  where exiftool >nul 2>nul || (
    echo ExifTool not found - downloading it once from exiftool.org ...
    powershell -NoProfile -ExecutionPolicy Bypass -File "tools\install_exiftool_windows.ps1"
  )
)
%PY% -c "import comtypes" >nul 2>nul || (
  echo Installing USB camera support ^(comtypes^) ...
  %PY% -m pip install --user --disable-pip-version-check comtypes
)
%PY% CameraCheck.py
if errorlevel 1 pause
