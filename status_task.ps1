# Skrypt sprawdzania stanu uslugi OverRadar Live
$taskName = 'OverRadar_Live_Background_Service'
$port = 5050

Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "  STATUS USLUGI OVERRADAR LIVE (HARMONOGRAM ZADAN)       " -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan

# 1. Sprawdzenie w Harmonogramie Zadan
$task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($task) {
    Write-Host "  [Harmonogram] Zadanie zarejestrowane: TAK" -ForegroundColor Green
    Write-Host ("  [Harmonogram] Stan zadania: " + $task.State) -ForegroundColor Cyan
    $info = Get-ScheduledTaskInfo -TaskName $taskName -ErrorAction SilentlyContinue
    if ($info) {
        Write-Host ("  [Harmonogram] Ostatnie uruchomienie: " + $info.LastRunTime) -ForegroundColor White
        Write-Host ("  [Harmonogram] Kod ostatniego wyniku: " + $info.LastTaskResult) -ForegroundColor White
    }
} else {
    # Sprawdzenie czy zadanie istnieje jako systemowe (wymaga admina) lub czy usluga dziala bezposrednio
    Write-Host "  [Harmonogram] Zadanie w Harmonogramie: Niezarejestrowane lub wymaga uprawnien Administratora" -ForegroundColor Yellow
    Write-Host "                (Aby zarejestrowac w Harmonogramie Windows, uruchom: ZAREJESTRUJ_HARMONOGRAM.bat jako Administrator)" -ForegroundColor Gray
}

# 2. Sprawdzenie procesow Python
Write-Host "`n  [Procesy Python w systemie]:" -ForegroundColor Yellow
$pyProcs = Get-Process python*, pythonw* -ErrorAction SilentlyContinue | Select-Object Id, ProcessName, StartTime
if ($pyProcs) {
    $pyProcs | Format-Table -AutoSize | Out-String | Write-Host -ForegroundColor White
} else {
    Write-Host "    Brak dzialajacych procesow python/pythonw." -ForegroundColor Gray
}

# 3. Sprawdzenie portu 5050
Write-Host "  [Port 5050 - Serwer Live Scanner]:" -ForegroundColor Yellow
try {
    $res = Invoke-WebRequest -Uri "http://127.0.0.1:$port/" -TimeoutSec 2 -UseBasicParsing
    Write-Host "    Serwer na porcie $port ODPOWIADA (HTTP $($res.StatusCode))" -ForegroundColor Green
} catch {
    Write-Host "    Serwer na porcie $port NIE ODPOWIADA" -ForegroundColor Red
}

# 4. Ostatnie linie logow uslugi tla
Write-Host "`n==========================================================" -ForegroundColor Cyan
Write-Host "  OSTATNIE LINIE Z: background_service.log" -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan

$logPath = Join-Path $PSScriptRoot "background_service.log"
if (Test-Path $logPath) {
    Get-Content $logPath -Tail 15 | ForEach-Object { Write-Host $_ -ForegroundColor Gray }
} else {
    Write-Host "Brak pliku background_service.log (usluga jeszcze nie generowala logow)." -ForegroundColor Gray
}

Write-Host "==========================================================" -ForegroundColor Cyan
try {
    if ([Environment]::UserInteractive -and -not [Console]::IsInputRedirected) {
        Write-Host "Nacisnij dowolny klawisz, aby zakonczyc..." -ForegroundColor Yellow
        $null = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")
    }
} catch {}
