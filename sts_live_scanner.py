import os
import sys
import io

# Zabezpieczenie dla pythonw.exe (brak konsoli stdout/stderr) oraz kodowania UTF-8
if sys.stdout is None or sys.stderr is None:
    try:
        log_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scanner_service.log")
        log_fp = open(log_file, "a", encoding="utf-8", buffering=1)
        if sys.stdout is None:
            sys.stdout = log_fp
        if sys.stderr is None:
            sys.stderr = log_fp
    except Exception:
        pass
elif hasattr(sys.stdout, 'buffer'):
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    except Exception:
        pass

if sys.platform == 'win32':
    try:
        import ctypes
        # SEM_FAILCRITICALERRORS (0x0001) | SEM_NOGPFAULTERRORBOX (0x0002)
        ctypes.windll.kernel32.SetErrorMode(0x0001 | 0x0002)
    except Exception:
        pass

import json
import threading
from http.server import SimpleHTTPRequestHandler
try:
    from http.server import ThreadingHTTPServer
except ImportError:
    from socketserver import ThreadingMixIn
    from http.server import HTTPServer
    class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
        daemon_threads = True

import urllib.parse


from engine.sts_flashscore_aggregator import STSFlashscoreAggregator
from engine.prematch_scanner import PrematchScanner
from engine.bet_tracker import BetTracker
from engine.browser_launcher import BrowserLauncher
from sts_live_config import TRIGGERS_CONFIG, SCAN_INTERVAL_SECONDS

