@echo off
REM Double-click this once to install dependencies and save your WDGoWars API key.
REM Heimdall has no third-party deps today, but we refresh requirements.txt
REM from GitHub anyway so the bootstrap stays consistent if a dep is added
REM in a future release.

python -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)" 2>nul
if errorlevel 1 (
    echo Heimdall requires Python 3.10 or newer. Your current Python is:
    python --version 2>nul || echo   ^(not found on PATH^)
    echo.
    echo Install Python 3.10+ from https://python.org/downloads/ and re-run.
    goto :done
)

echo [1/3] Refreshing requirements.txt from GitHub...
python -c "import urllib.request as u; u.urlretrieve('https://raw.githubusercontent.com/HiroAlleyCat/meshcore-to-wdgwars/main/requirements.txt', r'%~dp0requirements.txt')"
if errorlevel 1 (
    echo.
    echo Could not fetch requirements.txt. Check internet connection and
    echo that Python is installed and on PATH.
    goto :done
)

echo.
echo [2/3] Installing dependencies...
python -m pip install --upgrade -r "%~dp0requirements.txt"
if errorlevel 1 (
    echo.
    echo pip install failed. See messages above. Common fixes:
    echo   - upgrade Python to 3.10 or newer ^(check with: python --version^)
    echo   - run as administrator if pip needs elevated perms
    echo   - check that your firewall allows HTTPS to github.com
    goto :done
)

echo.
echo [3/3] Saving your WDGoWars API key...
python "%~dp0heimdall.py" --setup

:done
echo.
pause
