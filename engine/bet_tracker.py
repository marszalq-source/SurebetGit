"""
Moduł Dziennika Typera i Zarządzania Kapitałem (Personal Bet Tracker & Bankroll Manager).
Śledzi postawione zakłady, zysk/stratę, skuteczność (Win Rate %), Yield % oraz dzienny cel +10 zł.
"""
import json
import os
import time
import re
from typing import Dict, Any, List, Optional


BETS_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "user_bets.json")

class BetTracker:
    def __init__(self):
        self.file_path = BETS_FILE
        self._ensure_file_exists()

    def _ensure_file_exists(self):
        if not os.path.exists(self.file_path):
            initial_data = {
                "initial_bankroll": 48.0,
                "current_bankroll": 44.0,  # Po postawieniu 4 zł
                "daily_target_profit": 10.0,
                "daily_stop_loss": 6.0,
                "bets": [
                    {
                        "id": "bet_initial_01",
                        "date": time.strftime("%Y-%m-%d"),
                        "time": time.strftime("%H:%M"),
                        "match_title": "Preston Lions vs South Melb. & Strommen vs Raufoss",
                        "market": "Over 2.5 FT (AKO 2 mecze)",
                        "stake": 4.00,
                        "odds": 2.61,
                        "potential_win": 9.18,
                        "status": "PENDING",  # PENDING, WON, LOST, VOID
                        "profit_loss": 0.0,
                        "notes": "Kupon AKO: Puchar Australii + Puchar Norwegii"
                    }
                ]
            }
            with open(self.file_path, "w", encoding="utf-8") as f:
                json.dump(initial_data, f, indent=2, ensure_ascii=False)

    def _load_data(self) -> Dict[str, Any]:
        try:
            with open(self.file_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {"initial_bankroll": 48.0, "current_bankroll": 44.0, "bets": []}

    def _save_data(self, data: Dict[str, Any]):
        with open(self.file_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    def get_summary(self) -> Dict[str, Any]:
        """Zwraca pełne statystyki, wskaźniki KPI oraz postęp dziennego celu."""
        data = self._load_data()
        bets = data.get("bets", [])
        today_str = time.strftime("%Y-%m-%d")

        total_staked = 0.0
        total_returned = 0.0
        total_profit = 0.0
        daily_profit = 0.0

        won_count = 0
        lost_count = 0
        pending_count = 0
        settled_count = 0

        for b in bets:
            stake = float(b.get("stake", 0.0))
            status = b.get("status", "PENDING")
            p_l = float(b.get("profit_loss", 0.0))
            b_date = b.get("date", "")

            if status == "PENDING":
                pending_count += 1
            elif status == "WON":
                won_count += 1
                settled_count += 1
                total_staked += stake
                total_profit += p_l
                if b_date == today_str:
                    daily_profit += p_l
            elif status == "LOST":
                lost_count += 1
                settled_count += 1
                total_staked += stake
                total_profit += p_l
                if b_date == today_str:
                    daily_profit += p_l
            elif status == "VOID":
                settled_count += 1

        win_rate = round((won_count / max(1, settled_count)) * 100, 1) if settled_count > 0 else 0.0
        yield_pct = round((total_profit / max(1.0, total_staked)) * 100, 1) if total_staked > 0 else 0.0

        target = float(data.get("daily_target_profit", 10.0))
        target_progress_pct = max(0, min(100, int((daily_profit / max(1.0, target)) * 100)))

        return {
            "initial_bankroll": round(float(data.get("initial_bankroll", 48.0)), 2),
            "current_bankroll": round(float(data.get("initial_bankroll", 48.0)) + total_profit - (sum(b['stake'] for b in bets if b['status'] == 'PENDING')), 2),
            "daily_profit": round(daily_profit, 2),
            "daily_target_profit": target,
            "target_progress_pct": target_progress_pct,
            "total_profit": round(total_profit, 2),
            "total_bets": len(bets),
            "pending_bets": pending_count,
            "won_bets": won_count,
            "lost_bets": lost_count,
            "win_rate": win_rate,
            "yield_pct": yield_pct,
            "total_staked": round(total_staked, 2),
            "bets": sorted(bets, key=lambda x: x.get("id", ""), reverse=True)
        }

    def add_bet(self, match_title: str, market: str, stake: float, odds: float, notes: str = "", bet_type: str = "SOLO", legs: List[Dict[str, Any]] = None) -> Dict[str, Any]:
        data = self._load_data()
        bets = data.get("bets", [])
        
        stake = round(float(stake), 2)
        odds = round(float(odds), 2)
        potential_win = round(stake * odds * 0.88, 2)  # uwzględnia 12% podatku w STS

        bet_id = f"bet_{int(time.time() * 1000)}"
        new_bet = {
            "id": bet_id,
            "date": time.strftime("%Y-%m-%d"),
            "time": time.strftime("%H:%M"),
            "bet_type": bet_type,
            "legs_count": len(legs) if legs else 1,
            "legs": legs or [{"match": match_title, "market": market, "odds": odds}],
            "match_title": match_title,
            "market": market,
            "stake": stake,
            "odds": odds,
            "potential_win": potential_win,
            "status": "PENDING",
            "profit_loss": 0.0,
            "notes": notes
        }

        bets.append(new_bet)
        data["bets"] = bets
        self._save_data(data)
        return self.get_summary()

    def add_ako_bet(self, legs: List[Dict[str, Any]], stake: float, notes: str = "") -> Dict[str, Any]:
        """Dodaje kupon wielokrotny AKO złożony z wielu zdarzeń."""
        if not legs:
            return self.get_summary()

        total_odds = 1.0
        match_titles = []
        market_titles = []

        for l in legs:
            o = float(l.get('odds', 1.0))
            total_odds *= o
            m_name = l.get('match', 'Mecz')
            m_market = l.get('market', 'Over')
            match_titles.append(m_name)
            market_titles.append(f"{m_name} ({m_market} @{round(o, 2)})")

        total_odds = round(total_odds, 2)
        title = f"AKO ({len(legs)} zdarzenia): " + " + ".join(match_titles)
        market_str = " | ".join(market_titles)

        return self.add_bet(
            match_title=title,
            market=market_str,
            stake=stake,
            odds=total_odds,
            notes=notes or f"Kupon akumulowany AKO ({len(legs)} mecze)",
            bet_type="AKO",
            legs=legs
        )

    def _normalize_name(self, name: str) -> str:
        ALIASES = {
            'psg': 'paris saint germain',
            'paris sg': 'paris saint germain',
            'man utd': 'manchester united',
            'man united': 'manchester united',
            'mufc': 'manchester united',
            'man city': 'manchester city',
            'mcfc': 'manchester city',
            'barca': 'barcelona',
            'bvb': 'borussia dortmund',
            'dortmund': 'borussia dortmund',
            'bayern': 'bayern munich',
            'inter': 'internazionale milan',
            'juve': 'juventus',
            'wolves': 'wolverhampton',
            'spurs': 'tottenham',
            'dep.': 'deportivo',
            'atl.': 'atletico'
        }
        n = str(name).lower()
        for k, v in ALIASES.items():
            n = re.sub(rf'\b{k}\b', v, n)

        n = re.sub(r'\[k\]|\(k\)|u19|u20|u21|u23|fc|sc|ks|fk|cf|sk|cd|cs|if|il|de|ca|club|sporting', ' ', n)
        n = re.sub(r'[^a-z0-9\s]', ' ', n)
        words = [w.strip() for w in n.split() if len(w.strip()) > 1]
        return " ".join(sorted(words))

    def _matches_fixture(self, bet_match: str, live_home: str, live_away: str) -> bool:
        """Sprawdza czy zdarzenie z kuponu odpowiada meczowi z feedu przy użyciu zaawansowanego fuzzy matching."""
        bm_norm = self._normalize_name(bet_match)
        h_norm = self._normalize_name(live_home)
        a_norm = self._normalize_name(live_away)

        h_words = [w for w in h_norm.split() if len(w) > 2]
        a_words = [w for w in a_norm.split() if len(w) > 2]
        bm_words = bm_norm.split()

        has_home = any(w in bm_words or w in bm_norm for w in h_words) if h_words else (h_norm in bm_norm)
        has_away = any(w in bm_words or w in bm_norm for w in a_words) if a_words else (a_norm in bm_norm)

        if has_home and has_away:
            return True

        # Fallback na SequenceMatcher dla drobnych literówek
        import difflib
        combined_live = f"{live_home} vs {live_away}".lower()
        sim = difflib.SequenceMatcher(None, bet_match.lower(), combined_live).ratio()
        if sim > 0.55:
            return True

        return False


    def _evaluate_leg_status(self, leg: Dict[str, Any], match: Dict[str, Any]) -> Optional[str]:
        """
        Zwraca status typu dla pojedynczej pozycji: 'WON', 'LOST', 'VOID', lub None (w trakcie gry).
        Gwarantuje, że żaden trwający mecz nie zostanie przedwcześnie oznaczony jako LOST.
        """
        market = str(leg.get('market', '')).upper()
        stage_text = str(match.get('stage_text', '')).lower()
        is_live = match.get('is_live', True)
        half = match.get('half', '1H')
        minute = match.get('minute', 0)
        score_str = match.get('score_str', '0:0')
        ht_score = match.get('ht_score')
        status_code = str(match.get('status_code', ''))

        try:
            curr_tot = sum(map(int, score_str.split(':')))
        except Exception:
            curr_tot = 0

        # Mecz odwołany / przerwany -> VOID
        if any(w in stage_text for w in ['odwołan', 'przerwan', 'przełożon', 'anulowan', 'walkower', 'abandoned', 'postponed', 'cancelled']):
            return 'VOID'

        is_ht_market = any(kw in market for kw in ['HT', '1. POŁ', '1.POŁ', 'POŁOWA', '1H', 'FIRST HALF'])
        
        # Wyznacz linię bramkową (np. Over 0.5 -> 1, Over 1.5 -> 2, Over 2.5 -> 3, itp.)
        m_over = re.search(r'OVER\s+(\d+(?:\.\d+)?)', market)
        if m_over:
            target_line = float(m_over.group(1))
            target_goals = int(target_line + 0.5)
        elif '0.5' in market:
            target_goals = 1
        elif '1.5' in market:
            target_goals = 2
        elif '2.5' in market:
            target_goals = 3
        elif '3.5' in market:
            target_goals = 4
        elif '4.5' in market:
            target_goals = 5
        elif '5.5' in market:
            target_goals = 6
        else:
            target_goals = 1

        # 1. RYNKI 1. POŁOWY (HT)
        if is_ht_market:
            if curr_tot >= target_goals:
                return 'WON'
            if ht_score:
                try:
                    ht_tot = sum(map(int, ht_score.split(':')))
                    if ht_tot >= target_goals:
                        return 'WON'
                except Exception:
                    pass

            # Przegrana 1H TYLKO gdy 1. połowa definitywnie się zakończyła (przerwa HT, 2H, FT lub koniec)
            is_1h_finished = half in ('HT', '2H', 'FT') or 'koniec' in stage_text or (not is_live and minute >= 45)
            if is_1h_finished:
                if ht_score:
                    try:
                        ht_tot = sum(map(int, ht_score.split(':')))
                        return 'LOST' if ht_tot < target_goals else 'WON'
                    except Exception:
                        pass
                return 'LOST' if curr_tot < target_goals else 'WON'
            return None

        # 2. RYNKI CAŁEGO MECZU (FT)
        else:
            # Natychmiastowa wygrana po osiągnięciu progu bramkowego
            if curr_tot >= target_goals:
                return 'WON'

            # Przegrana FT TYLKO I WYŁĄCZNIE po upływie 90+ minut i gwizdku sędziego (FT / Koniec meczu)
            is_ft_finished = (half == 'FT' or 'koniec' in stage_text or 'ended' in stage_text or
                              status_code in ('3', '10', '11') or (not is_live and minute >= 90))
            if is_ft_finished and curr_tot < target_goals:
                return 'LOST'

            return None

    def auto_resolve_bets(self, matches: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Automatycznie rozlicza kupony SOLO i AKO na podstawie aktualnych i zakończonych meczów.
        """
        if not matches:
            return []

        data = self._load_data()
        bets = data.get("bets", [])
        resolved_any = False
        newly_resolved = []

        for b in bets:
            if b.get("status") != "PENDING":
                continue

            legs = b.get("legs", [])
            if not legs:
                # Wsteczna kompatybilność: utwórz 1 leg z pól match_title i market
                legs = [{"match": b.get("match_title", ""), "market": b.get("market", ""), "odds": b.get("odds", 1.8)}]
                b["legs"] = legs

            legs_statuses = []
            for leg in legs:
                leg_m_title = leg.get("match", "")
                leg_status = leg.get("status")

                if leg_status in ("WON", "LOST", "VOID"):
                    legs_statuses.append(leg_status)
                    continue

                # Znajdź mecz w feedzie
                matched_live = None
                for m in matches:
                    h = m.get('home_team', '')
                    a = m.get('away_team', '')
                    if self._matches_fixture(leg_m_title, h, a):
                        matched_live = m
                        break

                if matched_live:
                    st = self._evaluate_leg_status(leg, matched_live)
                    if st:
                        leg["status"] = st
                        legs_statuses.append(st)
                    else:
                        legs_statuses.append("PENDING")
                else:
                    legs_statuses.append("PENDING")

            # Ocena całego kuponu
            if "LOST" in legs_statuses:
                # Jakikolwiek przegrany typ w AKO/SOLO -> cały kupon przegrany
                b["status"] = "LOST"
                stake = float(b.get("stake", 0.0))
                b["profit_loss"] = round(-stake, 2)
                resolved_any = True
                newly_resolved.append(b)

            elif all(s in ("WON", "VOID") for s in legs_statuses) and len(legs_statuses) == len(legs):
                # Wszystkie pozycje wygrane lub unieważnione
                has_won = any(s == "WON" for s in legs_statuses)
                if has_won:
                    b["status"] = "WON"
                    stake = float(b.get("stake", 0.0))
                    pot_win = float(b.get("potential_win", 0.0))
                    b["profit_loss"] = round(pot_win - stake, 2)
                else:
                    b["status"] = "VOID"
                    b["profit_loss"] = 0.0
                resolved_any = True
                newly_resolved.append(b)

        if resolved_any:
            data["bets"] = bets
            self._save_data(data)

            # Sprawdź powiadomienie o celu dnia
            summary = self.get_summary()
            if summary.get("daily_profit", 0) >= summary.get("daily_target_profit", 10.0):
                try:
                    from .telegram_notifier import TelegramNotifier
                    TelegramNotifier().notify_daily_goal_achieved(summary['daily_profit'], summary['daily_target_profit'])
                except Exception:
                    pass

        return newly_resolved

    def resolve_bet(self, bet_id: str, status: str) -> Dict[str, Any]:
        """Ręczne rozliczenie zakładu: WON, LOST, VOID."""
        data = self._load_data()
        bets = data.get("bets", [])

        for b in bets:
            if b.get("id") == bet_id:
                b["status"] = status
                stake = float(b.get("stake", 0.0))
                pot_win = float(b.get("potential_win", 0.0))

                if status == "WON":
                    b["profit_loss"] = round(pot_win - stake, 2)
                elif status == "LOST":
                    b["profit_loss"] = round(-stake, 2)
                elif status == "VOID":
                    b["profit_loss"] = 0.0
                break

        data["bets"] = bets
        self._save_data(data)
        return self.get_summary()

    def delete_bet(self, bet_id: str) -> Dict[str, Any]:
        data = self._load_data()
        bets = data.get("bets", [])
        data["bets"] = [b for b in bets if b.get("id") != bet_id]
        self._save_data(data)
        return self.get_summary()

    def update_bankroll(self, new_bankroll: float) -> Dict[str, Any]:
        data = self._load_data()
        data["initial_bankroll"] = round(float(new_bankroll), 2)
        self._save_data(data)
        return self.get_summary()

