@echo off
REM Build a standalone CameraCheck.exe locally (the GitHub workflow does the same automatically).
cd /d "%~dp0\.."
py -3 -m pip install --upgrade pyinstaller comtypes pillow
py -3 -c "import comtypes.client as c; c.GetModule('portabledeviceapi.dll'); c.GetModule('portabledevicetypes.dll')"
py -3 tools\make_build_files.py
py -3 -m PyInstaller --noconfirm --name CameraCheck --windowed --icon build\CameraCheck.ico --version-file build\version_info.txt ^
  --collect-submodules comtypes --add-data "camcheck\web;camcheck\web" --add-data "camcheck\data;camcheck\data" CameraCheck.py
xcopy /E /I /Y tools\exiftool dist\CameraCheck\tools\exiftool
echo Built dist\CameraCheck\CameraCheck.exe - copy the whole dist\CameraCheck folder to each PC.
pause
