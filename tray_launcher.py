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

    def start_inprocess_server():
        global api_instance, server_thread
        if server_thread and server_thread.is_alive():
            return
        
        def _server_worker():
            global api_instance
            try:
                print("Initializing LiveApi in background worker...")
                api_instance = LiveApi()
                print("LiveApi initialized. Running HTTP server on port 5050...")
                run_http_server(5050, api_instance)
            except Exception as e:
                print(f"Error in server worker: {e}")

        server_thread = threading.Thread(target=_server_worker, daemon=True, name="TrayServerThread")
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
        try:
            icon.notify("Skaner STS Live został odświeżony!", "OverRadar Live")
        except Exception:
            pass

    def on_quit(icon, item):
        print("Quitting tray launcher...")
        icon.stop()
        os._exit(0)

    def main():
        print("Entering main()...")
        
        menu = pystray.Menu(
            item('⚽ OverRadar Live (Działa w tle)', lambda icon, item: None, enabled=False),
            pystray.Menu.SEPARATOR,
            item('🌐 Otwórz Panel Skanera', on_open_panel, default=True),
            item('📊 Otwórz Dziennik Typera & Stats', on_open_stats),
            pystray.Menu.SEPARATOR,
            item('🔄 Odśwież Skaner', on_restart),
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
            start_inprocess_server()

        print("Running pystray icon with setup callback...")
        icon.run(setup=_setup_app)

    if __name__ == "__main__":
        main()

except Exception as top_ex:
    import traceback
    traceback.print_exc(file=log_fp)
    print(f"CRITICAL ERROR: {top_ex}")
