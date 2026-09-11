"""
Silnik reguł i triggerów bramkowych (Over 0.5 / 1.5 HT, Over 1.5 / 2.5 FT, Post-Goal, Late Goal 2H).
Analizuje w czasie rzeczywistym statystyki z Flashscore oraz kursy z STS.
"""
import math
import re
import time
import threading
from collections import deque
from typing import Dict, Any, List, Optional, Tuple
from sts_live_config import (
    TRIGGERS_CONFIG,
    MIN_DANGER_INDEX_1H, MIN_DANGER_INDEX_2H, MIN_DANGER_INDEX_5M, MAX_FALLING_TREND,
    MIN_SOT_1H, MIN_SOT_2H, MIN_SOT_10M_REQUIRED,
    MIN_EV_4_STAR, MIN_EV_5_STAR,
    POLISH_TAX_MULTIPLIER, MIN_EV_PL_4_STAR, MIN_EV_PL_5_STAR,
    MIN_ODDS, MAX_ODDS, MIN_STARS, ALLOWED_OVER_LINES,
    SCENARIO_ODDS_RANGES, LEAGUE_TIER_1_KEYWORDS, LEAGUE_TIER_3_KEYWORDS,
    LEAGUE_WHITELIST, LEAGUE_BLACKLIST_KEYWORDS, ENABLE_LEAGUE_WHITELIST,
    ACTIVE_HOURS_ENABLED, ACTIVE_HOURS_START, ACTIVE_HOURS_END
)
from engine.shadow_logger import ShadowLogger


