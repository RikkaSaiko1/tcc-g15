@echo off
REM ===========================================================================
REM  tcc-g15 — build from a terminal with one command.
REM
REM    build.bat              portable single-file exe  (default)
REM    build.bat portable     same as above
REM    build.bat appdir       one-folder build (starts faster)
REM    build.bat zip          one-folder build + .zip to hand out
REM    build.bat all          appdir + zip + portable
REM
REM  Requires only Python + pip. No Inno Setup, no other installer needed.
REM  The Windows installer (.exe setup) is produced by GitHub Actions; see
REM  .github/workflows/build.yml.
REM ===========================================================================
setlocal EnableDelayedExpansion

cd /d "%~dp0"

set "MODE=%~1"
if "%MODE%"=="" set "MODE=portable"

REM --- Locate a Python interpreter -----------------------------------------
REM Prefer the project's own virtualenv if present, else the "py" launcher,
REM else whatever "python" resolves to.
set "PY="
if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"
if not defined PY (
  where py >nul 2>nul && set "PY=py -3"
)
if not defined PY (
  where python >nul 2>nul && set "PY=python"
)
if not defined PY (
  echo [ERROR] Python not found. Install Python 3.10+ and put it on PATH.
  exit /b 1
)

echo === tcc-g15 build: %MODE% ===
echo Using interpreter: %PY%
%PY% -c "import sys; print('Python ' + sys.version.split()[0])" || exit /b 1

REM --- Dependencies ---------------------------------------------------------
REM PyInstaller is a build tool, so it is installed here rather than listed in
REM requirements.txt (which describes what the app needs at runtime).
echo.
echo --- Checking dependencies ---
%PY% -c "import PySide6, wmi, winrt, bottle, waitress, psutil, windows_toasts, PyInstaller" 2>nul
if errorlevel 1 (
  echo Installing missing dependencies ^(needs network, runs once^)...
  %PY% -m pip install --upgrade pip
  %PY% -m pip install -r requirements.txt
  %PY% -m pip install pyinstaller
  if errorlevel 1 (
    echo [ERROR] Failed to install dependencies.
    exit /b 1
  )
) else (
  echo All dependencies present.
)

REM --- Clean previous output for the selected mode ---------------------------
set "OUTDIR=dist"
set "WORKDIR=build"
if /i "%MODE%"=="appdir" set "OUTDIR=dist-appdir"
if /i "%MODE%"=="appdir" set "WORKDIR=build-appdir"
if /i "%MODE%"=="zip"    set "OUTDIR=dist-appdir"
if /i "%MODE%"=="zip"    set "WORKDIR=build-appdir"

echo.
echo --- Building ---
if /i "%MODE%"=="portable" goto :build_portable
if /i "%MODE%"=="appdir"   goto :build_appdir
if /i "%MODE%"=="zip"      goto :build_appdir
if /i "%MODE%"=="all"      goto :build_all
echo [ERROR] Unknown mode "%MODE%". Use: portable ^| appdir ^| zip ^| all
exit /b 1

:build_portable
REM One file. Double-click to run. Slower first start (unpacks to temp).
%PY% -m PyInstaller --noconfirm --clean ^
  --workpath build --distpath dist tcc-g15-portable.spec || exit /b 1
echo.
echo [OK] dist\tcc-g15-portable.exe
goto :summary

:build_appdir
REM One folder. Starts faster, and is what the .zip is made from.
if exist "%OUTDIR%" rmdir /s /q "%OUTDIR%"
%PY% -m PyInstaller --noconfirm --clean ^
  --workpath "%WORKDIR%" --distpath "%OUTDIR%" tcc-g15.spec || exit /b 1
echo.
echo [OK] %OUTDIR%\tcc-g15\tcc-g15.exe
if /i "%MODE%"=="zip" goto :make_zip
goto :summary

:build_all
call "%~f0" appdir || exit /b 1
call "%~f0" portable || exit /b 1
call "%~f0" zip || exit /b 1
goto :summary

:make_zip
REM Zip the appdir build. Uses Python's zipfile, so nothing extra is needed.
echo.
echo --- Packing .zip ---
set "ZIPNAME=tcc-g15-portable.zip"
if exist "dist\%ZIPNAME%" del /q "dist\%ZIPNAME%"
%PY% -c "import shutil,os; shutil.make_archive(os.path.join('dist','tcc-g15-portable'), 'zip', os.path.join('dist-appdir'))" || exit /b 1
move /y "dist\tcc-g15-portable.zip" "dist\%ZIPNAME%" >nul
echo [OK] dist\%ZIPNAME%
goto :summary

:summary
echo.
echo ===========================================================
echo  Build finished. Output in: %CD%\dist
echo ===========================================================
dir /b "dist\*.exe" "dist\*.zip" 2>nul
echo.
echo Reminder: the app must be run as Administrator (it talks to WMI).
echo The Windows installer is built by GitHub Actions, not here.
exit /b 0
