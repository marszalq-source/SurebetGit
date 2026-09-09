@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Rejestracja Usługi OverRadar Live w Harmonogramie Zadań

:: Sprawdzenie czy skrypt ma uprawnienia Administratora
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo ========================================================
    echo   WYMAGANE UPRAWNIENIA ADMINISTRATORA
    echo ========================================================
    echo   Trwa uruchamianie z uprawnieniami Administratora...
    echo   Jesli pojawi sie okno UAC (Kontrola konta), kliknij TAK.
    powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process cmd -ArgumentList '/c \"\"%~dp0ZAREJESTRUJ_HARMONOGRAM.bat\"\"' -Verb RunAs"
    exit /b
)

echo ========================================================
echo   ⚽ INSTALACJA USŁUGI W HARMONOGRAMIE ZADAŃ ⚽
echo ========================================================
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup_task.ps1"
echo.
echo Naciśnij dowolny klawisz, aby zakończyć...
pause >nul