class GoalTriggersEngine:
    def __init__(self, config=None):
        self.config = config or TRIGGERS_CONFIG
        # Bufor serii czasowej (Sliding Time-Series Buffer w RAM)
        # match_key -> {'snapshots': deque(maxlen=120), 'last_goal_minute': int, 'last_score': (h, a), 'last_seen': float}
        self._match_history = {}
        self._history_lock = threading.Lock()
        self._last_cleanup_ts = time.time()
        self.shadow_logger = ShadowLogger()

    def _get_league_tier(self, league: str, home: str, away: str) -> Tuple[int, int]:
        """
        Zwraca (tier, points_modifier) dla League Quality Score:
        Tier 1: Top ligi o wysokiej bramkowości & płynności (+1 pkt do scoringu)
        Tier 2: Ligi standardowe / neutralne (0 pkt)
        Tier 3: Ligi defensywne / niszowe (-1 pkt kary, wymagają wybitnych statystyk na boisku)
        """
        txt = f"{league} {home} {away}".lower()
        for kw in LEAGUE_TIER_1_KEYWORDS:
            if kw in txt:
                return 1, 1
        for kw in LEAGUE_TIER_3_KEYWORDS:
            if kw in txt:
                return 3, -1
        return 2, 0

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

    @staticmethod
    def _read_team_pair(stats: Dict[str, Any], metric: str) -> Tuple[Optional[float], Optional[float]]:
        """Odczytuje parę home/away bez zamiany braku danych na sztuczne zero."""
        aliases = {
            'xg': (('xg_home', 'expected_goals_home'), ('xg_away', 'expected_goals_away')),
            'sot': (('shots_on_target_home', 'shots_on_target_total_home'),
                    ('shots_on_target_away', 'shots_on_target_total_away')),
        }
        home_keys, away_keys = aliases[metric]

        def _first_numeric(keys: Tuple[str, ...]) -> Optional[float]:
            for key in keys:
                value = stats.get(key)
                if value is not None:
                    try:
                        return max(0.0, float(value))
                    except (TypeError, ValueError):
                        return None
            return None

        return _first_numeric(home_keys), _first_numeric(away_keys)

    @staticmethod
    def _calculate_directional_10m(
        current: Dict[str, Any], baseline: Dict[str, Any], normalizer: float
    ) -> Dict[str, Optional[float]]:
        """Zwraca znormalizowane delty 10m per drużyna; None oznacza brak wiarygodnej pary danych."""
        result: Dict[str, Optional[float]] = {}
        for metric in ('xg', 'sot'):
            for side in ('home', 'away'):
                key = f'{metric}_{side}'
                current_value = current.get(key)
                baseline_value = baseline.get(key)
                result[f'delta_{metric}_{side}_10'] = (
                    round(max(0.0, float(current_value) - float(baseline_value)) * normalizer, 2)
                    if current_value is not None and baseline_value is not None else None
                )
        return result

    def _record_snapshot_and_get_deltas(self, match_key: str, snap: Dict[str, Any]) -> Dict[str, Any]:
        """
        Zapisuje stan meczu do bufora kroczącego i wylicza twarde delty z ostatnich 10 i 5 minut.
        Wykrywa także bramki i zarządza stanem Cool-down oraz True Post-Goal Baseline Reset.
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
                    'goal_anchor_snap': None,
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

                # Wykrycie nowej bramki: kotwiczymy punkt zerowy snapshotu (True Post-Goal Reset)
                if len(hist['snapshots']) > 0 and cur_score != prev_score and cur_goals > sum(prev_score):
                    hist['last_goal_minute'] = snap['minute']
                    hist['last_goal_time'] = now
                    hist['last_score'] = cur_score
                    hist['goal_anchor_snap'] = dict(snap)

            snaps = hist['snapshots']
            # Rejestruj próbkę jeśli minęło min. 8 sekund lub zmieniła się minuta
            if not snaps or (now - snaps[-1]['time'] >= 8.0) or (snap['minute'] != snaps[-1]['minute']):
                snaps.append(snap)

            # Szukamy snapshotu z przeszłości sprzed ~10 minut oraz sprzed ~5 minut
            target_min_10 = snap['minute'] - 10
            target_time_10 = now - 600.0

            target_min_5 = snap['minute'] - 5
            target_time_5 = now - 300.0

            past_snap_10 = None
            past_snap_5 = None
            for s in snaps:
                if s['minute'] <= target_min_10 or s['time'] <= target_time_10:
                    past_snap_10 = s
                if s['minute'] <= target_min_5 or s['time'] <= target_time_5:
                    past_snap_5 = s

            # TRUE POST-GOAL BASELINE RESET:
            # Jeśli w ciągu ostatnich 15 minut padł gol, delty nie mogą uwzględniać akcji sprzed bramki!
            goal_anchor = hist.get('goal_anchor_snap')
            last_goal_m = hist.get('last_goal_minute')
            if goal_anchor and last_goal_m is not None and (snap['minute'] - last_goal_m <= 15):
                anchor_t = goal_anchor['time']
                if past_snap_10 is not None and past_snap_10['time'] < anchor_t:
                    past_snap_10 = goal_anchor
                if past_snap_5 is not None and past_snap_5['time'] < anchor_t:
                    past_snap_5 = goal_anchor

            baseline_10 = past_snap_10 if past_snap_10 is not None else snaps[0]
            baseline_5 = past_snap_5 if past_snap_5 is not None else snaps[0]

            tracked_mins_10 = max(0.5, float(snap['minute'] - baseline_10['minute']) if snap['minute'] >= baseline_10['minute'] else (now - baseline_10['time']) / 60.0)
            tracked_mins_5 = max(0.5, float(snap['minute'] - baseline_5['minute']) if snap['minute'] >= baseline_5['minute'] else (now - baseline_5['time']) / 60.0)

            is_full_window = (past_snap_10 is not None) or (tracked_mins_10 >= 7.0)
            # Trend DI5 jest wiarygodny dopiero po rzeczywistym, niemal pełnym oknie 5 min.
            is_full_window_5 = past_snap_5 is not None and tracked_mins_5 >= 4.5
            has_da_10 = bool(snap.get('has_da') and baseline_10.get('has_da'))

            # Surowe różnice dla okna 10-minutowego
            raw_sot_10 = max(0, snap['sot'] - baseline_10['sot'])
            raw_shots_10 = max(0, snap['shots'] - baseline_10['shots'])
            raw_da_10 = max(0, snap['dangerous_attacks'] - baseline_10['dangerous_attacks']) if has_da_10 else 0
            raw_corners_10 = max(0, snap['corners'] - baseline_10['corners'])
            raw_xg_10 = max(0.0, snap['xg'] - baseline_10['xg'])
            raw_big_10 = max(0, snap['big_chances'] - baseline_10['big_chances'])

            # Surowe różnice dla okna 5-minutowego
            raw_sot_5 = max(0, snap['sot'] - baseline_5['sot'])
            raw_shots_5 = max(0, snap['shots'] - baseline_5['shots'])
            has_da_5 = bool(snap.get('has_da') and baseline_5.get('has_da'))
            raw_da_5 = max(0, snap['dangerous_attacks'] - baseline_5['dangerous_attacks']) if has_da_5 else 0
            raw_corners_5 = max(0, snap['corners'] - baseline_5['corners'])
            raw_xg_5 = max(0.0, snap['xg'] - baseline_5['xg'])
            raw_big_5 = max(0, snap['big_chances'] - baseline_5['big_chances'])

            if is_full_window and tracked_mins_10 >= 7.0:
                norm_10 = 10.0 / tracked_mins_10
                d_sot_10 = raw_sot_10 * norm_10
                d_shots_10 = raw_shots_10 * norm_10
                d_da_10 = raw_da_10 * norm_10
                d_corners_10 = raw_corners_10 * norm_10
                d_xg_10 = raw_xg_10 * norm_10
                d_big_10 = raw_big_10 * norm_10
            else:
                # Cold Start / wczesna faza
                if snap['half'] in ('2H', 'HT') or snap['minute'] >= 45:
                    eff_mins = max(5.0, float(snap['minute'] - 45.0))
                    norm_10 = min(2.0, 10.0 / eff_mins)
                    d_sot_10 = raw_sot_10 * norm_10 if raw_sot_10 > 0 else (snap['sot'] / max(1.0, snap['minute'] / 10.0))
                    d_shots_10 = raw_shots_10 * norm_10 if raw_shots_10 > 0 else (snap['shots'] / max(1.0, snap['minute'] / 10.0))
                    d_da_10 = raw_da_10 * norm_10 if has_da_10 else 0.0
                    d_corners_10 = raw_corners_10 * norm_10 if raw_corners_10 > 0 else (snap['corners'] / max(1.0, snap['minute'] / 10.0))
                    d_xg_10 = raw_xg_10 * norm_10 if raw_xg_10 > 0 else (snap['xg'] / max(1.0, snap['minute'] / 10.0))
                    d_big_10 = raw_big_10 * norm_10 if raw_big_10 > 0 else (snap['big_chances'] / max(1.0, snap['minute'] / 10.0))
                else:
                    eff_mins = max(5.0, float(snap['minute']))
                    norm_10 = min(2.0, 10.0 / eff_mins)
                    d_sot_10 = raw_sot_10 * norm_10 if raw_sot_10 > 0 else (snap['sot'] * norm_10)
                    d_shots_10 = raw_shots_10 * norm_10 if raw_shots_10 > 0 else (snap['shots'] * norm_10)
                    d_da_10 = raw_da_10 * norm_10 if has_da_10 else 0.0
                    d_corners_10 = raw_corners_10 * norm_10 if raw_corners_10 > 0 else (snap['corners'] * norm_10)
                    d_xg_10 = raw_xg_10 * norm_10 if raw_xg_10 > 0 else (snap['xg'] * norm_10)
                d_big_10 = raw_big_10 * norm_10 if raw_big_10 > 0 else (snap['big_chances'] * norm_10)

            directional_deltas = self._calculate_directional_10m(snap, baseline_10, norm_10)

            # Normalizacja okna 5m do skali 10m (dla bezpośredniej porównywalności DI5 z DI10)
            if is_full_window_5:
                norm_5 = 10.0 / tracked_mins_5
                d_sot_5 = raw_sot_5 * norm_5
                d_shots_5 = raw_shots_5 * norm_5
                d_da_5 = raw_da_5 * norm_5
                d_corners_5 = raw_corners_5 * norm_5
                d_xg_5 = raw_xg_5 * norm_5
                d_big_5 = raw_big_5 * norm_5
            else:
                # Cold start: zachowujemy DI5 do podglądu, lecz is_full_window_5=False
                # bezwzględnie blokuje wykorzystanie go jako potwierdzenia trendu.
                d_sot_5 = d_sot_10
                d_shots_5 = d_shots_10
                d_da_5 = d_da_10
                d_corners_5 = d_corners_10
                d_xg_5 = d_xg_10
                d_big_5 = d_big_10
                raw_sot_5 = round(raw_sot_10 / 2.0, 1)

            return {
                'delta_sot_10': round(d_sot_10, 2),
                'delta_shots_10': round(d_shots_10, 2),
                'delta_da_10': round(d_da_10, 2),
                'delta_corners_10': round(d_corners_10, 2),
                'delta_xg_10': round(d_xg_10, 2),
                'delta_big_10': round(d_big_10, 2),
                'raw_sot_10': raw_sot_10,
                **directional_deltas,

                'delta_sot_5': round(d_sot_5, 2),
                'delta_shots_5': round(d_shots_5, 2),
                'delta_da_5': round(d_da_5, 2),
                'delta_corners_5': round(d_corners_5, 2),
                'delta_xg_5': round(d_xg_5, 2),
                'delta_big_5': round(d_big_5, 2),
                'raw_sot_5': raw_sot_5,

                'has_da': has_da_10,
                'has_da_5': has_da_5,
                'last_goal_minute': hist.get('last_goal_minute'),
                'last_goal_time': hist.get('last_goal_time', 0.0),
                'is_full_window': is_full_window,
                'is_full_window_5': is_full_window_5
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
        xg_home, xg_away = self._read_team_pair(stats, 'xg')
        sot_home, sot_away = self._read_team_pair(stats, 'sot')

        # Dane wyliczone z kursów lub pochodne xG mogą służyć wyłącznie do podglądu.
        # Nie mogą tworzyć automatycznego sygnału value betting.
        if stats.get('is_estimated') is True or stats.get('xg_is_estimated') is True:
            return {
                'apm': 0.0, 'danger_index': 0, 'danger_index_10': 0,
                'danger_index_5': 0, 'trend': 0, 'danger_rating': 'BRAK DANYCH',
                'signals': [], 'has_signals': False, 'primary_signal': None,
                'top_recommendation': 'Brak sygnału: statystyki estymowane / syntetyczne'
            }

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
            'has_da': bool(
                stats.get('has_da') is True or
                ('dangerous_attacks_total' in stats and stats.get('dangerous_attacks_total') is not None)
            ),
            'corners': corners,
            'xg': xg_total,
            'xg_home': xg_home,
            'xg_away': xg_away,
            'sot_home': sot_home,
            'sot_away': sot_away,
            'big_chances': big_chances,
            'red_cards': red_cards,
            'is_finished': is_finished
        }

        deltas = self._record_snapshot_and_get_deltas(match_key, current_snapshot)

        # 1. Dynamiczne obliczenie APM w oknie kroczącym
        apm = self._calculate_dynamic_apm(minute, dangerous_attacks, shots_total, sot, corners, deltas)

        # 2. Obliczenie Indeksu Groźności dla okna 10m i 5m oraz wskaźnika trendu (Momentum Slope)
        danger_index_10, raw_di_10 = self._calculate_danger_index(deltas=deltas, suffix='_10', red_cards=red_cards)
        danger_index_5, raw_di_5 = self._calculate_danger_index(deltas=deltas, suffix='_5', red_cards=red_cards)
        trend = danger_index_5 - danger_index_10
        trend_ready = bool(deltas.get('is_full_window_5'))
        directional_10m = {
            'home_xg': deltas.get('delta_xg_home_10'),
            'away_xg': deltas.get('delta_xg_away_10'),
            'home_sot': deltas.get('delta_sot_home_10'),
            'away_sot': deltas.get('delta_sot_away_10'),
        }
        trend_state = (
            "UNCONFIRMED" if not trend_ready else
            ("RISING" if trend > 5 else ("FALLING" if trend < -15 else "STABLE"))
        )
        danger_index = danger_index_10
        d_rat = "EKSTREMALNY" if danger_index >= 75 else ("WYSOKI" if danger_index >= 55 else ("ŚREDNI" if danger_index >= 35 else "NISKI"))

        league_str = str(match_data.get('league', '')).lower()
        home_str = str(match_data.get('home_team', '')).lower()
        away_str = str(match_data.get('away_team', '')).lower()
        match_full_text = f"{league_str} {home_str} {away_str}"

        # BRAMKA 1: LIGA (Czarna lista wirtualnych, esportu i niestandardowych formatów)
        is_blacklisted = any(kw in match_full_text for kw in LEAGUE_BLACKLIST_KEYWORDS)
        if is_blacklisted:
            self.shadow_logger.log_evaluation(
                match=match_data, scenario_type="ALL", market="NONE", odds=1.0,
                di_10=danger_index_10, di_5=danger_index_5, trend=trend, trend_state=trend_state,
                sot_total=sot, sot_10m=deltas.get('delta_sot_10', 0.0), shots_total=shots_total,
                apm=apm, xg_total=xg_total, xg_10m=deltas.get('delta_xg_10', 0.0),
                model_probability=0.0, implied_probability=1.0, edge=0.0, ev=-1.0,
                raw_score=0, league_weight=0, effective_score=0,
                status="REJECTED", filter_stage="1_LEAGUE", rejection_reason=f"BLACKLISTED_LEAGUE ({league_str})"
            )
            return {
                'apm': apm, 'danger_index': danger_index, 'danger_index_10': danger_index_10,
                'danger_index_5': danger_index_5, 'trend': trend, 'danger_rating': d_rat,
                'signals': [], 'has_signals': False, 'primary_signal': None,
                'top_recommendation': f'Mecz wykluczony (sport wirtualny / esport: {league_str})'
            }

        # BRAMKA 1b: ANALITYCZNA CZARNA LISTA — Ligi juniorskie / amatorskie / pucharowe (niska wartość)
        # Oparcie na danych historycznych: WR<50% na n>=3 sygnałach (post-mortem 2026-09-01/08)
        # WYJĄTEK BEZWZGLĘDNY: Liga Revelacao U23 (Portugalia) — WR 85.7%, +15.08J na 7 syg -> ZAWSZE PRZEPUSZCZA
        _ANALYTIC_BL_KEYWORDS = [
            'U21', 'U-21',
            'U20', 'U-20',
            'U19', 'U-19',
            'U18', 'U-18',
            'U17', 'U-17',
            'AMATEUR', 'AMATORZY', 'DEVELOPMENT', 'RESERVE', 'REZERWY'
        ]
        _ANALYTIC_BL_EXACT = ['RUMUNIA, PUCHAR', 'CHINY, PUCHAR', 'ARABIA SAUDYJSKA, DIVISION 1']
        _league_upper = str(match_data.get('league', '')).upper()
        _match_text_upper = f"{_league_upper} {str(match_data.get('home_team', '')).upper()} {str(match_data.get('away_team', '')).upper()}"
        _is_revelacao = 'REVELACAO' in _match_text_upper  # wyjątek: Portugalia Liga Revelacao U23

        _analytic_blocked = (
            not _is_revelacao
            and (
                any(kw in _match_text_upper for kw in _ANALYTIC_BL_KEYWORDS)
                or any(ex in _league_upper for ex in _ANALYTIC_BL_EXACT)
                or ('RUMUNIA' in _league_upper and 'PUCHAR' in _league_upper)
                or ('CHINY' in _league_upper and 'PUCHAR' in _league_upper)
                or ('ARABIA' in _league_upper and ('DIVISION 1' in _league_upper or 'PUCHAR' in _league_upper))
            )
        )
        if _analytic_blocked:
            _bl_match = next(
                (kw for kw in _ANALYTIC_BL_KEYWORDS if kw in _match_text_upper),
                next((ex for ex in _ANALYTIC_BL_EXACT if ex in _league_upper), 'BL')
            )
            print(f"[GoalTriggers] [ANALYTIC_BL] Liga/mecz wykluczony analitycznie: '{_league_upper}' (pasuje: '{_bl_match}')")
            self.shadow_logger.log_evaluation(
                match=match_data, scenario_type="ALL", market="NONE", odds=1.0,
                di_10=danger_index_10, di_5=danger_index_5, trend=trend, trend_state=trend_state,
                sot_total=sot, sot_10m=deltas.get('delta_sot_10', 0.0), shots_total=shots_total,
                apm=apm, xg_total=xg_total, xg_10m=deltas.get('delta_xg_10', 0.0),
                model_probability=0.0, implied_probability=1.0, edge=0.0, ev=-1.0,
                raw_score=0, league_weight=0, effective_score=0,
                status="REJECTED", filter_stage="1b_ANALYTIC_LEAGUE",
                rejection_reason=f"ANALYTIC_LEAGUE_BLACKLIST (liga='{_league_upper}', kw='{_bl_match}')"
            )
            return {
                'apm': apm, 'danger_index': danger_index, 'danger_index_10': danger_index_10,
                'danger_index_5': danger_index_5, 'trend': trend, 'danger_rating': d_rat,
                'signals': [], 'has_signals': False, 'primary_signal': None,
                'top_recommendation': f'Mecz wykluczony analitycznie (liga niska jakosci: {_bl_match})'
            }

        # BRAMKA 2: STATUS & KWARANTANNA COOLDOWN PO BRAMCE
        last_goal_min = deltas.get('last_goal_minute')
        if last_goal_min is not None:
            mins_since_goal = minute - last_goal_min
            if 0 <= mins_since_goal <= 4:
                return {
                    'apm': apm, 'danger_index': danger_index, 'danger_index_10': danger_index_10,
                    'danger_index_5': danger_index_5, 'trend': trend, 'danger_rating': d_rat,
                    'signals': [], 'has_signals': False, 'primary_signal': None,
                    'top_recommendation': f'Cool-down po bramce ({mins_since_goal} min od gola: {home_score}:{away_score}) - oczekiwanie na stabilizację rynku'
                }

        # Blokada głębokiej końcówki meczu: zakaz wejścia po 82. minucie
        if minute >= 82 or half in ('FT', 'AET', 'PEN') or 'koniec' in str(match_data.get('stage_text', '')).lower():
            return {
                'apm': apm, 'danger_index': danger_index, 'danger_index_10': danger_index_10,
                'danger_index_5': danger_index_5, 'trend': trend, 'danger_rating': d_rat,
                'signals': [], 'has_signals': False, 'primary_signal': None,
                'top_recommendation': 'Końcówka meczu (zakaz wejścia po 82. minucie)'
            }

        # Wyznaczenie wagi ligi (League Quality Score)
        league_tier, league_weight = self._get_league_tier(league_str, home_str, away_str)

        # Filtrowanie anomalii: jałowe posiadanie lub blowout
        is_sterile_possession = (minute >= 25 and sot == 0 and dangerous_attacks >= 20)
        is_blowout_game = (half in ('2H', 'FT') and score_diff >= 3 and minute >= 60)
        if is_sterile_possession or is_blowout_game:
            return {
                'apm': apm, 'danger_index': danger_index, 'danger_index_10': danger_index_10,
                'danger_index_5': danger_index_5, 'trend': trend, 'danger_rating': d_rat,
                'signals': [], 'has_signals': False, 'primary_signal': None,
                'top_recommendation': 'Anomalia meczowa (jałowe posiadanie lub blowout)'
            }

        # BRAMKA 2b: ANALITYCZNE FILTRY WYNIKOWE / CZASOWE
        # Oparte na danych post-mortem 2026-09-01/08 (330 sygnałów, ND+PN 80 syg)
        # ─────────────────────────────────────────────────────────────────────────
        # FILTR A: 0:0 + Over 2.5 FT po 21. minucie — WR 51.8% (-6.60J overall), DROP
        #   Mecz musi zamknąć 3 bramki z remisu 0:0 — zbyt niskie prawdopodobieństwo
        _is_score_zero_zero = (home_score == 0 and away_score == 0)
        if _is_score_zero_zero and minute >= 21:
            # Ta blokada dotyczy WYŁĄCZNIE scenariuszy Over 2.5 FT (nie blokuje 1.5 FT ani 0.5 FT)
            # Zostanie zastosowana na poziomie kandydatów poniżej przez _zero_zero_block_25ft = True
            _zero_zero_block_25ft = True
        else:
            _zero_zero_block_25ft = False

        # FILTR B: 0:0 + Over 1.5 FT po 60. minucie — tylko 2 gole z 0:0 w ostatnich 30', DROP
        if _is_score_zero_zero and minute >= 60:
            _zero_zero_block_15ft = True
        else:
            _zero_zero_block_15ft = False

        # FILTR C: Zakaz wysokich linii (Over 2.5 / 3.5 / 4.5 FT) po 60. minucie (Minuta 61+)
        # Odrzucanie rynków wysokich linii w końcówkach meczu (WR 25-50% w historii, wysokie ryzyko spadku tempa)
        # WYJĄTEK: Niskie linie (Over 0.5 FT i Over 1.5 FT) są dozwolone zgodnie ze scenariuszami 4 i 5
        _late_game_high_line_block = (minute >= 61)

        # BRAMKA 3: RYNKI LIVE STS
        live_mkts = match_data.get('live_markets', [])
        if not live_mkts:
            return {
                'apm': apm, 'danger_index': danger_index, 'danger_index_10': danger_index_10,
                'danger_index_5': danger_index_5, 'trend': trend, 'danger_rating': d_rat,
                'signals': [], 'has_signals': False, 'primary_signal': None
            }

        # Wyszukanie rynków Over FT i Over HT
        available_over_ft = []
        available_over_ht = []
        for m in live_mkts:
            name = str(m.get('name', '')).upper()
            market_name = str(m.get('market', '')).upper()
            header_name = str(m.get('market_header', m.get('header', ''))).strip().lower()
            combined_txt = f"{name} {market_name}"
            odds = float(m.get('odds', 0.0))
            if odds < 1.15 or odds > 2.60:
                continue

            # PRE-SEND VALIDATION: Wykluczenie wycieków rynków drużynowych i połów
            is_team_leak = (
                any(w in combined_txt for w in ['1. DRUŻYNA', '2. DRUŻYNA', '1.DRUŻYNA', '2.DRUŻYNA', 'DRUŻYNA', 'DRUZYNA', 'GOSPODARZ', 'GOŚĆ', 'GOSC'])
                or any(w in header_name for w in ['drużyn', 'druzyn', 'gospodarz', 'gość', 'gosc'])
                or (m.get('is_match_total') is False and ('FT' in combined_txt or 'MECZ' in combined_txt))
            )
            if is_team_leak:
                print(f"[GoalTriggers] [BLOCKED] REJECTED_TEAM_MARKET_LEAK: Rynek {combined_txt} (naglowek: '{header_name}') odrzucony jako rynek druzynowy!")
                continue

            # PRE-SEND VALIDATION DLA FT: Rynek meczowy FT musi pochodzić wyłącznie z nagłówka meczowego
            if ('FT' in combined_txt or 'MECZ' in combined_txt or 'LICZBA GOLI' in combined_txt):
                if header_name and header_name not in ('liczba goli', 'liczba goli w meczu'):
                    print(f"[GoalTriggers] [BLOCKED] REJECTED_TEAM_MARKET_LEAK: Rynek FT {name} odrzucony! Pochodzi z niedozwolonego naglowka: '{header_name}'")
                    continue

            if 'OVER' in combined_txt and ('FT' in combined_txt or 'MECZ' in combined_txt or 'LICZBA GOLI' in combined_txt):
                m_match = re.search(r'OVER\s+(\d+(?:\.\d+)?)', combined_txt)
                if m_match:
                    l_val = float(m_match.group(1))
                    if l_val in (0.5, 1.5, 2.5) and total_goals < l_val:
                        available_over_ft.append((l_val, m))
            elif 'OVER' in combined_txt and ('HT' in combined_txt or '1. POŁ' in combined_txt or '1.POŁ' in combined_txt or '1H' in combined_txt):
                m_match = re.search(r'OVER\s+(\d+(?:\.\d+)?)', combined_txt)
                if m_match:
                    l_val = float(m_match.group(1))
                    if l_val >= 1.5 and total_goals < l_val <= 2.5:
                        available_over_ht.append((l_val, m))

        # PRE-SEND GATEKEEPER: Wyłącznie rynki autentyczne ze statusem STS_REAL
        available_over_ft = [item for item in available_over_ft if item[1].get('source') == 'STS_REAL']
        available_over_ht = [item for item in available_over_ht if item[1].get('source') == 'STS_REAL']

        available_over_ft.sort(key=lambda x: (x[0], x[1].get('odds', 99)))
        available_over_ht.sort(key=lambda x: (x[0], x[1].get('odds', 99)))

        lowest_over_ft = available_over_ft[0][1] if available_over_ft else None
        lowest_over_ft_line = available_over_ft[0][0] if available_over_ft else None
        lowest_over_ht = available_over_ht[0][1] if available_over_ht else None
        lowest_over_ht_line = available_over_ht[0][0] if available_over_ht else None

        # GENEROWANIE KANDYDATÓW (SCENARIUSZE)
        candidates = []

        # Scenariusz 1: OVER 0.5 / 1.5 FT w 1. połowie (14'-32', 0:0)
        if half == '1H' and 14 <= minute <= 32 and total_goals == 0 and available_over_ft:
            for l_val, m_obj in available_over_ft:
                if l_val in (0.5, 1.5):
                    candidates.append({
                        'type': 'OVER_1H_TO_FT',
                        'title': f"🎯 ALARM: OVER {l_val} FT",
                        'badge': f"OVER {l_val} FT",
                        'color': '#00E676',
                        'odds': float(m_obj.get('odds', 0)),
                        'market_obj': m_obj,
                        'line': l_val,
                        'desc': f"Złote Okno 1H ({minute}'). Wynik 0:0 pod ciągłym ostrzałem bramki."
                    })
                    break

        # Scenariusz 2: OVER 1.5 HT (14'-34', 1:0 lub 0:1)
        if half == '1H' and 14 <= minute <= 34 and total_goals == 1 and lowest_over_ht and lowest_over_ht_line == 1.5:
            candidates.append({
                'type': 'OVER_15_HT',
                'title': "⚡ ALARM: OVER 1.5 HT",
                'badge': "OVER 1.5 HT",
                'color': '#00E676',
                'odds': float(lowest_over_ht.get('odds', 0)),
                'market_obj': lowest_over_ht,
                'line': 1.5,
                'desc': f"Złote Okno 1H ({minute}'). Wysoka dynamika na 2. gola do przerwy."
            })

        # Scenariusz 3: Błyskawiczna reakcja po bramce (POST_GOAL_FT, 20'-78', goli >= 1)
        # ŻELAZNA REGUŁA NEXT GOAL: Dozwolona WYŁĄCZNIE najbliższa linia (total_goals + 0.5 FT).
        # Bezwzględny zakaz przeskoku na wyższą linię (+2 gole, np. Over 2.5 FT przy 1:0/0:1).
        if total_goals >= 1 and 20 <= minute <= 78 and available_over_ft:
            nearest_line = total_goals + 0.5
            valid_nearest = [item for item in available_over_ft if item[0] == nearest_line]
            if valid_nearest:
                target_line, target_m = valid_nearest[0]
                candidates.append({
                    'type': 'POST_GOAL_FT',
                    'title': f"⚡ NATYCHMIASTOWY ALARM: OVER {target_line} FT",
                    'badge': f"OVER {target_line} FT",
                    'color': '#00B0FF',
                    'odds': float(target_m.get('odds', 0)),
                    'market_obj': target_m,
                    'line': target_line,
                    'desc': f"Błyskawiczna reakcja po bramce ({minute}'). Mecz otwarty."
                })

        # Scenariusz 4: OVER 1.5 FT w 2. połowie (46'-68', goli <= 1)
        if half == '2H' and 46 <= minute <= 68 and total_goals <= 1 and available_over_ft:
            for l_val, m_obj in available_over_ft:
                if l_val == 1.5:
                    candidates.append({
                        'type': 'OVER_15_FT',
                        'title': "🎯 ALARM: OVER 1.5 FT",
                        'badge': "OVER 1.5 FT",
                        'color': '#00B0FF',
                        'odds': float(m_obj.get('odds', 0)),
                        'market_obj': m_obj,
                        'line': 1.5,
                        'desc': f"Świetny potencjał 2H ({minute}'). Wczesna faza drugiej części gry."
                    })
                    break

        # Scenariusz 5: Late Goal 2H (63'-75', różnica goli <= 2)
        # Wymóg najbliższej linii: przy goli >= 1 wyłącznie total_goals + 0.5, przy 0:0 linia 0.5 FT
        if half == '2H' and 63 <= minute <= 75 and score_diff <= 2 and available_over_ft:
            if total_goals >= 1:
                nearest_line = total_goals + 0.5
                valid_lines = [item for item in available_over_ft if item[0] == nearest_line]
            else:
                valid_lines = [item for item in available_over_ft if item[0] == 0.5]
            if valid_lines and valid_lines[0][0] <= 3.5:
                target_line, target_m = valid_lines[0]
                candidates.append({
                    'type': 'OVER_05_2H',
                    'title': f"\U0001f525 ALARM: OVER {target_line} FT",
                    'badge': f"OVER {target_line} FT",
                    'color': '#FF3D00',
                    'odds': float(target_m.get('odds', 0)),
                    'market_obj': target_m,
                    'line': target_line,
                    'desc': f"Swietne okno na kolejna bramke ({minute}')."
                })

        d_sot_10 = float(deltas.get('delta_sot_10', 0.0))
        d_shots_10 = float(deltas.get('delta_shots_10', 0.0))
        d_xg_10 = float(deltas.get('delta_xg_10', 0.0))
        d_big_10 = float(deltas.get('delta_big_10', 0.0))

        d_big_10 = float(deltas.get('delta_big_10', 0.0))

        def _log_eval(c_type, market, odds, stage, status, reason=None, stars=0, ev_val=-1.0, model_p=0.0, implied_p=1.0, edge_val=0.0, raw_s=0, eff_s=0, dang_att=None):
            self.shadow_logger.log_evaluation(
                match=match_data, scenario_type=c_type, market=market, odds=odds,
                di_10=danger_index_10, di_5=danger_index_5,
                raw_di_10=raw_di_10, raw_di_5=raw_di_5,
                trend=trend, trend_state=trend_state,
                sot_total=sot, sot_10m=d_sot_10, shots_total=shots_total,
                corners_total=corners, big_chances=big_chances,
                dangerous_attacks=dang_att if dang_att is not None else (int(stats['dangerous_attacks_total']) if stats.get('dangerous_attacks_total') is not None else None),
                apm=apm, xg_total=xg_total, xg_10m=d_xg_10,
                model_probability=model_p, implied_probability=implied_p,
                edge=edge_val, ev=ev_val,
                raw_score=raw_s, league_weight=league_weight, effective_score=eff_s,
                status=status, filter_stage=stage, rejection_reason=reason, stars=stars
            )

        filtered_signals = []

        # PRZEPŁYW LEJKA DLA KAŻDEGO KANDYDATA (FUNNEL EVALUATION)
        for cand in candidates:
            cand_type = cand['type']
            odds_v = cand['odds']
            badge_v = cand['badge']
            line_v = float(cand.get('line', 1.5))
            m_obj = cand.get('market_obj', {})
            # PRE-SEND GATEKEEPER: Wymóg rzeczywistej dostępności w STS (status STS_REAL)
            m_src = str(m_obj.get('source', '')).upper()
            if m_src != 'STS_REAL':
                _log_eval(cand_type, badge_v, odds_v, "0_PRE_SEND", "MARKET_NOT_AVAILABLE_ON_STS", f"Brak statusu STS_REAL (source='{m_src}')")
                print(f"[GoalTriggers] [DROPPED] MARKET_NOT_AVAILABLE_ON_STS: Odrzucono {badge_v}! Linia nie istnieje w STS ze statusem STS_REAL.")
                continue

            # PRE-SEND VALIDATION: Odrzucenie wycieków z kafelków innych niż meczowe
            m_hdr = str(m_obj.get('market_header', '')).strip().lower()
            if 'FT' in badge_v and m_hdr and m_hdr not in ('liczba goli', 'liczba goli w meczu'):
                _log_eval(cand_type, badge_v, odds_v, "0_PRE_SEND", "REJECTED_TEAM_MARKET_LEAK", f"Rynek FT z nagłówka '{m_hdr}'")
                print(f"[GoalTriggers] [BLOCKED] REJECTED_TEAM_MARKET_LEAK: Odrzucono {badge_v}! Rynek pochodzi z kafelka '{m_hdr}'.")
                continue
            if 'FT' in badge_v and m_obj.get('is_match_total') is False:
                _log_eval(cand_type, badge_v, odds_v, "0_PRE_SEND", "REJECTED_TEAM_MARKET_LEAK", "is_match_total == False")
                print(f"[GoalTriggers] [BLOCKED] REJECTED_TEAM_MARKET_LEAK: Odrzucono {badge_v}! is_match_total=False.")
                continue

            is_ht_market = ('HT' in badge_v or '1. POŁ' in badge_v or '1H' in badge_v or cand_type == 'OVER_15_HT')

            # 3b. BLOKADA OKNA 41'-45' DLA 1H ORAZ 36'-40' BEZ EKSTREMALNEJ INTENSYWNOŚCI
            if is_ht_market and 41 <= minute <= 45:
                _log_eval(cand_type, badge_v, odds_v, "3_TIME_WINDOW", "REJECTED", f"HALFTIME_LOCK (min {minute}')", implied_p=1.0/odds_v if odds_v>0 else 1.0)
                continue
            if is_ht_market and 36 <= minute <= 40 and (danger_index_10 < 75 or d_sot_10 < 1.5):
                _log_eval(cand_type, badge_v, odds_v, "3_TIME_WINDOW", "REJECTED", f"LATE_1H_FILTER (min {minute}', DI10={danger_index_10}, SoT10m={d_sot_10:.1f})", implied_p=1.0/odds_v if odds_v>0 else 1.0)
                continue

            # 3c. BRAMKA 2b — EGZEKUCJA ANALITYCZNYCH FILTROW WYNIKOWYCH / CZASOWYCH
            # Flagi obliczone wcześniej (_zero_zero_block_25ft, _zero_zero_block_15ft, _late_game_high_line_block)
            _badge_upper = badge_v.upper()
            _is_25plus_ft = any(line in _badge_upper for line in ('2.5 FT', '3.5 FT', '4.5 FT'))
            _is_15_ft = '1.5 FT' in _badge_upper
            _is_25_ft_exactly = '2.5 FT' in _badge_upper or '3.5 FT' in _badge_upper or '4.5 FT' in _badge_upper

            # FILTR A: Over 2.5 FT przy 0:0 po 21' -> DROP
            if _zero_zero_block_25ft and _is_25plus_ft:
                reason_a = f"ZERO_ZERO_25FT_AFTER_21 (score={home_score}:{away_score}, min={minute}')"
                _log_eval(cand_type, badge_v, odds_v, "2b_ANALYTIC", "REJECTED", reason_a, implied_p=1.0/odds_v if odds_v>0 else 1.0)
                print(f"[GoalTriggers] [ANALYTIC_FILTER] {reason_a} -> DROP {badge_v}")
                continue

            # FILTR B: Over 1.5 FT przy 0:0 po 60' -> DROP
            if _zero_zero_block_15ft and _is_15_ft:
                reason_b = f"ZERO_ZERO_15FT_AFTER_60 (score={home_score}:{away_score}, min={minute}')"
                _log_eval(cand_type, badge_v, odds_v, "2b_ANALYTIC", "REJECTED", reason_b, implied_p=1.0/odds_v if odds_v>0 else 1.0)
                print(f"[GoalTriggers] [ANALYTIC_FILTER] {reason_b} -> DROP {badge_v}")
                continue

            # FILTR C: Wysokie linie (2.5+) po 61' -> DROP (Over 1.5 FT i 0.5 FT dozwolone do 68')
            if _late_game_high_line_block and _is_25_ft_exactly:
                reason_c = f"LATE_GAME_HIGH_LINE (min={minute}' >= 61, rynek={badge_v})"
                _log_eval(cand_type, badge_v, odds_v, "2b_ANALYTIC", "REJECTED", reason_c, implied_p=1.0/odds_v if odds_v>0 else 1.0)
                print(f"[GoalTriggers] [ANALYTIC_FILTER] {reason_c} -> DROP {badge_v}")
                continue

            # FILTR D: Zakaz gry na +2 gole (np. Over 2.5 FT przy 1:0/0:1) przy goli >= 1
            if total_goals >= 1 and 'FT' in _badge_upper and line_v > (total_goals + 0.5):
                reason_d = f"HIGHER_LINE_JUMP_BLOCKED (score={home_score}:{away_score}, line={line_v} > {total_goals + 0.5} FT)"
                _log_eval(cand_type, badge_v, odds_v, "2b_ANALYTIC", "REJECTED", reason_d, implied_p=1.0/odds_v if odds_v>0 else 1.0)
                print(f"[GoalTriggers] [ANALYTIC_FILTER] {reason_d} -> DROP {badge_v}")
                continue

            # 4. KORYTARZ KURSOWY
            sc_min, sc_max = SCENARIO_ODDS_RANGES.get(cand_type, (1.45, 2.45))
            if not (sc_min <= odds_v <= sc_max):
                status_v = "WAITING_FOR_ODDS" if odds_v < sc_min else "REJECTED"
                reason_odds = f"WAITING_FOR_ODDS_WINDOW ({odds_v:.2f} < min {sc_min:.2f})" if odds_v < sc_min else f"ODDS_CORRIDOR_VIOLATION ({odds_v:.2f} not in [{sc_min}, {sc_max}])"
                _log_eval(cand_type, badge_v, odds_v, "4_ODDS", status_v, reason_odds, implied_p=1.0/odds_v if odds_v>0 else 1.0)
                continue

            # 5. INTENSYWNOŚĆ (DI10 i DI5)
            # Próg bazowy 4⭐: min. 55% w oknie 10m oraz min. 60% w oknie 5m (potwierdzenie tempa)
            if not trend_ready:
                _log_eval(cand_type, badge_v, odds_v, "5_INTENSITY", "REJECTED", "UNCONFIRMED_5M_TREND", implied_p=1.0/odds_v if odds_v>0 else 1.0)
                continue
            if danger_index_10 < 55 or danger_index_5 < 60:
                _log_eval(cand_type, badge_v, odds_v, "5_INTENSITY", "REJECTED", f"LOW_INTENSITY (DI10={danger_index_10}%, DI5={danger_index_5}%)", implied_p=1.0/odds_v if odds_v>0 else 1.0)
                continue

            # 6. ŚWIEŻOŚĆ STRZAŁÓW (SoT)
            # Wymóg świeżej aktywności: min. 0.8 SoT w oknie 10m oraz min. 2 SoT łącznie
            if d_sot_10 < 0.8 or sot < 2:
                _log_eval(cand_type, badge_v, odds_v, "6_SOT", "REJECTED", f"COLD_SOT (Total={sot}, 10m={d_sot_10:.1f})", implied_p=1.0/odds_v if odds_v>0 else 1.0)
                continue

            # 7. TREND (Filtr Wejścia - nie unieważnia otwartych typów, blokuje nowe wejścia)
            if trend_state == "FALLING":
                _log_eval(cand_type, badge_v, odds_v, "7_TREND", "REJECTED", f"FALLING_TREND ({trend}%)", implied_p=1.0/odds_v if odds_v>0 else 1.0)
                continue

            # 8. WARTOŚĆ OCZEKIWANA (EV & MODEL PROBABILITY - BRUTTO ORAZ NETTO PL 12%)
            rem_mins = max(1, (45 if is_ht_market else 90) - minute)
            goals_needed = max(1, int(math.ceil(line_v - total_goals)))
            ev_val, model_p, implied_p, edge_val, ev_pl = self._calculate_expected_value(
                xg=xg_total, danger_index=danger_index_10, apm=apm, sot=sot,
                minute=minute, rem_mins=rem_mins, odds=odds_v, goals_needed=goals_needed
            )

            # Twardy próg krawędzi netto po polskim podatku 12% (min. +5% netto)
            if ev_pl < MIN_EV_PL_4_STAR:
                _log_eval(cand_type, badge_v, odds_v, "8_EV", "REJECTED", f"INSUFFICIENT_EV_PL (EV_PL={ev_pl:.1%}, Gross_EV={ev_val:.1%}, min={MIN_EV_PL_4_STAR:.1%})", ev_val=ev_pl, model_p=model_p, implied_p=implied_p, edge_val=edge_val)
                continue

            # 9. 5-FILAROWY SCORING JAKOŚCI (0 - 9 PKT)
            score_f1 = 2 if (danger_index_10 >= 85 and danger_index_5 >= 70) else (1 if (danger_index_10 >= 75 and danger_index_5 >= 60) else 0)
            score_f2 = 2 if (sot >= 4 and d_sot_10 >= 2.0) else (1 if (sot >= 3 and d_sot_10 >= 1.0) else 0)
            score_f3 = 2 if (d_xg_10 >= 0.35 or (d_big_10 >= 1 and d_sot_10 >= 1.0)) else (1 if (d_xg_10 >= 0.20 or xg_total >= 0.85) else 0)
            score_f4 = 1 if apm >= 0.85 else 0
            score_f5 = 2 if ev_pl >= MIN_EV_PL_5_STAR else (1 if ev_pl >= MIN_EV_PL_4_STAR else 0)

            raw_score = score_f1 + score_f2 + score_f3 + score_f4 + score_f5
            effective_score = max(0, min(10, raw_score + league_weight))

            if raw_score < 5:
                _log_eval(cand_type, badge_v, odds_v, "9_SCORING", "REJECTED", f"LOW_RAW_SCORE ({raw_score}/9 pts)", ev_val=ev_pl, model_p=model_p, implied_p=implied_p, edge_val=edge_val, raw_s=raw_score, eff_s=effective_score)
                continue

            # 10. KWALIFIKACJA GWIAZDEK (NA BAZIE RAW SCORE + TWARDYCH WARUNKÓW)
            is_5_star = (
                raw_score >= 7 and
                ev_pl >= MIN_EV_PL_5_STAR and
                odds_v >= 1.60 and
                danger_index_10 >= 65 and
                danger_index_5 >= 70 and
                trend >= -5 and
                d_sot_10 >= 1.5
            )

            is_4_star = (
                raw_score >= 5 and
                ev_pl >= MIN_EV_PL_4_STAR and
                odds_v >= sc_min and
                danger_index_10 >= 55 and
                danger_index_5 >= 60 and
                trend >= -15 and
                d_sot_10 >= 0.8
            )

            if is_5_star:
                stars_awarded = 5
                # Decyzja analityczna: 5⭐ wyłączone z automatycznej wysyłki, śledzone wyłącznie w cieniu (Shadow Only)
                _log_eval(cand_type, badge_v, odds_v, "11_ACCEPTED", "SHADOW_5STAR", "SHADOW_ONLY_5_STAR", stars=5, ev_val=ev_pl, model_p=model_p, implied_p=implied_p, edge_val=edge_val, raw_s=raw_score, eff_s=effective_score)
                continue
            elif is_4_star:
                stars_awarded = 4
            else:
                _log_eval(cand_type, badge_v, odds_v, "10_STAR_THRESHOLDS", "REJECTED", "NOT_MEETING_STAR_HARD_GATES", ev_val=ev_pl, model_p=model_p, implied_p=implied_p, edge_val=edge_val, raw_s=raw_score, eff_s=effective_score)
                continue

            cand['stars'] = stars_awarded
            cand['ev'] = round(ev_val, 3)
            cand['ev_pl'] = round(ev_pl, 3)
            cand['priority_bonus'] = 1 if apm >= 1.05 else 0
            cand['apm_bonus'] = bool(apm >= 1.05)
            cand['raw_score'] = raw_score
            cand['effective_score'] = effective_score
            cand['trend'] = trend
            cand['trend_state'] = trend_state

            # Przydział poziomu: GOLDEN dla Over 1.5 HT w oknie 15-35' ze świeżym SoT, SILVER dla reszty
            if cand_type == 'OVER_15_HT' and 15 <= minute <= 35 and d_sot_10 >= 0.8:
                cand['tier'] = 'GOLDEN'
                cand['title'] = "🥇 GOLDEN SIGNAL: OVER 1.5 HT"
            else:
                cand['tier'] = 'SILVER'
                cand['title'] = f"🥈 SILVER: {cand['title']}"

            cand['is_golden'] = (cand.get('tier') == 'GOLDEN')
            cand['is_silver'] = (cand.get('tier') == 'SILVER')
            cand['di10'] = round(float(danger_index_10), 1)
            cand['di5'] = round(float(danger_index_5), 1)
            cand['sot10m'] = round(float(d_sot_10), 2)
            cand['apm'] = round(float(apm), 2)
            cand['xg'] = round(float(xg_total), 2)
            # Rozróżnienie realnego 0 od braku danych (None / null w OOS)
            has_da_metric = bool(stats.get('has_da') or ('dangerous_attacks_total' in stats and stats['dangerous_attacks_total'] is not None) or ('dangerous_attacks_home' in stats and 'dangerous_attacks_away' in stats))
            if has_da_metric:
                raw_da = stats.get('dangerous_attacks_total')
                if raw_da is None and 'dangerous_attacks_home' in stats:
                    raw_da = (stats.get('dangerous_attacks_home') or 0) + (stats.get('dangerous_attacks_away') or 0)
                cand['dangerous_attacks'] = int(raw_da) if raw_da is not None else None
            else:
                cand['dangerous_attacks'] = None

            has_bc_metric = bool(('big_chances_total' in stats and stats['big_chances_total'] is not None) or ('big_chances_home' in stats and 'big_chances_away' in stats))
            if has_bc_metric:
                raw_bc = stats.get('big_chances_total')
                if raw_bc is None and 'big_chances_home' in stats:
                    raw_bc = (stats.get('big_chances_home') or 0) + (stats.get('big_chances_away') or 0)
                cand['big_chances'] = int(raw_bc) if raw_bc is not None else None
            else:
                cand['big_chances'] = None

            cand['corners'] = int(corners)
            cand['signal_type'] = cand.get('tier', 'SILVER')
            cand['stats_provider'] = stats.get('source', 'FLASHSCORE')
            cand['xg_is_estimated'] = bool(stats.get('xg_is_estimated', True))

            # Loguj wejście produkcyjne w ShadowLoggerze
            _log_eval(cand_type, badge_v, odds_v, "11_ACCEPTED", "ACCEPTED", None, stars=stars_awarded, ev_val=ev_pl, model_p=model_p, implied_p=implied_p, edge_val=edge_val, raw_s=raw_score, eff_s=effective_score, dang_att=cand['dangerous_attacks'])

            filtered_signals.append(cand)

        signals = filtered_signals

        # =========================================================================
        # GWARANCJA CZYSTOŚCI INTERFEJSU: 1 MECZ = 1 NAJLEPSZY SYGNAŁ (TOP VALUE)
        # =========================================================================
        if len(signals) > 1:
            signals.sort(key=lambda s: (s.get('stars', 0), s.get('priority_bonus', 0), s.get('raw_score', 0), s.get('ev_pl', 0.0), s.get('odds', 0.0)), reverse=True)
            signals = [signals[0]]

        return {
            'apm': apm,
            'danger_index': danger_index,
            'danger_index_10': danger_index_10,
            'danger_index_5': danger_index_5,
            'trend': trend,
            'trend_state': trend_state,
            'trend_ready': trend_ready,
            'directional_10m': directional_10m,
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

    def _calculate_danger_index(self, deltas: Dict[str, Any], suffix: str = '_10', red_cards: int = 0) -> Tuple[int, int]:
        """
        Zwraca krotkę: (final_danger_index, raw_danger_index) w skali 0-100.
        Brak DA jest karą jakości danych, nie powodem do podbijania indeksu.
        """
        d_sot = float(deltas.get(f'delta_sot{suffix}', 0.0))
        d_shots = float(deltas.get(f'delta_shots{suffix}', 0.0))
        d_da = float(deltas.get(f'delta_da{suffix}', 0.0))
        d_corners = float(deltas.get(f'delta_corners{suffix}', 0.0))
        d_xg = float(deltas.get(f'delta_xg{suffix}', 0.0))
        d_big = float(deltas.get(f'delta_big{suffix}', 0.0))
        has_da = bool(deltas.get('has_da_5' if suffix == '_5' else 'has_da', False))

        raw_score = 0.0
        d_off_target = max(0.0, d_shots - d_sot)

        raw_score += min(25.0, d_sot * 12.5)
        raw_score += min(20.0, d_xg * 50.0)
        raw_score += min(12.0, d_off_target * 4.0)
        raw_score += min(8.0, d_corners * 4.0)
        if d_big > 0:
            raw_score += min(6.0, d_big * 6.0)
        if red_cards > 0:
            raw_score += 4.0

        if has_da:
            raw_score += min(25.0, d_da * 1.65)
            final_score = raw_score
        else:
            # Feed bez DA ma mniej dowodów na presję terytorialną. Nie kompensujemy
            # brakującej cechy mnożnikiem; obniżamy confidence o 15%.
            final_score = raw_score * 0.85

        final_di = max(5, min(100, int(round(final_score))))
        raw_di = max(5, min(100, int(round(raw_score))))
        return final_di, raw_di

    def _calculate_expected_value(self, xg: float, danger_index: int, apm: float, sot: int, minute: int, rem_mins: int, odds: float, goals_needed: int = 1, tax_multiplier: float = POLISH_TAX_MULTIPLIER) -> Tuple[float, float, float, float, float]:
        """
        Estymuje matematyczną Wartość Oczekiwaną brutto (EV) oraz netto po polskim podatku 12% (EV_PL):
        EV = (P * odds) - 1.0
        EV_PL = (P * odds * 0.88) - 1.0
        oraz parametry kalibracji prawdopodobieństwa z wykorzystaniem uogólnionej
        dystrybuanty rozkładu Poissona dla k brakujących bramek:
        P(X >= k) = 1 - sum_{i=0}^{k-1} (lambda^i * e^(-lambda) / i!)
        Dla k=1: P(X >= 1) = 1 - e^(-lambda)
        Dla k=2: P(X >= 2) = 1 - e^(-lambda) * (1 + lambda)
        Zwraca: (ev_gross, model_probability, implied_probability, edge, ev_pl)
        """
        if rem_mins <= 0 or odds <= 1.0:
            return -1.0, 0.0, 1.0, -1.0, -1.0

        implied_prob = 1.0 / odds

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

        k = max(1, int(goals_needed))
        lam = max(0.001, lambda_total)
        exp_neg_lam = math.exp(-lam)

        # Uogólniony wzór Poissona: P(X >= k) = 1 - P(X < k)
        prob_less_than_k = sum((math.pow(lam, i) * exp_neg_lam) / math.factorial(i) for i in range(k))
        prob_goal = 1.0 - prob_less_than_k
        prob_goal = min(0.92, max(0.02, prob_goal))

        # EV brutto = (P * kurs) - 1.0
        ev = (prob_goal * odds) - 1.0
        # EV netto (PL podatek 12%) = (P * kurs * 0.88) - 1.0
        ev_pl = (prob_goal * odds * tax_multiplier) - 1.0
        edge = prob_goal - implied_prob

        return round(ev, 4), round(prob_goal, 4), round(implied_prob, 4), round(edge, 4), round(ev_pl, 4)

