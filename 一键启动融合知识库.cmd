@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set "PYTHON_UTF8=1"
set "PYTHONDONTWRITEBYTECODE=1"
if exist ".venv\Scripts\python.exe" goto local_python
python --version >nul 2>nul
if not errorlevel 1 goto path_python
py -3 --version >nul 2>nul
if not errorlevel 1 goto windows_launcher
echo Python is missing. Install Python and add it to PATH, then try again.
goto failed

:local_python
".venv\Scripts\python.exe" start_fusion.py %*
goto check

:path_python
python start_fusion.py %*
goto check

:windows_launcher
py -3 start_fusion.py %*

:check
if not errorlevel 1 exit /b 0

:failed
if /i "%~1" neq "--no-browser" pause
exit /b 1
