import os
import sys

# Ensure process runs in script directory
os.chdir(os.path.dirname(os.path.abspath(__file__)))

# Log all exceptions and prints to a log file
log_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tray_launcher.log")
log_fp = open(log_file, "a", encoding="utf-8")
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

print(f"\n--- TRAY LAUNCHER START: {sys.executable} at {os.getcwd()} ---")

try:
    if sys.platform == 'win32':
        import ctypes
        _MUTEX_NAME = "Global\\OverRadarLiveScanner_SingleInstance_Mutex"
        _kernel32 = ctypes.windll.kernel32
        _mutex = _kernel32.CreateMutexW(None, False, _MUTEX_NAME)
        if _kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
            print("OverRadar Live instance is ALREADY RUNNING. Exiting duplicate process.")
            sys.exit(0)

    import threading
    import webbrowser
    from PIL import Image, ImageDraw
    import pystray
    from pystray import MenuItem as item

    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    if BASE_DIR not in sys.path:
        sys.path.insert(0, BASE_DIR)

    from sts_live_scanner import LiveApi, run_http_server
    from engine.notifications import is_sound_enabled, set_sound_enabled

    SERVER_URL = "http://127.0.0.1:5050"
    ICON_PATH = os.path.join(BASE_DIR, "assets", "app_icon.png")

    api_instance = None
    server_thread = None

    def get_or_create_icon_image():
        if os.path.exists(ICON_PATH):
            try:
                return Image.open(ICON_PATH)
            except Exception as ex:
                print(f"Error loading icon: {ex}")
                
        width, height = 64, 64
        img = Image.new('RGBA', (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        draw.ellipse([3, 3, width - 4, height - 4], fill='#0d1424', outline='#ffd600', width=4)
        draw.ellipse([16, 16, width - 17, height - 17], fill='#00e676', outline='#ffffff', width=2)
        draw.ellipse([26, 26, width - 27, height - 27], fill='#ffffff')
        return img

    def is_server_already_running(port=5050):
        import socket
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(0.5)
                return s.connect_ex(('127.0.0.1', port)) == 0
        except Exception:
            return False

    def start_inprocess_server():
        global api_instance, server_thread
        if server_thread and server_thread.is_alive():
            return
        
        if is_server_already_running(5050):
            print("Detected background scanner service already running on port 5050.")
            print("Tray launcher operating in attached mode (connecting to background service).")
            return

        def _server_worker():
            global api_instance, server_thread
            try:
                print("Initializing LiveApi in background worker...")
                api_instance = LiveApi()
                print("LiveApi initialized. Running HTTP server on port 5050...")
                run_http_server(5050, api_instance)
            except Exception as e:
                print(f"Error in server worker: {e}")

        server_thread = threading.Thread(target=_server_worker, daemon=False, name="TrayServerThread")
        server_thread.start()
        print("TrayServerThread launched.")

    def on_open_panel(icon, item):
        print("Opening panel:", SERVER_URL)
        webbrowser.open(SERVER_URL)

    def on_open_stats(icon, item):
        print("Opening stats:", f"{SERVER_URL}#stats")
        webbrowser.open(f"{SERVER_URL}#stats")

    def on_restart(icon, item):
        if api_instance:
            try:
                api_instance.refresh_all()
            except Exception as ex:
                print(f"Error refreshing: {ex}")
        else:
            try:
                import urllib.request
                urllib.request.urlopen("http://127.0.0.1:5050/api/scan", timeout=3)
            except Exception as ex:
                print(f"Error refreshing via HTTP: {ex}")
        try:
            icon.notify("Skaner STS Live został odświeżony!", "OverRadar Live")
        except Exception:
            pass

    def on_quit(icon, item):
        print("Quitting tray launcher...")
        icon.stop()
        os._exit(0)

    def get_sound_menu_label(item):
        if is_sound_enabled():
            return "🔇 Wyłącz dźwięk skanera"
        else:
            return "🔊 Włącz dźwięk skanera"

    def on_toggle_sound(icon, item):
        new_state = not is_sound_enabled()
        set_sound_enabled(new_state)
        try:
            import urllib.request
            urllib.request.urlopen(f"http://127.0.0.1:5050/api/sound/toggle?enabled={str(new_state).lower()}", timeout=1)
        except Exception:
            pass
        try:
            icon.update_menu()
        except Exception:
            pass
        status_text = "WŁĄCZONE 🔊" if new_state else "WYŁĄCZONE (Wyciszone) 🔇"
        try:
            icon.notify(f"Dźwięki powiadomień zostały {status_text}", "OverRadar Live – Dźwięk")
        except Exception:
            pass

    def main():
        print("Entering main()...")
        
        menu = pystray.Menu(
            item('⚽ OverRadar Live (Działa w tle)', lambda icon, item: None, enabled=False),
            pystray.Menu.SEPARATOR,
            item('🌐 Otwórz Panel Skanera', on_open_panel, default=True),
            item('📊 Otwórz Dziennik Typera & Stats', on_open_stats),
            pystray.Menu.SEPARATOR,
            item(get_sound_menu_label, on_toggle_sound, checked=lambda item: is_sound_enabled()),
            item('🔄 Odśwież Skaner', on_restart),
            pystray.Menu.SEPARATOR,
            item('❌ Wyłącz i Wyjdź', on_quit)
        )
        
        icon_img = get_or_create_icon_image()
        icon = pystray.Icon(
            "OverRadarLive",
            icon_img,
            "OverRadar Live – STS Goal Scanner (Działa w tle)",
            menu
        )
        
        def _setup_app(icon):
            icon.visible = True
            print("Pystray icon visible. Launching server worker...")
            try:
                icon.notify("OverRadar Live działa w tle i monitoruje mecze na żywo!", "OverRadar Live – STS Scanner")
            except Exception:
                pass
        def _watchdog_loop():
            import time
            while True:
                time.sleep(15)
                try:
                    if not is_server_already_running(5050):
                        print("[Watchdog] Port 5050 is down! Starting in-process server worker...")
                        start_inprocess_server()
                except Exception as ex:
                    print(f"[Watchdog Error] {ex}")

        watchdog_thread = threading.Thread(target=_watchdog_loop, daemon=True, name="TrayWatchdogThread")
        watchdog_thread.start()
        start_inprocess_server()

        print("Running pystray icon with setup callback...")
        try:
            icon.run(setup=_setup_app)
        except Exception as e:
            print(f"Pystray icon exception: {e}")
            start_inprocess_server()

        print("Pystray loop finished; keeping server thread alive indefinitely...")
        while True:
            if server_thread and server_thread.is_alive():
                server_thread.join(timeout=2.0)
            else:
                start_inprocess_server()
                import time
                time.sleep(2.0)

    if __name__ == "__main__":
        main()

except Exception as top_ex:
    import traceback
    traceback.print_exc(file=log_fp)
    print(f"CRITICAL ERROR: {top_ex}")
