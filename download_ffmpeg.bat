@echo off
chcp 65001 >nul
title Tai FFmpeg vao bin/
color 0B

set "SCRIPT_DIR=%~dp0"
set "BIN_DIR=%SCRIPT_DIR%bin"
set "TMP_DIR=%SCRIPT_DIR%_ffmpeg_tmp"

if exist "%BIN_DIR%\ffmpeg.exe" (
    echo [OK] ffmpeg.exe da co trong bin\ - khong can tai lai.
    echo      Xoa bin\ffmpeg.exe neu muon tai lai ban moi.
    pause & exit /b 0
)

echo Dang tao thu muc bin\ ...
if not exist "%BIN_DIR%" mkdir "%BIN_DIR%"

echo Dang tai FFmpeg essentials (~80MB) ...
powershell -NoProfile -Command ^
    "Invoke-WebRequest -Uri 'https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip' -OutFile '%TMP_DIR%_ffmpeg.zip' -UseBasicParsing"

if not exist "%TMP_DIR%_ffmpeg.zip" (
    echo [LOI] Tai that bai. Kiem tra ket noi mang.
    pause & exit /b 1
)

echo Dang giai nen ...
powershell -NoProfile -Command ^
    "Expand-Archive -Path '%TMP_DIR%_ffmpeg.zip' -DestinationPath '%TMP_DIR%' -Force"

:: Tim va copy ffmpeg.exe bang PowerShell (tranh loi phan tich duong dan co dau ngoac)
echo Tim kiem ffmpeg.exe trong thu muc giai nen...
powershell -NoProfile -Command ^
    "$f = Get-ChildItem -Path '%TMP_DIR%' -Recurse -Filter 'ffmpeg.exe' | Select-Object -First 1; if ($f) { Copy-Item $f.FullName -Destination '%BIN_DIR%\ffmpeg.exe' -Force; Write-Host ('[OK] Da copy: ' + $f.FullName) } else { Write-Host '[LOI] Khong tim thay ffmpeg.exe trong zip'; exit 1 }"
if errorlevel 1 goto :end_error

:cleanup
rd /s /q "%TMP_DIR%" >nul 2>&1
del "%TMP_DIR%_ffmpeg.zip" >nul 2>&1

if exist "%BIN_DIR%\ffmpeg.exe" (
    echo.
    echo ============================================
    echo   HOAN TAT! ffmpeg.exe da co trong bin\
    echo   Kich thuoc:
    for %%f in ("%BIN_DIR%\ffmpeg.exe") do echo   %%~zf bytes
    echo ============================================
) else (
    echo [LOI] Khong tim thay ffmpeg.exe sau khi giai nen.
    pause & exit /b 1
)
pause
exit /b 0

:end_error
echo [LOI] Khong copy duoc ffmpeg.exe.
rd /s /q "%TMP_DIR%" >nul 2>&1
del "%TMP_DIR%_ffmpeg.zip" >nul 2>&1
pause
exit /b 1