# Klasa API dla interfejsu (PyWebView + HTTP API)
class LiveApi:
    def __init__(self):
        self.aggregator = STSFlashscoreAggregator()
        self.prematch = PrematchScanner()
        self.tracker = BetTracker()
        self.browser_launcher = BrowserLauncher()

    def get_browser_config(self):
        return {
            "browsers": self.browser_launcher.browsers,
            "preference": self.browser_launcher.get_saved_preference()
        }

    def set_browser_preference(self, browser_id, remember=True):
        return {"success": self.browser_launcher.save_preference(str(browser_id), bool(remember))}

    def reset_browser_preference(self):
        return {"success": self.browser_launcher.reset_preference()}


    def scan_live(self, only_signals=False, min_minute=0, half_filter="ALL", demo_mode=False):
        try:
            return self.aggregator.scan_all(
                only_signals=bool(only_signals),
                min_minute=int(min_minute),
                half_filter=str(half_filter),
                demo_mode=bool(demo_mode)
            )
        except Exception as e:
            print(f"[API Error Live] {e}")
            return {"error": str(e), "matches": []}

    def scan_prematch(self, country="ALL", day_offset=0, time_filter="ALL", min_rating=40):
        try:
            return self.prematch.scan_upcoming(
                country_filter=str(country),
                day_offset=int(day_offset),
                time_filter=str(time_filter),
                min_rating=int(min_rating)
            )
        except Exception as e:
            print(f"[API Error Prematch] {e}")
            return {"error": str(e), "matches": []}

    def toggle_watchlist(self, match_id, match_info=None):
        try:
            return self.prematch.toggle_watchlist(str(match_id), match_info)
        except Exception as e:
            print(f"[API Error Watchlist] {e}")
            return False

    def get_watchlist(self):
        try:
            return self.prematch.get_watchlist_matches()
        except Exception as e:
            return []

    # === Tracker API ===
    def get_tracker_summary(self):
        try:
            return self.tracker.get_summary()
        except Exception as e:
            print(f"[API Error Tracker Summary] {e}")
            return {}

    def add_tracker_bet(self, match_title, market, stake, odds, notes=""):
        try:
            return self.tracker.add_bet(str(match_title), str(market), float(stake), float(odds), str(notes))
        except Exception as e:
            print(f"[API Error Tracker Add] {e}")
            return self.tracker.get_summary()

    def add_tracker_ako_bet(self, legs, stake, notes=""):
        try:
            if isinstance(legs, str):
                legs = json.loads(legs)
            return self.tracker.add_ako_bet(legs, float(stake), str(notes))
        except Exception as e:
            print(f"[API Error Tracker Add AKO] {e}")
            return self.tracker.get_summary()

    def resolve_tracker_bet(self, bet_id, status):
        try:
            return self.tracker.resolve_bet(str(bet_id), str(status))
        except Exception as e:
            print(f"[API Error Tracker Resolve] {e}")
            return self.tracker.get_summary()

    def delete_tracker_bet(self, bet_id):
        try:
            return self.tracker.delete_bet(str(bet_id))
        except Exception as e:
            print(f"[API Error Tracker Delete] {e}")
            return self.tracker.get_summary()

    def update_bankroll(self, amount):
        try:
            return self.tracker.update_bankroll(float(amount))
        except Exception as e:
            print(f"[API Error Bankroll Update] {e}")
            return self.tracker.get_summary()

    def get_telegram_config(self):
        try:
            from engine.telegram_notifier import TelegramNotifier
            return TelegramNotifier().load_config()
        except Exception as e:
            return {"error": str(e)}

    def save_telegram_config(self, token, chat_id, enabled=True, min_stars=2):
        try:
            from engine.telegram_notifier import TelegramNotifier
            cfg = {
                "bot_token": str(token),
                "chat_id": str(chat_id),
                "enabled": bool(enabled),
                "min_stars": int(min_stars)
            }
            return {"success": TelegramNotifier().save_config(cfg)}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def test_telegram(self):
        try:
            from engine.telegram_notifier import TelegramNotifier
            return TelegramNotifier().send_test_message()
        except Exception as e:
            return {"success": False, "error": str(e)}

    def get_telegram_pairing(self):
        try:
            from engine.telegram_notifier import TelegramNotifier
            return TelegramNotifier().get_pairing_info()
        except Exception as e:
            return {"error": str(e)}

    def set_telegram_pin(self, pin):
        try:
            from engine.telegram_notifier import TelegramNotifier
            tg = TelegramNotifier()
            tg.subscribers_data["pairing_pin"] = str(pin).strip()
            tg._save_subscribers_data(tg.subscribers_data)
            return {"success": True, "pin": pin}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def remove_telegram_subscriber(self, chat_id):
        try:
            from engine.telegram_notifier import TelegramNotifier
            tg = TelegramNotifier()
            subs = tg.subscribers_data.get("subscribers", [])
            tg.subscribers_data["subscribers"] = [s for s in subs if str(s.get("chat_id")) != str(chat_id)]
            tg._save_subscribers_data(tg.subscribers_data)
            return {"success": True}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def get_signal_stats(self, period="30d"):
        try:
            from engine.stats_engine import StatsEngine
            return StatsEngine().get_stats(str(period))
        except Exception as e:
            return {"error": str(e)}

    def get_signal_history(self):
        try:
            from engine.stats_engine import StatsEngine
            return StatsEngine().load_history()
        except Exception as e:
            return []

    def get_config(self):
        return TRIGGERS_CONFIG



