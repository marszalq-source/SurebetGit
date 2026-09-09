# Skrypt rejestracji zadania w Harmonogramie Zadań Windows (Wymaga uprawnień Administratora)
$ErrorActionPreference = "Stop"

$taskName = "OverRadar_Live_Background_Service"
$pythonExe = "C:\Users\Technolog\AppData\Local\Programs\Python\Python38\pythonw.exe"
$scriptPath = "C:\Users\Technolog\Desktop\SurebetGit\background_service.py"
$workingDir = "C:\Users\Technolog\Desktop\SurebetGit"

Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host " ⚽ REJESTRACJA ZADANIA W HARMONOGRAMIE ZADAŃ WINDOWS ⚽" -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan

# 1. Zatrzymanie działającego procesu standalone, jeśli jest aktywny
$standaloneProc = Get-Process python*, pythonw* -ErrorAction SilentlyContinue | Where-Object {
    $_.CommandLine -like "*sts_live_scanner.py*" -or $_.CommandLine -like "*tray_launcher.py*"
}
if ($standaloneProc) {
    Write-Host "Zatrzymywanie dotychczasowej instancji procesów (PID: $($standaloneProc.Id -join ', '))..." -ForegroundColor Yellow
    $standaloneProc | Stop-Process -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 1
}

# 2. Sprawdzenie czy zadanie już istnieje
$existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "Usuwanie poprzedniej wersji zadania '$taskName'..." -ForegroundColor Yellow
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
}

# 3. Tworzenie parametrów zadania
$action = New-ScheduledTaskAction -Execute $pythonExe -Argument "`"$scriptPath`"" -WorkingDirectory $workingDir
$trigger = New-ScheduledTaskTrigger -AtStartup
$trigger.Delay = 'PT20S'

$principal = New-ScheduledTaskPrincipal -UserId "NT AUTHORITY\SYSTEM" -LogonType ServiceAccount -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Days 0) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -StartWhenAvailable

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Description "OverRadar Live - Niezależny skaner kursów STS i goli uruchamiany w tle przy starcie systemu." -Force

Write-Host "`nPomyślnie zarejestrowano zadanie w systemie!" -ForegroundColor Green
Write-Host "  • Nazwa: $taskName" -ForegroundColor White
Write-Host "  • Wyzwalacz: Przy starcie komputera (AtStartup, opóźnienie 20s na sieć)" -ForegroundColor White
Write-Host "  • Konto: NT AUTHORITY\SYSTEM (Działa ZAWSZE, nawet na ekranie logowania z kłódką)" -ForegroundColor White
Write-Host "  • Uprawnienia: Najwyższe (Highest)" -ForegroundColor White
Write-Host "  • Logi usługi: $workingDir\background_service.log" -ForegroundColor White
Write-Host "==========================================================" -ForegroundColor Cyan

# 4. Uruchomienie zadania od razu
Write-Host "`nUruchamianie zadania w tle..." -ForegroundColor Cyan
Start-ScheduledTask -TaskName $taskName
Start-Sleep -Seconds 3

$state = (Get-ScheduledTask -TaskName $taskName).State
Write-Host "Aktualny stan zadania: $state" -ForegroundColor Green

# 5. Uruchomienie tray_launcher dla zalogowanego użytkownika (w trybie attached)
Write-Host "Uruchamianie ikony w zasobniku systemowym (Tray Launcher)..." -ForegroundColor Cyan
Start-Process $pythonExe "`"$workingDir\tray_launcher.py`"" -WorkingDirectory $workingDir
Write-Host "Gotowe! Skaner działa teraz jako niezależna usługa systemowa." -ForegroundColor Green
