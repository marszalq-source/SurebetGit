@echo off
cd /d "%~dp0"
echo ========================================================
echo   Trwa wysylanie zmian na GitHub...
echo ========================================================
echo.
"C:\Users\Technolog\AppData\Local\github-copilot-git-2.53.0-3\cmd\git.exe" push origin main
echo.
pause
