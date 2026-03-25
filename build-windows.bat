@echo off
REM TagWig Windows build script
REM Run this on a Windows machine with Python 3.11+ installed

echo Installing dependencies...
python -m pip install -r requirements.txt
python -m pip install pyinstaller

echo Building TagWig...
python -m PyInstaller TagWig-windows.spec --noconfirm

echo Done. Distributable is in dist\TagWig\
echo Zip the dist\TagWig folder and share it.
pause
