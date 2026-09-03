import os
import json
import time
import datetime
import threading
import urllib.request
import urllib.parse
from typing import Dict, Any, Optional, List

CONFIG_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bet_analytix_config.json")
SESSION_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bet_analytix_session.json")
BETS_MAP_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bet_analytix_bets.json")

API_BASE = "https://api-v2.bet-analytix.com"

class BetAnalytixSync:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(BetAnalytixSync, cls).__new__(cls)
            cls._instance._init_sync()
        return cls._instance

    def _init_sync(self):
        self.lock = threading.RLock()
        self.config = self.load_config()
        self.session = self.load_session()
        self.bets_map = self.load_bets_map()

    def load_config(self) -> Dict[str, Any]:
        default_cfg = {
            "enabled": True,
            "email": "marszalqwot@gmail.com",
            "password": "Lukasz504!",
            "bankroll_id": 1921642,
            "bankroll_name": "OverRadar Live",
            "stake_unit_value": 2.0,
            "use_units_as_stake": False,
            "bookmaker_id": 2,
            "auto_settle": True
        }
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    default_cfg.update(data)
            except Exception as e:
                print(f"[Bet-Analytix] Błąd odczytu config: {e}")
        return default_cfg

    def save_config(self, new_cfg: Optional[Dict[str, Any]] = None) -> bool:
        if new_cfg:
            self.config.update(new_cfg)
        try:
            with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
                json.dump(self.config, f, indent=2, ensure_ascii=False)
            return True
        except Exception as e:
            print(f"[Bet-Analytix] Błąd zapisu config: {e}")
            return False

    def load_session(self) -> Dict[str, Any]:
        if os.path.exists(SESSION_FILE):
            try:
                with open(SESSION_FILE, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                print(f"[Bet-Analytix] Błąd odczytu sesji: {e}")
        return {}

    def save_session(self, session_data: Dict[str, Any]):
        self.session = session_data
        try:
            with open(SESSION_FILE, 'w', encoding='utf-8') as f:
                json.dump(session_data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"[Bet-Analytix] Błąd zapisu sesji: {e}")

    def load_bets_map(self) -> Dict[str, Any]:
        if os.path.exists(BETS_MAP_FILE):
            try:
                with open(BETS_MAP_FILE, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                print(f"[Bet-Analytix] Błąd odczytu mapy zakładów: {e}")
        return {}

    def save_bets_map(self):
        with self.lock:
            try:
                with open(BETS_MAP_FILE, 'w', encoding='utf-8') as f:
                    json.dump(self.bets_map, f, indent=2, ensure_ascii=False)
            except Exception as e:
                print(f"[Bet-Analytix] Błąd zapisu mapy zakładów: {e}")

    def _get_headers(self, with_auth: bool = True) -> Dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "app": "appBax",
            "sid": "152120",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Origin": "https://app.bet-analytix.com",
            "Referer": "https://app.bet-analytix.com/"
        }
        if with_auth:
            token = self.session.get("accessToken") or self.session.get("token")
            if token:
                headers["Authorization"] = f"Bearer {token}"
        return headers

    def login(self, email: Optional[str] = None, password: Optional[str] = None) -> Dict[str, Any]:
        em = email or self.config.get("email")
        pw = password or self.config.get("password")
        if not em or not pw:
            return {"success": False, "error": "Brak podanego adresu e-mail lub hasła"}

        url = f"{API_BASE}/auth/login"
        payload = json.dumps({"email": em.strip(), "password": pw.strip()}).encode('utf-8')
        req = urllib.request.Request(url, data=payload, headers=self._get_headers(with_auth=False), method="POST")

        try:
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = json.loads(resp.read().decode('utf-8'))
                if "accessToken" in data or "token" in data:
                    self.save_session(data)
                    self.config["email"] = em
                    self.config["password"] = pw
                    self.save_config()
                    self.get_bankrolls()
                    return {"success": True, "data": data}
                return {"success": False, "error": "Odpowiedź nie zawiera tokena autoryzacji"}
        except urllib.error.HTTPError as e:
            try:
                err_data = json.loads(e.read().decode('utf-8'))
                return {"success": False, "error": err_data.get("message") or err_data.get("error") or str(err_data)}
            except Exception:
                return {"success": False, "error": f"Błąd HTTP {e.code}"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def refresh_token(self) -> bool:
        refresh = self.session.get("refreshToken") or self.session.get("refresh_token")
        if not refresh:
            return False
        url = f"{API_BASE}/auth/refresh-token"
        payload = json.dumps({"refreshToken": refresh}).encode('utf-8')
        req = urllib.request.Request(url, data=payload, headers=self._get_headers(with_auth=False), method="POST")
        try:
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = json.loads(resp.read().decode('utf-8'))
                if "accessToken" in data:
                    self.session.update(data)
                    self.save_session(self.session)
                    return True
        except Exception:
            pass
        return False

    def get_bankrolls(self) -> List[Dict[str, Any]]:
        url = f"{API_BASE}/bankrolls/paginated?page=1"
        req = urllib.request.Request(url, headers=self._get_headers(with_auth=True), method="GET")
        try:
            with urllib.request.urlopen(req, timeout=8) as resp:
                res = json.loads(resp.read().decode('utf-8'))
                bankrolls = res.get("data", []) if isinstance(res, dict) else res
                if bankrolls:
                    target_b = next((b for b in bankrolls if "overradar" in str(b.get("name", "")).lower()), bankrolls[0])
                    self.config["bankroll_id"] = target_b.get("id")
                    self.config["bankroll_name"] = target_b.get("name", "OverRadar Live")
                    self.save_config()
                return bankrolls
        except urllib.error.HTTPError as e:
            if e.code == 401:
                if self.refresh_token() or self.login():
                    return self.get_bankrolls()
        except Exception as e:
            print(f"[Bet-Analytix] Błąd pobierania bankrolli: {e}")
        return []

    def get_status(self) -> Dict[str, Any]:
        has_token = bool(self.session.get("accessToken") or self.session.get("token"))
        b_name = self.config.get("bankroll_name", "OverRadar Live")
        b_id = self.config.get("bankroll_id", 1921642)
        return {
            "enabled": self.config.get("enabled", True),
            "authenticated": has_token,
            "email": self.config.get("email", "marszalqwot@gmail.com"),
            "bankroll_id": b_id,
            "bankroll_name": b_name,
            "total_synced_bets": len(self.bets_map)
        }

    def create_bet_async(self, match: Dict[str, Any], signal: Dict[str, Any]):
        t = threading.Thread(target=self.create_bet, args=(match, signal), daemon=True, name="BetAnalytixCreateBet")
        t.start()

    def create_bet(self, match: Dict[str, Any], signal: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if not self.config.get("enabled", True):
            return None

        if not self.session.get("accessToken") and not self.session.get("token"):
            if not self.login():
                return None

        bankroll_seq_id = int(self.config.get("id_bankroll", 1))
        home = match.get("home_team", "").strip()
        away = match.get("away_team", "").strip()
        match_key = f"{home.lower()} vs {away.lower()}"

        if match_key in self.bets_map and self.bets_map[match_key].get("status") == "PENDING":
            return self.bets_map[match_key]

        now_utc = datetime.datetime.now(datetime.timezone.utc)
        date_str = now_utc.strftime("%Y-%m-%d")
        time_str = now_utc.strftime("%H:%M")

        odds = float(signal.get("odds", 1.80))
        stars = int(signal.get("stars", 2))
        badge = signal.get("badge", "OVER")
        league = match.get("league", "Piłka Nożna")

        if stars >= 5:
            units = 3.0
        elif stars == 4:
            units = 2.0
        else:
            units = 1.0

        if self.config.get("use_units_as_stake", False):
            stake_val = float(units)
        else:
            stake_val = round(units * float(self.config.get("stake_unit_value", 10.0)), 2)

        label_txt = f"{home} vs {away} - {badge}"

        commission_obj = {
            "percentage": int(self.config.get("tax_rate_percent", 12)),
            "base": self.config.get("commission_base", "grossGain"),
            "applyOnLoss": False
        } if self.config.get("deduct_tax", False) else None

        bet_data = {
            "bankroll": bankroll_seq_id,
            "date": date_str,
            "time": time_str,
            "type": 1,
            "stake": stake_val,
            "bookmaker": self.config.get("bookmaker_id", 2),
            "commission": commission_obj,
            "selections": [
                {
                    "label": label_txt,
                    "odds": odds,
                    "sport": 1,
                    "status": 0,
                    "bookmaker": self.config.get("bookmaker_id", 2),
                    "competition": None
                }
            ]
        }

        url = f"{API_BASE}/bet"
        req = urllib.request.Request(
            url,
            data=json.dumps(bet_data).encode('utf-8'),
            headers=self._get_headers(with_auth=True),
            method="POST"
        )

        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                res = json.loads(resp.read().decode('utf-8'))
                bet_item = res[0] if isinstance(res, list) and len(res) > 0 else (res if isinstance(res, dict) else {})
                bet_id = bet_item.get("id")
                if bet_id:
                    with self.lock:
                        self.bets_map[match_key] = {
                            "bet_id": bet_id,
                            "match": f"{home} vs {away}",
                            "badge": badge,
                            "odds": odds,
                            "stake": stake_val,
                            "status": "PENDING",
                            "created_at": time.time()
                        }
                        self.save_bets_map()
                    print(f"[Bet-Analytix] [OK] Dodano zaklad #{bet_id} na OverRadar Live: {label_txt} (@ {odds:.2f})")
                    return bet_item
        except urllib.error.HTTPError as e:
            if e.code == 401:
                if self.refresh_token() or self.login():
                    return self.create_bet(match, signal)
            print(f"[Bet-Analytix] Błąd HTTP dodawania zakładu ({e.code}): {e.read().decode('utf-8', errors='ignore')}")
        except Exception as e:
            print(f"[Bet-Analytix] Błąd dodawania zakładu: {e}")
        return None

    def settle_bet_async(self, match: Dict[str, Any], outcome: str):
        t = threading.Thread(target=self.settle_bet, args=(match, outcome), daemon=True, name="BetAnalytixSettleBet")
        t.start()

    def settle_bet(self, match: Dict[str, Any], outcome: str) -> bool:
        if not self.config.get("enabled", True) or not self.config.get("auto_settle", True):
            return False

        home = match.get("home_team", "").strip()
        away = match.get("away_team", "").strip()
        match_key = f"{home.lower()} vs {away.lower()}"

        bet_info = self.bets_map.get(match_key)
        if not bet_info or not bet_info.get("bet_id"):
            return False

        bet_id = bet_info["bet_id"]

        if outcome == "WON":
            status_code = 1
        elif outcome == "LOST":
            status_code = 2
        elif outcome == "VOID":
            status_code = 3
        else:
            return False

        # Pobierz bieżący stan zakładu i zaktualizuj status
        url = f"{API_BASE}/bet/{bet_id}"
        payload = json.dumps({"status": status_code}).encode('utf-8')
        req = urllib.request.Request(
            url,
            data=payload,
            headers=self._get_headers(with_auth=True),
            method="PUT"
        )

        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                with self.lock:
                    self.bets_map[match_key]["status"] = outcome
                    self.bets_map[match_key]["settled_at"] = time.time()
                    self.save_bets_map()
                print(f"[Bet-Analytix] [SETTLE] Rozliczono zaklad #{bet_id} na OverRadar Live jako: {outcome}")
                return True
        except urllib.error.HTTPError as e:
            if e.code == 401:
                if self.refresh_token() or self.login():
                    return self.settle_bet(match, outcome)
            print(f"[Bet-Analytix] Błąd HTTP rozliczania zakładu #{bet_id} ({e.code}): {e.read().decode('utf-8', errors='ignore')}")
        except Exception as e:
            print(f"[Bet-Analytix] Błąd rozliczania zakładu #{bet_id}: {e}")
        return False
