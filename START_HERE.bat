@echo off
setlocal
cd /d "%~dp0"
title Seamless Shorts Studio

echo.
echo ======================================================
echo   Seamless Shorts Studio - Starting...
echo ======================================================
echo.

set "PATH=%~dp0bin;%LOCALAPPDATA%\Microsoft\WinGet\Links;%PATH%"
set "PY_CMD="
set "PY_EXE="
set "PY_DIR="

rem 1. Local portable runtime inside kit folder
call :try_python "%~dp0runtime\python.exe"
if defined PY_EXE goto :found_python
call :try_python "%~dp0python\python.exe"
if defined PY_EXE goto :found_python

rem 2-5. Installed Python discovery ladder
call :probe_installed_python
if defined PY_CMD goto :found_python
if defined PY_EXE goto :found_python

rem 6. Attempt winget install + Registry PATH refresh + immediate re-probe
echo [!] Python 3 was not found on this PC.
where winget >nul 2>&1
if not errorlevel 1 (
    echo Attempting to install Python automatically via winget...
    winget install -e --id Python.Python.3.12 --accept-source-agreements --accept-package-agreements
    call :refresh_path_from_registry
    call :probe_installed_python
    if defined PY_CMD goto :found_python
    if defined PY_EXE goto :found_python
)

rem 7. Zero-admin embedded Python zip fallback
echo Attempting zero-admin portable Python setup in "%~dp0runtime"...
where powershell >nul 2>&1
if not errorlevel 1 (
    set "SS_RT_DIR=%~dp0runtime"
    powershell -NoProfile -ExecutionPolicy Bypass -Command "$ProgressPreference='SilentlyContinue'; $rt=$env:SS_RT_DIR; New-Item -ItemType Directory -Force -Path $rt | Out-Null; $zip=Join-Path $rt 'python-embed.zip'; Invoke-WebRequest -UseBasicParsing -Uri 'https://www.python.org/ftp/python/3.12.8/python-3.12.8-embed-amd64.zip' -OutFile $zip; Expand-Archive -Force -Path $zip -DestinationPath $rt; Remove-Item -Force $zip; Get-ChildItem (Join-Path $rt 'python*._pth') | ForEach-Object { (Get-Content $_.FullName) -replace '#import site','import site' | Set-Content $_.FullName }" >nul 2>&1
    call :try_python "%~dp0runtime\python.exe"
    if defined PY_EXE goto :found_python
)

echo.
echo [!] Could not automatically install Python.
echo.
echo You have two easy options:
echo   1. Double-click Seamless_Shorts_Player.html in this folder to preview
echo      and sync Shorts directly in your browser with zero install.
echo   2. Or install Python from https://www.python.org/downloads/
echo      (check "Add python.exe to PATH" during setup), then run START_HERE.bat again.
echo.
pause
exit /b 1

:found_python
if defined PY_DIR set "PATH=%PY_DIR%;%PY_DIR%\Scripts;%PATH%"
for %%S in (
    "%APPDATA%\Python\Python315\Scripts"
    "%APPDATA%\Python\Python314\Scripts"
    "%APPDATA%\Python\Python313\Scripts"
    "%APPDATA%\Python\Python312\Scripts"
    "%APPDATA%\Python\Python311\Scripts"
    "%APPDATA%\Python\Python310\Scripts"
) do (
    if exist %%S set "PATH=%%~S;%PATH%"
)

echo Launching Seamless Shorts Studio in your browser...
echo Keep this window open while using the app; close it when done.
echo.
if defined PY_EXE goto :run_py_exe
%PY_CMD% seamless_shorts_agent.py --ui %*
goto :after_run
:run_py_exe
call "%PY_EXE%" seamless_shorts_agent.py --ui %*
:after_run
if errorlevel 1 (
    echo.
    echo [!] Seamless Shorts Studio stopped with an error. See details above.
    pause
    exit /b 1
)
exit /b 0

:probe_installed_python
py -3 -c "import sys; sys.exit(0 if sys.version_info[0]>=3 else 1)" >nul 2>&1
if not errorlevel 1 (
    set "PY_CMD=py -3"
    for /f "delims=" %%I in ('py -3 -c "import os,sys; print(os.path.dirname(sys.executable))" 2^>nul') do set "PY_DIR=%%I"
    exit /b 0
)

