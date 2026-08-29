@echo off
title STS LIVE GOAL SCANNER
chcp 65001 >nul
echo ========================================================
echo   ⚽ URUCHAMIANIE STS LIVE GOAL SCANNER ⚽
echo ========================================================
echo.
echo  Trwa uruchamianie silnika skanera live i interfejsu...
echo  Aplikacja otworzy sie automatycznie w oknie / przegladarce.
echo.
python sts_live_scanner.py
if errorlevel 1 (
    echo.
    echo [Blad]: Nie udalo sie uruchomic programu.
    pause
)