# Lekki lokalny serwer HTTP dla obsługi w przeglądarce i pywebview
class CustomHTTPHandler(SimpleHTTPRequestHandler):
    api_instance = None
    web_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web_live")

    def translate_path(self, path):
        # Usuń parametry query
        path = path.split('?', 1)[0].split('#', 1)[0]
        if path == "/" or path == "":
            return os.path.join(self.web_dir, "index.html")
        
        rel_path = path.lstrip('/')
        full_path = os.path.join(self.web_dir, rel_path)
        return full_path

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)

        # 0. Inteligentne otwieranie linków / Wybór przeglądarki
        if parsed.path in ('/open', '/launch'):
            target_url = params.get('url', params.get('target', ['https://www.sts.pl/live/pilka-nozna']))[0]
            pref = self.api_instance.browser_launcher.get_saved_preference()
            
            if pref and pref.get('browser_id'):
                bid = pref['browser_id']
                if bid != 'default':
                    self.api_instance.browser_launcher.launch(target_url, browser_id=bid)
                # Natychmiastowe przekierowanie HTTP 302 do meczu STS (okno nie znika, przechodzi wprost do oferty)
                self.send_response(302)
                self.send_header('Location', target_url)
                self.end_headers()
                return
            else:
                html = self.api_instance.browser_launcher.render_chooser_html(target_url)
                self.send_response(200)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.end_headers()
                self.wfile.write(html.encode('utf-8'))
                return


        elif parsed.path == '/api/browser/choose':
            bid = params.get('id', ['default'])[0]
            rem = params.get('remember', ['true'])[0].lower() == 'true'
            target_url = params.get('url', ['https://www.sts.pl/live/pilka-nozna'])[0]
            
            if rem:
                self.api_instance.browser_launcher.save_preference(bid, remember=True)
            self.api_instance.browser_launcher.launch(target_url, browser_id=bid)
            self._send_json({'success': True, 'browser_id': bid})
            return

        elif parsed.path == '/api/browser/config':
            res = self.api_instance.get_browser_config()
            self._send_json(res)
            return

        elif parsed.path == '/api/browser/reset':
            res = self.api_instance.reset_browser_preference()
            self._send_json(res)
            return

        # 1. Endpoint Live
        elif parsed.path == '/api/scan':
            only_signals = params.get('only_signals', ['false'])[0].lower() == 'true'
            half = params.get('half', ['ALL'])[0]
            demo = params.get('demo', ['false'])[0].lower() == 'true'
            
            result = self.api_instance.scan_live(only_signals=only_signals, half_filter=half, demo_mode=demo)
            self._send_json(result)
            return


        # 2. Endpoint Prematch
        elif parsed.path == '/api/prematch':
            country = params.get('country', ['ALL'])[0]
            day = int(params.get('day', ['0'])[0])
            time_f = params.get('time_filter', ['ALL'])[0]
            min_r = int(params.get('min_rating', ['40'])[0])
            
            result = self.api_instance.scan_prematch(country=country, day_offset=day, time_filter=time_f, min_rating=min_r)
            self._send_json(result)
            return

        # 3. Endpoint Watchlist
        elif parsed.path == '/api/watchlist':
            result = self.api_instance.get_watchlist()
            self._send_json(result)
            return

        elif parsed.path == '/api/watchlist/toggle':
            m_id = params.get('id', [''])[0]
            status = self.api_instance.toggle_watchlist(m_id)
            self._send_json({'id': m_id, 'is_watched': status})
            return

        # 4. Endpointy Dziennika Typera (Bet Tracker)
        elif parsed.path == '/api/tracker':
            result = self.api_instance.get_tracker_summary()
            self._send_json(result)
            return

        elif parsed.path == '/api/tracker/add':
            match_t = params.get('match', ['Kupon Live'])[0]
            market_t = params.get('market', ['Over 0.5 HT'])[0]
            stake_v = float(params.get('stake', ['4.0'])[0])
            odds_v = float(params.get('odds', ['1.80'])[0])
            notes_v = params.get('notes', [''])[0]
            result = self.api_instance.add_tracker_bet(match_t, market_t, stake_v, odds_v, notes_v)
            self._send_json(result)
            return

        elif parsed.path == '/api/tracker/add_ako':
            legs_raw = params.get('legs', ['[]'])[0]
            try:
                legs_list = json.loads(legs_raw)
            except Exception:
                legs_list = []
            stake_v = float(params.get('stake', ['4.0'])[0])
            notes_v = params.get('notes', [''])[0]
            result = self.api_instance.add_tracker_ako_bet(legs_list, stake_v, notes_v)
            self._send_json(result)
            return

        elif parsed.path == '/api/tracker/resolve':
            bet_id = params.get('id', [''])[0]
            status = params.get('status', ['WON'])[0]
            result = self.api_instance.resolve_tracker_bet(bet_id, status)
            self._send_json(result)
            return

        elif parsed.path == '/api/tracker/delete':
            bet_id = params.get('id', [''])[0]
            result = self.api_instance.delete_tracker_bet(bet_id)
            self._send_json(result)
            return

        elif parsed.path == '/api/tracker/bankroll':
            amt = float(params.get('amount', ['50.0'])[0])
            result = self.api_instance.update_bankroll(amt)
            self._send_json(result)
            return

        # 5. Endpointy Telegram
        elif parsed.path == '/api/telegram/config':
            result = self.api_instance.get_telegram_config()
            self._send_json(result)
            return

        elif parsed.path == '/api/telegram/save':
            token = params.get('token', [''])[0]
            chat_id = params.get('chat_id', [''])[0]
            enabled = params.get('enabled', ['true'])[0].lower() == 'true'
            min_stars = int(params.get('min_stars', ['2'])[0])
            result = self.api_instance.save_telegram_config(token, chat_id, enabled, min_stars)
            self._send_json(result)
            return

        elif parsed.path == '/api/telegram/test':
            result = self.api_instance.test_telegram()
            self._send_json(result)
            return

        elif parsed.path == '/api/telegram/pairing':
            result = self.api_instance.get_telegram_pairing()
            self._send_json(result)
            return

        elif parsed.path == '/api/telegram/set_pin':
            pin = params.get('pin', ['7777'])[0]
            result = self.api_instance.set_telegram_pin(pin)
            self._send_json(result)
            return

        elif parsed.path == '/api/telegram/remove_sub':
            cid = params.get('chat_id', [''])[0]
            result = self.api_instance.remove_telegram_subscriber(cid)
            self._send_json(result)
            return

        # 5. Endpointy Statystyk Sygnałów
        elif parsed.path == '/api/stats':
            period = params.get('period', ['30d'])[0]
            result = self.api_instance.get_signal_stats(period)
            self._send_json(result)
            return

        elif parsed.path == '/api/stats/history':
            result = self.api_instance.get_signal_history()
            self._send_json({'history': result})
            return

        elif parsed.path == '/api/debug':
            try:
                from engine.sts_live_engine import STSLiveEngine
                from engine.flashscore_engine import FlashscoreEngine
                sts = STSLiveEngine()
                fs = FlashscoreEngine()
                sts_m = sts.fetch_live_matches(include_esports=False)
                fs_m = fs.get_live_soccer_matches()
                self._send_json({
                    'status': 'ok',
                    'sts_matches_count': len(sts_m),
                    'fs_matches_count': len(fs_m),
                    'sts_sample': [f"{m.get('home_team')} vs {m.get('away_team')}" for m in sts_m[:3]],
                    'fs_sample': [f"{m.get('home_team')} vs {m.get('away_team')}" for m in fs_m[:3]],
                })
            except Exception as e:
                import traceback
                self._send_json({'status': 'error', 'error': str(e), 'trace': traceback.format_exc()})
            return


        super().do_GET()

    def _send_json(self, data):
        self.send_response(200)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode('utf-8'))

    def log_message(self, format, *args):
        # Wyciszenie logów requestów statycznych
        return


