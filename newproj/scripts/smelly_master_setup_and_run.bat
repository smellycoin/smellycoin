@echo off
REM SMELLY Chain Master Setup & Run (Windows, no Docker)
REM This script:
REM  1) Creates/activates a local .venv
REM  2) Upgrades pip and installs requirements
REM  3) Performs an editable install for import stability
REM  4) Initializes the database/genesis
REM  5) Launches all core services each in its own terminal window:
REM     - Node RPC
REM     - Wallet Backend
REM     - Wallet UI
REM     - Masternode
REM     - Pool (Stratum)
REM     - Explorer
REM     - Solo Miner (optional; disabled by default)
REM     - Pool Miner (optional; disabled by default)
REM  6) Runs basic smoke tests to verify readiness

setlocal ENABLEDELAYEDEXPANSION

REM Change to script directory root (project root)
cd /d %~dp0..
set PROJECT_ROOT=%CD%
echo Project root: %PROJECT_ROOT%

REM Kill any previous processes that are holding our known ports to avoid 10048 bind errors
echo.
echo Cleaning up any processes on SMELLY ports (Node 28445, Pool 28446, MN 28447, Explorer 28448, Wallet 28450)...
for %%P in (28445 28446 28447 28448 28450) do (
  for /f "skip=4 tokens=5" %%A in ('netstat -ano ^| findstr /R /C:":%%P .*LISTENING"') do (
    echo - Terminating PID %%A on port %%P
    taskkill /F /PID %%A >NUL 2>&1
  )
)
echo Port cleanup complete.

REM Ensure Python is available
where python >NUL 2>&1
IF ERRORLEVEL 1 (
  echo ERROR: python not found on PATH. Install Python 3.11+ and ensure it's on PATH.
  pause
  exit /b 1
)

REM Create venv if missing
if not exist ".venv" (
  echo Creating virtual environment .venv ...
  python -m venv .venv
  if ERRORLEVEL 1 (
    echo ERROR: Failed to create virtual environment.
    pause
    exit /b 1
  )
)

REM Activate venv
call .venv\Scripts\activate
if ERRORLEVEL 1 (
  echo ERROR: Failed to activate virtual environment.
  pause
  exit /b 1
)

echo Upgrading pip ...
python -m pip install --upgrade pip

echo Installing requirements ...
pip install -r requirements.txt
if ERRORLEVEL 1 (
  echo ERROR: Failed to install Python dependencies from requirements.txt
  pause
  exit /b 1
)

echo Editable install of the project ...
pip install -e .
if ERRORLEVEL 1 (
  echo ERROR: Editable install failed. Proceeding without it, but imports may rely on launcher.
)

echo Initializing database and genesis ...
python -m tools.run init

REM Helper to open new terminal windows with a title and command
set "PSHELL=powershell -NoLogo -NoExit -Command"
set "PYTHON_ACT=.venv\Scripts\activate; python"

REM Configurable options (edit as needed)
set SOLO_MINER_ENABLE=0
set SOLO_MINER_ADDR=SMELLY_SOLO
set SOLO_MINER_LOOP=1

set POOL_MINER_ENABLE=0
set POOL_MINER_HOST=127.0.0.1
set POOL_MINER_PORT=28446
set POOL_MINER_ADDR=SMELLY_POOL_MINER
set POOL_MINER_INTENSITY=2

echo.
echo Step-by-step launcher (press ENTER to start each service). This avoids automation and lets you verify each one.

echo.
REM Helper to start a small titled window (80x25 rows approx) using mode command (columns,lines)
set "SMELLY_MODE=mode con: cols=100 lines=28"

echo [1/7] Start Node RPC window (press ENTER once)...
pause >NUL
start "SMELLY Node RPC" cmd /k "%SMELLY_MODE% && .venv\Scripts\activate && python -m tools.run node"
echo - Node RPC window opened: 'SMELLY Node RPC'
echo   Verify it shows: 'Uvicorn running on http://127.0.0.1:28445'
echo   When ready, press ENTER once to start the Wallet Backend...
pause >NUL

echo.
echo [2/7] Start Wallet Backend (press ENTER once)...
echo   TIP: If you previously started Wallet Backend, close that window first to free port 28449.
pause >NUL
start "SMELLY Wallet Backend" cmd /k "%SMELLY_MODE% && .venv\Scripts\activate && python -m tools.run wallet-backend"
echo - Wallet Backend window opened: 'SMELLY Wallet Backend'
echo   If you see WinError 10048 (port-in-use), close any other 'SMELLY Wallet Backend' windows and press ENTER to try opening again...
set /p _retry_wallet="Press ENTER to re-open Wallet Backend (or type SKIP then ENTER to continue): "
if /I not "%_retry_wallet%"=="SKIP" (
  start "SMELLY Wallet Backend" cmd /k "%SMELLY_MODE% && .venv\Scripts\activate && python -m tools.run wallet-backend"
)
echo   When it shows it’s running, press ENTER once to start the Pool...
pause >NUL

