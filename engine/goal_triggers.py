"""
Silnik reguł i triggerów bramkowych (Over 0.5 / 1.5 HT, Over 1.5 / 2.5 FT, Post-Goal, Late Goal 2H).
Analizuje w czasie rzeczywistym statystyki z Flashscore oraz kursy z STS.
"""
import math
import re
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
    'argentyna: primera b', 'argentyna: torneo federal', 'argentyna: primera c',
    'south africa: premier league', 'rpa: premier league', 'south africa: national first',
    'tunisia: ligue 1', 'tunezja',
    'venezuela: primera division', 'wenezuela',
    'romania: liga 2', 'rumunia: liga 2',
    'rezerwy', 'reserve league', 'torneo juvenil', 'u19 league', 'u20 league', 'młodzieżowa'
]


class GoalTriggersEngine:
    def __init__(self, config=None):
        self.config = config or TRIGGERS_CONFIG

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

        # 1. Dynamiczne obliczenie APM (Attacks per minute)
        apm = self._calculate_dynamic_apm(minute, dangerous_attacks, shots_total, sot, corners)

        # 2. Obliczenie Indeksu Groźności (Danger/Pressure Index: 0 - 100)
        danger_index = self._calculate_danger_index(
            minute=minute,
            shots=shots_total,
            sot=sot,
            apm=apm,
            xg=xg_total,
            corners=corners,
            red_cards=red_cards,
            big_chances=big_chances
        )

        d_rat = "EKSTREMALNY" if danger_index >= 75 else ("WYSOKI" if danger_index >= 55 else ("ŚREDNI" if danger_index >= 35 else "NISKI"))

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
            if odds < 1.15:
                continue

            if 'OVER' in combined_txt and ('FT' in combined_txt or 'MECZ' in combined_txt or 'LICZBA GOLI' in combined_txt):
                m_match = re.search(r'OVER\s+(\d+(?:\.\d+)?)', combined_txt)
                if m_match:
                    l_val = float(m_match.group(1))
                    if l_val > total_goals:
                        available_over_ft.append((l_val, m))
            elif 'OVER' in combined_txt and ('HT' in combined_txt or '1. POŁ' in combined_txt or '1.POŁ' in combined_txt or '1H' in combined_txt):
                m_match = re.search(r'OVER\s+(\d+(?:\.\d+)?)', combined_txt)
                if m_match:
                    l_val = float(m_match.group(1))
                    if l_val > total_goals:
                        available_over_ht.append((l_val, m))

        available_over_ft.sort(key=lambda x: (x[0], x[1].get('odds', 99)))
        available_over_ht.sort(key=lambda x: (x[0], x[1].get('odds', 99)))

        lowest_over_ft = available_over_ft[0][1] if available_over_ft else None
        lowest_over_ft_line = available_over_ft[0][0] if available_over_ft else None

        lowest_over_ht = available_over_ht[0][1] if available_over_ht else None
        lowest_over_ht_line = available_over_ht[0][0] if available_over_ht else None

        # Filtrowanie lig anty-bramkowych
        league_str = str(match_data.get('league', '')).lower()
        is_anti_goal_league = any(kw in league_str for kw in ANTI_GOAL_LEAGUES_KEYWORDS)

        # Filtrowanie anomalii: jałowe posiadanie (dużo ataków, ale 0 strzałów celnych w minucie 25+)
        is_sterile_possession = (minute >= 25 and sot == 0 and shots_total <= 2)

        # Filtrowanie anomalii: mecz rozstrzygnięty (blowout w 2. połowie)
        is_blowout_game = (half in ('2H', 'FT') and score_diff >= 3 and minute >= 60)

        signals = []

        # =========================================================================
        # --- A. STRATEGIA 1: OVER 0.5 HT (ZŁOTE OKNO 14'-34' MIN, WYNIK 0:0) ---
        # =========================================================================
        cfg_05_ht = self.config.get('OVER_05_HT', {})
        min_m_05ht = cfg_05_ht.get('min_minute', 14)
        max_m_05ht = cfg_05_ht.get('max_minute', 34)

        if (lowest_over_ht and lowest_over_ht_line == 0.5 and total_goals == 0 and half == '1H' and
                min_m_05ht <= minute <= max_m_05ht and not is_anti_goal_league and not is_sterile_possession):

            min_sot = max(2, cfg_05_ht.get('min_sot', 2))
            if sot >= min_sot and shots_total >= 5 and (apm >= 0.82 or xg_total >= 0.45 or danger_index >= 60):
                odds_val = float(lowest_over_ht['odds'])
                reasons = []
                confidence = 1

                # Weryfikacja statystyczna
                if apm >= 0.88:
                    confidence += 1
                    reasons.append(f"Wysokie tempo APM ({apm:.2f} ataku/min)")
                if sot >= 4:
                    confidence += 2
                    reasons.append(f"{sot} celne strzały w światło")
                elif sot >= 3:
                    confidence += 1
                    reasons.append(f"{sot} celne strzały")
                if shots_total >= 7:
                    confidence += 1
                    reasons.append(f"{shots_total} oddanych strzałów")
                if xg_total >= 0.60:
                    confidence += 1
                    reasons.append(f"Wysokie xG: {xg_total:.2f}")
                if danger_index >= 70:
                    confidence += 1
                    reasons.append(f"Danger Index: {danger_index}%")

                rem_mins_1h = max(1, 45 - minute)
                ev = self._calculate_expected_value(xg_total, danger_index, apm, sot, minute, rem_mins_1h, odds_val)

                # Sweet spot dla Over 0.5 HT: 1.45 - 2.15
                odds_ok = (1.40 <= odds_val <= 2.15 and confidence >= 2) or (2.15 < odds_val <= 2.50 and confidence >= 4 and sot >= 3)
                if odds_ok and ev >= -0.04:
                    stars_count = min(5, max(2, confidence))
                    signals.append({
                        'type': 'OVER_05_HT',
                        'title': f"🎯 ALARM: OVER 0.5 HT",
                        'badge': f"OVER 0.5 HT",
                        'color': '#00E676',
                        'odds': odds_val,
                        'stars': stars_count,
                        'ev': round(ev, 3),
                        'desc': f"Złote Okno 1H ({minute}'). Wysokie prawdopodobieństwo gola do przerwy. " + ", ".join(reasons)
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
        if (total_goals >= 1 and (minute >= 20 or half in ('HT', '2H')) and minute <= 78 and
                lowest_over_ft and not is_blowout_game and not is_sterile_possession):

            # Wymóg: minimum 3 strzały celne, 6 strzałów łącznych i tempo gry
            if sot >= 3 and shots_total >= 6 and (apm >= 0.80 or xg_total >= 0.60 or danger_index >= 60):
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
                    if 1.35 <= o_val <= 2.35:
                        target_over_ft = m_obj
                        target_line = l_val
                        break

                # Jeśli pierwsza linia miała kurs < 1.35, weź kolejną z kursem >= 1.35
                if not target_over_ft and valid_lines:
                    for l_val, m_obj in valid_lines:
                        o_val = float(m_obj.get('odds', 0))
                        if o_val >= 1.35:
                            target_over_ft = m_obj
                            target_line = l_val
                            break

                # Fallback jeśli brak rynków z podstrony: domyślna linia +1 gol
                if not target_over_ft:
                    immediate_next_line = total_goals + 1.5 if total_goals >= 2 else (total_goals + 0.5)
                    for l_val, m_obj in available_over_ft:
                        if abs(l_val - immediate_next_line) < 0.1:
                            target_over_ft = m_obj
                            target_line = l_val
                            break

                if target_over_ft and target_line:
                    odds_val = float(target_over_ft['odds'])
                    rem_mins_ft = max(1, 90 - minute)
                    ev = self._calculate_expected_value(xg_total, danger_index, apm, sot, minute, rem_mins_ft, odds_val)

                    # Sweet spot: 1.35 - 2.25
                    if confidence >= 2 and 1.35 <= odds_val <= 2.25 and ev >= -0.04:
                        time_desc = "Przerwa (HT)" if half == 'HT' else f"{minute}' min"
                        stars_count = min(5, max(2, confidence))
                        signals.append({
                            'type': 'POST_GOAL_FT',
                            'title': f"⚡ NATYCHMIASTOWY ALARM: OVER {target_line} FT",
                            'badge': f"OVER {target_line} FT",
                            'color': '#00B0FF',
                            'odds': odds_val,
                            'stars': stars_count,
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
                total_goals <= cfg_15_ft.get('max_score_sum', 1) and not is_sterile_possession):

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

                # Sweet spot kursowy: 1.55 - 2.35
                if confidence >= 2 and 1.50 <= odds_val <= 2.40 and ev >= -0.05:
                    stars_count = min(5, max(2, confidence))
                    signals.append({
                        'type': 'OVER_15_FT',
                        'title': f"🎯 ALARM: OVER {lowest_over_ft_line} FT",
                        'badge': f"OVER {lowest_over_ft_line} FT",
                        'color': '#00B0FF',
                        'odds': odds_val,
                        'stars': stars_count,
                        'ev': round(ev, 3),
                        'desc': f"Świetny potencjał 2H ({minute}'). Wysoka szansa na kolejne bramki. " + ", ".join(reasons)
                    })

        # =========================================================================
        # --- E. STRATEGIA 5: LATE GOAL W KOŃCÓWCE (OVER 0.5 2H / 63'-85' MIN) ---
        # =========================================================================
        cfg_05_2h = self.config.get('OVER_05_2H', {})
        min_m_2h = cfg_05_2h.get('min_minute', 63)
        max_m_2h = cfg_05_2h.get('max_minute', 85)

        if (lowest_over_ft and half == '2H' and min_m_2h <= minute <= max_m_2h and
                score_diff <= cfg_05_2h.get('allowed_score_diff', 2) and not is_blowout_game and not is_sterile_possession):

            # Zabezpieczenie przed wysokimi liniami Over 4.5+ w samej końcówce
            is_high_line_late = (lowest_over_ft_line is not None and lowest_over_ft_line >= 4.5 and minute >= 75)

            if not is_high_line_late and sot >= cfg_05_2h.get('min_sot', 2):
                reasons = []
                confidence = 1

                if apm >= cfg_05_2h.get('min_apm', 0.85):
                    confidence += 1
                    reasons.append(f"Wysokie APM końcówki: {apm:.2f}")
                if sot >= 3:
                    confidence += 1
                    reasons.append(f"{sot} celnych strzałów")
                elif sot >= 2:
                    reasons.append(f"{sot} celne strzały")
                if shots_total >= cfg_05_2h.get('min_shots_total', 6):
                    confidence += 1
                    reasons.append(f"{shots_total} strzałów")
                if xg_total >= 0.80:
                    confidence += 1
                    reasons.append(f"xG: {xg_total:.2f}")
                if danger_index >= 60:
                    confidence += 1
                    reasons.append(f"Napór końcówki: {danger_index}%")

                odds_val = float(lowest_over_ft['odds'])
                rem_mins_ft = max(1, 90 - minute)
                ev = self._calculate_expected_value(xg_total, danger_index, apm, sot, minute, rem_mins_ft, odds_val)

                # Sweet spot kursowy: 1.50 - 2.45
                if confidence >= 2 and 1.45 <= odds_val <= 2.45 and ev >= -0.05:
                    stars_count = min(5, max(2, confidence))
                    signals.append({
                        'type': 'OVER_05_2H',
                        'title': f"🔥 ALARM: OVER {lowest_over_ft_line} FT",
                        'badge': f"OVER {lowest_over_ft_line} FT",
                        'color': '#FF3D00',
                        'odds': odds_val,
                        'stars': stars_count,
                        'ev': round(ev, 3),
                        'desc': f"Świetne okno na kolejną bramkę ({minute}'). " + ", ".join(reasons)
                    })

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

    def _calculate_dynamic_apm(self, minute: int, dangerous_attacks: int, shots: int, sot: int, corners: int) -> float:
        """
        Oblicza dynamiczny wskaźnik APM (Attacks Per Minute) znormalizowany i zabezpieczony przed skrajnościami.
        """
        if minute <= 3:
            return 0.0

        minute_f = float(max(1, minute))

        if dangerous_attacks > 0:
            raw_dang_rate = dangerous_attacks / minute_f
            shot_corn_rate = (sot * 3.5 + max(0, shots - sot) * 1.5 + corners * 1.0) / minute_f
            # Zbalansowane APM: 60% z ataków niebezpiecznych + 40% z wygenerowanych okazji
            apm = (raw_dang_rate * 0.60) + (shot_corn_rate * 0.40)
        else:
            # Wskaźnik zastępczy oparty o strzały, strzały celne i rzuty rożne
            apm = (sot * 4.0 + max(0, shots - sot) * 1.8 + corners * 1.4) / minute_f

        return round(min(3.0, max(0.1, apm)), 2)

    def _calculate_danger_index(self, minute: int, shots: int, sot: int, apm: float, xg: float, corners: int, red_cards: int, big_chances: int = 0) -> int:
        """
        Zwraca Indeks Groźności (0-100) oparty na tempie akcji na 10 minut meczu.
        Eliminuje problem sztucznego zawyżania punktów w końcówkach spotkań.
        """
        if minute <= 0:
            return 20

        # Liczba 10-minutowych bloków czasu (min. 0.5 bloku)
        time_blocks = max(0.5, minute / 10.0)

        score = 0.0

        # 1. Wkład APM (max 30 pkt)
        score += min(30.0, apm * 25.0)

        # 2. Wkład Strzałów Celnych (SoT pace na 10 min - max 25 pkt)
        # Średnia liga to ~0.45 SoT/10m; 1.0+ SoT/10m to nawałnica
        sot_rate_10m = sot / time_blocks
        score += min(25.0, sot_rate_10m * 22.0)

        # 3. Wkład xG (xG pace na 10 min - max 20 pkt)
        # Średnia liga to ~0.15 xG/10m; 0.30+ xG/10m to wysoki napór
        xg_rate_10m = xg / time_blocks
        score += min(20.0, xg_rate_10m * 50.0)

        # 4. Wkład Łącznych Strzałów (Shots pace na 10 min - max 12 pkt)
        shots_rate_10m = shots / time_blocks
        score += min(12.0, shots_rate_10m * 4.5)

        # 5. Wkład Rzutów Rożnych (Corners pace na 10 min - max 8 pkt)
        corners_rate_10m = corners / time_blocks
        score += min(8.0, corners_rate_10m * 3.5)

        # 6. Duże szanse (Big chances - max 8 pkt)
        if big_chances > 0:
            score += min(8.0, (big_chances / time_blocks) * 12.0)

        # 7. Czerwona kartka (otwarta przestrzeń - bonus 5 pkt)
        if red_cards > 0:
            score += 5.0

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

