@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Status Usługi OverRadar Live

echo ========================================================
echo   ⚽ STATUS USŁUGI OVERRADAR LIVE (HARMONOGRAM ZADAŃ) ⚽
echo ========================================================
echo.

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "$task = Get-ScheduledTask -TaskName 'OverRadar_Live_Background_Service' -ErrorAction SilentlyContinue; " ^
    "if ($task) { " ^
    "    Write-Host '  [Harmonogram] Zadanie zarejestrowane: TAK' -ForegroundColor Green; " ^
    "    Write-Host ('  [Harmonogram] Stan zadania: ' + $task.State) -ForegroundColor Cyan; " ^
    "    $info = Get-ScheduledTaskInfo -TaskName 'OverRadar_Live_Background_Service'; " ^
    "    Write-Host ('  [Harmonogram] Ostatnie uruchomienie: ' + $info.LastRunTime); " ^
    "    Write-Host ('  [Harmonogram] Kod wyniku: ' + $info.LastTaskResult); " ^
    "} else { " ^
    "    Write-Host '  [Harmonogram] Zadanie zarejestrowane: NIE (Uruchom ZAREJESTRUJ_HARMONOGRAM.bat)' -ForegroundColor Red; " ^
    "} " ^
    "Write-Host ''; " ^
    "Write-Host '  [Procesy Python w tle]:'; " ^
    "Get-Process python*, pythonw* -ErrorAction SilentlyContinue | Select-Object Id, ProcessName, StartTime | Format-Table -AutoSize; " ^
    "Write-Host '  [Port 5050]:'; " ^
    "try { " ^
    "    $res = Invoke-WebRequest -Uri 'http://127.0.0.1:5050/' -TimeoutSec 2 -UseBasicParsing; " ^
    "    Write-Host ('    Serwer na porcie 5050 ODPOWIADA (Status: ' + $res.StatusCode + ')') -ForegroundColor Green; " ^
    "} catch { " ^
    "    Write-Host '    Serwer na porcie 5050 NIE ODPOWIADA!' -ForegroundColor Red; " ^
    "} "

echo.
echo ========================================================
echo   OSTATNIE 15 LINII Z: background_service.log
echo ========================================================
if exist background_service.log (
    powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-Content background_service.log -Tail 15"
) else (
    echo Brak pliku background_service.log (usługa jeszcze nie była uruchamiana).
)
echo ========================================================
echo.
pause
