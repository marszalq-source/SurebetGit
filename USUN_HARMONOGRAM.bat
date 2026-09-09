@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Usunięcie Usługi OverRadar Live z Harmonogramu Zadań

net session >nul 2>&1
if %errorlevel% neq 0 (
    echo ========================================================
    echo   WYMAGANE UPRAWNIENIA ADMINISTRATORA
    echo ========================================================
    echo   Trwa uruchamianie z uprawnieniami Administratora...
    echo   Jesli pojawi sie okno UAC (Kontrola konta), kliknij TAK.
    powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process cmd -ArgumentList '/c \"\"%~dp0USUN_HARMONOGRAM.bat\"\"' -Verb RunAs"
    exit /b
)

echo ========================================================
echo   ⚽ USUWANIE ZADANIA Z HARMONOGRAMU ZADAŃ ⚽
echo ========================================================
echo.

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "$taskName = 'OverRadar_Live_Background_Service'; " ^
    "$task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue; " ^
    "if ($task) { " ^
    "    Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue; " ^
    "    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false; " ^
    "    Write-Host 'Pomyślnie wyrejestrowano zadanie z systemu.' -ForegroundColor Green; " ^
    "} else { " ^
    "    Write-Host 'Zadanie nie było zarejestrowane.' -ForegroundColor Yellow; " ^
    "} "

echo.
echo Naciśnij dowolny klawisz, aby zakończyć...
pause >nul
