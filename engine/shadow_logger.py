import os
import json
import time
import re
import threading
from typing import Dict, Any, Optional, List

SHADOW_LOG_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "telegram_shadow_log.jsonl")

class ShadowLogger:
    _instance = None
    _lock = threading.RLock()

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(ShadowLogger, cls).__new__(cls)
            cls._instance._init_logger()
        return cls._instance

    def _init_logger(self):
        self._last_logged_time = {}

    def log_evaluation(
        self,
        match: Dict[str, Any],
        scenario_type: str,
        market: str,
        odds: float,
        di_10: int,
        di_5: int,
        trend: int,
        trend_state: str,
        sot_total: int,
        sot_10m: float,
        shots_total: int,
        apm: float,
        xg_total: float,
        xg_10m: float,
        model_probability: float,
        implied_probability: float,
        edge: float,
        ev: float,
        raw_score: int,
        league_weight: int,
        effective_score: int,
        status: str,  # "ACCEPTED" or "REJECTED"
        filter_stage: str,  # e.g. "1_LEAGUE", "4_ODDS", "5_INTENSITY", "6_SOT", "8_TREND", "9_EV", "10_SCORING", "11_ACCEPTED"
        rejection_reason: Optional[str] = None,
        stars: int = 0,
        corners_total: int = 0,
        big_chances: int = 0,
        raw_di_10: Optional[int] = None,
        raw_di_5: Optional[int] = None
    ):
        """
        Zapisuje pełną telemetrię ewaluacji i kalibracji do pliku JSONL pod backtest empiryczny.
        Dławienie (throttling): zapobiega duplikowaniu wpisów dla tego samego meczu częściej niż co 60 sekund.
        """
        home = str(match.get('home_team', '')).strip()
        away = str(match.get('away_team', '')).strip()
        minute = int(match.get('minute', 0))
        score_str = str(match.get('score_str', f"{match.get('home_score', 0)}:{match.get('away_score', 0)}"))
        league = str(match.get('league', ''))

        key = f"{home.lower()}_vs_{away.lower()}_{market}_{filter_stage}_{status}"
        now = time.time()
        last_time = self._last_logged_time.get(key, 0)
        # Ogranicznik logowania: ten sam etap/status maks raz na 60s
        if now - last_time < 60 and status == "REJECTED":
            return
        self._last_logged_time[key] = now

        date_str = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(now))
        day_str = time.strftime('%Y%m%d', time.localtime(now))
        match_uid = str(match.get('flashscore_id') or match.get('id') or f"{home}_{away}_{day_str}")

        entry = {
            "timestamp": int(now),
            "date": date_str,
            "match_id": match_uid,
            "home": home,
            "away": away,
            "league": league,
            "minute": minute,
            "score": score_str,
            "scenario": scenario_type,
            "market": market,
            "entry_odds": round(float(odds), 2),
            "di_10": int(di_10),           # FINAL AUTHORITATIVE RESCALED DI (0-100)
            "di_5": int(di_5),             # FINAL AUTHORITATIVE RESCALED DI (0-100)
            "raw_di_10": int(raw_di_10) if raw_di_10 is not None else int(di_10),  # RAW UNRESCALED (0-71)
            "raw_di_5": int(raw_di_5) if raw_di_5 is not None else int(di_5),      # RAW UNRESCALED (0-71)
            "trend": int(trend),
            "trend_state": trend_state,    # "RISING", "STABLE", "FALLING"
            "sot_total": int(sot_total),
            "sot_10m": round(float(sot_10m), 1),
            "shots_total": int(shots_total),
            "corners_total": int(corners_total),
            "big_chances": int(big_chances),
            "apm": round(float(apm), 2),
            "xg_total": round(float(xg_total), 2),
            "xg_10m": round(float(xg_10m), 2),
            "signal_timestamp": int(now),
            "stats_timestamp": int(match.get("stats_timestamp") or now),
            "odds_timestamp": int(match.get("odds_timestamp") or now),
            
            # Parametry kalibracji modelu prawdopodobieństwa i EV:
            "model_probability": round(float(model_probability), 4),
            "implied_probability": round(float(implied_probability), 4),
            "edge": round(float(edge), 4),
            "ev": round(float(ev), 4),

            # Scoring:
            "raw_score": int(raw_score),          # Czysty wynik 0-9
            "league_weight": int(league_weight),  # -1 / 0 / +1
            "effective_score": int(effective_score),
            "stars": int(stars),

            # Status decyzyjny i punkt odcięcia lejka (Funnel):
            "status": status,                     # "ACCEPTED" lub "REJECTED"
            "filter_stage": filter_stage,         # np. "4_ODDS", "6_SOT", "8_TREND", "9_EV", "11_ACCEPTED"
            "rejection_reason": rejection_reason,

            # Wyniki rozliczenia (uzupełniane asynchronicznie po zakończeniu meczu):
            "settlement_score": None,             # np. "2:1"
            "ht_settlement_score": None,          # np. "1:0"
            "result": None,                       # "WIN", "LOSS", "VOID"
            "profit_units": None,                 # np. +1.36J lub -1.0J
            "goal_after_entry": None,             # True/False
            "goal_time_after_entry": None
        }

        with self._lock:
            try:
                with open(SHADOW_LOG_FILE, "a", encoding="utf-8") as f:
                    f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            except Exception as e:
                print(f"[ShadowLogger] Błąd zapisu do loga: {e}")

    def _settle_record(self, record: dict, ft_goals: int, ht_goals: Optional[int], score_str: str, ht_score_str: Optional[str]) -> bool:
        record["settlement_score"] = score_str
        if ht_score_str:
            record["ht_settlement_score"] = str(ht_score_str)
        market = str(record.get("market", "")).upper()
        odds = float(record.get("entry_odds", 1.50))

        try:
            init_score = record.get("score", "0:0")
            init_tot = sum(map(int, init_score.split(":")))
        except Exception:
            init_tot = 0

        m = re.search(r'OVER\s+(\d+(?:\.\d+)?)', market)
        if m:
            line_val = float(m.group(1))
            is_ht = ('HT' in market or '1. POŁ' in market)
            if not is_ht:
                if ft_goals > line_val:
                    record["result"] = "WIN"
                    record["profit_units"] = round(odds - 1.0, 2)
                    record["goal_after_entry"] = (ft_goals > init_tot)
                else:
                    record["result"] = "LOSS"
                    record["profit_units"] = -1.0
                    record["goal_after_entry"] = (ft_goals > init_tot)
            else:
                if ht_goals is not None:
                    if ht_goals > line_val:
                        record["result"] = "WIN"
                        record["profit_units"] = round(odds - 1.0, 2)
                        record["goal_after_entry"] = (ht_goals > init_tot)
                    else:
                        record["result"] = "LOSS"
                        record["profit_units"] = -1.0
                        record["goal_after_entry"] = (ht_goals > init_tot)
                else:
                    if ft_goals <= line_val:
                        record["result"] = "LOSS"
                        record["profit_units"] = -1.0
                        record["goal_after_entry"] = False
                    else:
                        record["result"] = "WIN"
                        record["profit_units"] = round(odds - 1.0, 2)
                        record["goal_after_entry"] = True
        return True

    def update_settled_match(self, home: str, away: str, settlement_score: str, ht_score: Optional[str] = None):
        """Uzupełnia wynik dla pojedynczego meczu (deleguje do batch)."""
        self.update_settled_matches_batch([{
            "home_team": home,
            "away_team": away,
            "score_str": settlement_score,
            "ht_score": ht_score
        }])

    def update_settled_matches_batch(self, finished_matches: List[Dict[str, Any]]):
        """
        Wydajna, jedno-przebiegowa aktualizacja wyników końcowych w telegram_shadow_log.jsonl
        dla całej listy zakończonych meczów z Flashscore/STS.
        """
        if not os.path.exists(SHADOW_LOG_FILE) or not finished_matches:
            return

        parsed_fin = []
        for m in finished_matches:
            score_str = m.get("score_str") or m.get("score")
            if not score_str or ":" not in str(score_str):
                continue
            try:
                fh, fa = map(int, str(score_str).split(":"))
                ft_goals = fh + fa
            except Exception:
                continue

            ht_goals = None
            ht_s = m.get("ht_score")
            if ht_s and ":" in str(ht_s):
                try:
                    hh, ha = map(int, str(ht_s).split(":"))
                    ht_goals = hh + ha
                except Exception:
                    ht_goals = None

            h_clean = m.get("home_team", "").strip().lower()
            a_clean = m.get("away_team", "").strip().lower()
            if h_clean and a_clean:
                parsed_fin.append({
                    "home": h_clean,
                    "away": a_clean,
                    "score_str": str(score_str),
                    "ht_score_str": str(ht_s) if ht_s else None,
                    "ft_goals": ft_goals,
                    "ht_goals": ht_goals
                })

        if not parsed_fin:
            return

        with self._lock:
            try:
                with open(SHADOW_LOG_FILE, "r", encoding="utf-8") as f:
                    lines = f.readlines()

                modified = False
                new_lines = []

                for line in lines:
                    line_str = line.strip()
                    if not line_str:
                        continue
                    try:
                        record = json.loads(line_str)
                        if record.get("settlement_score") is None:
                            r_home = record.get("home", "").strip().lower()
                            r_away = record.get("away", "").strip().lower()

                            for fin in parsed_fin:
                                fh = fin["home"]
                                fa = fin["away"]
                                if (fh in r_home or r_home in fh) and (fa in r_away or r_away in fa):
                                    self._settle_record(
                                        record,
                                        ft_goals=fin["ft_goals"],
                                        ht_goals=fin["ht_goals"],
                                        score_str=fin["score_str"],
                                        ht_score_str=fin["ht_score_str"]
                                    )
                                    modified = True
                                    break

                        new_lines.append(json.dumps(record, ensure_ascii=False) + "\n")
                    except Exception:
                        new_lines.append(line)

                if modified:
                    tmp_file = SHADOW_LOG_FILE + ".tmp"
                    with open(tmp_file, "w", encoding="utf-8") as f:
                        f.writelines(new_lines)
                    if os.path.exists(tmp_file):
                        os.replace(tmp_file, SHADOW_LOG_FILE)
            except Exception as e:
                print(f"[ShadowLogger] Błąd batch aktualizacji wyników: {e}")
