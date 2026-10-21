@echo off
setlocal
chcp 65001 >nul
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
if exist "%~dp0whack-a-claude.exe" goto native
py -3 -c "import sys;sys.exit(0 if sys.version_info >= (3,10) else 1)" >nul 2>&1
if not errorlevel 1 (
  set "PY_CMD=py -3"
  goto source
)
python -c "import sys;sys.exit(0 if sys.version_info >= (3,10) else 1)" >nul 2>&1
if not errorlevel 1 (
  set "PY_CMD=python"
  goto source
)
python3 -c "import sys;sys.exit(0 if sys.version_info >= (3,10) else 1)" >nul 2>&1
if not errorlevel 1 (
  set "PY_CMD=python3"
  goto source
)
echo Python 3.10+ was not found: https://www.python.org/downloads/
echo Or download the standalone ZIP from GitHub Releases.
pause
exit /b 1
:native
"%~dp0whack-a-claude.exe" %*
goto done
:source
%PY_CMD% "%~dp0whack_a_claude.py" %*
:done
set "launch_result=%errorlevel%"
if not "%launch_result%"=="0" pause
exit /b %launch_result%
