# Skrypt usuwania zadania z Harmonogramu Zadan Windows
$taskName = 'OverRadar_Live_Background_Service'

Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "  USUWANIE ZADANIA Z HARMONOGRAMU ZADAN                  " -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan

$task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($task) {
    Write-Host "Zatrzymywanie zadania '$taskName'..." -ForegroundColor Yellow
    Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
    Write-Host "Pomyslnie wyrejestrowano zadanie z systemu Windows." -ForegroundColor Green
} else {
    Write-Host "Zadanie '$taskName' nie bylo zarejestrowane." -ForegroundColor Gray
}

try {
    Write-Host "`nNacisnij dowolny klawisz, aby zakonczyc..." -ForegroundColor Yellow
    $null = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")
} catch {}
