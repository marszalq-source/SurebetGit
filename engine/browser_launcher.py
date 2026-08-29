"""
Moduł inteligentnego wyboru i uruchamiania przeglądarki dla oferty STS Live.
Obsługuje zapamiętywanie wyboru użytkownika i bezpośrednie otwieranie linków z Telegrama i aplikacji.
"""
import os
import sys
import json
import subprocess
import webbrowser
from typing import List, Dict, Any, Optional

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_DIR = os.path.join(BASE_DIR, "config")
PREF_FILE = os.path.join(CONFIG_DIR, "browser_pref.json")

class BrowserLauncher:
    def __init__(self):
        os.makedirs(CONFIG_DIR, exist_ok=True)
        self.browsers = self._detect_installed_browsers()

    def _detect_installed_browsers(self) -> List[Dict[str, Any]]:
        local_app_data = os.environ.get('LOCALAPPDATA', '')
        prog_files = os.environ.get('ProgramFiles', 'C:\\Program Files')
        prog_files_x86 = os.environ.get('ProgramFiles(x86)', 'C:\\Program Files (x86)')
        
        candidates = {
            'chrome': {
                'name': 'Google Chrome',
                'icon': '🌐',
                'badge': 'Chrome',
                'paths': [
                    os.path.join(prog_files, 'Google', 'Chrome', 'Application', 'chrome.exe'),
                    os.path.join(prog_files_x86, 'Google', 'Chrome', 'Application', 'chrome.exe'),
                    os.path.join(local_app_data, 'Google', 'Chrome', 'Application', 'chrome.exe'),
                ]
            },
            'brave': {
                'name': 'Brave Browser',
                'icon': '🦁',
                'badge': 'Brave',
                'paths': [
                    os.path.join(prog_files, 'BraveSoftware', 'Brave-Browser', 'Application', 'brave.exe'),
                    os.path.join(local_app_data, 'BraveSoftware', 'Brave-Browser', 'Application', 'brave.exe'),
                ]
            },
            'edge': {
                'name': 'Microsoft Edge',
                'icon': '🌊',
                'badge': 'Edge',
                'paths': [
                    os.path.join(prog_files_x86, 'Microsoft', 'Edge', 'Application', 'msedge.exe'),
                    os.path.join(prog_files, 'Microsoft', 'Edge', 'Application', 'msedge.exe'),
                ]
            },
            'firefox': {
                'name': 'Mozilla Firefox',
                'icon': '🦊',
                'badge': 'Firefox',
                'paths': [
                    os.path.join(prog_files, 'Mozilla Firefox', 'firefox.exe'),
                    os.path.join(prog_files_x86, 'Mozilla Firefox', 'firefox.exe'),
                ]
            },
            'opera': {
                'name': 'Opera / Opera GX',
                'icon': '🔴',
                'badge': 'Opera',
                'paths': [
                    os.path.join(local_app_data, 'Programs', 'Opera', 'opera.exe'),
                    os.path.join(local_app_data, 'Programs', 'Opera GX', 'opera.exe'),
                ]
            }
        }
        
        found = []
        for bid, data in candidates.items():
            exe_path = None
            for p in data['paths']:
                if os.path.exists(p):
                    exe_path = p
                    break
            if exe_path:
                found.append({
                    'id': bid,
                    'name': data['name'],
                    'icon': data['icon'],
                    'badge': data['badge'],
                    'path': exe_path,
                    'is_installed': True
                })
                
        found.append({
            'id': 'default',
            'name': 'Domyślna przeglądarka systemowa',
            'icon': '💻',
            'badge': 'Domyślna Windows',
            'path': 'default',
            'is_installed': True
        })
        return found

    def get_saved_preference(self) -> Optional[Dict[str, Any]]:
        if os.path.exists(PREF_FILE):
            try:
                with open(PREF_FILE, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    if data.get("browser_id"):
                        return data
            except Exception:
                pass
        return None

    def save_preference(self, browser_id: str, remember: bool = True) -> bool:
        try:
            with open(PREF_FILE, 'w', encoding='utf-8') as f:
                json.dump({
                    "browser_id": browser_id,
                    "remember": remember
                }, f, indent=2, ensure_ascii=False)
            return True
        except Exception as e:
            print(f"[BrowserLauncher] Błąd zapisu preferencji: {e}")
            return False

    def reset_preference(self) -> bool:
        try:
            if os.path.exists(PREF_FILE):
                os.remove(PREF_FILE)
            return True
        except Exception:
            return False

    def launch(self, target_url: str, browser_id: Optional[str] = None) -> bool:
        pref = self.get_saved_preference()
        bid = browser_id or (pref.get("browser_id") if pref else None)
        
        if not bid:
            return False

        if bid == 'default':
            webbrowser.open(target_url)
            return True

        target_b = next((b for b in self.browsers if b['id'] == bid), None)
        if target_b and target_b.get('path') and target_b['path'] != 'default' and os.path.exists(target_b['path']):
            try:
                subprocess.Popen([target_b['path'], target_url], close_fds=True)
                return True
            except Exception as ex:
                print(f"[BrowserLauncher] Błąd uruchomienia {bid}: {ex}")
                webbrowser.open(target_url)
                return True
        else:
            webbrowser.open(target_url)
            return True

    def render_chooser_html(self, target_url: str) -> str:
        options_html = ""
        for b in self.browsers:
            bid = b['id']
            name = b['name']
            icon = b['icon']
            badge = b['badge']
            options_html += f"""
            <div class="browser-card" onclick="chooseBrowser('{bid}')">
                <div class="browser-icon">{icon}</div>
                <div class="browser-info">
                    <div class="browser-name">{name}</div>
                    <div class="browser-badge">{badge}</div>
                </div>
                <div class="browser-action">Wybierz ➔</div>
            </div>
            """

        return f"""<!DOCTYPE html>
<html lang="pl">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Wybierz przeglądarkę – STS Live Goal Scanner</title>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700;800&display=swap" rel="stylesheet">
    <style>
        * {{ box-sizing: border-box; margin: 0; padding: 0; font-family: 'Inter', sans-serif; }}
        body {{
            background: #0d1424;
            color: #ffffff;
            display: flex;
            align-items: center;
            justify-content: center;
            min-height: 100vh;
            padding: 20px;
        }}
        .modal-box {{
            background: #131d2d;
            border: 1px solid #23344e;
            border-radius: 16px;
            width: 100%;
            max-width: 520px;
            padding: 32px;
            box-shadow: 0 16px 40px rgba(0, 0, 0, 0.6);
            text-align: center;
        }}
        .logo-badge {{
            display: inline-block;
            background: rgba(255, 214, 0, 0.12);
            color: #ffd600;
            border: 1px solid #ffd600;
            font-size: 11px;
            font-weight: 800;
            padding: 4px 12px;
            border-radius: 20px;
            margin-bottom: 16px;
            text-transform: uppercase;
            letter-spacing: 1px;
        }}
        h1 {{
            font-size: 22px;
            font-weight: 800;
            margin-bottom: 8px;
            color: #ffffff;
        }}
        p.subtitle {{
            font-size: 13px;
            color: #8da2c0;
            margin-bottom: 24px;
            line-height: 1.5;
        }}
        .browser-list {{
            display: flex;
            flex-direction: column;
            gap: 12px;
            margin-bottom: 24px;
            text-align: left;
        }}
        .browser-card {{
            background: #182438;
            border: 1px solid #283a54;
            border-radius: 12px;
            padding: 14px 18px;
            display: flex;
            align-items: center;
            cursor: pointer;
            transition: all 0.2s ease;
        }}
        .browser-card:hover {{
            background: #20314b;
            border-color: #ffd600;
            transform: translateY(-2px);
            box-shadow: 0 4px 16px rgba(255, 214, 0, 0.15);
        }}
        .browser-icon {{
            font-size: 26px;
            margin-right: 16px;
        }}
        .browser-info {{
            flex: 1;
        }}
        .browser-name {{
            font-size: 15px;
            font-weight: 700;
            color: #ffffff;
        }}
        .browser-badge {{
            font-size: 11px;
            color: #8da2c0;
            margin-top: 2px;
        }}
        .browser-action {{
            font-size: 13px;
            font-weight: 700;
            color: #ffd600;
        }}
        .remember-container {{
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 8px;
            font-size: 13px;
            color: #cbd5e1;
            cursor: pointer;
            user-select: none;
            padding: 8px;
        }}
        .remember-container input {{
            width: 16px;
            height: 16px;
            accent-color: #ffd600;
            cursor: pointer;
        }}
        .success-overlay {{
            display: none;
            padding: 20px;
        }}
        .success-icon {{ font-size: 48px; margin-bottom: 12px; }}
        .success-title {{ font-size: 18px; font-weight: 800; color: #00e676; margin-bottom: 6px; }}
        .success-sub {{ font-size: 13px; color: #8da2c0; }}
    </style>
</head>
<body>

    <div class="modal-box" id="chooser-box">
        <div class="logo-badge">⚽ STS LIVE SCANNER</div>
        <h1>Wybierz przeglądarkę</h1>
        <p class="subtitle">Wybierz, w której przeglądarce chcesz otwierać ofertę zakładów STS na żywo po kliknięciu sygnału.</p>

        <div class="browser-list">
            {options_html}
        </div>

        <label class="remember-container">
            <input type="checkbox" id="remember-cb" checked>
            <span>Zapamiętaj mój wybór (nie pytaj ponownie)</span>
        </label>
    </div>

    <div class="modal-box success-overlay" id="success-box">
        <div class="success-icon">🚀</div>
        <div class="success-title">Otwieranie oferty STS...</div>
        <div class="success-sub" id="success-desc">Uruchomiono wybraną przeglądarkę. To okno możesz zamknąć.</div>
    </div>

    <script>
        const targetUrl = "{target_url}";

        async function chooseBrowser(browserId) {{
            const remember = document.getElementById('remember-cb').checked;
            
            document.getElementById('chooser-box').style.display = 'none';
            document.getElementById('success-box').style.display = 'block';

            try {{
                await fetch(`/api/browser/choose?id=${{browserId}}&remember=${{remember}}&url=${{encodeURIComponent(targetUrl)}}`);
            }} catch(e) {{
                console.error(e);
            }}

            setTimeout(() => {{
                window.location.href = targetUrl;
            }}, 600);
        }}
    </script>
</body>
</html>"""
