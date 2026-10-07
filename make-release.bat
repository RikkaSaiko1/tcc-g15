@echo off
REM ===========================================================================
REM  tcc-g15 - release build
REM
REM  Produces, in .\ :
REM    dist\tcc-g15-installer-<version>.exe   Windows installer
REM    tcc-g15-portable.zip                   no-install build (unzip and run)
REM
REM  Usage:  make-release.bat
REM
REM  Inno Setup is located automatically: winget installs it per-user
REM  (%LOCALAPPDATA%\Programs), the classic installer uses Program Files,
REM  so both are probed. The upstream version of this script hard-coded
REM  "C:\Program Files (x86)\Inno Setup 6\iscc.exe" and failed whenever
REM  Inno Setup lived anywhere else.
REM ===========================================================================
setlocal EnableDelayedExpansion

cd /d "%~dp0"

REM --- Locate Python --------------------------------------------------------
set "PY="
if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"
if not defined PY ( where py >nul 2>nul && set "PY=py -3" )
if not defined PY ( where python >nul 2>nul && set "PY=python" )
if not defined PY (
  echo [ERROR] Python not found on PATH.
  exit /b 1
)

echo === tcc-g15 release build ===
%PY% -c "import sys; print('Python ' + sys.version.split()[0])" || exit /b 1

REM --- Dependencies --------------------------------------------------------
echo.
echo --- Checking dependencies ---
%PY% -c "import PySide6, wmi, winrt, bottle, waitress, psutil, windows_toasts, PyInstaller" 2>nul
if errorlevel 1 (
  echo Installing missing dependencies ^(needs network, runs once^)...
  %PY% -m pip install --upgrade pip
  %PY% -m pip install -r requirements.txt
  %PY% -m pip install pyinstaller
  if errorlevel 1 (
    echo [ERROR] Dependency installation failed.
    exit /b 1
  )
) else (
  echo All dependencies present.
)

REM --- Locate ISCC ---------------------------------------------------------
echo.
echo --- Locating Inno Setup ---
set "ISCC="
for %%P in (
  "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"
  "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
  "%ProgramFiles%\Inno Setup 6\ISCC.exe"
) do (
  if not defined ISCC if exist %%P set "ISCC=%%~P"
)
if not defined ISCC (
  REM Last resort: search the usual roots for ISCC.exe.
  for /f "delims=" %%F in ('dir /b /s "%LOCALAPPDATA%\Programs\ISCC.exe" 2^>nul') do (
    if not defined ISCC set "ISCC=%%F"
  )
)
if not defined ISCC (
  echo [ERROR] ISCC.exe ^(Inno Setup^) not found.
  echo         Install it with:  winget install JRSoftware.InnoSetup
  exit /b 1
)
echo Using: !ISCC!

REM --- Stage 1: ONEDIR (what the installer packs) --------------------------
echo.
echo --- Building ONEDIR ---
if exist build rmdir /s /q build
if exist dist\tcc-g15 rmdir /s /q "dist\tcc-g15"
%PY% -m PyInstaller --noconfirm --clean ^
  --workpath build --distpath dist tcc-g15.spec || exit /b 1

if not exist "dist\tcc-g15\tcc-g15.exe" (
  echo [ERROR] ONEDIR build did not produce dist\tcc-g15\tcc-g15.exe
  exit /b 1
)

REM --- Stage 2: compile the installer --------------------------------------
echo.
echo --- Compiling installer ---
"!ISCC!" installer-inno-config.iss || exit /b 1

REM --- Stage 3: portable zip -----------------------------------------------
REM The ONEDIR tree above is reused as-is: a folder build is smaller than a
REM one-file exe and starts faster, since it never unpacks to %TEMP%.
REM The archive is written to the repo root, NOT into dist/ - archiving a
REM directory while writing the archive into it makes the file grow forever.
echo.
echo --- Packing portable zip ---
set "ZIPFILE=tcc-g15-portable.zip"
if exist "%ZIPFILE%" del /q "%ZIPFILE%"
%PY% -c "import shutil; shutil.make_archive(r'tcc-g15-portable','zip',r'dist',r'tcc-g15')" || exit /b 1
if not exist "%ZIPFILE%" (
  echo [ERROR] zip was not created.
  exit /b 1
)

echo.
echo ===========================================================
echo  Done.
echo ===========================================================
dir /b "dist\tcc-g15-installer-*.exe" "%ZIPFILE%" 2>nul
echo.
echo Reminder: the app must be run as Administrator (it talks to WMI).
exit /b 0
