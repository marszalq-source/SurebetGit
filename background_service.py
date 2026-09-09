#!/usr/bin/env python3
"""
OverRadar Live - Niezależna Usługa Tła (Background Service)
Przeznaczona do uruchamiania w Harmonogramie Zadań Windows (Task Scheduler)
przy starcie komputera (AtStartup), niezależnie od tego, czy użytkownik jest zalogowany.
"""
import os
import sys
import time
import socket
import threading
import traceback

# Ustawienie katalogu roboczego
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
os.chdir(BASE_DIR)
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

# Plik logów usługi
LOG_FILE = os.path.join(BASE_DIR, "background_service.log")
log_fp = open(LOG_FILE, "a", encoding="utf-8")

class FlushLogger:
    def __init__(self, fp):
        self.fp = fp
    def write(self, msg):
        self.fp.write(msg)
        self.fp.flush()
    def flush(self):
        self.fp.flush()

sys.stdout = FlushLogger(log_fp)
sys.stderr = FlushLogger(log_fp)

def log(msg: str):
    now_str = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{now_str}] [Service] {msg}")

def wait_for_network(max_wait_seconds=60):
    """Czeka na dostępność sieci po starcie systemu."""
    log("Oczekiwanie na dostępność sieci po starcie systemu...")
    start_time = time.time()
    while time.time() - start_time < max_wait_seconds:
        try:
            # Sprawdzenie rozwiązania DNS lub połączenia TCP
            socket.gethostbyname("flashscore.com")
            log("Sieć jest aktywna i dostępna (DNS OK).")
            return True
        except Exception:
            time.sleep(3)
    log("Ostrzeżenie: Przekroczono czas oczekiwania na sieć (60s). Próba uruchomienia skanera...")
    return False

def check_port_in_use(port=5050):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1.0)
        return s.connect_ex(('127.0.0.1', port)) == 0

def main():
    log("=" * 60)
    log("ROZRUCH USŁUGI TŁA OVERRADAR LIVE (SYSTEM TASK)")
    log(f"Python: {sys.executable} | Katalog: {BASE_DIR}")
    log("=" * 60)

    # 1. Sprawdzenie czy port 5050 jest już zajęty
    if check_port_in_use(5050):
        log("BŁĄD: Port 5050 jest już zajęty przez inną instancję. Zamykanie usługi tła.")
        sys.exit(1)

    # 2. Oczekiwanie na sieć
    wait_for_network(max_wait_seconds=60)

    # 3. Inicjalizacja skanera i serwera HTTP
    try:
        from sts_live_scanner import LiveApi, run_http_server

        log("Inicjalizacja LiveApi (silnik STS, Flashscore, Telegram, Triggery)...")
        api = LiveApi()
        log("LiveApi zainicjalizowany pomyślnie.")

        port = int(os.environ.get("PORT", 5050))
        log(f"Uruchamianie serwera HTTP na porcie {port}...")
        
        # Uruchomienie serwera HTTP (blokujące w głównym wątku)
        run_http_server(port=port, api_inst=api)

    except Exception as ex:
        log(f"KRYTYCZNY BŁĄD USŁUGI: {ex}")
        traceback.print_exc(file=log_fp)
        sys.exit(1)

if __name__ == "__main__":
    main()
