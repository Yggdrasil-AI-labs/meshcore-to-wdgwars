@echo off
REM Drag a MeshMapper CSV onto this file, or run it from a terminal.
REM Usage: run.bat path\to\your_export.csv
if "%~1"=="" (
  echo Usage: %~nx0 path\to\your_export.csv
  python "%~dp0heimdall.py" --help
) else (
  python "%~dp0heimdall.py" %*
)
echo.
pause
