@echo off
cd /d "%~dp0"
echo ========================================================
echo   Trwa wysylanie zmian na GitHub...
echo ========================================================
echo.
git push origin main
echo.
pause
