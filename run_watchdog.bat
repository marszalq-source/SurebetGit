@echo off
title OverRadar Live Watchdog (Port 5050)
cd /d "%~dp0"
echo ============================================================
echo   OverRadar Live - Watchdog Monitor (Port 5050)
echo ============================================================
python scanner_watchdog.py
pause
