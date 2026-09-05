"""
Silnik reguł i triggerów bramkowych (Over 0.5 / 1.5 HT, Over 1.5 / 2.5 FT, Post-Goal, Late Goal 2H).
Analizuje w czasie rzeczywistym statystyki z Flashscore oraz kursy z STS.
"""
import math
import re
import time
import threading
from collections import deque
from typing import Dict, Any, List, Optional
from sts_live_config import TRIGGERS_CONFIG

# Rozszerzona baza lig o skrajnie niskiej bramkowości (często 0:0 lub poniżej 1.8 gola/mecz)
ANTI_GOAL_LEAGUES_KEYWORDS = [
    'egipt', 'egypt', 'division 2', '2. division', '2.division',
    'kolumbia: liga kobiety', 'kolumbia: primera a', 'kolumbia: primera b',
    'colombia: primera a', 'colombia: primera b',
    'ekwador: serie b', 'ecuador: serie b',
    'iran: pro league', 'iran: league 1', 'iran',
    'morocco: botola', 'maroko: botola', 'maroko: 2. botola',
    'algeria: ligue 1', 'algeria: ligue 2', 'algieria',
    'greece: super league 2', 'grecja: super league 2',
    'argentina: primera b', 'argentina: torneo federal', 'argentina: primera nacional',
    'argentyna: primera b', 'argentyna: torneo federal', 'argentyna: primera c', 'argentina: primera c', 'primera c',
    'gruzja', 'georgia', 'erovnuli',
    'azerbejdżan', 'azerbejdzan', 'azerbaijan',
    'oman', 'omani',
    'national league south', 'national league - south',
    'south africa: premier league', 'rpa: premier league', 'south africa: national first',
    'tunisia: ligue 1', 'tunezja',
    'venezuela: primera division', 'wenezuela', 'venezuela',
    'romania: liga 2', 'rumunia: liga 2',
    'sri lanka', 'bangladesh', 'bhutan', 'maldywy', 'maldives', 'nepal',
    'uganda', 'tanzania', 'kenya', 'zambia', 'zimbabwe', 'rwanda', 'burundi', 'zanzibar',
    'liga bet', 'liga alef', 'liga gimel', 'npl', 'campeonato de portugal',
    'amatorzy', 'juniorzy', 'u19', 'u20', 'u21', 'socca'
]


