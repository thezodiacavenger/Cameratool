@echo off
REM Optional: build a standalone CameraCheck.exe (no Python needed on shop PCs).
REM Run on a Windows machine with Python installed. Output: dist\CameraCheck\
cd /d "%~dp0\.."
py -3 -m pip install --upgrade pyinstaller comtypes
py -3 -m PyInstaller --noconfirm --name CameraCheck --icon NONE --collect-submodules comtypes ^
  --add-data "camcheck\web;camcheck\web" --add-data "camcheck\data;camcheck\data" CameraCheck.py
xcopy /E /I /Y tools\exiftool dist\CameraCheck\tools\exiftool
echo Built dist\CameraCheck\CameraCheck.exe - copy the whole dist\CameraCheck folder to each PC.
pause
