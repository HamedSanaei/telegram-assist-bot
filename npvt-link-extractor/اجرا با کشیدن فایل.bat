@echo off
setlocal
chcp 65001 >nul

if "%~1"=="" (
    echo فایل TXT یا NPVT را روی این فایل BAT بکشید و رها کنید.
    echo.
    pause
    exit /b 1
)

where py >nul 2>nul
if %errorlevel%==0 (
    py "%~dp0npvt_link_extractor.py" "%~1"
) else (
    python "%~dp0npvt_link_extractor.py" "%~1"
)

echo.
pause
