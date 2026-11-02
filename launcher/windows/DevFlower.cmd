@echo off
setlocal
set "HERE=%~dp0"
for %%I in ("%HERE%..\..") do set "REPO=%%~fI"
where python >nul 2>nul
if errorlevel 1 (
  echo Python 3 was not found in PATH. Install Python 3 and try again. 1>&2
  exit /b 1
)
python -B "%REPO%\tools\devflower_launch.py" %*
exit /b %errorlevel%