rem Skip Microsoft Store WindowsApps alias stub when probing python on PATH
for /f "delims=" %%P in ('where python 2^>nul') do (
    echo "%%~P" | find /I "\WindowsApps\" >nul
    if errorlevel 1 (
        call :try_python "%%~P"
        if defined PY_EXE exit /b 0
    )
)

rem Probe Windows Registry PythonCore InstallPath (HKCU, HKLM, Wow6432Node)
for %%R in (
    "HKCU\Software\Python\PythonCore"
    "HKLM\Software\Python\PythonCore"
    "HKLM\Software\Wow6432Node\Python\PythonCore"
) do (
    for /f "tokens=*" %%V in ('reg query %%R 2^>nul') do (
        for /f "tokens=2*" %%A in ('reg query "%%V\InstallPath" /v ExecutablePath 2^>nul ^| findstr /I "REG_SZ REG_EXPAND_SZ"') do (
            call :try_python "%%~B"
            if defined PY_EXE exit /b 0
        )
        for /f "tokens=2*" %%A in ('reg query "%%V\InstallPath" /ve 2^>nul ^| findstr /I "REG_SZ REG_EXPAND_SZ"') do (
            call :try_python "%%~B\python.exe"
            if defined PY_EXE exit /b 0
            call :try_python "%%~Bpython.exe"
            if defined PY_EXE exit /b 0
        )
    )
)

rem Probe standard Windows install directories and package managers
for %%D in (
    "C:\Python315\python.exe"
    "C:\Python314\python.exe"
    "C:\Python313\python.exe"
    "C:\Python312\python.exe"
    "C:\Python311\python.exe"
    "C:\Python310\python.exe"
    "%LOCALAPPDATA%\Programs\Python\Python315\python.exe"
    "%LOCALAPPDATA%\Programs\Python\Python314\python.exe"
    "%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
    "%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
    "%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
    "%LOCALAPPDATA%\Programs\Python\Python310\python.exe"
    "%ProgramFiles%\Python315\python.exe"
    "%ProgramFiles%\Python314\python.exe"
    "%ProgramFiles%\Python313\python.exe"
    "%ProgramFiles%\Python312\python.exe"
    "%ProgramFiles%\Python311\python.exe"
    "%ProgramFiles%\Python310\python.exe"
    "%USERPROFILE%\scoop\apps\python\current\python.exe"
    "%USERPROFILE%\scoop\shims\python.exe"
    "%USERPROFILE%\.pyenv\pyenv-win\shims\python.exe"
    "%USERPROFILE%\.pyenv\pyenv-win\shims\python.bat"
    "C:\ProgramData\chocolatey\bin\python.exe"
) do (
    call :try_python %%D
    if defined PY_EXE exit /b 0
)
exit /b 1

:try_python
if "%~1"=="" exit /b 1
if not exist "%~1" exit /b 1
echo "%~1" | find /I "\WindowsApps\" >nul
if not errorlevel 1 exit /b 1
call "%~1" -c "import sys; sys.exit(0 if sys.version_info[0]>=3 else 1)" >nul 2>&1
if errorlevel 1 exit /b 1
set "PY_EXE=%~1"
set "PY_DIR=%~dp1"
if "%PY_DIR:~-1%"=="\" set "PY_DIR=%PY_DIR:~0,-1%"
exit /b 0

:refresh_path_from_registry
set "SYS_REG_PATH="
set "USR_REG_PATH="
for /f "tokens=2*" %%A in ('reg query "HKLM\SYSTEM\CurrentControlSet\Control\Session Manager\Environment" /v Path 2^>nul ^| findstr /I "REG_SZ REG_EXPAND_SZ"') do (
    call set "SYS_REG_PATH=%%B"
)
for /f "tokens=2*" %%A in ('reg query "HKCU\Environment" /v Path 2^>nul ^| findstr /I "REG_SZ REG_EXPAND_SZ"') do (
    call set "USR_REG_PATH=%%B"
)
if defined SYS_REG_PATH set "PATH=%SYS_REG_PATH%;%PATH%"
if defined USR_REG_PATH set "PATH=%USR_REG_PATH%;%PATH%"
exit /b 0
