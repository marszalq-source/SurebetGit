"""
OverRadar Live – System Tray Launcher (Windows Notification Area)
Uruchamia skaner STS Live w tle (zasobnik systemowy obok zegarka)
BEZ ŻADNYCH OKIEN I BEZ IKON NA PASKU ZADAŃ.
"""
import os
import sys
import time
import subprocess
import threading
import webbrowser
from PIL import Image, ImageDraw
import pystray
from pystray import MenuItem as item

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SERVER_URL = "http://127.0.0.1:5050"
ICON_PATH = os.path.join(BASE_DIR, "assets", "app_icon.png")

server_proc = None

def get_or_create_icon_image():
    if os.path.exists(ICON_PATH):
        try:
            return Image.open(ICON_PATH)
        except Exception:
            pass
            
    # Fallback dynamic icon
    width, height = 64, 64
    img = Image.new('RGBA', (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse([3, 3, width - 4, height - 4], fill='#0d1424', outline='#ffd600', width=4)
    draw.ellipse([16, 16, width - 17, height - 17], fill='#00e676', outline='#ffffff', width=2)
    draw.ellipse([26, 26, width - 27, height - 27], fill='#ffffff')
    return img

def start_scanner_server():
    global server_proc
    if server_proc and server_proc.poll() is None:
        return
        
    cmd = [sys.executable, os.path.join(BASE_DIR, "sts_live_scanner.py"), "--server"]
    
    # Flagi Windows ukrywające okno konsoli
    CREATE_NO_WINDOW = 0x08000000
    try:
        server_proc = subprocess.Popen(
            cmd,
            cwd=BASE_DIR,
            creationflags=CREATE_NO_WINDOW,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
    except Exception as e:
        print(f"Błąd uruchamiania serwera: {e}")

def stop_scanner_server():
    global server_proc
    if server_proc:
        try:
            server_proc.terminate()
            server_proc.wait(timeout=2.0)
        except Exception:
            try:
                server_proc.kill()
            except Exception:
                pass
        server_proc = None

def on_open_panel(icon, item):
    webbrowser.open(SERVER_URL)

def on_open_stats(icon, item):
    webbrowser.open(f"{SERVER_URL}#stats")

def on_restart(icon, item):
    stop_scanner_server()
    time.sleep(1.0)
    start_scanner_server()
    try:
        icon.notify("Skaner STS Live został zrestartowany pomyślnie!", "OverRadar Live")
    except Exception:
        pass

def on_quit(icon, item):
    stop_scanner_server()
    icon.stop()

def main():
    # 1. Start serwera w tle
    start_scanner_server()
    
    # 2. Utworzenie menu zasobnika
    menu = pystray.Menu(
        item('⚽ OverRadar Live (Aktywny)', lambda icon, item: None, enabled=False),
        pystray.Menu.SEPARATOR,
        item('🌐 Otwórz Panel Skanera', on_open_panel, default=True),
        item('📊 Otwórz Dziennik Typera & Stats', on_open_stats),
        pystray.Menu.SEPARATOR,
        item('🔄 Restartuj Skaner', on_restart),
        item('❌ Wyłącz i Wyjdź', on_quit)
    )
    
    icon_img = get_or_create_icon_image()
    icon = pystray.Icon(
        "OverRadarLive",
        icon_img,
        "OverRadar Live – STS Goal Scanner (Działa w tle)",
        menu
    )
    
    # Uruchomienie pętli ikony w trayu (blokuje wątek główny)
    icon.run()

if __name__ == "__main__":
    main()