def run_http_server(port=5050, api_inst=None):
    CustomHTTPHandler.api_instance = api_inst
    server = ThreadingHTTPServer(('0.0.0.0', port), CustomHTTPHandler)
    print(f"  [Server] 🌐 Serwer Live Scanner uruchomiony na: http://0.0.0.0:{port}")
    server.serve_forever()



def main():
    print("=" * 65)
    print("  ⚽ STS LIVE GOAL SCANNER (STS + Flashscore) ⚽")
    print("  Strategie: Over 0.5 / 1.5 HT & Over 0.5 2H / 1.5 FT")
    print("=" * 65)

    api = LiveApi()
    port = int(os.environ.get("PORT", 5050))

    # Start lokalnego serwera w tle
    server_thread = threading.Thread(target=run_http_server, args=(port, api), daemon=True)
    server_thread.start()

    # Sprawdzenie trybu --cli / --server
    if "--server" in sys.argv:
        print(f"Uruchomiono w trybie serwera. Wejdź na http://127.0.0.1:{port}")
        server_thread.join()
        return

    # Uruchomienie okna natywnego PyWebView
    try:
        import webview
        base_dir = os.path.dirname(os.path.abspath(__file__))
        html_path = os.path.join(base_dir, "web_live", "index.html")

        window = webview.create_window(
            title='STS LIVE GOAL SCANNER ⚽ (Flashscore In-Play + STS Odds)',
            url=f'http://127.0.0.1:{port}',
            js_api=api,
            width=1240,
            height=860,
            resizable=True,
            min_size=(950, 650)
        )
        webview.start(debug=False)

    except Exception as e:
        print(f"[PyWebView Info] {e}")
        print(f"Otwórz aplikację w przeglądarce: http://127.0.0.1:{port}")
        import webbrowser
        webbrowser.open(f'http://127.0.0.1:{port}')
        server_thread.join()


if __name__ == '__main__':
    main()