class GoalTriggersEngine:
    def __init__(self, config=None):
        self.config = config or TRIGGERS_CONFIG
        # Bufor serii czasowej (Sliding Time-Series Buffer w RAM)
        # match_key -> {'snapshots': deque(maxlen=120), 'last_goal_minute': int, 'last_score': (h, a), 'last_seen': float}
        self._match_history = {}
        self._history_lock = threading.Lock()
        self._last_cleanup_ts = time.time()

    def _cleanup_old_history(self, now: float):
        """Usuwa nieaktywne i zakończone mecze z pamięci RAM (zapobiega wyciekom 24/7)."""
        if now - self._last_cleanup_ts < 60:
            return
        self._last_cleanup_ts = now
        with self._history_lock:
            keys_to_del = []
            for k, v in self._match_history.items():
                last_seen = v.get('last_seen', now)
                is_fin = v.get('is_finished', False)
                # 1. Jeśli mecz zakończony (FT) - usuń po 5 minutach od ostatniego odpytania
                if is_fin and (now - last_seen > 300):
                    keys_to_del.append(k)
                # 2. Jeśli mecz zniknął z feedu live i nie widziano go od 20 minut - usuń
                elif (now - last_seen > 1200):
                    keys_to_del.append(k)

            for k in keys_to_del:
                hist = self._match_history.pop(k, None)
                if hist and 'snapshots' in hist:
                    hist['snapshots'].clear()

    def cleanup_unseen_matches(self, active_live_keys: Any, now: Optional[float] = None):
        """Usuwa z RAM mecze, które nie znajdują się już w bieżącym feedzie Live."""
        if now is None:
            now = time.time()
        active_set = set(active_live_keys) if active_live_keys else set()
        with self._history_lock:
            keys_to_del = [
                k for k, v in self._match_history.items()
                if k not in active_set and (now - v.get('last_seen', now) > 600)
            ]
            for k in keys_to_del:
                hist = self._match_history.pop(k, None)
                if hist and 'snapshots' in hist:
                    hist['snapshots'].clear()

    def purge_match(self, match_key: str):
        """Natychmiastowe usunięcie pojedynczego meczu z RAM."""
        with self._history_lock:
            hist = self._match_history.pop(match_key, None)
            if hist and 'snapshots' in hist:
                hist['snapshots'].clear()

    def _record_snapshot_and_get_deltas(self, match_key: str, snap: Dict[str, Any]) -> Dict[str, Any]:
        """
        Zapisuje stan meczu do bufora kroczącego i wylicza twarde delty z ostatnich 10 minut.
        Wykrywa także bramki i zarządza stanem Cool-down po golu.
        """
        now = snap['time']
        self._cleanup_old_history(now)

        with self._history_lock:
            if match_key not in self._match_history:
                self._match_history[match_key] = {
                    'snapshots': deque(maxlen=120),
                    'last_goal_minute': None,
                    'last_goal_time': 0.0,
                    'last_score': (snap['home_score'], snap['away_score']),
                    'last_seen': now,
                    'is_finished': snap.get('is_finished', False)
                }
                hist = self._match_history[match_key]
            else:
                hist = self._match_history[match_key]
                hist['last_seen'] = now
                hist['is_finished'] = snap.get('is_finished', False)

                prev_score = hist.get('last_score', (0, 0))
                cur_score = (snap['home_score'], snap['away_score'])
                cur_goals = snap['total_goals']

                # Wykrycie nowej bramki: TYLKO jeśli mecz był już wcześniej w buforze i wynik wzrósł!
                if len(hist['snapshots']) > 0 and cur_score != prev_score and cur_goals > sum(prev_score):
                    hist['last_goal_minute'] = snap['minute']
                    hist['last_goal_time'] = now
                    hist['last_score'] = cur_score

            snaps = hist['snapshots']
            # Rejestruj próbkę jeśli minęło min. 8 sekund lub zmieniła się minuta
            if not snaps or (now - snaps[-1]['time'] >= 8.0) or (snap['minute'] != snaps[-1]['minute']):
                snaps.append(snap)

            # Szukamy snapshotu z przeszłości sprzed ~10 minut
            target_min = snap['minute'] - 10
            target_time = now - 600.0

            past_snap = None
            for s in snaps:
                if s['minute'] <= target_min or s['time'] <= target_time:
                    past_snap = s
                else:
                    break

            baseline = past_snap if past_snap is not None else snaps[0]
            tracked_mins = max(0.5, float(snap['minute'] - baseline['minute']) if snap['minute'] >= baseline['minute'] else (now - baseline['time']) / 60.0)
            is_full_window = (past_snap is not None) or (tracked_mins >= 7.0)

            # Surowe różnice między stanem obecnym a bazowym
            raw_sot = max(0, snap['sot'] - baseline['sot'])
            raw_shots = max(0, snap['shots'] - baseline['shots'])
            raw_da = max(0, snap['dangerous_attacks'] - baseline['dangerous_attacks'])
            raw_corners = max(0, snap['corners'] - baseline['corners'])
            raw_xg = max(0.0, snap['xg'] - baseline['xg'])
            raw_big = max(0, snap['big_chances'] - baseline['big_chances'])

            if is_full_window and tracked_mins >= 7.0:
                # Pełne okno kroczące: dokładna normalizacja na 10 minut
                norm = 10.0 / tracked_mins
                d_sot = raw_sot * norm
                d_shots = raw_shots * norm
                d_da = raw_da * norm
                d_corners = raw_corners * norm
                d_xg = raw_xg * norm
                d_big = raw_big * norm
            else:
                # Cold Start (krótki czas monitorowania meczu):
                # Hybrydowy fallback oparty o upływ czasu danej połowy
                if snap['half'] in ('2H', 'HT') or snap['minute'] >= 45:
                    # Wariant B: w 2. połowie odcinamy czas 1. połowy (czas efektywny od 45')
                    eff_mins = max(5.0, float(snap['minute'] - 45.0))
                    norm = min(2.0, 10.0 / eff_mins)
                    d_sot = raw_sot * norm if raw_sot > 0 else (snap['sot'] / max(1.0, snap['minute'] / 10.0))
                    d_shots = raw_shots * norm if raw_shots > 0 else (snap['shots'] / max(1.0, snap['minute'] / 10.0))
                    d_da = raw_da * norm if raw_da > 0 else (snap['dangerous_attacks'] / max(1.0, snap['minute'] / 10.0))
                    d_corners = raw_corners * norm if raw_corners > 0 else (snap['corners'] / max(1.0, snap['minute'] / 10.0))
                    d_xg = raw_xg * norm if raw_xg > 0 else (snap['xg'] / max(1.0, snap['minute'] / 10.0))
                    d_big = raw_big * norm if raw_big > 0 else (snap['big_chances'] / max(1.0, snap['minute'] / 10.0))
                else:
                    # W 1. połowie: normalizacja do upływu minut meczu z tłumieniem min. 5 min
                    eff_mins = max(5.0, float(snap['minute']))
                    norm = min(2.0, 10.0 / eff_mins)
                    d_sot = raw_sot * norm if raw_sot > 0 else (snap['sot'] * norm)
                    d_shots = raw_shots * norm if raw_shots > 0 else (snap['shots'] * norm)
                    d_da = raw_da * norm if raw_da > 0 else (snap['dangerous_attacks'] * norm)
                    d_corners = raw_corners * norm if raw_corners > 0 else (snap['corners'] * norm)
                    d_xg = raw_xg * norm if raw_xg > 0 else (snap['xg'] * norm)
                    d_big = raw_big * norm if raw_big > 0 else (snap['big_chances'] * norm)

            return {
                'delta_sot_10': round(d_sot, 2),
                'delta_shots_10': round(d_shots, 2),
                'delta_da_10': round(d_da, 2),
                'delta_corners_10': round(d_corners, 2),
                'delta_xg_10': round(d_xg, 2),
                'delta_big_10': round(d_big, 2),
                'last_goal_minute': hist.get('last_goal_minute'),
                'last_goal_time': hist.get('last_goal_time', 0.0),
                'is_full_window': is_full_window
            }

    def evaluate_match(self, match_data: Dict[str, Any], stats: Dict[str, Any], sts_odds: Dict[str, Any]) -> Dict[str, Any]:
        """
        Ocenia mecz i wyznacza wskaźniki intensywności oraz aktywne sygnały bramkowe.
        Zwraca tylko sygnały o potwierdzonej wartości (Value Bet / Sweet Spot).
        """
        minute = max(0, min(120, int(match_data.get('minute', 0))))
        is_started = match_data.get('is_started', True)
        half = str(match_data.get('half', '1H')).upper()

        # Jeśli mecz jeszcze się nie rozpoczął lub minuta wynosi 0
        if not is_started or half == 'PRE' or minute == 0:
            return {
                'danger_index': 0,
                'danger_rating': 'OCZEKUJE',
                'apm': 0.0,
                'signals': [],
                'has_signals': False,
                'primary_signal': None,
                'top_recommendation': 'Mecz przed rozpoczęciem'
            }

        home_score = max(0, int(match_data.get('home_score', 0)))
        away_score = max(0, int(match_data.get('away_score', 0)))
        total_goals = home_score + away_score
        score_diff = abs(home_score - away_score)

        # Statystyki z feedu meczowego (Flashscore / Radar STS)
        xg_total = max(0.0, float(stats.get('xg_total', 0.0)))
        shots_total = max(0, int(stats.get('shots_total', 0)))
        sot = max(0, int(stats.get('shots_on_target_total', 0)))
        dangerous_attacks = max(0, int(stats.get('dangerous_attacks_total', 0)))
        corners = max(0, int(stats.get('corners_total', 0)))
        red_cards = max(0, int(stats.get('red_cards_total', 0)))
        big_chances = max(0, int(stats.get('big_chances_total', 0)))

        # Identyfikator meczu dla bufora serii czasowej
        m_home = str(match_data.get('home_team', '')).strip()
        m_away = str(match_data.get('away_team', '')).strip()
        match_key = str(match_data.get('flashscore_id') or match_data.get('id') or f"{m_home}_{m_away}").strip().lower()
        if not match_key or match_key == '_':
            match_key = f"match_obj_{id(match_data)}"

        # Rejestracja snapshotu w buforze kroczącym i obliczenie delt z ostatnich 10 minut
        now_ts = float(match_data.get('timestamp') or time.time())
        stage_text = str(match_data.get('stage_text', '')).strip().upper()
        is_finished = bool(
            match_data.get('is_finished') or 
            match_data.get('status') in ('FT', 'ENDED', 'FINISHED', 'AET', 'PEN') or
            stage_text in ('FT', 'KONIEC', 'ZAKOŃCZONY', 'ENDED', 'AET', 'PEN.')
        )

        current_snapshot = {
            'time': now_ts,
            'minute': minute,
            'half': half,
            'home_score': home_score,
            'away_score': away_score,
            'total_goals': total_goals,
            'shots': shots_total,
            'sot': sot,
            'dangerous_attacks': dangerous_attacks,
            'corners': corners,
            'xg': xg_total,
            'big_chances': big_chances,
            'red_cards': red_cards,
            'is_finished': is_finished
        }

        deltas = self._record_snapshot_and_get_deltas(match_key, current_snapshot)

        # 1. Dynamiczne obliczenie APM w oknie kroczącym
        apm = self._calculate_dynamic_apm(minute, dangerous_attacks, shots_total, sot, corners, deltas)

        # 2. Obliczenie Indeksu Groźności (Danger/Pressure Index: 0 - 100) na bazie 10-minutowego okna delty
        danger_index = self._calculate_danger_index(
            deltas=deltas,
            red_cards=red_cards,
            minute=minute,
            half=half,
            total_sot=sot,
            total_shots=shots_total
        )

        d_rat = "EKSTREMALNY" if danger_index >= 75 else ("WYSOKI" if danger_index >= 55 else ("ŚREDNI" if danger_index >= 35 else "NISKI"))

        # -------------------------------------------------------------------------
        # A. TWARDY COOL-DOWN PO BRAMCE (GAME STATE RESET: 5 MINUT KWARANTANNY)
        # -------------------------------------------------------------------------
        last_goal_min = deltas.get('last_goal_minute')
        if last_goal_min is not None:
            mins_since_goal = minute - last_goal_min
            if 0 <= mins_since_goal <= 5:
                return {
                    'apm': apm,
                    'danger_index': danger_index,
                    'danger_rating': d_rat,
                    'signals': [],
                    'has_signals': False,
                    'primary_signal': None,
                    'top_recommendation': f'Cool-down po bramce ({mins_since_goal} min od gola: {home_score}:{away_score}) - oczekiwanie na stabilizację rynku'
                }

        # 1. Sprawdzenie Złotego Okna (jeśli aktywne):
        try:
            import datetime
            from sts_live_config import (
                ACTIVE_HOURS_ENABLED, ACTIVE_HOURS_START, ACTIVE_HOURS_END,
                MIN_DANGER_INDEX_2H, MIN_DANGER_INDEX_1H,
                MIN_SOT_1H, MIN_SOT_2H, MIN_STARS, ALLOWED_OVER_LINES
            )
            now_hour = datetime.datetime.now().hour
            if ACTIVE_HOURS_ENABLED:
                is_active_hour = (now_hour >= ACTIVE_HOURS_START or now_hour < ACTIVE_HOURS_END)
                if not is_active_hour:
                    return {
                        'apm': apm,
                        'danger_index': danger_index,
                        'danger_rating': d_rat,
                        'signals': [],
                        'has_signals': False,
                        'primary_signal': None,
                        'top_recommendation': f'Przerwa w typowaniu ({ACTIVE_HOURS_START}:00 - {ACTIVE_HOURS_END:02d}:00)'
                    }
        except Exception:
            MIN_DANGER_INDEX_2H = 90
            MIN_DANGER_INDEX_1H = 85
            MIN_SOT_1H = 3
            MIN_SOT_2H = 3
            MIN_STARS = 4
            ALLOWED_OVER_LINES = [0.5, 1.5, 2.5]

        # 2. Progi Indeksu Groźności (Danger Index w oknie 10 min)
        is_second_half = (half in ('HT', '2H') or minute >= 45)
        min_required_danger = MIN_DANGER_INDEX_2H if is_second_half else MIN_DANGER_INDEX_1H
        if danger_index < min_required_danger:
            return {
                'apm': apm,
                'danger_index': danger_index,
                'danger_rating': d_rat,
                'signals': [],
                'has_signals': False,
                'primary_signal': None,
                'top_recommendation': f'Zbyt niski napór {"2H" if is_second_half else "1H"} (Danger: {danger_index}%, wymagane: min. {min_required_danger}%)'
            }

        # 3. Wymóg strzałów celnych: min. SoT w meczu LUB min. 1 celny w ostatnich 10 minutach
        d_sot_val = deltas.get('delta_sot_10', 0.0)
        min_required_sot = MIN_SOT_2H if is_second_half else MIN_SOT_1H
        if sot < min_required_sot and d_sot_val < 1.0:
            return {
                'apm': apm,
                'danger_index': danger_index,
                'danger_rating': d_rat,
                'signals': [],
                'has_signals': False,
                'primary_signal': None,
                'top_recommendation': f'Zbyt mało strzałów celnych (SoT: {sot}, w ost. 10 min: {d_sot_val:.1f})'
            }

        # Blokada głębokiej końcówki meczu: zakaz wejścia po 82. minucie
        if minute >= 82 or half in ('FT', 'AET', 'PEN') or 'koniec' in str(match_data.get('stage_text', '')).lower():
            return {
                'apm': apm,
                'danger_index': danger_index,
                'danger_rating': d_rat,
                'signals': [],
                'has_signals': False,
                'primary_signal': None,
                'top_recommendation': 'Końcówka meczu (zakaz wejścia po 82. minucie)'
            }

        # 3. Analiza rynków STS — sygnały tylko dla potwierdzonych i istniejących rynków
        live_mkts = match_data.get('live_markets', [])
        if not live_mkts:
            return {
                'apm': apm,
                'danger_index': danger_index,
                'danger_rating': d_rat,
                'signals': [],
                'has_signals': False,
                'primary_signal': None
            }

        # Wyciągnij dostępne rynki Over FT i Over HT
        available_over_ft = []
        available_over_ht = []
        for m in live_mkts:
            name = str(m.get('name', '')).upper()
            market_name = str(m.get('market', '')).upper()
            combined_txt = f"{name} {market_name}"
            odds = float(m.get('odds', 0.0))
            # ŻELAZNA ZASADA: Tylko bezpieczny przedział kursowy 1.35 - 2.45
            if odds < 1.35 or odds > 2.45:
                continue

            if 'OVER' in combined_txt and ('FT' in combined_txt or 'MECZ' in combined_txt or 'LICZBA GOLI' in combined_txt):
                m_match = re.search(r'OVER\s+(\d+(?:\.\d+)?)', combined_txt)
                if m_match:
                    l_val = float(m_match.group(1))
                    # ŻELAZNA ZASADA: Dopuszczamy wyłącznie bezpieczne linie FT: 0.5, 1.5, 2.5 FT
                    if l_val in (0.5, 1.5, 2.5) and total_goals < l_val:
                        available_over_ft.append((l_val, m))
            elif 'OVER' in combined_txt and ('HT' in combined_txt or '1. POŁ' in combined_txt or '1.POŁ' in combined_txt or '1H' in combined_txt):
                m_match = re.search(r'OVER\s+(\d+(?:\.\d+)?)', combined_txt)
                if m_match:
                    l_val = float(m_match.group(1))
                    # Usunięto Over 0.5 HT - dopuszczamy wyłącznie Over 1.5 HT lub wyżej
                    if l_val >= 1.5 and total_goals < l_val <= 2.5:
                        available_over_ht.append((l_val, m))

        # Priorytet dla rynków pobranych w 100% bezpośrednio ze strony STS (STS_REAL)
        real_ft = [item for item in available_over_ft if item[1].get('source') == 'STS_REAL']
        if real_ft:
            available_over_ft = real_ft

        real_ht = [item for item in available_over_ht if item[1].get('source') == 'STS_REAL']
        if real_ht:
            available_over_ht = real_ht

        available_over_ft.sort(key=lambda x: (x[0], x[1].get('odds', 99)))
        available_over_ht.sort(key=lambda x: (x[0], x[1].get('odds', 99)))

        lowest_over_ft = available_over_ft[0][1] if available_over_ft else None
        lowest_over_ft_line = available_over_ft[0][0] if available_over_ft else None

        lowest_over_ht = available_over_ht[0][1] if available_over_ht else None
        lowest_over_ht_line = available_over_ht[0][0] if available_over_ht else None

        # Filtrowanie lig: WHITELIST & BLACKLIST
        league_str = str(match_data.get('league', '')).lower()
        home_str = str(match_data.get('home_team', '')).lower()
        away_str = str(match_data.get('away_team', '')).lower()
        match_full_text = f"{league_str} {home_str} {away_str}"

        from sts_live_config import (
            LEAGUE_WHITELIST, LEAGUE_BLACKLIST_KEYWORDS, MIN_ODDS, MAX_ODDS,
            ENABLE_LEAGUE_WHITELIST
        )

        # 1. Czarna lista: wykluczenie tylko rozgrywek wirtualnych i esportowych
        is_blacklisted = any(kw in match_full_text for kw in LEAGUE_BLACKLIST_KEYWORDS)
        if is_blacklisted:
            return {
                'apm': apm,
                'danger_index': danger_index,
                'danger_rating': d_rat,
                'signals': [],
                'has_signals': False,
                'primary_signal': None,
                'top_recommendation': f'Mecz wykluczony (sport wirtualny / esport / futsal: {league_str})'
            }

        # 2. Biała lista lig: tylko jeśli jawnie aktywowana w konfiguracji
        if ENABLE_LEAGUE_WHITELIST:
            is_whitelisted = any(wl in match_full_text for wl in LEAGUE_WHITELIST)
            if not is_whitelisted:
                return {
                    'apm': apm,
                    'danger_index': danger_index,
                    'danger_rating': d_rat,
                    'signals': [],
                    'has_signals': False,
                    'primary_signal': None,
                    'top_recommendation': f'Liga poza białą listą płynności ({league_str})'
                }

        is_anti_goal_league = any(kw in match_full_text for kw in ANTI_GOAL_LEAGUES_KEYWORDS)
        if is_anti_goal_league:
            return {
                'apm': apm,
                'danger_index': danger_index,
                'danger_rating': d_rat,
                'signals': [],
                'has_signals': False,
                'primary_signal': None,
                'top_recommendation': f'Liga antybramkowa / niszowa wykluczona ({league_str})'
            }

        # Filtrowanie anomalii: jałowe posiadanie (dużo ataków, ale 0 strzałów celnych w minucie 25+)
        is_sterile_possession = (minute >= 25 and sot == 0 and dangerous_attacks >= 20)

        # Filtrowanie anomalii: mecz rozstrzygnięty (blowout w 2. połowie)
        is_blowout_game = (half in ('2H', 'FT') and score_diff >= 3 and minute >= 60)

        signals = []

        # =========================================================================
        # --- A. STRATEGIA 1: OVER 0.5 / 1.5 FT W 1. POŁOWIE (ZŁOTE OKNO 14'-32' MIN, WYNIK 0:0) ---
        # =========================================================================
        if (half == '1H' and 14 <= minute <= 32 and total_goals == 0 and lowest_over_ft and
                not is_anti_goal_league and not is_sterile_possession):
            if sot >= 3 and danger_index >= 85:
                # Wybierz optymalną bezpieczną linię meczową FT o kursie >= MIN_ODDS
                target_over = None
                for l_val, m_obj in available_over_ft:
                    o_val = float(m_obj.get('odds', 0))
                    if l_val in (0.5, 1.5, 2.5) and MIN_ODDS <= o_val <= MAX_ODDS:
                        target_over = (l_val, m_obj)
                        break

                if target_over:
                    t_line, t_obj = target_over
                    odds_val = float(t_obj.get('odds', 0))
                    confidence = 2
                    reasons = [f"Potężny napór 1H (Danger: {danger_index}%)", f"{sot} strzałów celnych"]
                    if apm >= 0.90:
                        confidence += 1
                        reasons.append(f"APM: {apm:.2f}")
                    if sot >= 4:
                        confidence += 1
                    if shots_total >= 8:
                        confidence += 1
                        reasons.append(f"{shots_total} strzałów")
                    if xg_total >= 0.90:
                        confidence += 1
                        reasons.append(f"xG: {xg_total:.2f}")

                    rem_mins_ft = max(1, 90 - minute)
                    ev = self._calculate_expected_value(xg_total, danger_index, apm, sot, minute, rem_mins_ft, odds_val)
                    if confidence >= 3 and MIN_ODDS <= odds_val <= MAX_ODDS:
                        stars_count = min(5, max(4, confidence))
                        signals.append({
                            'type': 'OVER_1H_TO_FT',
                            'title': f"🎯 ALARM: OVER {t_line} FT",
                            'badge': f"OVER {t_line} FT",
                            'color': '#00E676',
                            'odds': odds_val,
                            'stars': stars_count,
                            'ev': round(ev, 3),
                            'desc': f"Złote Okno 1H ({minute}'). Wynik 0:0 pod ciągłym ostrzałem bramki. " + ", ".join(reasons)
                        })

        # =========================================================================
        # --- B. STRATEGIA 2: OVER 1.5 HT (ZŁOTE OKNO 14'-34' MIN, WYNIK 1:0/0:1) ---
        # =========================================================================
        cfg_15_ht = self.config.get('OVER_15_HT', {})
        min_m_15ht = cfg_15_ht.get('min_minute', 14)
        max_m_15ht = cfg_15_ht.get('max_minute', 32)

        if (lowest_over_ht and lowest_over_ht_line == 1.5 and total_goals == 1 and half == '1H' and
                min_m_15ht <= minute <= max_m_15ht and not is_anti_goal_league and not is_sterile_possession):

            min_sot_15 = max(3, cfg_15_ht.get('min_sot', 3))
            if sot >= min_sot_15 and shots_total >= 6 and (apm >= 0.88 or xg_total >= 0.65):
                odds_val = float(lowest_over_ht['odds'])
                reasons = []
                confidence = 1

                reasons.append(f"Mecz otwarty (wynik {home_score}:{away_score})")
                if apm >= 0.92:
                    confidence += 1
                    reasons.append(f"Intensywność APM ({apm:.2f})")
                if sot >= 4:
                    confidence += 2
                    reasons.append(f"{sot} strzałów celnych")
                elif sot >= 3:
                    confidence += 1
                    reasons.append(f"{sot} strzały celne")
                if shots_total >= 7:
                    confidence += 1
                    reasons.append(f"{shots_total} strzałów")
                if xg_total >= 0.80:
                    confidence += 1
                    reasons.append(f"xG: {xg_total:.2f}")

                rem_mins_1h = max(1, 45 - minute)
                ev = self._calculate_expected_value(xg_total, danger_index, apm, sot, minute, rem_mins_1h, odds_val)

                # Sweet spot dla Over 1.5 HT: 1.55 - 2.45
                odds_ok = (1.55 <= odds_val <= 2.45 and confidence >= 2) or (2.45 < odds_val <= 2.80 and confidence >= 4 and sot >= 4)
                if odds_ok and ev >= -0.04:
                    stars_count = min(5, max(2, confidence))
                    signals.append({
                        'type': 'OVER_15_HT',
                        'title': f"⚡ ALARM: OVER 1.5 HT",
                        'badge': f"OVER 1.5 HT",
                        'color': '#00E676',
                        'odds': odds_val,
                        'stars': stars_count,
                        'ev': round(ev, 3),
                        'desc': f"Złote Okno 1H ({minute}'). Wysoka dynamika na 2. gola do przerwy. " + ", ".join(reasons)
                    })

        # =========================================================================
        # --- C. STRATEGIA 3: BŁYSKAWICZNA REAKCJA PO GOLU (POST_GOAL_FT) ---
        # =========================================================================
        req_danger_post = MIN_DANGER_INDEX_2H if (half in ('HT', '2H') or minute >= 45) else MIN_DANGER_INDEX_1H
        if (total_goals >= 1 and (minute >= 20 or half in ('HT', '2H')) and minute <= 78 and
                lowest_over_ft and not is_blowout_game and not is_sterile_possession and not is_anti_goal_league):

            # Wymóg: minimum 3 strzały celne, 6 strzałów łącznych i napór min. 85% (1H) / 90% (HT/2H)
            if sot >= 3 and shots_total >= 6 and danger_index >= req_danger_post:
                reasons = []
                confidence = 1
                reasons.append(f"Mecz otwarty po bramce ({home_score}:{away_score})")

                if sot >= 5:
                    confidence += 2
                    reasons.append(f"{sot} strzałów celnych")
                elif sot >= 3:
                    confidence += 1
                    reasons.append(f"{sot} strzały celne")
                if apm >= 0.90:
                    confidence += 1
                    reasons.append(f"Napór APM: {apm:.2f}")
                if shots_total >= 8:
                    confidence += 1
                    reasons.append(f"{shots_total} łącznych strzałów")
                if xg_total >= 1.00:
                    confidence += 1
                    reasons.append(f"Wysokie xG: {xg_total:.2f}")
                if danger_index >= 70:
                    confidence += 1
                    reasons.append(f"Danger: {danger_index}%")

                # Inteligentny dobór linii z realnych rynków STS:
                # Wybierz linię powyżej aktualnej sumy bramek o optymalnym kursie (1.35 - 2.35)
                valid_lines = [item for item in available_over_ft if item[0] > total_goals]
                valid_lines.sort(key=lambda x: x[0])

                target_over_ft = None
                target_line = None

                for l_val, m_obj in valid_lines:
                    o_val = float(m_obj.get('odds', 0))
                    min_req_odds = 1.38 if (l_val == total_goals + 0.5) else MIN_ODDS
                    if min_req_odds <= o_val <= MAX_ODDS:
                        target_over_ft = m_obj
                        target_line = l_val
                        break

                # Fallback jeśli brak rynków z podstrony: domyślna linia +1 gol o poprawnym kursie
                if not target_over_ft:
                    for l_val, m_obj in available_over_ft:
                        o_val = float(m_obj.get('odds', 0))
                        min_req_odds = 1.38 if (l_val == total_goals + 0.5) else MIN_ODDS
                        if l_val > total_goals and min_req_odds <= o_val <= MAX_ODDS:
                            target_over_ft = m_obj
                            target_line = l_val
                            break

                if target_over_ft and target_line:
                    odds_val = float(target_over_ft['odds'])
                    req_min_post = 1.38 if (target_line == total_goals + 0.5) else MIN_ODDS
                    rem_mins_ft = max(1, 90 - minute)
                    ev = self._calculate_expected_value(xg_total, danger_index, apm, sot, minute, rem_mins_ft, odds_val)

                    # Warunek kursowy: MIN_ODDS (1.60) - MAX_ODDS (2.45)
                    if confidence >= 2 and req_min_post <= odds_val <= MAX_ODDS and ev >= -0.02:
                        time_desc = "Przerwa (HT)" if half == 'HT' else f"{minute}' min"
                        signals.append({
                            'type': 'POST_GOAL_FT',
                            'title': f"⚡ NATYCHMIASTOWY ALARM: OVER {target_line} FT",
                            'badge': f"OVER {target_line} FT",
                            'color': '#00B0FF',
                            'odds': odds_val,
                            'stars': 4,
                            'ev': round(ev, 3),
                            'desc': f"Błyskawiczna reakcja po bramce ({time_desc}). " + ", ".join(reasons)
                        })

        # =========================================================================
        # --- D. STRATEGIA 4: OVER 1.5 FT W 2. POŁOWIE (WCZESNA 2H: 46'-68' MIN) ---
        # =========================================================================
        cfg_15_ft = self.config.get('OVER_15_FT', {})
        min_m_15ft = cfg_15_ft.get('min_minute', 46)
        max_m_15ft = cfg_15_ft.get('max_minute', 68)

        if (lowest_over_ft and half == '2H' and min_m_15ft <= minute <= max_m_15ft and
                total_goals <= cfg_15_ft.get('max_score_sum', 1) and not is_sterile_possession and not is_anti_goal_league):

            min_sot_ft = cfg_15_ft.get('min_sot', 3)
            min_shots_ft = cfg_15_ft.get('min_shots_total', 6)
            min_xg_ft = cfg_15_ft.get('min_xg', 0.75)

            if sot >= min_sot_ft and (shots_total >= min_shots_ft or xg_total >= min_xg_ft or apm >= 0.80):
                odds_val = float(lowest_over_ft['odds'])
                reasons = []
                confidence = 1

                reasons.append(f"Mocny start 2. połowy ({minute}')")
                if sot >= 4:
                    confidence += 1
                    reasons.append(f"{sot} celnych strzałów")
                elif sot >= 3:
                    reasons.append(f"{sot} celne strzały")
                if apm >= 0.85:
                    confidence += 1
                    reasons.append(f"APM: {apm:.2f}")
                if shots_total >= 8:
                    confidence += 1
                    reasons.append(f"{shots_total} strzałów")
                if xg_total >= 0.90:
                    confidence += 1
                    reasons.append(f"xG: {xg_total:.2f}")

                rem_mins_ft = max(1, 90 - minute)
                ev = self._calculate_expected_value(xg_total, danger_index, apm, sot, minute, rem_mins_ft, odds_val)

                # Warunek kursowy: min. 1.38 dla Over 1.5 FT, maks 2.45
                req_min_15 = 1.38 if (lowest_over_ft_line == 1.5) else MIN_ODDS
                if confidence >= 2 and req_min_15 <= odds_val <= MAX_ODDS and ev >= -0.02:
                    signals.append({
                        'type': 'OVER_15_FT',
                        'title': f"🎯 ALARM: OVER {lowest_over_ft_line} FT",
                        'badge': f"OVER {lowest_over_ft_line} FT",
                        'color': '#00B0FF',
                        'odds': odds_val,
                        'stars': 4,
                        'ev': round(ev, 3),
                        'desc': f"Świetny potencjał 2H ({minute}'). Wysoka szansa na kolejne bramki. " + ", ".join(reasons)
                    })

        # =========================================================================
        # --- E. STRATEGIA 5: KOLEJNY GOL W 2. POŁOWIE (OVER 0.5 2H / 63'-75' MIN) ---
        # =========================================================================
        cfg_05_2h = self.config.get('OVER_05_2H', {})
        min_m_2h = cfg_05_2h.get('min_minute', 63)
        max_m_2h = min(75, cfg_05_2h.get('max_minute', 75))

        if (lowest_over_ft and half == '2H' and min_m_2h <= minute <= max_m_2h and
                score_diff <= cfg_05_2h.get('allowed_score_diff', 2) and not is_blowout_game and not is_sterile_possession and not is_anti_goal_league):

            # Zabezpieczenie przed wysokimi liniami Over 4.5+ w samej końcówce
            is_high_line_late = (lowest_over_ft_line is not None and lowest_over_ft_line >= 4.5)

            if not is_high_line_late and sot >= cfg_05_2h.get('min_sot', 2):
                reasons = []
                confidence = 1

                if apm >= cfg_05_2h.get('min_apm', 0.85):
                    confidence += 1
                    reasons.append(f"Wysokie APM: {apm:.2f}")
                if sot >= 3:
                    confidence += 1
                    reasons.append(f"{sot} celnych strzałów")
                elif sot >= 2:
                    reasons.append(f"{sot} celne strzały")
                if shots_total >= 7:
                    confidence += 1
                    reasons.append(f"{shots_total} strzałów")
                if xg_total >= 0.80:
                    confidence += 1
                    reasons.append(f"xG: {xg_total:.2f}")
                if danger_index >= 60:
                    confidence += 1
                    reasons.append(f"Napór 2H: {danger_index}%")

                odds_val = float(lowest_over_ft['odds'])
                rem_mins_ft = max(1, 90 - minute)
                ev = self._calculate_expected_value(xg_total, danger_index, apm, sot, minute, rem_mins_ft, odds_val)

                # Warunek kursowy: MIN_ODDS (1.60) - MAX_ODDS (2.45)
                if confidence >= 2 and MIN_ODDS <= odds_val <= MAX_ODDS and ev >= -0.02:
                    signals.append({
                        'type': 'OVER_05_2H',
                        'title': f"🔥 ALARM: OVER {lowest_over_ft_line} FT",
                        'badge': f"OVER {lowest_over_ft_line} FT",
                        'color': '#FF3D00',
                        'odds': odds_val,
                        'stars': 4,
                        'ev': round(ev, 3),
                        'desc': f"Świetne okno na kolejną bramkę ({minute}'). " + ", ".join(reasons)
                    })

        # =========================================================================
        # ŻELAZNA FILTRACJA MATEMATYCZNA: PEWNIAKI 4⭐ / 5⭐, LINIE 0.5-2.5 FT
        # =========================================================================
        filtered_signals = []
        for s in signals:
            odds_v = float(s.get('odds', 0.0))
            badge_str = str(s.get('badge', '')).upper()

            # 1. Bezwzględny warunek minimalnego kursu: min. 1.38 dla Over 1.5 FT (+1 gol), min. 1.50 dla wyższych
            is_safe_plus_one = ('OVER 1.5 FT' in badge_str and total_goals == 1) or ('OVER 0.5 FT' in badge_str and total_goals == 0)
            req_floor = 1.38 if is_safe_plus_one else MIN_ODDS
            if odds_v < req_floor or odds_v > MAX_ODDS:
                continue

            # 2. Tylko linie meczowe FT: 0.5 FT, 1.5 FT, 2.5 FT
            if not any(ab in badge_str for ab in ('OVER 0.5 FT', 'OVER 1.5 FT', 'OVER 2.5 FT')):
                continue

            # 3. Żelazny warunek Indeksu Groźności (min. 85% w 1H, min. 90% w HT/2H) oraz min. 3 strzały celne
            req_danger = MIN_DANGER_INDEX_2H if is_second_half else MIN_DANGER_INDEX_1H
            if danger_index < req_danger or sot < 3:
                continue

            # 4. Transparentny, matematyczny system oceny jakości i gwiazdek:
            score_pts = 0
            if sot >= 5: score_pts += 2
            elif sot >= 3: score_pts += 1

            if apm >= 1.05: score_pts += 2
            elif apm >= 0.90: score_pts += 1

            if xg_total >= 1.20: score_pts += 2
            elif xg_total >= 0.85: score_pts += 1

            ev_val = float(s.get('ev', 0.0))
            if ev_val >= 0.08: score_pts += 2
            elif ev_val >= 0.01: score_pts += 1

            # Definicja progów:
            # 5⭐ (Super-Lock, 3J): score >= 5, EV >= +0.05, Kurs >= 1.60
            # 4⭐ (Wysoka pewność, 2J):
            #   - Dla bezpiecznych linii +1 gol (Over 1.5 FT przy 1 bramce): score >= 3, EV >= +0.01, Kurs >= 1.38
            #   - Dla linii standardowych: score >= 3, EV >= +0.02, Kurs >= 1.55
            req_min_odds_4star = 1.38 if is_safe_plus_one else 1.55

            if score_pts >= 5 and ev_val >= 0.05 and odds_v >= 1.60:
                s['stars'] = 5
                filtered_signals.append(s)
            elif score_pts >= 3 and ev_val >= 0.01 and odds_v >= req_min_odds_4star:
                s['stars'] = 4
                filtered_signals.append(s)
            else:
                continue

        signals = filtered_signals

        # =========================================================================
        # GWARANCJA CZYSTOŚCI INTERFEJSU: 1 MECZ = 1 NAJLEPSZY SYGNAŁ (TOP VALUE)
        # =========================================================================
        if len(signals) > 1:
            # Sortuj wg (stars, EV, kurs) dla wyboru bezwzględnie najlepszego Value Betu
            signals.sort(key=lambda s: (s.get('stars', 0), s.get('ev', 0.0), s.get('odds', 0.0)), reverse=True)
            signals = [signals[0]]

        return {
            'apm': apm,
            'danger_index': danger_index,
            'danger_rating': d_rat,
            'signals': signals,
            'has_signals': len(signals) > 0,
            'primary_signal': signals[0] if signals else None
        }

    def _calculate_dynamic_apm(self, minute: int, dangerous_attacks: int, shots: int, sot: int, corners: int, deltas: Optional[Dict[str, Any]] = None) -> float:
        """
        Oblicza dynamiczny wskaźnik APM (Attacks Per Minute) znormalizowany i zabezpieczony przed skrajnościami.
        Gdy dostępne jest okno 10-minutowe, liczy tempo z ostatniego okresu gry.
        """
        if minute <= 3:
            return 0.0

        if deltas and deltas.get('is_full_window'):
            d_da = deltas.get('delta_da_10', 0.0)
            d_sot = deltas.get('delta_sot_10', 0.0)
            d_shots = deltas.get('delta_shots_10', 0.0)
            d_corn = deltas.get('delta_corners_10', 0.0)
            d_off = max(0.0, d_shots - d_sot)
            # 10-minutowe tempo podzielone przez 10 = APM na minutę
            apm_10m = (d_da * 0.60 + (d_sot * 2.5 + d_off * 1.2 + d_corn * 0.8) * 0.40) / 10.0
            return round(min(3.0, max(0.1, apm_10m)), 2)

        minute_f = float(max(1, minute))
        if dangerous_attacks > 0:
            raw_dang_rate = dangerous_attacks / minute_f
            shot_corn_rate = (sot * 3.5 + max(0, shots - sot) * 1.5 + corners * 1.0) / minute_f
            apm = (raw_dang_rate * 0.60) + (shot_corn_rate * 0.40)
        else:
            apm = (sot * 4.0 + max(0, shots - sot) * 1.8 + corners * 1.4) / minute_f

        return round(min(3.0, max(0.1, apm)), 2)

    def _calculate_danger_index(self, deltas: Dict[str, Any], red_cards: int = 0, minute: int = 0, half: str = '1H', total_sot: int = 0, total_shots: int = 0) -> int:
        """
        Zwraca Indeks Groźności (0-100) oparty na 10-minutowym oknie kroczącym (Rolling Window Delta).
        Eliminuje problem inercji w 2H oraz multikolinearność (brak dublowania tych samych strzałów).
        """
        d_sot = float(deltas.get('delta_sot_10', 0.0))
        d_shots = float(deltas.get('delta_shots_10', 0.0))
        d_da = float(deltas.get('delta_da_10', 0.0))
        d_corners = float(deltas.get('delta_corners_10', 0.0))
        d_xg = float(deltas.get('delta_xg_10', 0.0))
        d_big = float(deltas.get('delta_big_10', 0.0))

        score = 0.0

        # 1. Dominacja terytorialna / Ataki niebezpieczne w 10 min (max 25 pkt)
        # 15+ ataków w 10 min = 25 pkt
        score += min(25.0, d_da * 1.65)

        # 2. Bezpośrednie zagrożenie bramkowe: Strzały celne w 10 min (max 25 pkt)
        # 2+ strzałów celnych w 10 min = 25 pkt (1 celny = 12.5 pkt)
        score += min(25.0, d_sot * 12.5)

        # 3. Jakość wykreowanych okazji: xG w 10 min (max 20 pkt)
        # 0.35+ xG w 10 min = 20 pkt
        score += min(20.0, d_xg * 50.0)

        # 4. Próby strzałowe niecelne lub zablokowane (max 12 pkt)
        # Czysta ortogonalność: celne zostały policzone wyżej, więc liczymy tylko NIEcelne!
        d_off_target = max(0.0, d_shots - d_sot)
        score += min(12.0, d_off_target * 4.0)

        # 5. Rzuty rożne w 10 min (max 8 pkt)
        # 2 rożne w 10 min = 8 pkt
        score += min(8.0, d_corners * 4.0)

        # 6. Duże szanse bramkowe (Big chances - max 6 pkt)
        if d_big > 0:
            score += min(6.0, d_big * 6.0)

        # 7. Czerwona kartka (bonus 4 pkt)
        if red_cards > 0:
            score += 4.0

        return max(5, min(100, int(round(score))))

    def _calculate_expected_value(self, xg: float, danger_index: int, apm: float, sot: int, minute: int, rem_mins: int, odds: float) -> float:
        """
        Estymuje matematyczną Wartość Oczekiwaną (Expected Value, EV = P * odds - 1.0)
        za pomocą modelu intensywności Poissona dla pozostałego czasu gry z uwzględnieniem doliczonego czasu.
        """
        if rem_mins <= 0 or odds <= 1.0:
            return -1.0

        # Uwzględnienie realnego czasu z doliczeniem (stoppage time buffer)
        stoppage_buffer = 4 if rem_mins >= 10 else (2 if rem_mins >= 3 else 0)
        effective_rem_mins = rem_mins + stoppage_buffer

        effective_min = max(10, minute)
        xg_per_min = max(0.005, xg / float(effective_min))
        sot_per_min = max(0.010, sot / float(effective_min))

        # Współczynnik intensywności meczu
        intensity_factor = max(0.40, min(1.80, (danger_index / 55.0) * (0.60 + apm * 0.40)))
        lambda_per_min = (xg_per_min * 0.50 + sot_per_min * 0.12 + (apm * 0.008)) * intensity_factor
        lambda_total = lambda_per_min * effective_rem_mins

        # Prawdopodobieństwo min. 1 gola w pozostałym czasie: P(X >= 1) = 1 - e^(-lambda)
        prob_goal = 1.0 - math.exp(-max(0.01, lambda_total))
        prob_goal = min(0.92, max(0.05, prob_goal))

        # EV = (P * kurs) - 1.0
        ev = (prob_goal * odds) - 1.0
        return round(ev, 3)

