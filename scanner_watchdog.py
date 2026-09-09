#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
OverRadar Live – Scanner Healthcheck & Auto-Restart Watchdog
===========================================================
Cyklicznie weryfikuje dostępność skanera na porcie 5050 (Live HTTP API).
W przypadku braku odpowiedzi (zawieszenie pętli, crash, zacięcie socketu):
1. Wykrywa i natychmiast ubija wiszący proces (taskkill /F /PID).
2. Uwalnia gniazdo sieciowe i muteks systemowy.
3. Automatycznie restartuje `tray_launcher.py` w odłączonym procesie (`pythonw.exe`).
4. Rejestruje wszystkie incydenty w `scanner_watchdog.log`.

Użycie:
  python scanner_watchdog.py              # Uruchomienie w pętli (co 30s)
  python scanner_watchdog.py --once       # Pojedynczy test (np. dla Harmonogramu Zadań Windows)
  python scanner_watchdog.py --interval 15 # Własny interwał w sekundach
"""

import os
import sys
import time
import json
import argparse
import subprocess
import urllib.request
import urllib.error
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_FILE = os.path.join(BASE_DIR, "scanner_watchdog.log")
DEFAULT_PORT = 5050
DEFAULT_INTERVAL = 30
DEFAULT_FAIL_THRESHOLD = 2
HEALTH_URL = f"http://127.0.0.1:{DEFAULT_PORT}/api/telegram/config"
BACKUP_HEALTH_URL = f"http://127.0.0.1:{DEFAULT_PORT}/api/stats?period=1d"

# Obsługa braku konsoli (pythonw.exe na Windows)
if sys.stdout is None or sys.stderr is None:
    try:
        _null_log = open(LOG_FILE, "a", encoding="utf-8", buffering=1)
        if sys.stdout is None:
            sys.stdout = _null_log
        if sys.stderr is None:
            sys.stderr = _null_log
    except Exception:
        pass

def log(msg: str, level: str = "INFO"):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    entry = f"[{ts}] [{level}] {msg}"
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(entry + "\n")
    except Exception:
        pass
    try:
        if sys.stdout is not None:
            try:
                print(entry, flush=True)
            except UnicodeEncodeError:
                print(entry.encode(sys.stdout.encoding or "ascii", errors="replace").decode(sys.stdout.encoding or "ascii"), flush=True)
    except Exception:
        pass

def get_pythonw_path() -> str:
    """Zwraca ścieżkę do bezokienkowego interpretera pythonw.exe."""
    py_dir = os.path.dirname(sys.executable)
    candidates = [
        os.path.join(py_dir, "pythonw.exe"),
        os.path.join(py_dir, "python.exe"),
        sys.executable
    ]
    for c in candidates:
        if os.path.exists(c):
            return c
    return sys.executable

def get_pids_on_port(port: int = DEFAULT_PORT) -> list:
    """Pobiera listę PID procesów nasłuchujących na danym porcie (Windows netstat)."""
    pids = set()
    try:
        cmd = f'netstat -ano | findstr :{port}'
        output = subprocess.check_output(cmd, shell=True, text=True, stderr=subprocess.DEVNULL)
        for line in output.strip().splitlines():
            parts = line.split()
            if len(parts) >= 5 and "LISTENING" in parts[3].upper():
                try:
                    pids.add(int(parts[4]))
                except ValueError:
                    pass
    except Exception:
        pass
    return list(pids)

def get_launcher_pids() -> list:
    """Wyszukuje PID-y procesów Pythona uruchomionych ze skryptem tray_launcher.py."""
    pids = set()
    try:
        cmd = 'wmic process where "name like \'python%\'" get ProcessId,CommandLine /format:csv'
        output = subprocess.check_output(cmd, shell=True, text=True, stderr=subprocess.DEVNULL)
        for line in output.strip().splitlines():
            if "tray_launcher.py" in line:
                parts = line.split(",")
                if len(parts) >= 3:
                    try:
                        pids.add(int(parts[-1].strip()))
                    except ValueError:
                        pass
    except Exception:
        pass
    return list(pids)

def check_health(timeout: float = 6.0) -> bool:
    """
    Weryfikuje responsywność serwera HTTP.
    Odpytuje lekki endpoint /api/telegram/config lub zapasowy /api/stats.
    """
    for url in (HEALTH_URL, BACKUP_HEALTH_URL):
        try:
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "OverRadarWatchdog/1.0", "Accept": "application/json"}
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                if resp.status == 200:
                    return True
        except Exception:
            continue
    return False

def kill_process_tree(pid: int):
    """Zabija proces i jego potomków w systemie Windows."""
    try:
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(pid)],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
    except Exception as e:
        log(f"Błąd podczas zabijania PID {pid}: {e}", level="WARN")

def stop_scanner():
    """Zatrzymuje wszelkie instancje skanera."""
    target_pids = set(get_pids_on_port(DEFAULT_PORT)) | set(get_launcher_pids())
    if not target_pids:
        log("Brak aktywnych procesów skanera do zatrzymania.")
        return

    for pid in target_pids:
        log(f"Zatrzymywanie procesu skanera PID: {pid}...", level="WARN")
        kill_process_tree(pid)

    # Odczekanie na całkowite zwolnienie socketów, procesów i muteksu systemowego
    for _ in range(8):
        time.sleep(0.5)
        if not get_pids_on_port(DEFAULT_PORT) and not get_launcher_pids():
            break
    time.sleep(1.0)

def start_scanner() -> bool:
    """Uruchamia proces tray_launcher.py w tle jako w pełni odłączony proces systemowy."""
    pythonw = get_pythonw_path()
    launcher_script = os.path.join(BASE_DIR, "tray_launcher.py")

    if not os.path.exists(launcher_script):
        log(f"KRYTYCZNY: Nie odnaleziono pliku {launcher_script}!", level="ERROR")
        return False

    log(f"Uruchamianie skanera: {pythonw} {launcher_script} ...")
    try:
        # Flagi Windows do całkowitego odłączenia procesu potomnego
        DETACHED_PROCESS = 0x00000008
        CREATE_NEW_PROCESS_GROUP = 0x00000200
        creation_flags = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP

        subprocess.Popen(
            [pythonw, launcher_script],
            cwd=BASE_DIR,
            creationflags=creation_flags,
            close_fds=True
        )

        # Oczekiwanie na start HTTP serwera (do 15 sekund)
        for attempt in range(15):
            time.sleep(1.0)
            if check_health(timeout=2.0):
                new_pids = get_pids_on_port(DEFAULT_PORT)
                pid_str = f" (PID: {new_pids[0]})" if new_pids else ""
                log(f"✅ Skaner wystartował pomyślnie{pid_str} i odpowiada na porcie {DEFAULT_PORT}!")
                return True

        log("⚠️ Skaner został uruchomiony, ale port 5050 jeszcze nie odpowiedział (może trwać inicjalizacja).", level="WARN")
        return True
    except Exception as e:
        log(f"Błąd uruchamiania skanera: {e}", level="ERROR")
        return False

def restart_scanner():
    """Przeprowadza pełny bezpieczny cykl restartu skanera."""
    log("🔄 Rozpoczynanie procedury restartu skanera...", level="WARN")
    stop_scanner()
    success = start_scanner()
    if success:
        log("🎉 Restart zakończony sukcesem.", level="INFO")
    else:
        log("❌ Restart zakończony niepowodzeniem!", level="ERROR")

def main():
    parser = argparse.ArgumentParser(description="Watchdog skanera OverRadar Live")
    parser.add_argument("--once", action="store_true", help="Wykonaj tylko pojedynczy test i zakończ")
    parser.add_argument("--interval", type=int, default=DEFAULT_INTERVAL, help=f"Interwał sprawdzania w sekundach (domyślnie: {DEFAULT_INTERVAL})")
    parser.add_argument("--threshold", type=int, default=DEFAULT_FAIL_THRESHOLD, help=f"Liczba kolejnych błędów przed restartem (domyślnie: {DEFAULT_FAIL_THRESHOLD})")
    parser.add_argument("--restart", action="store_true", help="Wymuś natychmiastowy restart skanera i zakończ")
    args = parser.parse_args()

    if args.restart:
        restart_scanner()
        return

    log(f"Uruchomiono Watchdog Skanera (port: {DEFAULT_PORT}, interwał: {args.interval}s, próg błędów: {args.threshold})")

    consecutive_failures = 0

    while True:
        is_healthy = check_health(timeout=6.0)

        if is_healthy:
            if consecutive_failures > 0:
                log(f"Status powrócił do normy po {consecutive_failures} błędach.", level="INFO")
            consecutive_failures = 0
            if not args.once:
                time.sleep(args.interval)
            else:
                log(f"Healthcheck OK (port {DEFAULT_PORT} aktywny).")
                sys.exit(0)
        else:
            consecutive_failures += 1
            log(f"⚠️ Healthcheck FAILED ({consecutive_failures}/{args.threshold}) – brak odpowiedzi na porcie {DEFAULT_PORT}", level="WARN")

            if consecutive_failures >= args.threshold:
                log(f"🚨 Przekroczono limit błędów ({consecutive_failures}). Wymuszanie restartu skanera!", level="ERROR")
                restart_scanner()
                consecutive_failures = 0
                time.sleep(5.0)

            if args.once:
                sys.exit(1)

            time.sleep(args.interval)

if __name__ == "__main__":
    main()
