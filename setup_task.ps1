# Skrypt rejestracji zadania w Harmonogramie Zadan Windows (Wymaga uprawnien Administratora)
$ErrorActionPreference = "Stop"

$taskName = "OverRadar_Live_Background_Service"
$pythonExe = "C:\Users\Technolog\AppData\Local\Programs\Python\Python38\pythonw.exe"
$scriptPath = "C:\Users\Technolog\Desktop\SurebetGit\background_service.py"
$workingDir = "C:\Users\Technolog\Desktop\SurebetGit"

Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "  REJESTRACJA ZADANIA W HARMONOGRAMIE ZADAN WINDOWS       " -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan

# 1. Zatrzymanie dzialajacej instancji procesow
try {
    $processes = Get-CimInstance Win32_Process -Filter "Name like 'python%'" -ErrorAction SilentlyContinue | Where-Object {
        $_.CommandLine -like "*sts_live_scanner.py*" -or $_.CommandLine -like "*tray_launcher.py*" -or $_.CommandLine -like "*background_service.py*"
    }
    if ($processes) {
        Write-Host "Zatrzymywanie dotychczasowej instancji procesow..." -ForegroundColor Yellow
        foreach ($p in $processes) {
            Write-Host "  Zamykam PID: $($p.ProcessId)" -ForegroundColor Gray
            Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
        }
        Start-Sleep -Seconds 2
    }
} catch {
    Write-Host "Ostrzezenie przy zatrzymywaniu procesow: $_" -ForegroundColor Yellow
}

# 2. Usuniecie poprzedniej wersji zadania, jesli istnieje
$existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "Usuwanie poprzedniej wersji zadania '$taskName'..." -ForegroundColor Yellow
    Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
    Start-Sleep -Seconds 1
}

# 3. Parametry nowego zadania systemowego
$action = New-ScheduledTaskAction -Execute $pythonExe -Argument "`"$scriptPath`"" -WorkingDirectory $workingDir
$trigger = New-ScheduledTaskTrigger -AtStartup
$trigger.Delay = 'PT20S'

$principal = New-ScheduledTaskPrincipal -UserId "NT AUTHORITY\SYSTEM" -LogonType ServiceAccount -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Days 0) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -StartWhenAvailable

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Description "OverRadar Live - Niezalezny skaner kursow STS i goli dzialajacy w tle przy starcie komputera." -Force

Write-Host "`nPomyslnie zarejestrowano zadanie w systemie Windows!" -ForegroundColor Green
Write-Host "  • Nazwa: $taskName" -ForegroundColor White
Write-Host "  • Wyzwalacz: Przy starcie komputera (AtStartup, opoznienie 20s na polaczenie z siecia)" -ForegroundColor White
Write-Host "  • Konto: NT AUTHORITY\SYSTEM (Dziala ZAWSZE, nawet na ekranie logowania z klodka)" -ForegroundColor White
Write-Host "  • Uprawnienia: Najwyzsze (Highest / Administrator)" -ForegroundColor White
Write-Host "  • Logi uslugi: $workingDir\background_service.log" -ForegroundColor White
Write-Host "==========================================================" -ForegroundColor Cyan

# 4. Uruchomienie uslugi teraz
Write-Host "`nUruchamianie uslugi systemowej w tle..." -ForegroundColor Cyan
Start-ScheduledTask -TaskName $taskName
Start-Sleep -Seconds 3

$state = (Get-ScheduledTask -TaskName $taskName).State
Write-Host "Aktualny stan zadania: $state" -ForegroundColor Green

# 5. Uruchomienie tray_launcher dla zalogowanego uzytkownika (w trybie attached)
Write-Host "Uruchamianie ikony w zasobniku systemowym (Tray Launcher)..." -ForegroundColor Cyan
Start-Process $pythonExe "`"$workingDir\tray_launcher.py`"" -WorkingDirectory $workingDir
Write-Host "`nSUKCES! Skaner dziala w tle jako usluga systemowa." -ForegroundColor Green
try {
    Write-Host "`nNacisnij dowolny klawisz, aby zamknac to okno..." -ForegroundColor Yellow
    $null = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")
} catch {}