echo.
echo [3/7] Start Stratum Pool (press ENTER once)...
pause >NUL
start "SMELLY Pool" cmd /k "%SMELLY_MODE% && .venv\Scripts\activate && python -m tools.run pool"
echo - Pool window opened: 'SMELLY Pool'
echo   Press ENTER once to start the Explorer...
pause >NUL

echo.
echo [4/7] Start Explorer (press ENTER once)...
echo   TIP: If you previously started Explorer or Web Wallet UI, close those windows first to free port 28448.
pause >NUL
start "SMELLY Explorer" cmd /k "%SMELLY_MODE% && .venv\Scripts\activate && python -m tools.run explorer"
echo - Explorer window opened: 'SMELLY Explorer'
echo   If you see WinError 10048 (port-in-use), close any other 'SMELLY Explorer' / 'SMELLY Web Wallet UI' windows using port 28448 and press ENTER to try opening Explorer again...
set /p _retry_explorer="Press ENTER to re-open Explorer (or type SKIP then ENTER to continue): "
if /I not "%_retry_explorer%"=="SKIP" (
  start "SMELLY Explorer" cmd /k "%SMELLY_MODE% && .venv\Scripts\activate && python -m tools.run explorer"
)
echo   Open http://127.0.0.1:28448 after starting the Wallet UI. Press ENTER once to start Wallet UI...
pause >NUL

echo.
echo [5/7] Start Web Wallet UI (press ENTER once)...
pause >NUL
start "SMELLY Web Wallet UI" cmd /k "%SMELLY_MODE% && .venv\Scripts\activate && python -m tools.run wallet-ui"
echo - Web Wallet UI window opened: 'SMELLY Web Wallet UI'
echo   If you see WinError 10048 (port-in-use), close any other 'SMELLY Web Wallet UI' / 'SMELLY Explorer' windows using port 28448 and press ENTER to try opening Web Wallet UI again...
set /p _retry_wallet_ui="Press ENTER to re-open Web Wallet UI (or type SKIP then ENTER to continue): "
if /I not "%_retry_wallet_ui%"=="SKIP" (
  start "SMELLY Web Wallet UI" cmd /k "%SMELLY_MODE% && .venv\Scripts\activate && python -m tools.run wallet-ui"
)
echo   Press ENTER once to start Masternode...
pause >NUL

echo.
echo [6/7] Start Masternode (press ENTER once)...
pause >NUL
start "SMELLY Masternode" cmd /k "%SMELLY_MODE% && .venv\Scripts\activate && python -m tools.run masternode"
echo - Masternode window opened: 'SMELLY Masternode'
echo   Press ENTER once to optionally start miners...
pause >NUL

echo.
echo [7/7] Optional miners:
echo   A) Start Solo Miner (press S and ENTER) using address %SOLO_MINER_ADDR%
echo   B) Start Pool Miner (press P and ENTER) to connect to %POOL_MINER_HOST%:%POOL_MINER_PORT%
echo   C) Skip miners (just press ENTER)
set /p USER_CHOICE="Your choice [S/P/ENTER]: "
if /I "%USER_CHOICE%"=="S" (
  start "SMELLY Solo Miner" cmd /k "%SMELLY_MODE% && .venv\Scripts\activate && python -m tools.run solo-miner --miner-address %SOLO_MINER_ADDR% --loop"
  echo - Solo Miner started.
) else if /I "%USER_CHOICE%"=="P" (
  start "SMELLY Pool Miner" cmd /k "%SMELLY_MODE% && .venv\Scripts\activate && python -m tools.run pool-miner --host %POOL_MINER_HOST% --port %POOL_MINER_PORT% --address %POOL_MINER_ADDR% --intensity %POOL_MINER_INTENSITY%"
  echo - Pool Miner started.
) else (
  echo - Skipping miners.
)

echo.
echo All requested windows have been opened step-by-step.
echo  - Explorer/Web Wallet UI: http://127.0.0.1:28448
echo  - Node RPC title: SMELLY Node RPC
echo  - Wallet Backend title: SMELLY Wallet Backend
echo  - Wallet UI title: SMELLY Web Wallet UI
echo  - Explorer title: SMELLY Explorer
echo  - Pool title: SMELLY Pool
echo  - Masternode title: SMELLY Masternode
echo.
echo This orchestrator window will remain open. Close it at any time; service windows stay up.
pause >NUL
