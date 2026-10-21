@echo off
setlocal
cd /d "%~dp0"

where py >nul 2>nul
if not errorlevel 1 (
  set "PYCMD=py"
) else (
  where python >nul 2>nul
  if errorlevel 1 (
    echo ERROR: Python not found.
    pause
    exit /b 1
  )
  set "PYCMD=python"
)

if exist ".venv" rmdir /s /q ".venv"
if exist "build" rmdir /s /q "build"
if exist "dist" rmdir /s /q "dist"
if exist "SPAIK-DNS-SWITCHER.spec" del /q "SPAIK-DNS-SWITCHER.spec"

%PYCMD% -m venv .venv
if errorlevel 1 goto FAIL
call ".venv\Scripts\activate.bat"
python -m pip install --upgrade pip
python -m pip install -r requirements.txt PyInstaller
if errorlevel 1 goto FAIL
python -m PyInstaller --noconfirm --clean --onefile --windowed --uac-admin --name "SPAIK-DNS-SWITCHER" main.py
if errorlevel 1 goto FAIL

start "" "%cd%\dist"
echo SUCCESS: dist\SPAIK-DNS-SWITCHER.exe
pause
exit /b 0

:FAIL
echo BUILD FAILED
pause
exit /b 1
