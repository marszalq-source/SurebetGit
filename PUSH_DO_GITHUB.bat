@echo off
cd /d "%~dp0"
echo ========================================================
echo   Trwa wysylanie zmian na GitHub...
echo ========================================================
echo.
set PATH=%PATH%;C:\Users\Technolog\AppData\Local\github-copilot-git-2.53.0-3\cmd
git add .
git commit -m "Aktualizacja OverRadar Live: optymalizacja podatkowa 12%%, model Poissona k-goli, usluga tla Task Scheduler"
git push origin main
echo.
pause
