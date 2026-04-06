@echo off
chcp 65001 >nul
title Tai file cai dat
color 0B

echo ============================================
echo   Dang tai file cai dat...
echo ============================================
echo.

set "SCRIPT_DIR=%~dp0"
set "INSTALLERS_DIR=%SCRIPT_DIR%installers"
if not exist "%INSTALLERS_DIR%" mkdir "%INSTALLERS_DIR%"

:: ── Tai Python 3.12 ─────────────────────────────────────────────────
echo [1/2] Dang tai Python 3.12.9 (khoang 25MB)...
powershell -NoProfile -Command "Invoke-WebRequest -Uri 'https://www.python.org/ftp/python/3.12.9/python-3.12.9-amd64.exe' -OutFile '%INSTALLERS_DIR%\python_installer.exe' -UseBasicParsing"
if exist "%INSTALLERS_DIR%\python_installer.exe" (
    echo  [OK] Tai Python thanh cong.
) else (
    echo  [LOI] Tai Python that bai. Kiem tra ket noi mang.
    pause & exit /b 1
)
echo.

:: ── Tai FFmpeg (essentials build) ───────────────────────────────────
echo [2/2] Dang tai FFmpeg (~80MB)...
powershell -NoProfile -Command "Invoke-WebRequest -Uri 'https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip' -OutFile '%INSTALLERS_DIR%\ffmpeg.zip' -UseBasicParsing"
if exist "%INSTALLERS_DIR%\ffmpeg.zip" (
    echo  [OK] Tai FFmpeg thanh cong.
) else (
    echo  [LOI] Tai FFmpeg that bai. Kiem tra ket noi mang.
    pause & exit /b 1
)
echo.

echo ============================================
echo   Tai xong! Bay gio hay chay SETUP.bat
echo ============================================
pause
