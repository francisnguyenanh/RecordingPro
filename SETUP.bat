@echo off
chcp 65001 >nul
title ScreenCapturePro v3 - Auto Setup
color 0A

:: Phai chay voi quyen Admin de cai Python va FFmpeg
net session >nul 2>&1
if errorlevel 1 (
    echo Dang yeu cau quyen Admin...
    powershell -Command "Start-Process '%~f0' -Verb RunAs"
    exit /b
)

echo ============================================
echo   ScreenCapturePro v3 - Auto Setup
echo ============================================
echo.

set "SCRIPT_DIR=%~dp0"
set "INSTALLERS_DIR=%SCRIPT_DIR%installers"

:: ── 1. Cai Python ───────────────────────────────────────────────────
echo [1/5] Kiem tra Python...
python --version >nul 2>&1
if not errorlevel 1 (
    for /f "tokens=2 delims= " %%v in ('python --version 2^>^&1') do set PYVER=%%v
    echo  [OK] Python %PYVER% da co san.
    goto :check_bundled_ffmpeg
)

echo  Chua co Python. Dang cai dat...
if not exist "%INSTALLERS_DIR%\python_installer.exe" (
    echo  [LOI] Khong tim thay installers\python_installer.exe
    echo  Vui long chay download_installers.bat truoc
    pause & exit /b 1
)
"%INSTALLERS_DIR%\python_installer.exe" /quiet InstallAllUsers=1 PrependPath=1 Include_pip=1
if errorlevel 1 (
    echo  [LOI] Cai Python that bai!
    pause & exit /b 1
)
:: Refresh PATH trong session hien tai
for /f "tokens=*" %%i in ('powershell -NoProfile -Command "[System.Environment]::GetEnvironmentVariable('PATH','Machine') + ';' + [System.Environment]::GetEnvironmentVariable('PATH','User')"') do set "PATH=%%i"
echo  [OK] Python da cai dat thanh cong.

:check_bundled_ffmpeg
echo.

:: ── 2. Kiem tra FFmpeg bundled ──────────────────────────────────────
echo [2/5] Kiem tra FFmpeg bundled...
if exist "%SCRIPT_DIR%bin\ffmpeg.exe" (
    echo  [OK] Phat hien bin\ffmpeg.exe - su dung bundled binary.
    goto :create_venv
)

:: Fallback: kiem tra PATH
ffmpeg -version >nul 2>&1
if not errorlevel 1 (
    echo  [OK] FFmpeg co trong PATH - su dung.
    goto :create_venv
)

echo  [CANH BAO] Khong tim thay ffmpeg.exe trong bin\ va khong co trong PATH.
echo  Chay download_ffmpeg.bat de tai tu dong, hoac dat ffmpeg.exe vao thu muc bin\
echo  App van co the chay nhung se LOI khi xu ly video.
echo.

:create_venv
echo.

:: ── 3. Tao Virtual Environment ──────────────────────────────────────
echo [3/5] Tao virtual environment (.venv)...
cd /d "%SCRIPT_DIR%"
if exist ".venv\" (
    echo  [OK] .venv da ton tai, bo qua.
) else (
    python -m venv .venv
    if errorlevel 1 ( echo  [LOI] Khong tao duoc .venv! & pause & exit /b 1 )
    echo  [OK] Tao .venv thanh cong.
)
echo.

:: ── 4. Cai thu vien ─────────────────────────────────────────────────
echo [4/5] Cai dat thu vien (co the mat 3-5 phut)...
echo.
call .venv\Scripts\activate.bat
pip install --upgrade pip -q
pip install -r requirements.txt
pip install winsdk >nul 2>&1
echo.
echo  [OK] Cai dat thu vien hoan tat.
echo.

:: ── 5. Tao RUN.bat ──────────────────────────────────────────────────
echo [5/5] Tao file chay nhanh (RUN.bat)...
(
    echo @echo off
    echo cd /d "%%~dp0"
    echo call .venv\Scripts\activate.bat
    echo start /b pythonw tray.py
    echo timeout /t 2 ^>nul
    echo start http://127.0.0.1:5000
) > "%SCRIPT_DIR%RUN.bat"
echo  [OK] Da tao RUN.bat
echo.

echo ============================================
echo   SETUP HOAN TAT!
echo ============================================
echo.
echo   Tu nay chi can click doi-click RUN.bat de mo app.
echo   Giao dien web: http://127.0.0.1:5000
echo ============================================
echo.
set /p START="Chay ung dung ngay bay gio? (y/n): "
if /i "%START%"=="y" (
    start /b pythonw tray.py
    timeout /t 2 >nul
    start http://127.0.0.1:5000
)
pause
