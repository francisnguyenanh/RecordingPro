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
    goto :install_ffmpeg
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

:install_ffmpeg
echo.

:: ── 2. Cai FFmpeg ───────────────────────────────────────────────────
echo [2/5] Kiem tra FFmpeg...
ffmpeg -version >nul 2>&1
if not errorlevel 1 (
    echo  [OK] FFmpeg da co trong PATH.
    goto :create_venv
)

echo  Chua co FFmpeg. Dang cai dat...
if not exist "%INSTALLERS_DIR%\ffmpeg.zip" (
    echo  [LOI] Khong tim thay installers\ffmpeg.zip
    echo  Vui long chay download_installers.bat truoc
    pause & exit /b 1
)

:: Giai nen ffmpeg vao C:\ffmpeg
echo  Dang giai nen FFmpeg vao C:\ffmpeg ...
powershell -NoProfile -Command "Expand-Archive -Path '%INSTALLERS_DIR%\ffmpeg.zip' -DestinationPath 'C:\ffmpeg_tmp' -Force"

:: Tim thu muc ffmpeg ben trong zip (ten co the thay doi theo phien ban)
for /d %%d in ("C:\ffmpeg_tmp\ffmpeg-*") do (
    xcopy /E /I /Y "%%d" "C:\ffmpeg" >nul 2>&1
)
rd /s /q "C:\ffmpeg_tmp" >nul 2>&1

:: Them C:\ffmpeg\bin vao System PATH
powershell -NoProfile -Command "$old=[System.Environment]::GetEnvironmentVariable('PATH','Machine'); if ($old -notlike '*C:\ffmpeg\bin*'){[System.Environment]::SetEnvironmentVariable('PATH',$old+';C:\ffmpeg\bin','Machine')}"
set "PATH=%PATH%;C:\ffmpeg\bin"

ffmpeg -version >nul 2>&1
if errorlevel 1 (
    echo  [LOI] Cai FFmpeg that bai!
    pause & exit /b 1
)
echo  [OK] FFmpeg da cai dat thanh cong.

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
