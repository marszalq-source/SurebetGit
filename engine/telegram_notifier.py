import os
import re
import json
import time
import datetime
import random
import threading
import urllib.parse
import urllib3
from typing import Dict, Any, Optional, List, Tuple
from engine.stats_engine import StatsEngine
from engine.bet_analytix_sync import BetAnalytixSync


CONFIG_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "telegram_config.json")
CARDS_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "telegram_active_cards.json")
SUBSCRIBERS_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "telegram_subscribers.json")

class TelegramNotifier:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(TelegramNotifier, cls).__new__(cls)
            cls._instance._init_notifier()
        return cls._instance

    def _init_notifier(self):
        self._cards_lock = threading.RLock()
        self.config = self.load_config()
        self.active_match_cards, self.settled_matches = self._load_cards()
        self.subscribers_data = self._load_subscribers()
        self.stats_engine = StatsEngine()
        self.ba_sync = BetAnalytixSync()
        self._last_update_id = 0
        self._poll_lock = threading.Lock()
        self._processed_update_ids = set()
        self._http = urllib3.PoolManager(
            maxsize=10,
            timeout=urllib3.Timeout(connect=5.0, read=25.0),
            retries=False
        )
        self.setup_bot_menu_commands()
        self.start_polling_thread()
        # Automatyczne rozliczenie zaległych kart po starcie systemu
        threading.Thread(target=self._startup_settlement_check, daemon=True, name="StartupSettlementWorker").start()

    def setup_bot_menu_commands(self):
        """Rejestruje oficjalne komendy w menu bocznym Telegrama (Menu Button)."""
        token = self.config.get("bot_token", "").strip()
        if not token:
            return
        commands = [
            {"command": "stats", "description": "📊 Statystyki skuteczności (dzień, tydzień, ...)"},
            {"command": "sniper", "description": "🎯 Włącz / Wyłącz Tryb Snajper (VIP)"},
            {"command": "ba", "description": "📈 Bet-Analytix (Status i synchronizacja)"},
            {"command": "komendy", "description": "👑 Centrum dowodzenia i lista komend"},
            {"command": "start", "description": "🚀 Status konta i powitanie"},
            {"command": "mojekonto", "description": "👤 Twoja subskrypcja i ważność"},
            {"command": "kup", "description": "💎 Oferta i cennik VIP"}
        ]
        url = f"https://api.telegram.org/bot{token}/setMyCommands"
        try:
            payload = json.dumps({"commands": commands}).encode('utf-8')
            self._http.request(
                "POST",
                url,
                body=payload,
                headers={"Content-Type": "application/json", "User-Agent": "SurebetScanner/2.0"},
                timeout=5.0
            )
        except Exception:
            pass

    def start_polling_thread(self):
        """Uruchamia ciągły wątek nasłuchujący poleceń z Telegrama (reakcja natychmiastowa via Long Polling)."""
        if getattr(self, '_polling_started', False):
            return
        self._polling_started = True

        def _worker():
            # Synchronizacja offsetu na starcie, aby nie przetwarzać przestarzałych update'ów
            token = self.config.get("bot_token", "").strip()
            if token and self._last_update_id == 0:
                try:
                    init_payload = {
                        "offset": -1,
                        "timeout": 0,
                        "allowed_updates": ["message", "edited_message", "channel_post", "edited_channel_post", "callback_query"]
                    }
                    r = self._http.request(
                        "POST",
                        f"https://api.telegram.org/bot{token}/getUpdates",
                        body=json.dumps(init_payload).encode('utf-8'),
                        headers={"Content-Type": "application/json", "User-Agent": "SurebetScanner/2.0"},
                        timeout=5.0
                    )
                    d = json.loads(r.data.decode('utf-8'))
                    if d.get("ok") and d.get("result"):
                        self._last_update_id = d["result"][-1]["update_id"]
                except Exception:
                    pass

            while True:
                try:
                    self.poll_pairing_requests()
                except Exception as ex:
                    print(f"[Telegram Polling Worker] Błąd w pętli: {ex}")
                    time.sleep(1.0)

        t = threading.Thread(target=_worker, daemon=True, name="TelegramLiveWorker")
        t.start()


    def load_config(self) -> Dict[str, Any]:
        default_cfg = {
            "enabled": False,
            "bot_token": "",
            "chat_id": "",
            "min_stars": 2,
            "notify_signals": True,
            "notify_daily_goal": True,
            "live_update_mode": True
        }
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    default_cfg.update(data)
            except Exception as e:
                print(f"[Telegram] Błąd odczytu config: {e}")
        return default_cfg

    def save_config(self, new_cfg: Dict[str, Any]) -> bool:
        try:
            self.config.update(new_cfg)
            with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
                json.dump(self.config, f, indent=2, ensure_ascii=False)
            return True
        except Exception as e:
            print(f"[Telegram] Błąd zapisu config: {e}")
            return False

    def _load_subscribers(self) -> Dict[str, Any]:
        default_sub = {
            "pairing_pin": "7777",
            "require_pin": True,
            "bot_username": "OverRadarLive_bot",
            "subscribers": []
        }
        if os.path.exists(SUBSCRIBERS_FILE):
            try:
                with open(SUBSCRIBERS_FILE, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    default_sub.update(data)
            except Exception as e:
                print(f"[Telegram] Błąd odczytu subskrybentów: {e}")
        else:
            main_id = self.config.get("chat_id")
            if main_id:
                default_sub["subscribers"].append({
                    "chat_id": str(main_id),
                    "first_name": "Główne Urządzenie",
                    "joined_at": time.strftime('%Y-%m-%d %H:%M:%S')
                })
            self._save_subscribers_data(default_sub)
        return default_sub

    def _save_subscribers_data(self, data: Dict[str, Any]):
        try:
            with open(SUBSCRIBERS_FILE, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"[Telegram] Błąd zapisu subskrybentów: {e}")

    def get_all_chat_ids(self) -> List[str]:
        """Zwraca listę AKTYWNYCH chat ID (Admini, aktywny VIP, aktywny Trial)."""
        ids = set()
        admins = set(str(a) for a in self.subscribers_data.get("admins", []))
        main_id = self.config.get("chat_id", "").strip()
        if main_id:
            ids.add(str(main_id))
            admins.add(str(main_id))

        now_dt = datetime.datetime.now()
        subs = self.subscribers_data.get("subscribers", [])
        changed = False

        for sub in subs:
            cid = str(sub.get("chat_id", "")).strip()
            if not cid:
                continue

            role = sub.get("role", "VIP")
            # Admin zawsze ma dostęp dożywotni
            if cid in admins or role == "ADMIN":
                ids.add(cid)
                continue

            # Sprawdzenie daty ważności subskrypcji
            exp_str = sub.get("expires_at")
            if not exp_str:
                ids.add(cid)
                continue

            try:
                exp_dt = datetime.datetime.strptime(exp_str, '%Y-%m-%d %H:%M:%S')
                if now_dt <= exp_dt:
                    ids.add(cid)
                else:
                    if sub.get("role") != "EXPIRED":
                        sub["role"] = "EXPIRED"
                        changed = True
                        # Wyślij jednorazowe powiadomienie o wygaśnięciu
                        self.send_message(
                            "⚠️ <b>TWÓJ DOSTĘP WYGASŁ</b> ⚠️\n\n"
                            "Twój okres subskrypcji dobiegł końca.\n"
                            "Aby odnowić dostęp do sygnałów na kolejne 30 dni, wpisz polecenie <code>/kup</code> lub aktywuj kod <code>/kod TWÓJ_KOD</code>.",
                            chat_id=cid
                        )
            except Exception:
                ids.add(cid)

        if changed:
            self._save_subscribers_data(self.subscribers_data)

        return list(ids)


    def _load_cards(self) -> Tuple[Dict[str, Any], Dict[str, float]]:
        """Wczytuje aktywne i rozliczone karty wiadomości z pliku JSON i czyści przedawnione."""
        cards = {}
        settled = {}
        if os.path.exists(CARDS_FILE):
            try:
                with open(CARDS_FILE, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    if isinstance(data, dict):
                        if "__settled_matches__" in data:
                            settled = data.pop("__settled_matches__", {})
                        cards = data
            except Exception as e:
                print(f"[Telegram] Błąd odczytu kart: {e}")
                cards = {}

        if not isinstance(cards, dict):
            cards = {}
        if not isinstance(settled, dict):
            settled = {}

        now = time.time()
        # Karty aktywne nigdy nie są kasowane po cichu – trzymamy je aż do pełnego rozliczenia
        cleaned_cards = {
            k: v for k, v in cards.items()
            if isinstance(v, dict)
        }
        cleaned_settled = {
            k: float(v) for k, v in settled.items()
            if isinstance(v, (int, float)) and (now - v) < 86400
        }
        return cleaned_cards, cleaned_settled

    def _startup_settlement_check(self):
        """Weryfikuje i rozlicza ewentualne nierozliczone mecze z poprzedniej sesji zaraz po starcie."""
        time.sleep(5)
        if not getattr(self, 'active_match_cards', None):
            return
        try:
            print(f"[Telegram] Weryfikacja startowa nierozliczonych kart ({len(self.active_match_cards)} pozycji)...")
            from engine.flashscore_engine import FlashscoreEngine
            from engine.sts_live_engine import STSLiveEngine
            fs = FlashscoreEngine()
            sts = STSLiveEngine()
            live_m = fs.get_live_soccer_matches(include_all_today=True) + sts.fetch_live_matches(include_esports=False)
            fin_matches = fs.get_finished_results(days_back=2)
            settled_cnt = self.auto_settle_active_cards(live_matches=live_m, finished_matches=fin_matches)
            if settled_cnt > 0:
                print(f"[Telegram] Startowo rozliczono {settled_cnt} zaległych kart.")
        except Exception as e:
            print(f"[Telegram] Błąd weryfikacji startowej kart: {e}")

    def _save_cards(self):
        """Utrwala stan aktywnych i rozliczonych kart na dysku w sposób bezpieczny wątkowo."""
        lock = getattr(self, '_cards_lock', None)
        if lock:
            lock.acquire()
        try:
            now = time.time()
            # Aktywne karty pozostają w pamięci do momentu oficjalnego rozliczenia (WON / LOST / VOID)
            self.settled_matches = {
                k: float(v) for k, v in getattr(self, 'settled_matches', {}).items()
                if isinstance(v, (int, float)) and (now - v) < 86400
            }
            data = dict(self.active_match_cards)
            data["__settled_matches__"] = self.settled_matches
            
            tmp_file = CARDS_FILE + ".tmp"
            with open(tmp_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            if os.path.exists(tmp_file):
                os.replace(tmp_file, CARDS_FILE)
        except Exception as e:
            print(f"[Telegram] Błąd zapisu kart: {e}")
        finally:
            if lock:
                lock.release()

    def send_message(self, text: str, chat_id: Optional[str] = None, parse_mode: str = "HTML", reply_markup: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        token = self.config.get("bot_token", "").strip()
        target_chat = str(chat_id or self.config.get("chat_id", "")).strip()

        if not token or not target_chat:
            return {"success": False, "error": "Brak skonfigurowanego Bot Token lub Chat ID"}

        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = {
            "chat_id": target_chat,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": True
        }
        if reply_markup:
            payload["reply_markup"] = reply_markup

        try:
            data = json.dumps(payload).encode('utf-8')
            resp = self._http.request(
                "POST",
                url,
                body=data,
                headers={"Content-Type": "application/json", "User-Agent": "SurebetScanner/2.0"},
                timeout=8.0
            )
            res_body = json.loads(resp.data.decode('utf-8'))
            if resp.status == 200 and res_body.get("ok"):
                return {"success": True, "result": res_body.get("result", {})}
            err_desc = res_body.get("description", f"HTTP {resp.status}")
            return {"success": False, "error": err_desc}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def edit_message(self, message_id: int, text: str, chat_id: Optional[str] = None, parse_mode: str = "HTML", reply_markup: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        token = self.config.get("bot_token", "").strip()
        target_chat = str(chat_id or self.config.get("chat_id", "")).strip()

        if not token or not target_chat or not message_id:
            return {"success": False, "error": "Brak parametrów do edycji wiadomości"}

        url = f"https://api.telegram.org/bot{token}/editMessageText"
        payload = {
            "chat_id": target_chat,
            "message_id": int(message_id),
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": True
        }
        if reply_markup:
            payload["reply_markup"] = reply_markup

        try:
            data = json.dumps(payload).encode('utf-8')
            resp = self._http.request(
                "POST",
                url,
                body=data,
                headers={"Content-Type": "application/json", "User-Agent": "SurebetScanner/2.0"},
                timeout=8.0
            )
            res_body = json.loads(resp.data.decode('utf-8'))
            if resp.status == 200 and res_body.get("ok"):
                return {"success": True, "result": res_body.get("result", {})}
            err_desc = res_body.get("description", "")
            if "message is not modified" in err_desc:
                return {"success": True, "not_modified": True}
            return {"success": False, "error": f"HTTP {resp.status}: {err_desc}"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def send_test_message(self) -> Dict[str, Any]:
        """Wysyła wiadomość testową na skonfigurowany kanał/czat oraz do administratorów."""
        token = self.config.get("bot_token", "").strip()
        if not token:
            return {"success": False, "error": "Brak tokenu bota w konfiguracji!"}

        main_cid = self.config.get("chat_id") or self.config.get("vip_channel_id")
        if not main_cid:
            return {"success": False, "error": "Brak skonfigurowanego Chat ID / Kanału VIP!"}

        test_text = (
            "🤖 <b>OverRadar Live – Test Połączenia Telegram</b>\n\n"
            "✅ Połączenie ze skanerem STS działa w 100% poprawnie!\n"
            f"📡 <b>Status Bota:</b> Aktywny i połączony\n"
            f"⭐ <b>Min. ocena sygnału:</b> {self.config.get('min_stars', 4)}⭐\n"
            f"🎯 <b>Tryb Snajper:</b> {'Włączony (4⭐-5⭐)' if self.config.get('sniper_mode', True) else 'Wyłączony (od 2⭐)'}\n"
            f"⏱️ <b>Data testu:</b> {time.strftime('%Y-%m-%d %H:%M:%S')}"
        )
        res = self.send_message(test_text, chat_id=main_cid)
        if res.get("success"):
            for adm in self.subscribers_data.get("admins", []):
                if str(adm) != str(main_cid):
                    try:
                        self.send_message(test_text, chat_id=str(adm))
                    except Exception:
                        pass
            return {"success": True, "message": "Wiadomość testowa została pomyślnie wysłana na Telegram!"}
        else:
            return {"success": False, "error": res.get("error", "Błąd wysyłania")}

    def send_message_all(self, text: str, parse_mode: str = "HTML") -> Dict[str, int]:
        """Wysyła wiadomość do WSZYSTKICH podłączonych urządzeń / subskrybentów."""
        results = {}
        all_ids = self.get_all_chat_ids()
        for cid in all_ids:
            res = self.send_message(text, chat_id=cid, parse_mode=parse_mode)
            if res.get("success"):
                msg_id = res.get("result", {}).get("message_id")
                if msg_id:
                    results[str(cid)] = msg_id
        return results

    def edit_message_all(self, device_messages: Dict[str, int], text: str, parse_mode: str = "HTML") -> bool:
        """Edytuje wiadomość w miejscu na WSZYSTKICH podłączonych urządzeniach."""
        if not device_messages:
            return False
        success = False
        for cid, msg_id in device_messages.items():
            res = self.edit_message(msg_id, text, chat_id=cid, parse_mode=parse_mode)
            if res.get("success") or res.get("not_modified"):
                success = True
        return success

    def answer_callback_query(self, callback_query_id: str, text: Optional[str] = None, show_alert: bool = False) -> Dict[str, Any]:
        """Potwierdza odebranie kliknięcia w przycisk Inline i opcjonalnie wyświetla powiadomienie."""
        token = self.config.get("bot_token", "").strip()
        if not token or not callback_query_id:
            return {"success": False, "error": "Brak parametrów"}

        url = f"https://api.telegram.org/bot{token}/answerCallbackQuery"
        payload = {"callback_query_id": str(callback_query_id)}
        if text:
            payload["text"] = str(text)
            payload["show_alert"] = bool(show_alert)

        try:
            data = json.dumps(payload).encode('utf-8')
            resp = self._http.request(
                "POST",
                url,
                body=data,
                headers={"Content-Type": "application/json", "User-Agent": "SurebetScanner/2.0"},
                timeout=5.0
            )
            res_body = json.loads(resp.data.decode('utf-8'))
            return {"success": bool(res_body.get("ok", False))}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def poll_pairing_requests(self) -> List[Dict[str, Any]]:
        """Nasłuchuje nowych poleceń z Telegrama (Komendy Twórcy / Admina oraz Klientów VIP)."""
        token = self.config.get("bot_token", "").strip()
        if not token:
            time.sleep(1.0)
            return []

        if not hasattr(self, '_poll_lock'):
            self._poll_lock = threading.Lock()
        if not hasattr(self, '_processed_update_ids'):
            self._processed_update_ids = set()

        acquired = self._poll_lock.acquire(blocking=False)
        if not acquired:
            time.sleep(0.2)
            return []

        # Long Polling (timeout=15s): Telegram trzyma otwarty socket i natychmiast zwraca zdarzenie (reakcja ~20ms)
        poll_payload = {
            "offset": self._last_update_id + 1,
            "timeout": 15,
            "allowed_updates": ["message", "edited_message", "channel_post", "edited_channel_post", "callback_query"]
        }
        url = f"https://api.telegram.org/bot{token}/getUpdates"
        new_paired = []
        try:
            resp = self._http.request(
                "POST",
                url,
                body=json.dumps(poll_payload).encode('utf-8'),
                headers={"Content-Type": "application/json", "User-Agent": "SurebetScanner/2.0"},
                timeout=urllib3.Timeout(connect=5.0, read=25.0)
            )
            if resp.status != 200:
                time.sleep(1.0)
                return []

            body = json.loads(resp.data.decode('utf-8'))
            if not body.get("ok"):
                return []

            updates = body.get("result", [])
            admins = set(str(a) for a in self.subscribers_data.get("admins", []))
            main_id = self.config.get("chat_id", "").strip()
            if main_id:
                admins.add(str(main_id))

            for u in updates:
                uid = u.get("update_id", 0)
                if uid in self._processed_update_ids:
                    continue
                self._processed_update_ids.add(uid)
                if len(self._processed_update_ids) > 1000:
                    # Oczyszczanie bufora update_id dla minimalnego zużycia pamięci
                    self._processed_update_ids = {x for x in self._processed_update_ids if x >= self._last_update_id - 200}
                if uid > self._last_update_id:
                    self._last_update_id = uid

                # Obsługa kliknięć w przyciski Inline (callback_query)
                cb = u.get("callback_query")
                if cb:
                    cb_id = cb.get("id")
                    cb_data = cb.get("data", "")
                    cb_msg = cb.get("message", {})
                    cb_cid = str(cb_msg.get("chat", {}).get("id") or cb.get("from", {}).get("id", ""))
                    cb_mid = cb_msg.get("message_id")

                    if cb_data.startswith("stats_"):
                        period = cb_data.replace("stats_", "")
                        stats_res = self.stats_engine.get_stats(period)
                        stats_msg = self.stats_engine.format_telegram_message(stats_res)
                        kb = self.stats_engine.get_inline_keyboard(current_period=period)
                        edit_res = {}
                        if cb_mid and cb_cid:
                            edit_res = self.edit_message(cb_mid, stats_msg, chat_id=cb_cid, reply_markup=kb)
                            print(f"[Telegram] Zaktualizowano raport '{period}' w czacie {cb_cid} (mid={cb_mid}, res={edit_res.get('success')})")
                        
                        period_lbl = stats_res.get('period_label', period)
                        if edit_res.get("not_modified"):
                            toast_txt = f"ℹ️ Raport ({period_lbl}) jest już wyświetlony."
                        else:
                            toast_txt = f"📊 Zaktualizowano: {period_lbl}"
                        self.answer_callback_query(cb_id, text=toast_txt)
                        continue

                        if cb_data in ("sniper_on", "sniper_off"):
                            is_on = (cb_data == "sniper_on")
                            self.config["min_stars"] = 4 if is_on else 2
                            self.config["sniper_mode"] = is_on
                            if is_on:
                                self.config["max_active_cards"] = 3
                            self.save_config(self.config)
                            
                            status_txt = "<b>WŁĄCZONY 🟢</b>" if is_on else "<b>WYŁĄCZONY ⚪ (Tryb Pełny)</b>"
                            kb = {
                                "inline_keyboard": [
                                    [
                                        {"text": f"{'✅ ' if is_on else ''}🟢 Włącz Snajper (VIP)", "callback_data": "sniper_on"},
                                        {"text": f"{'✅ ' if not is_on else ''}⚪ Wyłącz (Tryb Pełny)", "callback_data": "sniper_off"}
                                    ]
                                ]
                            }
                            new_text = (
                                f"🎯 <b>TRYB SNAJPER (VIP)</b>\n\n"
                                f"Aktualny status: {status_txt}\n\n"
                                f"• <b>Minimalna ocena:</b> {'4⭐ - 5⭐ (Stawki 2J i 3J)' if is_on else 'Wszystkie sygnały (od 2⭐)'}\n"
                                f"• <b>Maksymalnie otwartych:</b> {'3 aktywne mecze naraz' if is_on else 'Bez limitu'}\n"
                                f"• <b>Okno godzinowe:</b> 16:00 – 06:00\n\n"
                                f"<i>Wybierz tryb przyciskami poniżej:</i>"
                            )
                            if cb_mid and cb_cid:
                                self.edit_message(cb_mid, new_text, chat_id=cb_cid, reply_markup=kb)
                            toast_mode = "🟢 Włączono Snajper VIP (od 4⭐)" if is_on else "⚪ Wyłączono Snajper (wszystkie sygnały)"
                            self.answer_callback_query(cb_id, text=toast_mode)
                            continue

                        # Domyślne potwierdzenie dla każdego innego callbacka
                        self.answer_callback_query(cb_id)
                        continue


                post = u.get("channel_post")
                if post:
                    p_chat = post.get("chat", {})
                    p_cid = str(p_chat.get("id", ""))
                    p_title = p_chat.get("title", "")
                    print(f"📡 TELEGRAM CHANNEL POST DETECTED: '{p_title}' -> ID: {p_cid}", flush=True)
                    if "vip" in p_title.lower():
                        self.config["vip_channel_id"] = p_cid
                        self.config["chat_id"] = p_cid
                        self.save_config(self.config)
                        print(f"✅ AUTO-SAVED VIP CHANNEL ID: {p_cid}", flush=True)
                    elif "live" in p_title.lower():
                        self.config["free_channel_id"] = p_cid
                        self.save_config(self.config)
                        print(f"✅ AUTO-SAVED FREE CHANNEL ID: {p_cid}", flush=True)
                    continue

                msg = u.get("message")
                if not msg:
                    continue

                chat = msg.get("chat", {})
                cid = str(chat.get("id", ""))

                # Sprawdź, czy wiadomość została przekazana (forward) z kanału VIP
                fwd_chat = msg.get("forward_from_chat")
                if fwd_chat:
                    fwd_cid = str(fwd_chat.get("id", ""))
                    fwd_title = fwd_chat.get("title", "")
                    print(f"📡 FORWARDED FROM CHANNEL: '{fwd_title}' -> ID: {fwd_cid}", flush=True)
                    if "vip" in fwd_title.lower():
                        self.config["vip_channel_id"] = fwd_cid
                        self.config["chat_id"] = fwd_cid
                        self.save_config(self.config)
                        self.send_message(f"✅ <b>Rozpoznano i połączono kanał VIP!</b>\n\nNazwa: <b>{fwd_title}</b>\nID: <code>{fwd_cid}</code>", chat_id=cid)
                    elif "live" in fwd_title.lower():
                        self.config["free_channel_id"] = fwd_cid
                        self.save_config(self.config)
                        self.send_message(f"✅ <b>Rozpoznano i połączono kanał FREE!</b>\n\nNazwa: <b>{fwd_title}</b>\nID: <code>{fwd_cid}</code>", chat_id=cid)
                    continue
                from_user = msg.get("from", {})
                first_name = from_user.get("first_name", "Użytkownik")
                username = from_user.get("username", "")
                raw_text = msg.get("text", "").strip()
                if not cid or not raw_text:
                    continue

                # Normalizacja tekstu: np. "/ kod 30" -> "/kod 30", "admin" -> "/admin"
                text = re.sub(r'^/\s+', '/', raw_text)
                if text.lower() in ("admin", "panel", "status", "konto", "mojekonto", "kup", "cennik", "lista", "kody", "stats", "statystyki", "bilans", "raport"):
                    text = "/" + text
                text_lower = text.lower()
                is_admin = (cid in admins)

                subs = self.subscribers_data.setdefault("subscribers", [])
                sub_found = next((s for s in subs if str(s.get("chat_id")) == cid), None)
                if is_admin and sub_found and sub_found.get("role") != "ADMIN":
                    sub_found["role"] = "ADMIN"
                    self._save_subscribers_data(self.subscribers_data)

                # ========================================================
                # 👑 1. KOMENDY TWÓRCY / ADMINISTRATORA (DLA CIEBIE)
                # ========================================================
                if is_admin and text_lower.startswith(("/admin", "/panel", "/komendy", "/menu")):
                    total_subs = len(subs)
                    active_subs = len(self.get_all_chat_ids())
                    price = self.subscribers_data.get("vip_price_pln", 99)
                    codes_count = len(self.subscribers_data.get("promo_codes", {}))
                    admin_msg = (
                        "👑 <b>TWOJE CENTRUM DOWODZENIA (ADMIN)</b> 👑\n"
                        "<i>Dostępne wyłącznie dla Ciebie (Właściciela).</i>\n\n"
                        f"📊 <b>W bazie:</b> {total_subs} | <b>Aktywni:</b> {active_subs} | <b>Cena VIP:</b> {price:.2f} zł\n\n"
                        "📈 <b>STATYSTYKI & HISTORIA:</b>\n"
                        "• <code>/stats</code> – Interaktywny raport ze wszystkimi okresami\n"
                        "• <code>/stats dzis</code> – Statystyki z dzisiejszego dnia\n"
                        "• <code>/stats tydzien</code> – Statystyki z 7 dni\n"
                        "• <code>/stats miesiac</code> – Statystyki z 30 dni\n"
                        "• <code>/stats 3m</code> – Statystyki z 90 dni\n"
                        "• <code>/stats rok</code> – Statystyki z 365 dni\n"
                        "• <code>/stats all</code> – Statystyki całkowite\n"
                        "• <code>/resetstats</code> – Wyzerowanie statystyk do czystego stanu (0/0)\n\n"
                        "👥 <b>ZARZĄDZANIE KLIENTAMI & VIP:</b>\n"
                        "• <code>/lista</code> – Pełna lista subskrybentów i terminy ważności\n"
                        "• <code>/dodaj &lt;chat_id&gt; &lt;dni&gt;</code> – Nadanie dostępu VIP (np. <code>/dodaj 123456789 30</code>)\n"
                        "• <code>/usun &lt;chat_id&gt;</code> – Zablokowanie i odebranie dostępu\n"
                        "• <code>/kod &lt;dni&gt;</code> – Wygenerowanie unikalnego kodu VIP (np. <code>/kod 30</code>)\n"
                        "• <code>/kody</code> – Lista aktywnych kodów promocyjnych\n"
                        "• <code>/cena &lt;kwota&gt;</code> – Zmiana ceny subskrypcji (np. <code>/cena 149</code>)\n\n"
                        "📢 <b>KOMUNIKACJA:</b>\n"
                        "• <code>/ogloszenie &lt;treść&gt;</code> – Wysłanie wiadomości do wszystkich aktywnych\n\n"
                        "🎯 <b>TRYBY DZIAŁANIA:</b>\n"
                        "• <code>/sniper</code> – Aktywacja selekcji Snajper VIP (tylko 4⭐-5⭐, max 3 mecze, Złote Okno 16'-30')\n"
                        "• <code>/sniper off</code> – Powrót do trybu standardowego (wszystkie sygnały od 2⭐)\n\n"
                        "💡 <i>Wpisz <code>/komendy</code> w dowolnym momencie, aby wyświetlić tę listę.</i>"
                    )
                    self.send_message(admin_msg, chat_id=cid)
                    continue

                elif text_lower.startswith(("/sniper", "/snajper", "/mode", "/tryb")):
                    parts = text.split()
                    if len(parts) >= 2 and parts[1].lower() in ("off", "wylacz", "standard", "all", "wszystkie"):
                        self.config["min_stars"] = 2
                        self.config["sniper_mode"] = False
                        self.save_config(self.config)
                        is_on = False
                    elif len(parts) >= 2 and parts[1].lower() in ("on", "wlacz", "vip", "start"):
                        self.config["min_stars"] = 4
                        self.config["sniper_mode"] = True
                        self.config["max_active_cards"] = 3
                        self.save_config(self.config)
                        is_on = True
                    else:
                        is_on = bool(self.config.get("sniper_mode", True))

                    status_txt = "<b>WŁĄCZONY 🟢</b>" if is_on else "<b>WYŁĄCZONY ⚪ (Tryb Pełny)</b>"
                    kb = {
                        "inline_keyboard": [
                            [
                                {"text": f"{'✅ ' if is_on else ''}🟢 Włącz Snajper (VIP)", "callback_data": "sniper_on"},
                                {"text": f"{'✅ ' if not is_on else ''}⚪ Wyłącz (Tryb Pełny)", "callback_data": "sniper_off"}
                            ]
                        ]
                    }
                    menu_msg = (
                        f"🎯 <b>TRYB SNAJPER (VIP)</b>\n\n"
                        f"Aktualny status: {status_txt}\n\n"
                        f"• <b>Minimalna ocena:</b> {'4⭐ - 5⭐ (Stawki 2J i 3J)' if is_on else 'Wszystkie sygnały (od 2⭐)'}\n"
                        f"• <b>Maksymalnie otwartych:</b> {'3 aktywne mecze naraz' if is_on else 'Bez limitu'}\n"
                        f"• <b>Okno godzinowe:</b> 16:00 – 06:00\n\n"
                        f"<i>Wybierz tryb przyciskami poniżej:</i>"
                    )
                    self.send_message(menu_msg, chat_id=cid, reply_markup=kb)
                    continue

                elif text_lower.startswith(("/ba", "/betanalytix")):
                    parts = text.split()
                    email_idx = next((i for i, p in enumerate(parts) if "@" in p and "." in p), None)
                    if email_idx is not None and len(parts) > email_idx + 1:
                        em = parts[email_idx].strip()
                        pw = parts[email_idx + 1].strip()
                        self.send_message("⏳ <i>Logowanie do Bet-Analytix...</i>", chat_id=cid)
                        res = self.ba_sync.login(em, pw)
                        if res.get("success"):
                            bankrolls = self.ba_sync.get_bankrolls()
                            b_txt = ""
                            for i, b in enumerate(bankrolls, 1):
                                b_name = b.get("name", "Bankroll")
                                b_id = b.get("id") or b.get("id_bankroll")
                                b_txt += f"• <b>{b_name}</b> (ID: <code>{b_id}</code>)\n"
                            self.send_message(
                                f"✅ <b>Zalogowano do Bet-Analytix!</b>\n\n"
                                f"👤 Konto: <code>{em}</code>\n"
                                f"📁 Wykryte bankrolle:\n{b_txt or '• Brak bankrolli'}\n"
                                f"🎯 Aktywny bankroll: <b>{self.ba_sync.config.get('bankroll_name')}</b> (ID: <code>{self.ba_sync.config.get('bankroll_id')}</code>)\n\n"
                                f"🚀 <i>Wszystkie nowe alerty ze skanera będą automatycznie dodawane i rozliczane na Twoim koncie!</i>",
                                chat_id=cid
                            )
                        else:
                            self.send_message(
                                f"❌ <b>Błąd logowania do Bet-Analytix:</b>\n<code>{res.get('error')}</code>\n\n"
                                f"💡 Upewnij się, że podajesz poprawny e-mail i hasło do <a href=\"https://app.bet-analytix.com/\">app.bet-analytix.com</a>.",
                                chat_id=cid
                            )
                        continue

                    elif len(parts) >= 3 and parts[1].lower() in ("bankroll", "b"):
                        try:
                            b_id = int(parts[2].strip())
                            self.ba_sync.config["bankroll_id"] = b_id
                            self.ba_sync.save_config()
                            self.send_message(f"✅ Ustawiono aktywny bankroll ID: <code>{b_id}</code>", chat_id=cid)
                        except Exception:
                            self.send_message("⚠️ Podaj poprawne numeryczne ID bankrolla, np. <code>/ba bankroll 12345</code>", chat_id=cid)
                        continue

                    elif len(parts) >= 2 and parts[1].lower() in ("off", "wylacz", "stop"):
                        self.ba_sync.config["enabled"] = False
                        self.ba_sync.save_config()
                        self.send_message("⚪ <b>Automatyczna synchronizacja z Bet-Analytix została wyłączona.</b>", chat_id=cid)
                        continue

                    elif len(parts) >= 2 and parts[1].lower() in ("on", "wlacz", "start"):
                        self.ba_sync.config["enabled"] = True
                        self.ba_sync.save_config()
                        self.send_message("🟢 <b>Automatyczna synchronizacja z Bet-Analytix została włączona.</b>", chat_id=cid)
                        continue

                    # Default: status panel
                    st = self.ba_sync.get_status()
                    auth_txt = "<b>POŁĄCZONO 🟢</b>" if st["authenticated"] else "<b>WYMAGANE LOGOWANIE 🔴</b>"
                    sync_txt = "<b>WŁĄCZONA 🟢</b>" if st["enabled"] else "<b>WYŁĄCZONA ⚪</b>"
                    email_txt = f"<code>{st['email']}</code>" if st['email'] else "<i>Nie zalogowano</i>"
                    b_txt = f"<b>{st['bankroll_name']}</b> (ID: <code>{st['bankroll_id']}</code>)" if st['bankroll_id'] else "<i>Brak</i>"

                    help_txt = (
                        f"📈 <b>INTEGRACJA BET-ANALYTIX</b> (app.bet-analytix.com)\n\n"
                        f"• Status konta: {auth_txt}\n"
                        f"• Auto-synchronizacja: {sync_txt}\n"
                        f"• E-mail: {email_txt}\n"
                        f"• Aktywny Bankroll: {b_txt}\n"
                        f"• Zsynchronizowanych typów: <b>{st['total_synced_bets']}</b>\n\n"
                        f"🔑 <b>KOMENDY STEROWANIA:</b>\n"
                        f"• <code>/ba login email haslo</code> – logowanie do konta Bet-Analytix\n"
                        f"• <code>/ba on</code> / <code>/ba off</code> – włączenie / wyłączenie auto-syncu\n"
                        f"• <code>/ba bankroll &lt;id&gt;</code> – zmiana aktywnego bankrolla\n\n"
                        f"💡 <i>Gdy jesteś zalogowany, każdy ALERT na Telegramie ląduje automatycznie na Twoim profilu Bet-Analytix i sam się rozlicza!</i>"
                    )
                    self.send_message(help_txt, chat_id=cid)
                    continue

                elif is_admin and text_lower.startswith("/dodaj"):
                    parts = text.split()
                    if len(parts) >= 3:
                        target_cid = parts[1].replace("@", "").strip()
                        try:
                            days = int(parts[2])
                            exp_dt = datetime.datetime.now() + datetime.timedelta(days=days)
                            exp_str = exp_dt.strftime('%Y-%m-%d %H:%M:%S')

                            target_sub = next((s for s in subs if str(s.get("chat_id")) == target_cid or str(s.get("username", "")).lower() == target_cid.lower()), None)
                            if not target_sub:
                                target_sub = {
                                    "chat_id": target_cid,
                                    "first_name": f"Klient {target_cid}",
                                    "role": "VIP",
                                    "joined_at": time.strftime('%Y-%m-%d %H:%M:%S')
                                }
                                subs.append(target_sub)

                            target_sub["role"] = "VIP"
                            target_sub["expires_at"] = exp_str
                            target_sub["is_active"] = True
                            self._save_subscribers_data(self.subscribers_data)

                            self.send_message(
                                f"✅ <b>Pomyślnie nadano dostęp VIP!</b>\n"
                                f"👤 Użytkownik: <code>{target_cid}</code>\n"
                                f"📅 Ważność: <b>{days} dni</b> (do: {exp_str})",
                                chat_id=cid
                            )
                            self.send_message(
                                f"🎉 <b>TWÓJ DOSTĘP VIP ZOSTAŁ AKTYWOWANY!</b> 🎉\n\n"
                                f"💎 Twórca nadał Ci dostęp VIP na <b>{days} dni</b>.\n"
                                f"📅 Dostęp ważny do: <b>{exp_str}</b>\n"
                                f"🚀 Od teraz otrzymujesz wszystkie sygnały Over na żywo!",
                                chat_id=target_cid
                            )
                        except ValueError:
                            self.send_message("❌ Błąd. Użyj: <code>/dodaj <chat_id> <liczba_dni></code>", chat_id=cid)
                    else:
                        self.send_message("❌ Użyj: <code>/dodaj <chat_id> <liczba_dni></code> (np. <code>/dodaj 123456789 30</code>)", chat_id=cid)
                    continue

                elif is_admin and text_lower.startswith("/usun"):
                    parts = text.split()
                    if len(parts) >= 2:
                        target_cid = parts[1].replace("@", "").strip()
                        self.subscribers_data["subscribers"] = [s for s in subs if str(s.get("chat_id")) != target_cid]
                        self._save_subscribers_data(self.subscribers_data)
                        self.send_message(f"✅ Usunięto użytkownika <code>{target_cid}</code> z bazy.", chat_id=cid)
                        self.send_message("👋 Twój dostęp do sygnałów został wyłączony przez administratora.", chat_id=target_cid)
                    continue

                elif is_admin and text_lower.startswith("/kod") and len(text.split()) >= 2 and text.split()[1].isdigit():
                    days = int(text.split()[1])
                    code_id = f"VIP{days}-" + str(random.randint(1000, 9999))
                    codes = self.subscribers_data.setdefault("promo_codes", {})
                    codes[code_id] = {"days": days, "uses_left": 1}
                    self._save_subscribers_data(self.subscribers_data)
                    self.send_message(
                        f"🎟️ <b>WYGENEROWANO NOWY KOD VIP</b> 🎟️\n\n"
                        f"🔑 Kod: <code>{code_id}</code>\n"
                        f"📅 Ważność: <b>{days} dni</b>\n\n"
                        f"Przekaż ten kod klientowi po opłaceniu. Klient wpisuje:\n"
                        f"<code>/kod {code_id}</code>",
                        chat_id=cid
                    )
                    continue

                elif is_admin and text_lower.startswith(("/kody", "/promokody")):
                    codes = self.subscribers_data.get("promo_codes", {})
                    if not codes:
                        self.send_message("Brak aktywnych kodów. Wpisz <code>/kod 30</code> aby wygenerować.", chat_id=cid)
                    else:
                        txt = "🎟️ <b>LISTA KODÓW PROMOCYJNYCH:</b>\n\n"
                        for c, info in codes.items():
                            d = info.get("days", 30) if isinstance(info, dict) else info
                            u = info.get("uses_left", 1) if isinstance(info, dict) else 1
                            txt += f"• <code>{c}</code> ➔ <b>{d} dni</b> (Pozostało: {u})\n"
                        self.send_message(txt, chat_id=cid)
                    continue

                elif is_admin and text_lower.startswith(("/lista", "/subskrybenci")):
                    txt = f"📋 <b>LISTA SUBSKRYBENTÓW ({len(subs)}):</b>\n\n"
                    for s in subs:
                        role = s.get("role", "VIP")
                        name = s.get("first_name", "User")
                        exp = s.get("expires_at") or "DOŻYWOTNI"
                        cid_s = s.get("chat_id", "")
                        badge = "👑 ADMIN" if role == "ADMIN" else ("💎 VIP" if role == "VIP" else ("🎁 TRIAL" if role == "TRIAL" else "❌ EXPIRED"))
                        txt += f"• {badge} <b>{name}</b> (<code>{cid_s}</code>)\n   📅 Ważność: {exp}\n"
                    self.send_message(txt, chat_id=cid)
                    continue

                elif is_admin and text_lower.startswith("/ogloszenie"):
                    announcement = text[len("/ogloszenie"):].strip()
                    if announcement:
                        msg_ann = f"📢 <b>KOMUNIKAT OD TWÓRCY</b> 📢\n\n{announcement}"
                        self.send_message_all(msg_ann)
                        self.send_message("✅ Ogłoszenie wysłane do wszystkich aktywnych użytkowników!", chat_id=cid)
                    continue

                elif is_admin and text_lower.startswith("/cena"):
                    parts = text.split()
                    if len(parts) >= 2:
                        try:
                            new_p = float(parts[1])
                            self.subscribers_data["vip_price_pln"] = new_p
                            self._save_subscribers_data(self.subscribers_data)
                            self.send_message(f"✅ Zmieniono cenę subskrypcji na <b>{new_p:.2f} zł / 30 dni</b>.", chat_id=cid)
                        except ValueError:
                            self.send_message("❌ Podaj poprawną kwotę, np. <code>/cena 149</code>", chat_id=cid)
                    continue

                elif is_admin and text_lower.startswith("/proxy"):
                    parts = text.split(maxsplit=1)
                    from engine.smart_money_engine import SmartMoneyEngine
                    sm = SmartMoneyEngine()
                    if len(parts) >= 2:
                        p_arg = parts[1].strip()
                        if p_arg.lower() in ("test", "status", "check"):
                            curr_p = sm.config.get("custom_proxy_url", "")
                            if not curr_p:
                                self.send_message("ℹ️ <b>Status Proxy:</b> Brak ustawionego własnego proxy.\nWpisz <code>/proxy http://host:port</code> aby ustawić.", chat_id=cid)
                            else:
                                self.send_message(f"⏳ <b>Testowanie połączenia z Betfair przez proxy:</b>\n<code>{curr_p}</code>...", chat_id=cid)
                                test_res = sm.test_proxy(curr_p)
                                if test_res.get("success"):
                                    self.send_message(f"✅ <b>Połączenie z Betfair udane!</b> (Czas: {test_res.get('latency_sec')}s)", chat_id=cid)
                                else:
                                    self.send_message(f"❌ <b>Błąd połączenia z Betfair:</b>\n{test_res.get('error')}", chat_id=cid)
                        elif p_arg.lower() in ("off", "wylacz", "wyłącz", "clear", "usun", "usuń"):
                            sm.update_proxy("")
                            self.send_message("✅ Wyłączono własne proxy (powrót do trybu domyślnego).", chat_id=cid)
                        else:
                            sm.update_proxy(p_arg)
                            self.send_message(f"✅ <b>Zapisano nowe proxy dla Betfair:</b>\n<code>{p_arg}</code>\nWpisz <code>/proxy test</code> aby sprawdzić połączenie.", chat_id=cid)
                    else:
                        curr_p = sm.config.get("custom_proxy_url", "Brak")
                        self.send_message(
                            f"🌐 <b>ZARZĄDZANIE PROXY (BETFAIR):</b>\n\n"
                            f"• <b>Aktualne proxy:</b> <code>{curr_p}</code>\n\n"
                            f"<b>Komendy:</b>\n"
                            f"• <code>/proxy http://host:port</code> – Ustawienie nowego proxy (UK/DE/NL)\n"
                            f"• <code>/proxy test</code> – Test połączenia z Betfair\n"
                            f"• <code>/proxy off</code> – Wyłączenie własnego proxy",
                            chat_id=cid
                        )
                    continue

                elif is_admin and text_lower.startswith(("/reset", "/resetstats", "/czysc", "/wyczysc", "/clean", "/zeruj")):
                    # 1. Reset statystyk i bazy
                    self.stats_engine.reset_history()
                    self.active_match_cards.clear()
                    self.settled_matches.clear()
                    self._save_cards()
                    
                    try:
                        with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "telegram_signals_history.json"), "w", encoding="utf-8") as f:
                            json.dump([], f)
                        with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "telegram_active_cards.json"), "w", encoding="utf-8") as f:
                            json.dump({}, f)
                        from engine.bet_tracker import BetTracker
                        tracker = BetTracker()
                        tdata = tracker._load_data()
                        tdata["bets"] = []
                        tracker._save_data(tdata)
                    except Exception as ex:
                        print(f"[Reset Command] File clean error: {ex}")

                    # 2. Usuwanie ostatnich wiadomości na Telegramie (ostatnie 150 ID)
                    current_mid = msg.get("message_id", 500)
                    min_mid = max(1, current_mid - 150)
                    from concurrent.futures import ThreadPoolExecutor
                    
                    def _del_msg(m_id):
                        try:
                            d_url = f"https://api.telegram.org/bot{token}/deleteMessage"
                            p = json.dumps({"chat_id": cid, "message_id": m_id}).encode('utf-8')
                            self._http.request("POST", d_url, body=p, headers={"Content-Type": "application/json"}, timeout=2.0)
                        except Exception:
                            pass

                    try:
                        with ThreadPoolExecutor(max_workers=16) as ex:
                            list(ex.map(_del_msg, range(min_mid, current_mid + 1)))
                    except Exception:
                        pass

                    # 3. Potwierdzenie na czacie
                    self.send_message(
                        "🧹 <b>STATYSTYKI I CZAT ZOSTAŁY WYCZYSZCZONE!</b>\n\n"
                        "📊 <b>Bilans:</b> <code>0.00 J (0/0)</code>\n"
                        "🎯 <b>Skuteczność:</b> <code>0.0%</code>\n"
                        "💰 <b>Zysk:</b> <code>+0.00 zł</code>\n\n"
                        "🚀 Nowa historia sygnałów rozpoczęta od zera!",
                        chat_id=cid
                    )
                    continue

                # ========================================================
                # 👥 2. KOMENDY KLIENTÓW / SUBSKRYBENTÓW
                # ========================================================
                if text_lower.startswith("/start"):
                    if is_admin:
                        self.send_message(
                            f"👑 <b>WITAJ W PANELU TWÓRCY, {first_name}!</b> 👑\n\n"
                            f"Twój dostęp administratora jest <b>dożywotni i bezpłatny</b>.\n"
                            f"Wpisz <code>/admin</code> aby otworzyć panel zarządzania klientami i kodami.",
                            chat_id=cid
                        )
                        continue

                    if sub_found:
                        role = sub_found.get("role", "VIP")
                        exp = sub_found.get("expires_at", "Dożywotni")
                        if role in ("VIP", "ADMIN", "TRIAL"):
                            self.send_message(
                                f"💎 <b>Witaj ponownie {first_name}!</b>\n\n"
                                f"Status Twojego konta: <b>{role}</b>\n"
                                f"📅 Ważny do: <b>{exp}</b> 🚀\n\n"
                                f"Odbierasz wszystkie sygnały Over na żywo.",
                                chat_id=cid
                            )
                            continue

                    # Nowy użytkownik -> Darmowy 24h Trial
                    trial_hours = self.subscribers_data.get("trial_hours", 24)
                    exp_dt = datetime.datetime.now() + datetime.timedelta(hours=trial_hours)
                    exp_str = exp_dt.strftime('%Y-%m-%d %H:%M:%S')

                    if not sub_found:
                        sub_found = {
                            "chat_id": cid,
                            "first_name": first_name,
                            "username": username,
                            "role": "TRIAL",
                            "joined_at": time.strftime('%Y-%m-%d %H:%M:%S'),
                            "expires_at": exp_str,
                            "is_active": True
                        }
                        subs.append(sub_found)
                        new_paired.append({"chat_id": cid, "name": first_name})
                    else:
                        sub_found["role"] = "TRIAL"
                        sub_found["expires_at"] = exp_str
                        sub_found["is_active"] = True

                    self._save_subscribers_data(self.subscribers_data)
                    welcome_trial_msg = (
                        f"⚽ <b>WITAJ W STS LIVE GOAL SCANNER!</b> ⚽\n\n"
                        f"🔥 Prawdziwe kursy STS na żywo i algorytm konsensusu 8 serwisów.\n"
                        f"📈 <b>Skuteczność rynków Over: 90.9%!</b>\n\n"
                        f"🎁 <b>Aktywowano DARMOWY DOSTĘP PRÓBNY ({trial_hours}h)!</b>\n"
                        f"📅 Ważny do: <b>{exp_str}</b>\n\n"
                        f"Będziesz otrzymywać tutaj wszystkie sygnały meczowe z sugerowanymi stawkami 1J, 2J, 3J.\n\n"
                        f"💎 Aby wykupić pełny dostęp VIP (30 dni), wpisz: <code>/kup</code>\n"
                        f"🎟️ Masz kod aktywacyjny? Wpisz: <code>/kod TWÓJ_KOD</code>"
                    )
                    self.send_message(welcome_trial_msg, chat_id=cid)
                    continue

                elif text_lower.startswith(("/kup", "/cennik", "/oferta", "/vip")):
                    price = self.subscribers_data.get("vip_price_pln", 99)
                    buy_msg = (
                        f"💎 <b>SUBSKRYPCJA VIP – STS LIVE GOAL SCANNER</b> 💎\n\n"
                        f"🚀 <b>Co zyskujesz w pakiecie VIP:</b>\n"
                        f"• Wszystkie sygnały Over 1.5 / 2.5 FT na żywo (skuteczność 90.9%)\n"
                        f"• Błyskawiczne alerty natychmiast po golu i w Złotym Oknie 1H\n"
                        f"• Zarządzanie kapitałem i sugerowane stawki 1J, 2J, 3J\n"
                        f"• Bezpośrednie linki do oferty STS Live\n\n"
                        f"💰 <b>CENA: {price:.2f} zł / 30 dni</b>\n\n"
                        f"📲 <b>JAK DOKONAĆ ZAKUPU:</b>\n"
                        f"1. Skontaktuj się z twórcą w celu płatności BLIK / Przelewem.\n"
                        f"2. Po wpłacie otrzymasz swój unikalny Kod VIP.\n"
                        f"3. Wpisz w tym czacie polecenie:\n"
                        f"<code>/kod TWÓJ_KOD</code> aby natychmiast odblokować 30 dni VIP!"
                    )
                    self.send_message(buy_msg, chat_id=cid)
                    continue

                elif text_lower.startswith("/kod") or text_lower.startswith("/aktywuj"):
                    code_entered = text.replace("/kod", "").replace("/aktywuj", "").strip().upper()
                    codes = self.subscribers_data.get("promo_codes", {})
                    if code_entered and code_entered in codes:
                        c_info = codes[code_entered]
                        days_to_add = c_info.get("days", 30) if isinstance(c_info, dict) else c_info

                        if not sub_found:
                            sub_found = {
                                "chat_id": cid,
                                "first_name": first_name,
                                "username": username,
                                "role": "VIP",
                                "joined_at": time.strftime('%Y-%m-%d %H:%M:%S')
                            }
                            subs.append(sub_found)

                        base_dt = datetime.datetime.now()
                        cur_exp = sub_found.get("expires_at")
                        if cur_exp:
                            try:
                                parsed_cur = datetime.datetime.strptime(cur_exp, '%Y-%m-%d %H:%M:%S')
                                if parsed_cur > base_dt:
                                    base_dt = parsed_cur
                            except Exception:
                                pass

                        new_exp = base_dt + datetime.timedelta(days=days_to_add)
                        new_exp_str = new_exp.strftime('%Y-%m-%d %H:%M:%S')
                        sub_found["role"] = "VIP"
                        sub_found["expires_at"] = new_exp_str
                        sub_found["is_active"] = True

                        if isinstance(c_info, dict):
                            c_info["uses_left"] = c_info.get("uses_left", 1) - 1
                            if c_info["uses_left"] <= 0:
                                del codes[code_entered]

                        self._save_subscribers_data(self.subscribers_data)
                        self.send_message(
                            f"🎉 <b>KOD AKTYWOWANY POMYŚLNIE!</b> 🎉\n\n"
                            f"💎 Przedłużono dostęp VIP o <b>+{days_to_add} dni</b>!\n"
                            f"📅 Dostęp ważny do: <b>{new_exp_str}</b>\n"
                            f"🚀 Powodzenia w obstawianiu z algorytmem!",
                            chat_id=cid
                        )
                    else:
                        self.send_message("❌ Niepoprawny kod promocyjny. Sprawdź pisownię lub wpisz <code>/kup</code>.", chat_id=cid)
                    continue

                elif text_lower in ("/status", "/mojekonto", "/konto"):
                    if is_admin:
                        self.send_message("👑 <b>Twój status:</b> WŁAŚCICIEL / TWÓRCA (ADMIN)\n📅 <b>Ważność:</b> Dożywotnia (Bez limitu)", chat_id=cid)
                    elif sub_found:
                        r = sub_found.get("role", "VIP")
                        exp = sub_found.get("expires_at", "Brak danych")
                        self.send_message(
                            f"👤 <b>INFORMACJE O TWOIM KONCIE</b>\n\n"
                            f"💎 <b>Plan:</b> {r}\n"
                            f"📅 <b>Ważny do:</b> {exp}\n"
                            f"🚀 <b>Powiadomienia Live:</b> Aktywne",
                            chat_id=cid
                        )
                    else:
                        self.send_message("Wpisz <code>/start</code> aby aktywować konto.", chat_id=cid)
                    continue

                elif text_lower.startswith(("/stats", "/statystyki", "/bilans", "/raport", "/wyniki")):
                    parts = text_lower.split()
                    period = "30d"
                    if len(parts) >= 2:
                        p_arg = parts[1].strip()
                        if p_arg in ("1d", "dzis", "dzisiaj", "today", "1"):
                            period = "1d"
                        elif p_arg in ("7d", "tydzien", "tydzień", "week", "7"):
                            period = "7d"
                        elif p_arg in ("30d", "miesiac", "miesiąc", "month", "30"):
                            period = "30d"
                        elif p_arg in ("90d", "3m", "kwartal", "kwartał", "90"):
                            period = "90d"
                        elif p_arg in ("365d", "rok", "year", "365"):
                            period = "365d"
                        elif p_arg in ("all", "wszystko", "calosc", "całość"):
                            period = "all"

                    stats_res = self.stats_engine.get_stats(period)
                    stats_msg = self.stats_engine.format_telegram_message(stats_res)
                    kb = self.stats_engine.get_inline_keyboard(current_period=period)
                    self.send_message(stats_msg, chat_id=cid, reply_markup=kb)
                    continue

                elif text_lower in ("/pomoc", "/help"):
                    if is_admin:
                        admin_msg = (
                            "👑 <b>TWOJE CENTRUM DOWODZENIA (ADMIN)</b> 👑\n"
                            "<i>Dostępne wyłącznie dla Ciebie (Właściciela).</i>\n\n"
                            "📈 <b>STATYSTYKI & HISTORIA:</b>\n"
                            "• <code>/stats</code> – Interaktywny raport ze wszystkimi okresami\n"
                            "• <code>/stats dzis</code> – Statystyki z dzisiejszego dnia\n"
                            "• <code>/stats tydzien</code> – Statystyki z 7 dni\n"
                            "• <code>/stats miesiac</code> – Statystyki z 30 dni\n"
                            "• <code>/stats 3m</code> – Statystyki z 90 dni\n"
                            "• <code>/stats rok</code> – Statystyki z 365 dni\n"
                            "• <code>/stats all</code> – Statystyki całkowite\n"
                            "• <code>/resetstats</code> – Wyzerowanie statystyk (0/0)\n\n"
                            "👥 <b>ZARZĄDZANIE KLIENTAMI & VIP:</b>\n"
                            "• <code>/lista</code> – Pełna lista subskrybentów i ważności\n"
                            "• <code>/dodaj &lt;chat_id&gt; &lt;dni&gt;</code> – Nadanie dostępu VIP\n"
                            "• <code>/usun &lt;chat_id&gt;</code> – Zablokowanie i odebranie dostępu\n"
                            "• <code>/kod &lt;dni&gt;</code> – Wygenerowanie unikalnego kodu VIP\n"
                            "• <code>/kody</code> – Lista aktywnych kodów promocyjnych\n"
                            "• <code>/cena &lt;kwota&gt;</code> – Zmiana ceny subskrypcji\n\n"
                            "📢 <b>KOMUNIKACJA:</b>\n"
                            "• <code>/ogloszenie &lt;treść&gt;</code> – Wysłanie wiadomości do wszystkich"
                        )
                        self.send_message(admin_msg, chat_id=cid)
                    else:
                        self.send_message(
                            "ℹ️ <b>DOSTĘPNE POLECENIA:</b>\n\n"
                            "• <code>/stats</code> – Statystyki skuteczności (dzień, tydzień, miesiąc, rok)\n"
                            "• <code>/start</code> – Aktywacja konta / Powitanie\n"
                            "• <code>/kup</code> – Cennik i zakup subskrypcji VIP\n"
                            "• <code>/kod KOD</code> – Aktywacja kodu promocyjnego\n"
                            "• <code>/mojekonto</code> – Stan Twojej subskrypcji\n"
                            "• <code>/stop</code> – Wyłączenie powiadomień",
                            chat_id=cid
                        )
                    continue

                elif text_lower in ("/stop", "/wyloguj", "/odlacz"):
                    self.subscribers_data["subscribers"] = [s for s in subs if str(s.get("chat_id")) != cid]
                    self._save_subscribers_data(self.subscribers_data)
                    self.send_message("👋 <b>POWIADOMIENIA WYŁĄCZONE</b>\nTwoje urządzenie zostało odłączone.", chat_id=cid)
                    continue

        except Exception as e:
            print(f"[Telegram] Błąd w poll_pairing_requests: {e}")
            time.sleep(1.0)

        finally:
            if acquired:
                try:
                    self._poll_lock.release()
                except Exception:
                    pass
        return new_paired


    def get_pairing_info(self) -> Dict[str, Any]:
        bot_user = self.subscribers_data.get("bot_username", "OverRadarLive_bot")
        pin = self.subscribers_data.get("pairing_pin", "7777")
        join_url = f"https://t.me/{bot_user}?start={pin}"
        qr_url = f"https://api.qrserver.com/v1/create-qr-code/?size=260x260&data={urllib.parse.quote(join_url)}"
        
        return {
            "bot_username": bot_user,
            "pairing_pin": pin,
            "join_url": join_url,
            "qr_code_url": qr_url,
            "subscribers": self.subscribers_data.get("subscribers", []),
            "total_devices": len(self.get_all_chat_ids())
        }

    def _normalize_name(self, name: str) -> str:
        from engine.live_matcher import normalize_team_name
        return normalize_team_name(str(name or ""))

    def _find_existing_card_key(self, home: str, away: str) -> Optional[str]:
        from engine.live_matcher import LiveMatcher
        lock = getattr(self, '_cards_lock', None)
        if lock: lock.acquire()
        try:
            for key, card in self.active_match_cards.items():
                card_h = card.get('home_team', '')
                card_a = card.get('away_team', '')
                if LiveMatcher.is_same_fixture(home, away, card_h, card_a):
                    return key

            h_norm = self._normalize_name(home)
            a_norm = self._normalize_name(away)
            if not h_norm or not a_norm:
                return None

            GENERIC_STOP_WORDS = {
                'u15', 'u16', 'u17', 'u18', 'u19', 'u20', 'u21', 'u22', 'u23',
                'fc', 'cf', 'sc', 'ac', 'fk', 'ks', 'sp', 'cd', 'sk', 'sv', 'afc', 'bsc', 'ffc',
                'united', 'utd', 'city', 'town', 'women', 'kobiety', 'region', 'club', 'team',
                'youth', 'juniors', 'reserve', 'reserves', 'ii', 'iii', 'b', 'c', '1', '2'
            }
                
            h_words = set(w for w in h_norm.split() if len(w) >= 3 and w not in GENERIC_STOP_WORDS)
            a_words = set(w for w in a_norm.split() if len(w) >= 3 and w not in GENERIC_STOP_WORDS)

            for key, card in self.active_match_cards.items():
                card_h = card.get('home_team', '')
                card_a = card.get('away_team', '')
                ch_norm = self._normalize_name(card_h)
                ca_norm = self._normalize_name(card_a)
                if not ch_norm or not ca_norm:
                    continue

                ch_words = set(w for w in ch_norm.split() if len(w) >= 3 and w not in GENERIC_STOP_WORDS)
                ca_words = set(w for w in ca_norm.split() if len(w) >= 3 and w not in GENERIC_STOP_WORDS)

                if h_words and ch_words and a_words and ca_words:
                    h_match = bool(h_words.intersection(ch_words) or any(w in ch_norm for w in h_words if len(w) >= 4))
                    a_match = bool(a_words.intersection(ca_words) or any(w in ca_norm for w in a_words if len(w) >= 4))
                    if h_match and a_match:
                        return key

            return None
        finally:
            if lock: lock.release()

    def _is_match_already_settled(self, home: str, away: str) -> bool:
        """Sprawdza czy dany mecz został już dzisiaj definitywnie rozliczony (WIN / LOSS / VOID)."""
        from engine.live_matcher import LiveMatcher
        lock = getattr(self, '_cards_lock', None)
        if lock: lock.acquire()
        try:
            for key in list(getattr(self, 'settled_matches', {}).keys()):
                if '_vs_' in key:
                    s_h, s_a = key.split('_vs_', 1)
                    if LiveMatcher.is_same_fixture(home, away, s_h, s_a):
                        return True
            return False
        finally:
            if lock: lock.release()

    def notify_goal_signal(self, match: Dict[str, Any], signal: Dict[str, Any]) -> bool:
        if not self.config.get("enabled", False) or not self.config.get("notify_signals", True):
            return False

        home, away = match.get("home_team", ""), match.get("away_team", "")
        # Kategoryczna blokada powtórnych sygnałów dla meczów już rozliczonych dzisiaj (WIN / LOSS / VOID)
        if self._is_match_already_settled(home, away):
            return False

        stars = signal.get("stars", 2)
        min_stars = self.config.get("min_stars", 4)
        if stars < min_stars:
            return False

        # Weryfikacja limitu jednocześnie aktywnych kart w czacie (Tryb Snajper UX: max 3 mecze)
        max_active = self.config.get("max_active_cards", 3)
        existing_key = self._find_existing_card_key(home, away)
        if not existing_key and len(self.active_match_cards) >= max_active:
            # Osiągnięto limit 3 otwartych pozycji - wstrzymujemy nowe sygnały do rozstrzygnięcia
            return False

        # Mapowanie jednostek i sugerowanych stawek:
        # 5 gwiazdek (Super-Lock / Top EV) -> 3J (6.00 zł)
        # 4 gwiazdki (Wysoka pewność) -> 2J (4.00 zł)
        # 2-3 gwiazdki (Standard Value) -> 1J (2.00 zł)
        if stars >= 5:
            unit_tag = "3J"
            stake_desc = "6.00 zł (MAX)"
        elif stars == 4:
            unit_tag = "2J"
            stake_desc = "4.00 zł"
        else:
            unit_tag = "1J"
            stake_desc = "2.00 zł"

        home, away = match.get("home_team", ""), match.get("away_team", "")
        score = match.get("score_str", "0:0")
        minute = match.get("minute", 0)
        half = match.get("half", "1H")
        league = match.get("league", "Piłka Nożna")
        odds_val = signal.get("odds", 1.80)
        badge = signal.get("badge", "OVER")
        desc = signal.get("desc", "")
        sts_url = match.get('sts_url', 'https://www.sts.pl/live/pilka-nozna')
        open_url = f"http://127.0.0.1:5050/open?url={urllib.parse.quote(sts_url)}"
        danger = match.get('danger_index', 50)
        apm = match.get('apm', 0.8)

        # Precyzyjne formatowanie minuty (zawsze minuta np. 23' lub Przerwa, NIGDY sam tekst 'Live')
        stage_raw = str(match.get('stage_text', '')).lower()
        if half == 'HT' or 'przerw' in stage_raw:
            time_display = "Przerwa"
            header_time = "HT"
        elif isinstance(minute, int) and minute > 0:
            time_display = f"{minute}'"
            header_time = f"{minute}'"
        else:
            m_dig = re.search(r'(\d+)', f"{minute} {stage_raw}")
            if m_dig:
                val = int(m_dig.group(1))
                time_display = f"{val}'"
                header_time = f"{val}'"
            else:
                time_display = "23'" if half == '1H' else "68'"
                header_time = time_display

        # Smart Money (Betfair) & Pinnacle Benchmark wykorzystywane wewnątrz silnika
        from engine.smart_money_engine import SmartMoneyEngine
        from engine.pinnacle_engine import PinnacleEngine
        sm_engine = SmartMoneyEngine()
        pin_engine = PinnacleEngine()
        sm_data = sm_engine.get_smart_money_data(match, signal)
        pin_data = pin_engine.get_sharp_benchmark(match, signal)

        msg = (
            f"<b>ALERT</b> <i>({header_time})</i>\n\n"
            f"⚽️ <b>{home} vs {away}</b>  <code>[{score}]</code>\n"
            f"🏆 <b>Liga:</b> {league}\n"
            f"⏱️ <b>Czas:</b> {time_display}\n\n"
            f"🎯 <b>Rekomendacja:</b> <code>{badge}</code>\n"
            f"💰 <b>Sugerowana Stawka:</b> <code>{unit_tag}</code>\n"
            f"📈 <b>Kurs STS:</b> <b>{odds_val:.2f}</b>\n"
            f"🔥 <b>{danger}%</b> (APM: {apm})"
        )

        now = time.time()
        existing_key = self._find_existing_card_key(home, away)
        if self.config.get("live_update_mode", True) and existing_key and existing_key in self.active_match_cards:
            card = self.active_match_cards[existing_key]
            dev_msgs = card.get("device_messages", {})

            # ŻELAZNA ZASADA: ZABLOKOWANIE REKOMENDACJI (BRAK SKAKANIA / FLAPPINGU)
            # Raz podany typ (np. OVER 1.5 FT) jest nienaruszalny aż do rozliczenia meczu.
            frozen_badge = card.get("badge", badge)
            frozen_unit_tag = card.get("unit_tag", unit_tag)
            init_odds = card.get("initial_odds", odds_val)

            # Aktualizacja dynamicznych statystyk meczu w pamięci
            card["last_seen_minute"] = minute
            card["last_seen_score"] = score
            card["last_seen_time"] = now
            card["last_odds"] = odds_val
            card["danger"] = danger
            card["apm"] = apm

            # Konstrukcja wiadomości ze stałą rekomendacją i aktualnym czasem/wynikiem
            update_msg = (
                f"<b>ALERT</b> <i>({header_time})</i>\n\n"
                f"⚽️ <b>{home} vs {away}</b>  <code>[{score}]</code>\n"
                f"🏆 <b>Liga:</b> {league}\n"
                f"⏱️ <b>Czas:</b> {time_display}\n\n"
                f"🎯 <b>Rekomendacja:</b> <code>{frozen_badge}</code>\n"
                f"💰 <b>Sugerowana Stawka:</b> <code>{frozen_unit_tag}</code>\n"
                f"📈 <b>Kurs STS:</b> <b>{init_odds:.2f}</b>\n"
                f"🔥 <b>{danger}%</b> (APM: {apm})"
            )

            # Throttle: edytuj wiadomość na Telegramie tylko jeśli minęło >= 45s lub zmienił się wynik
            score_changed = (score != card.get("initial_score"))
            time_since_edit = now - card.get("last_edit_time", 0)
            if card.get("last_text") != update_msg and (score_changed or time_since_edit >= 45):
                self.edit_message_all(dev_msgs, update_msg)
                card["last_text"] = update_msg
                card["last_edit_time"] = now
                self._save_cards()
            return True

        dev_msgs = self.send_message_all(msg)
        if dev_msgs:
            new_key = f"{self._normalize_name(home)}_vs_{self._normalize_name(away)}"
            
            # Natywne powiadomienie Windows Toast oraz dźwięk (niezależnie od Telegrama)
            try:
                from engine.notifications import send_windows_notification, play_surebet_sound
                send_windows_notification(
                    title=f"⚽ ALARM GOAL: {badge} ({odds_val:.2f})",
                    msg=f"{home} vs {away} [{score}] ({time_display})\nIndeks: {danger}% | Stawka: {unit_tag}"
                )
                play_surebet_sound()
            except Exception:
                pass

            # Dystrybucja na kanał FREE (Darmowy Typ Dnia - max 2 alerty dziennie)
            free_cid = self.config.get("free_channel_id")
            if free_cid:
                today_str = time.strftime('%Y-%m-%d')
                free_data = self.config.setdefault("_free_picks_tracker", {})
                picks_today = free_data.setdefault(today_str, [])
                if len(picks_today) < 2 and stars >= 4:
                    picks_today.append(new_key)
                    self.save_config(self.config)
                    free_msg = (
                        "🎁 <b>DARMOWY TYP DNIA | OVERRADAR LIVE</b> ⚽\n\n"
                        + msg + "\n\n"
                        + "<i>To jest bezpłatna próbka możliwości algorytmu.</i>\n"
                        + "👑 <b>Chcesz wszystkie alerty na żywo 24/7 i Tryb Snajper (91.4%)?</b>\n"
                        + "👉 Odbierz 3 dni darmowego Trialu VIP!"
                    )
                    res_free = self.send_message(free_msg, chat_id=free_cid)
                    if res_free.get("success"):
                        f_mid = res_free.get("result", {}).get("message_id")
                        if f_mid:
                            dev_msgs[str(free_cid)] = f_mid
            badge_u = badge.upper()
            sig_type = str(signal.get("type", "")).upper()
            try:
                init_tot = sum(map(int, score.split(":")))
            except Exception:
                init_tot = 0

            # Precyzyjne wyznaczenie okresu docelowego: TYLKO wyraźnie oznaczone HT są rynkami 1. połowy
            is_ht_market = any(kw in badge_u for kw in ['HT', '1. POŁ', '1.POŁ', '1H', 'POŁOWA', 'FIRST HALF']) or \
                           any(kw in sig_type for kw in ['HT', '05_HT', '15_HT'])
            target_period = '1H' if is_ht_market else 'FT'

            match_over = re.search(r'OVER\s+(\d+(?:\.\d+)?)', badge_u)
            if match_over:
                line_val = float(match_over.group(1))
                target_goals = int(line_val + 0.5)
            else:
                target_goals = init_tot + 1

            self.active_match_cards[new_key] = {
                "device_messages": dev_msgs,
                "home_team": home,
                "away_team": away,
                "league": league,
                "last_text": msg,
                "last_odds": odds_val,
                "initial_odds": odds_val,
                "initial_minute": minute,
                "initial_score": score,
                "initial_goals": init_tot,
                "target_goals": target_goals,
                "target_period": target_period,
                "smart_money_section": "",
                "created_at": now,
                "last_edit_time": now,
                "last_seen_time": now,
                "last_seen_minute": minute,
                "last_seen_score": score,
                "last_seen_half": half,
                "sts_url": sts_url,
                "badge": badge,
                "unit_tag": unit_tag,
                "stars": stars,
                "desc": desc,
                "danger": danger,
                "apm": apm,
                "initial_danger": danger,
                "initial_apm": apm
            }
            self.stats_engine.record_signal(match, signal, unit_tag)
            try:
                self.ba_sync.create_bet_async(match, signal)
            except Exception:
                pass
            self._save_cards()
            self._last_emitted_signal_time = now
            return True
        return False


    def check_and_update_match_status(self, match: Dict[str, Any]) -> bool:
        if not self.config.get("enabled", False):
            return False

        home = match.get('home_team', '')
        away = match.get('away_team', '')
        existing_key = self._find_existing_card_key(home, away)
        if not existing_key or existing_key not in self.active_match_cards:
            return False

        card = self.active_match_cards[existing_key]
        dev_msgs = card.get("device_messages", {})
        if not dev_msgs and card.get("message_id"):
            dev_msgs = {str(self.config.get("chat_id")): card.get("message_id")}

        current_score = match.get("score_str", "0:0")
        minute = match.get('minute', 0)
        half = match.get('half', '1H')
        stage_text = match.get('stage_text', f"{minute}'")
        is_live = match.get('is_live', True)
        status_code = str(match.get('status_code', ''))

        try:
            curr_tot = sum(map(int, current_score.split(":")))
        except Exception:
            curr_tot = 0

        target_goals = card.get('target_goals', 1)
        target_period = card.get('target_period', 'FT')
        badge = card.get('badge', 'OVER')
        odds_val = card.get('last_odds', 1.70)
        league = card.get('league', match.get('league', 'Piłka Nożna'))
        unit_tag = card.get('unit_tag', '1J')
        units = 1
        if '3' in unit_tag: units = 3
        elif '2' in unit_tag: units = 2
        now = time.time()

        if half == 'HT' or 'przerw' in str(stage_text).lower() or str(stage_text).strip() == '13':
            time_display = "Przerwa"
        elif half == 'FT' or 'koniec' in str(stage_text).lower() or str(stage_text).strip() == '3':
            time_display = f"{minute}'" if minute > 0 else "Koniec meczu"
        elif isinstance(minute, int) and minute > 0:
            time_display = f"{minute}'"
        else:
            m_dig = re.search(r'(\d+)', f"{minute} {stage_text}")
            if m_dig:
                time_display = f"{m_dig.group(1)}'"
            else:
                time_display = "23'" if half == '1H' else "68'"

        # 1. SCENARIUSZ: TYP WYGRANY (ZIELONY ZNACZEK OK ✅ 🟢)
        is_won = False
        if target_period == '1H':
            if curr_tot >= target_goals:
                is_won = True
            elif match.get('ht_score'):
                try:
                    ht_tot = sum(map(int, match['ht_score'].split(':')))
                    if ht_tot >= target_goals:
                        is_won = True
                except Exception:
                    pass
        else:
            if curr_tot >= target_goals:
                is_won = True

        danger = match.get('danger_index', card.get('danger', 50))
        apm = match.get('apm', card.get('apm', 0.8))

        init_m = card.get('initial_minute', card.get('last_seen_minute', minute))
        init_odds = card.get('initial_odds', card.get('last_odds', 1.70))

        if is_won:
            win_time = f"{minute}'" if (minute > 0 and minute <= 90) else (time_display if time_display != "Koniec meczu" else ("45'" if target_period == '1H' else "90'"))
            profit_units = round(units * (init_odds - 1.0), 2)
            win_msg = (
                f"✅ <b>ALERT</b> <i>(Trafiono: {win_time})</i>\n\n"
                f"⚽️ <b>{home} vs {away}</b>  <code>[{current_score}]</code>\n"
                f"🏆 <b>Liga:</b> {league}\n"
                f"⏱️ <b>Typ podany w:</b> <b>{init_m}' min</b> | <b>Trafiono w:</b> <b>{win_time}</b>\n\n"
                f"🎯 <b>Rekomendacja:</b> <code>{badge}</code>\n"
                f"💰 <b>Sugerowana Stawka:</b> <code>{unit_tag}</code>\n"
                f"📈 <b>Trafiony Kurs STS:</b> <b>{init_odds:.2f}</b>\n"
                f"🔥 <b>{danger}%</b> (APM: {apm})\n\n"
                f"🎉 <b>STATUS:</b> <b>WYGRANA +{profit_units:.2f} J</b>"
            )
            self.edit_message_all(dev_msgs, win_msg)
            self.active_match_cards.pop(existing_key, None)
            self.settled_matches[existing_key] = time.time()
            self.stats_engine.settle_signal(home, away, "WON", current_score, final_odds=init_odds)
            try:
                from engine.notifications import send_windows_notification, play_surebet_sound
                send_windows_notification(
                    title=f"✅ TRAFIONE! GOL: {home} vs {away} [{current_score}]",
                    msg=f"Typ: {badge} (kurs {init_odds:.2f}) | WYGRANA +{profit_units:.2f}J"
                )
                play_surebet_sound()
            except Exception:
                pass
            try:
                self.ba_sync.settle_bet_async(match, "WON")
            except Exception:
                pass
            self._save_cards()
            return True

        # 2. SCENARIUSZ: MECZ ODWOŁANY / PRZERWANY (ZWROT STAWKI 🟡 🔄)
        st_lower = str(stage_text).lower()
        if any(w in st_lower for w in ['odwołan', 'przerwan', 'przełożon', 'walkower', 'abandoned', 'postponed', 'cancelled', 'canc']):
            void_msg = (
                f"🟡 <b>ALERT</b> <i>({stage_text})</i>\n\n"
                f"⚽️ <b>{home} vs {away}</b>  <code>[{current_score}]</code>\n"
                f"🏆 <b>Liga:</b> {league}\n"
                f"⏱️ <b>Typ podany w:</b> <b>{init_m}' min</b>\n\n"
                f"🎯 <b>Rekomendacja:</b> <code>{badge}</code>\n"
                f"💰 <b>Sugerowana Stawka:</b> <code>{unit_tag}</code>\n"
                f"📈 <b>Kurs STS:</b> <b>{init_odds:.2f}</b>\n"
                f"🔥 <b>{danger}%</b> (APM: {apm})\n\n"
                f"🔄 <b>STATUS:</b> <b>ZWROT (VOID)</b>"
            )
            self.edit_message_all(dev_msgs, void_msg)
            self.active_match_cards.pop(existing_key, None)
            self.settled_matches[existing_key] = time.time()
            self.stats_engine.settle_signal(home, away, "VOID", current_score, final_odds=init_odds)
            try:
                self.ba_sync.settle_bet_async(match, "VOID")
            except Exception:
                pass
            self._save_cards()
            return True

        # 3. SCENARIUSZ: TYP PRZEGRANY (CZERWONY ZNACZEK X ❌ 🔴)
        is_period_finished = False
        st_low = str(stage_text).lower()

        if target_period == '1H':
            # 1H kończy się tylko w przerwie HT, w 2H lub po zakończeniu meczu
            is_1h_over = (half in ('HT', '2H', 'FT') or 'koniec' in st_low or (not is_live and minute >= 45))
            if is_1h_over:
                if match.get('ht_score'):
                    try:
                        ht_tot = sum(map(int, match['ht_score'].split(':')))
                        if ht_tot < target_goals:
                            is_period_finished = True
                    except Exception:
                        if curr_tot < target_goals:
                            is_period_finished = True
                else:
                    if curr_tot < target_goals:
                        is_period_finished = True
        elif target_period == 'FT':
            # FT kończy się TYLKO I WYŁĄCZNIE po upływie 90+ minut i końcowym gwizdku sędziego
            # OCHRONA ABSOLUTNA: Mecz trwający na żywo, w 1H, HT lub 2H, lub przed 88. minutą NIGDY nie jest przegrany!
            if is_live or half in ('1H', 'HT', '2H') or (isinstance(minute, int) and 0 < minute < 88):
                is_ft_over = False
            else:
                is_ft_over = (
                    (half == 'FT' or 'koniec' in st_low or 'ended' in st_low or status_code in ('3', '8', '9'))
                    and not is_live
                    and (minute >= 88 or 'koniec' in st_low or status_code in ('3', '8', '9'))
                )
            if is_ft_over and curr_tot < target_goals:
                is_period_finished = True

        if is_period_finished:
            loss_time = f"{minute}'" if (minute > 0 and minute <= 90) else ("90'" if target_period == 'FT' else "45'")
            loss_units = float(units)
            orig_danger = card.get('initial_danger', card.get('danger', 85))
            orig_apm = card.get('initial_apm', card.get('apm', 0.9))
            loss_msg = (
                f"❌ <b>ALERT</b> <i>(Rozliczenie: {loss_time})</i>\n\n"
                f"⚽️ <b>{home} vs {away}</b>  <code>[{current_score}]</code>\n"
                f"🏆 <b>Liga:</b> {league}\n"
                f"⏱️ <b>Typ podany w:</b> <b>{init_m}' min</b> | <b>Koniec:</b> <b>{loss_time}</b>\n\n"
                f"🎯 <b>Rekomendacja:</b> <code>{badge}</code>\n"
                f"💰 <b>Sugerowana Stawka:</b> <code>{unit_tag}</code>\n"
                f"📈 <b>Kurs początkowy STS:</b> <b>{init_odds:.2f}</b>\n"
                f"🔥 <b>{orig_danger}%</b> (APM: {orig_apm})\n\n"
                f"📉 <b>STATUS:</b> <b>PRZEGRANA -{loss_units:.2f} J</b>"
            )
            self.edit_message_all(dev_msgs, loss_msg)
            self.active_match_cards.pop(existing_key, None)
            self.settled_matches[existing_key] = time.time()
            self.stats_engine.settle_signal(home, away, "LOST", current_score, final_odds=init_odds)
            try:
                self.ba_sync.settle_bet_async(match, "LOST")
            except Exception:
                pass
            self._save_cards()
            return True

        # 4. SCENARIUSZ: LIVE UPDATE IN-PLACE (Płynna aktualizacja minuty, wyniku i kursu w tej samej wiadomości)
        if self.config.get("live_update_mode", True) and is_live and not is_won and not is_period_finished:
            latest_odds = odds_val
            for mkt in match.get('live_markets', []):
                if badge.upper().replace(' ', '') in str(mkt.get('name', '')).upper().replace(' ', ''):
                    latest_odds = mkt.get('odds', odds_val)
                    break

            orig_danger = card.get('initial_danger', card.get('danger', 85))
            orig_apm = card.get('initial_apm', card.get('apm', 0.9))
            orig_odds = card.get('initial_odds', card.get('last_odds', 1.70))
            init_m = card.get('initial_minute', '')
            time_info = f"{time_display} (Typ z: {init_m}')" if init_m else time_display
            
            odds_str = f"<b>{orig_odds:.2f}</b>"
            if 1.10 <= latest_odds <= 3.20 and abs(latest_odds - orig_odds) > 0.05:
                odds_str += f" <i>(Aktualny: {latest_odds:.2f})</i>"

            updated_msg = (
                f"<b>ALERT</b> <i>({time_display})</i>\n\n"
                f"⚽️ <b>{home} vs {away}</b>  <code>[{current_score}]</code>\n"
                f"🏆 <b>Liga:</b> {league}\n"
                f"⏱️ <b>Czas:</b> {time_info}\n\n"
                f"🎯 <b>Rekomendacja:</b> <code>{badge}</code>\n"
                f"💰 <b>Sugerowana Stawka:</b> <code>{unit_tag}</code>\n"
                f"📈 <b>Kurs STS:</b> {odds_str}\n"
                f"🔥 <b>{orig_danger}%</b> (APM: {orig_apm})"
            )

            score_changed = (current_score != card.get("last_seen_score"))
            time_since_edit = now - card.get("last_edit_time", 0)
            
            card["last_seen_score"] = current_score
            card["last_seen_minute"] = minute
            card["last_seen_time"] = now
            if latest_odds <= 3.20:
                card["last_odds"] = latest_odds

            if card.get("last_text") != updated_msg and (score_changed or time_since_edit >= 45):
                self.edit_message_all(dev_msgs, updated_msg)
                card["last_text"] = updated_msg
                card["last_edit_time"] = now
                self._save_cards()
            return False

    def auto_settle_active_cards(self, live_matches: List[Dict[str, Any]], finished_matches: Optional[List[Dict[str, Any]]] = None) -> int:
        """
        Natychmiastowe i inteligentne rozliczanie wszystkich aktywnych sygnałów:
        1. Mecze trwające na żywo w STS -> sprawdza natychmiast czy padł gol (WON ✅) lub aktualizuje minutę/wynik.
        2. Mecze zakończone w bazie (Flashscore/STS) -> natychmiast rozlicza status końcowy (WON ✅ / LOST ❌).
        3. Mecze, które zniknęły z oferty STS Live po upływie czasu gry -> sprawdza i rozlicza definitywny wynik końcowy.
        """
        if not self.active_match_cards:
            return 0

        settled_count = 0
        now = time.time()
        finished_list = finished_matches or []
        
        lock = getattr(self, '_cards_lock', None)
        if lock: lock.acquire()
        try:
            active_keys = list(self.active_match_cards.keys())

            for key in active_keys:
                if key not in self.active_match_cards:
                    continue
                card = self.active_match_cards[key]
                card_home = card.get('home_team', '')
                card_away = card.get('away_team', '')
                if not card_home or not card_away:
                    continue

                # 1. Sprawdź czy mecz jest w feedzie LIVE (STS lub Flashscore)
                from engine.live_matcher import LiveMatcher
                live_m = next((m for m in live_matches if LiveMatcher.is_same_fixture(card_home, card_away, m.get('home_team', ''), m.get('away_team', ''))), None)
                if live_m:
                    card['last_seen_time'] = now
                    card['last_seen_minute'] = live_m.get('minute', card.get('last_seen_minute', 0))
                    card['last_seen_score'] = live_m.get('score_str', card.get('last_seen_score', '0:0'))
                    card['last_seen_half'] = live_m.get('half', card.get('last_seen_half', '1H'))
                    if live_m.get('sts_url'):
                        card['sts_url'] = live_m['sts_url']

                    if self.check_and_update_match_status(live_m):
                        settled_count += 1
                    continue

                # 2. Sprawdź czy mecz jest w feedzie meczy zakończonych (Flashscore / STS)
                fin_m = next((m for m in finished_list if LiveMatcher.is_same_fixture(card_home, card_away, m.get('home_team', ''), m.get('away_team', ''))), None)
                if fin_m:
                    fin_m_copy = dict(fin_m)
                    fin_m_copy['home_team'] = card_home
                    fin_m_copy['away_team'] = card_away
                    if self.check_and_update_match_status(fin_m_copy):
                        settled_count += 1
                    continue

                # 3. Mecz zniknął z oferty STS Live (zakończył się)
                card_age = now - card.get('created_at', now)
                last_minute = card.get('last_seen_minute', 0)
                if not last_minute:
                    m_min = re.search(r'\((\d+)\'\)', card.get('last_text', ''))
                    if m_min:
                        last_minute = int(m_min.group(1))
                target_period = card.get('target_period', 'FT')
                last_score = card.get('last_seen_score', card.get('initial_score', '0:0'))

                is_finished_event = False
                time_since_seen = now - card.get('last_seen_time', card.get('created_at', now))
                if target_period == '1H':
                    # 1H kończy się tylko gdy minęła 45. minuta i mecz zniknął z 1H na min. 4 minuty
                    if last_minute >= 45 and time_since_seen > 240:
                        is_finished_event = True
                    elif card_age > 3600: # Ponad 60 minut od sygnału z 1. połowy
                        is_finished_event = True
                elif target_period == 'FT':
                    # Mecz kończy się gdy osiągnął min. 90. minutę i zniknął z STS Live na >180s, LUB gdy upłynął realistyczny czas trwania
                    init_m = card.get('initial_minute', last_minute or 0)
                    rem_mins = max(10, 95 - (init_m if isinstance(init_m, int) else 45))
                    if isinstance(init_m, int) and init_m < 45:
                        rem_mins += 15 # dolicz przerwę HT
                    max_expected_seconds = max(1800, (rem_mins + 15) * 60)

                    if last_minute >= 90 and time_since_seen > 180:
                        is_finished_event = True
                    elif time_since_seen > 300 and card_age >= max_expected_seconds:
                        is_finished_event = True
                    elif card_age > 7200: # Zapasowy limit 120 minut
                        is_finished_event = True

                if is_finished_event:
                    synthetic_finished = {
                        'home_team': card_home,
                        'away_team': card_away,
                        'league': card.get('league', 'Piłka Nożna'),
                        'score_str': last_score,
                        'home_score': int(last_score.split(':')[0]) if ':' in last_score else 0,
                        'away_score': int(last_score.split(':')[1]) if ':' in last_score else 0,
                        'minute': 90 if target_period == 'FT' else 45,
                        'half': 'FT' if target_period == 'FT' else 'HT',
                        'stage_text': 'Koniec meczu',
                        'is_live': False
                    }
                    if self.check_and_update_match_status(synthetic_finished):
                        settled_count += 1

            return settled_count
        finally:
            if lock: lock.release()

    def check_and_notify_goal_event(self, match: Dict[str, Any]) -> bool:
        return self.check_and_update_match_status(match)

    def check_and_notify_whale_anomaly(self, match: Dict[str, Any], signals: Optional[List[Dict[str, Any]]] = None) -> bool:
        """
        Weryfikuje czy dany mecz wykazuje anomalię zrzutu kapitału (Whale Surge)
        i wysyła dedykowany alert anomalii giełdowej na Telegram.
        Gwarantuje 100% brak duplikatów (zapis na dysku w telegram_whale_anomalies.json).
        """
        if not self.config.get("enabled", False):
            return False
            
        home = match.get("home_team", "")
        away = match.get("away_team", "")
        if not home or not away:
            return False
            
        # Jeśli mecz ma już aktywną kartę sygnału lub został już rozliczony -> NIE wysyłaj osobnego alertu
        if self._find_existing_card_key(home, away) or self._is_match_already_settled(home, away):
            return False
            
        anomaly_file = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "telegram_whale_anomalies.json")
        seen_anomalies = {}
        if os.path.exists(anomaly_file):
            try:
                with open(anomaly_file, 'r', encoding='utf-8') as f:
                    seen_anomalies = json.load(f)
            except Exception:
                seen_anomalies = {}
                
        match_key = f"{self._normalize_name(home)}_vs_{self._normalize_name(away)}"
        now = time.time()
        last_sent = seen_anomalies.get(match_key, 0)
        # Twarda blokada: max 1 alert na ten sam mecz w ciągu 6 godzin
        if now - last_sent < 21600:
            return False
            
        from engine.smart_money_engine import SmartMoneyEngine
        sm = SmartMoneyEngine()
        sig = signals[0] if signals else None
        anomaly = sm.detect_and_format_anomaly(match, sig)
        if not anomaly:
            return False
            
        msg = anomaly.get("msg_text", "")
        dev_msgs = self.send_message_all(msg)
        if dev_msgs:
            seen_anomalies[match_key] = now
            try:
                with open(anomaly_file, 'w', encoding='utf-8') as f:
                    json.dump(seen_anomalies, f, indent=2)
            except Exception as ex:
                print(f"[Whale] Błąd zapisu telegram_whale_anomalies.json: {ex}")
            return True
        return False

    def notify_daily_goal_achieved(self, profit: float, target: float) -> bool:
        if not self.config.get("enabled", False) or not self.config.get("notify_daily_goal", True):
            return False
        msg = (
            f"🎉 <b>CEL DZIENNY OSIĄGNIĘTY!</b> 🏆\n\n"
            f"💰 <b>Dzisiejszy Zysk:</b> <code>+{profit:.2f} zł</code>\n"
            f"🎯 <b>Cel Dnia:</b> +{target:.2f} zł\n\n"
            f"🛡️ <b>ZASADA MISTRZA:</b> Zamknij aplikację na dziś i ciesz się wygraną!"
        )
        res = self.send_message_all(msg)
        return bool(res)
