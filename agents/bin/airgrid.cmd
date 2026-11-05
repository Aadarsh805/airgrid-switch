@echo off
rem `airgrid` for agents whose shell is cmd or PowerShell. Same rules as the bash wrapper: identity
rem comes from .env in the agent's directory, and the repo is never on the import path (L11, L44).
setlocal
if exist .env (
    for /f "usebackq tokens=1,* delims==" %%a in (".env") do (
        if "%%a"=="AIRGRID_API_URL" set "AIRGRID_API_URL=%%b"
        if "%%a"=="AIRGRID_ROLE" set "AIRGRID_ROLE=%%b"
        if "%%a"=="AIRGRID_TOKEN" set "AIRGRID_TOKEN=%%b"
    )
)
set PYTHONPATH=
set PYTHONHOME=
set VIRTUAL_ENV=
uv run --quiet --no-project --python 3.12 --with typer --with httpx python "%~dp0airgrid.py" %*
exit /b %errorlevel%
