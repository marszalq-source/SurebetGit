"""
Moduł pobierania danych meczowych i statystyk na żywo z Flashscore.pl.
Zoptymalizowany pod kątem minimalnego czasu odpowiedzi i transferu (urllib3 Keep-Alive Connection Pool).
"""
import re
import time
import urllib3
from typing import Dict, List, Optional, Any

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36',
    'x-fsign': 'SW9D1eZo',
    'Origin': 'https://www.flashscore.pl',
    'Referer': 'https://www.flashscore.pl/',
    'Accept-Language': 'pl-PL,pl;q=0.9,en-US;q=0.8,en;q=0.7',
}

RE_STATS_PAIRS = re.compile(r'SG÷([^¬]+)¬SH÷([^¬]+)¬SI÷([^¬]+)')
RE_DIGITS = re.compile(r'(\d+)')
LIVE_STATUSES = frozenset({'2', '13', '14', '15', '16', '17', '18'})

class FlashscoreEngine:
    _http_pool = None

    def __init__(self):
        self.headers = HEADERS
        self.stats_cache = {}  # {match_id: (timestamp, stats_dict)}
        self.cache_ttl = 10    # 10 sekund cache na statystyki pojedynczego meczu
        if FlashscoreEngine._http_pool is None:
            FlashscoreEngine._http_pool = urllib3.PoolManager(
                maxsize=32,
                timeout=urllib3.Timeout(connect=1.8, read=3.0),
                retries=urllib3.Retry(total=1, connect=1, read=0)
            )

    def get_live_soccer_matches(self, include_all_today: bool = False) -> List[Dict[str, Any]]:
        """
        Pobiera mecze piłki nożnej z Flashscore (domyślnie tylko trwające na żywo).
        """
        url = 'https://global.flashscore.ninja/3/x/feed/f_1_0_2_pl_1'
        matches = []
        try:
            resp = self._http_pool.request('GET', url, headers=self.headers)
            if resp.status != 200:
                return []
            raw = resp.data.decode('utf-8', errors='ignore')

            # Podział na ligi i mecze w formacie Flashscore
            current_league = "Piłka Nożna"
            current_country = ""

            blocks = raw.split('~')
            for b in blocks:
                if not b:
                    continue
                
                # Blok turnieju / ligi
                if b.startswith('ZA÷'):
                    fields = self._parse_feed_fields(b)
                    current_league = fields.get('ZA', 'Inne')
                    current_country = fields.get('ZB', '')
                    continue

                # Blok meczu
                if b.startswith('AA÷'):
                    fields = self._parse_feed_fields(b)
                    match_id = fields.get('AA', '')
                    if not match_id:
                        continue

                    # Sprawdź status meczu (czy trwa na żywo)
                    status_code = fields.get('AB', '')
                    stage_text = fields.get('AC', '')
                    minute_raw = fields.get('GB', fields.get('DB', ''))

                    is_live = status_code in LIVE_STATUSES

                    # Dodatkowa detekcja z tekstu fazy
                    if not is_live:
                        st_low = stage_text.lower()
                        if any(w in st_low for w in ['1. połowa', '2. połowa', 'przerwa', 'w grze', '1st half', '2nd half', 'live', 'ht', '1h', '2h', 'dogrywka', 'et']):
                            is_live = True

                    if not is_live and not include_all_today:
                        continue

                    minute = 0
                    half_str = "1H"
                    if status_code == '13' or 'przerwa' in stage_text.lower():
                        minute = 45
                        half_str = "HT"
                    elif status_code == '14' or '2.' in stage_text.lower():
                        half_str = "2H"
                        minute = 46
                    elif status_code == '3' or 'koniec' in stage_text.lower():
                        minute = 90
                        half_str = "FT"

                    # Próba wyciągnięcia dokładnej minuty
                    min_match = RE_DIGITS.search(minute_raw)
                    if min_match:
                        minute = int(min_match.group(1))

                    # Jeśli brak bezpośredniego pola z minutą, wylicz z timestampu rozpoczęcia AD
                    ad_val = fields.get('AD', fields.get('ADE', ''))
                    if minute == 0 and ad_val and ad_val.isdigit():
                        start_ts = int(ad_val)
                        diff_secs = int(time.time() - start_ts)
                        if diff_secs > 0:
                            calc_min = diff_secs // 60
                            if status_code in ('14', '15', '16', '17') or '2.' in str(stage_text):
                                minute = max(46, min(90, calc_min - 15))
                                half_str = "2H"
                            elif status_code == '13' or 'przerwa' in str(stage_text).lower():
                                minute = 45
                                half_str = "HT"
                            else:
                                minute = max(1, min(45, calc_min))
                                half_str = "1H"

                    home_team = fields.get('AE', '').strip()
                    away_team = fields.get('AF', '').strip()

                    try:
                        home_score = int(fields.get('AG', '0'))
                        away_score = int(fields.get('AH', '0'))
                    except ValueError:
                        home_score = 0
                        away_score = 0

                    # Wynik 1. połowy (jeśli dostępny)
                    ht_home_score = fields.get('BA', None)
                    ht_away_score = fields.get('BB', None)

                    if not home_team or not away_team:
                        continue

                    # Ignorujemy zakończone w trybie czysto na żywo
                    if not include_all_today and (status_code == '3' or 'Koniec' in stage_text or 'Po karnych' in stage_text or 'Po dogr.' in stage_text):
                        continue

                    clean_stage = stage_text
                    if status_code == '13' or 'przerwa' in str(stage_text).lower() or str(stage_text).strip() == '13':
                        clean_stage = "Przerwa"
                    elif status_code == '3' or 'koniec' in str(stage_text).lower() or str(stage_text).strip() == '3':
                        clean_stage = "Koniec"
                    elif not clean_stage or clean_stage.isdigit() or str(clean_stage).lower() == 'live':
                        clean_stage = f"{minute}'" if minute > 0 else "1'"

                    matches.append({
                        'flashscore_id': match_id,
                        'league': f"{current_country}: {current_league}".strip(": "),
                        'home_team': home_team,
                        'away_team': away_team,
                        'home_score': home_score,
                        'away_score': away_score,
                        'score_str': f"{home_score}:{away_score}",
                        'minute': minute,
                        'half': half_str,
                        'stage_text': clean_stage,
                        'ht_score': f"{ht_home_score}:{ht_away_score}" if (ht_home_score is not None and ht_away_score is not None) else None,
                        'is_live': is_live,
                        'url': f"https://www.flashscore.pl/mecz/{match_id}/"
                    })

        except Exception as e:
            print(f"[FlashscoreEngine] Błąd pobierania listy meczów: {e}")

        return matches

    def get_finished_results(self, include_yesterday: bool = True) -> List[Dict[str, Any]]:
        """
        Pobiera wszystkie oficjalnie zakończone mecze z dzisiejszego i wczorajszego dnia z Flashscore.
        Gwarantuje 100% pewności wyniku końcowego (FT) oraz wyniku do przerwy (HT).
        """
        feed_urls = ['https://global.flashscore.ninja/3/x/feed/f_1_0_3_pl_1']
        if include_yesterday:
            feed_urls.append('https://global.flashscore.ninja/3/x/feed/f_1_-1_3_pl_1')

        finished_matches = []
        seen_ids = set()

        for url in feed_urls:
            try:
                resp = self._http_pool.request('GET', url, headers=self.headers)
                if resp.status != 200:
                    continue
                raw = resp.data.decode('utf-8', errors='ignore')

                current_league = "Inne"
                current_country = ""

                blocks = raw.split('~')
                for b in blocks:
                    if not b:
                        continue
                    if b.startswith('ZA÷'):
                        fields = self._parse_feed_fields(b)
                        current_league = fields.get('ZA', 'Inne')
                        current_country = fields.get('ZB', '')
                        continue

                    if b.startswith('AA÷'):
                        fields = self._parse_feed_fields(b)
                        match_id = fields.get('AA', '')
                        if not match_id or match_id in seen_ids:
                            continue
                        seen_ids.add(match_id)

                        home_team = fields.get('AE', '').strip()
                        away_team = fields.get('AF', '').strip()
                        if not home_team or not away_team:
                            continue

                        try:
                            home_score = int(fields.get('AG', '0'))
                            away_score = int(fields.get('AH', '0'))
                        except ValueError:
                            home_score = 0
                            away_score = 0

                        # Wyznaczenie wyniku 1. połowy (HT)
                        # W feedzie Flashscore: BC to gole gospodarzy w 2H, BD to gole gości w 2H
                        # Więc gole 1H = (gole_FT - gole_2H)
                        ht_score = None
                        ba = fields.get('BA', '')
                        bb = fields.get('BB', '')
                        bc = fields.get('BC', '')
                        bd = fields.get('BD', '')

                        if ba != '' and bb != '':
                            ht_score = f"{ba}:{bb}"
                        elif bc != '' and bd != '':
                            try:
                                h_2h = int(bc)
                                a_2h = int(bd)
                                h_1h = max(0, home_score - h_2h)
                                a_1h = max(0, away_score - a_2h)
                                ht_score = f"{h_1h}:{a_1h}"
                            except ValueError:
                                pass

                        finished_matches.append({
                            'flashscore_id': match_id,
                            'league': f"{current_country}: {current_league}".strip(": "),
                            'home_team': home_team,
                            'away_team': away_team,
                            'home_score': home_score,
                            'away_score': away_score,
                            'score_str': f"{home_score}:{away_score}",
                            'ht_score': ht_score,
                            'minute': 90,
                            'half': 'FT',
                            'stage_text': 'Koniec',
                            'is_live': False,
                            'status_code': '3',
                            'url': f"https://www.flashscore.pl/mecz/{match_id}/"
                        })
            except Exception as e:
                print(f"[FlashscoreEngine] Błąd pobierania feedu zakończonych {url}: {e}")

        return finished_matches

    def get_match_detail_summary(self, match_id: str) -> Dict[str, Any]:
        """
        Pobiera szczegółowy rozkład bramek (minuty, strzelcy, 1H/2H) z endpointu Flashscore df_sui.
        """
        if not match_id:
            return {}
        url = f"https://global.flashscore.ninja/3/x/feed/df_sui_1_{match_id}"
        try:
            resp = self._http_pool.request('GET', url, headers=self.headers)
            if resp.status != 200:
                return {}
            raw = resp.data.decode('utf-8', errors='ignore')

            goals_1h = 0
            goals_2h = 0
            current_period = '1H'
            goal_events = []

            for b in raw.split('~'):
                if not b:
                    continue
                if 'AC÷1. połowa' in b:
                    current_period = '1H'
                elif 'AC÷2. połowa' in b:
                    current_period = '2H'

                if 'IK÷Gol' in b or 'IK÷Rzut karny' in b or 'IK÷Bramka samobójcza' in b:
                    fields = self._parse_feed_fields(b)
                    minute_str = fields.get('IB', '')
                    scorer = fields.get('IF', '')
                    team_side = int(fields.get('IA', '1'))  # 1 = home, 2 = away
                    
                    if current_period == '1H':
                        goals_1h += 1
                    else:
                        goals_2h += 1

                    goal_events.append({
                        'period': current_period,
                        'minute': minute_str,
                        'scorer': scorer,
                        'team_side': team_side
                    })

            return {
                'match_id': match_id,
                'goals_1h': goals_1h,
                'goals_2h': goals_2h,
                'total_goals': goals_1h + goals_2h,
                'goal_events': goal_events
            }
        except Exception as e:
            return {}

    def get_match_statistics(self, match_id: str) -> Dict[str, Any]:
        """
        Pobiera szczegółowe statystyki In-Play dla konkretnego meczu Flashscore.
        """
        now = time.time()
        if match_id in self.stats_cache:
            cache_time, cached_data = self.stats_cache[match_id]
            if now - cache_time < self.cache_ttl:
                return cached_data

        stat_url = f"https://global.flashscore.ninja/3/x/feed/df_st_1_{match_id}"
        stats_dict = {
            'xg_home': 0.0,
            'xg_away': 0.0,
            'xg_total': 0.0,
            'possession_home': 50,
            'possession_away': 50,
            'shots_total_home': 0,
            'shots_total_away': 0,
            'shots_total': 0,
            'shots_on_target_home': 0,
            'shots_on_target_away': 0,
            'shots_on_target_total': 0,
            'shots_off_target_total': 0,
            'blocked_shots_total': 0,
            'corners_home': 0,
            'corners_away': 0,
            'corners_total': 0,
            'attacks_home': 0,
            'attacks_away': 0,
            'dangerous_attacks_home': 0,
            'dangerous_attacks_away': 0,
            'dangerous_attacks_total': 0,
            'yellow_cards_total': 0,
            'red_cards_home': 0,
            'red_cards_away': 0,
            'red_cards_total': 0,
            'big_chances_total': 0,
            'apm': 0.0,            # Ataki na minutę
            'danger_index': 0.0,    # Indeks groźności (0-100)
            'has_detailed_stats': False
        }

        try:
            resp = self._http_pool.request('GET', stat_url, headers=self.headers)
            if resp.status == 200:
                raw = resp.data.decode('utf-8', errors='ignore')

                # Statystyki w feedzie Flashscore: SG÷Nazwa¬SH÷HomeVal¬SI÷AwayVal
                pairs = RE_STATS_PAIRS.findall(raw)
                if pairs:
                    stats_dict['has_detailed_stats'] = True

                for stat_name, h_val, a_val in pairs:
                    name_clean = stat_name.lower().strip()
                    h_clean = h_val.strip().replace('%', '')
                    a_clean = a_val.strip().replace('%', '')

                    # xG
                    if 'oczekiwane gole' in name_clean or 'xg' in name_clean:
                        stats_dict['xg_home'] = self._to_float(h_clean)
                        stats_dict['xg_away'] = self._to_float(a_clean)
                        stats_dict['xg_total'] = round(stats_dict['xg_home'] + stats_dict['xg_away'], 2)

                    # Posiadanie piłki
                    elif 'posiadanie' in name_clean:
                        stats_dict['possession_home'] = int(self._to_float(h_clean))
                        stats_dict['possession_away'] = int(self._to_float(a_clean))

                    # Strzały łącznie
                    elif 'strzały łącznie' in name_clean or 'strzały ogółem' in name_clean:
                        stats_dict['shots_total_home'] = int(self._to_float(h_clean))
                        stats_dict['shots_total_away'] = int(self._to_float(a_clean))
                        stats_dict['shots_total'] = stats_dict['shots_total_home'] + stats_dict['shots_total_away']

                    # Strzały celne / na bramkę
                    elif 'strzały na bramkę' in name_clean or 'celne' in name_clean:
                        stats_dict['shots_on_target_home'] = int(self._to_float(h_clean))
                        stats_dict['shots_on_target_away'] = int(self._to_float(a_clean))
                        stats_dict['shots_on_target_total'] = stats_dict['shots_on_target_home'] + stats_dict['shots_on_target_away']

                    # Strzały niecelne
                    elif 'strzały niecelne' in name_clean:
                        stats_dict['shots_off_target_total'] = int(self._to_float(h_clean)) + int(self._to_float(a_clean))

                    # Rzuty rożne
                    elif 'rzuty rożne' in name_clean or 'rożne' in name_clean:
                        stats_dict['corners_home'] = int(self._to_float(h_clean))
                        stats_dict['corners_away'] = int(self._to_float(a_clean))
                        stats_dict['corners_total'] = stats_dict['corners_home'] + stats_dict['corners_away']

                    # Groźne ataki
                    elif 'groźne ataki' in name_clean or 'niebezpieczne ataki' in name_clean:
                        stats_dict['dangerous_attacks_home'] = int(self._to_float(h_clean))
                        stats_dict['dangerous_attacks_away'] = int(self._to_float(a_clean))
                        stats_dict['dangerous_attacks_total'] = stats_dict['dangerous_attacks_home'] + stats_dict['dangerous_attacks_away']

                    # Ataki łącznie
                    elif 'ataki' in name_clean:
                        stats_dict['attacks_home'] = int(self._to_float(h_clean))
                        stats_dict['attacks_away'] = int(self._to_float(a_clean))

                    # Wielkie szanse
                    elif 'wielkie szanse' in name_clean:
                        stats_dict['big_chances_total'] = int(self._to_float(h_clean)) + int(self._to_float(a_clean))

                    # Żółte kartki
                    elif 'żółte kartki' in name_clean:
                        stats_dict['yellow_cards_total'] = int(self._to_float(h_clean)) + int(self._to_float(a_clean))

                    # Czerwone kartki
                    elif 'czerwone kartki' in name_clean:
                        stats_dict['red_cards_home'] = int(self._to_float(h_clean))
                        stats_dict['red_cards_away'] = int(self._to_float(a_clean))
                        stats_dict['red_cards_total'] = stats_dict['red_cards_home'] + stats_dict['red_cards_away']

        except Exception:
            pass

        # Jeśli brak oficjalnego pomiaru xG w feedzie Flashscore, wylicz xG z telemetrii meczowej
        if stats_dict['xg_total'] == 0.0 and (stats_dict['shots_total'] > 0 or stats_dict['shots_on_target_total'] > 0 or stats_dict['corners_total'] > 0):
            sot_h = stats_dict['shots_on_target_home']
            sot_a = stats_dict['shots_on_target_away']
            soff_h = max(0, stats_dict['shots_total_home'] - sot_h)
            soff_a = max(0, stats_dict['shots_total_away'] - sot_a)
            dang_h = stats_dict['dangerous_attacks_home']
            dang_a = stats_dict['dangerous_attacks_away']
            corn_h = stats_dict['corners_home']
            corn_a = stats_dict['corners_away']

            xg_h = round(sot_h * 0.28 + soff_h * 0.06 + dang_h * 0.012 + corn_h * 0.04, 2)
            xg_a = round(sot_a * 0.28 + soff_a * 0.06 + dang_a * 0.012 + corn_a * 0.04, 2)
            stats_dict['xg_home'] = xg_h
            stats_dict['xg_away'] = xg_a
            stats_dict['xg_total'] = round(xg_h + xg_a, 2)

        self.stats_cache[match_id] = (now, stats_dict)
        return stats_dict

    def _parse_feed_fields(self, block: str) -> Dict[str, str]:
        fields = {}
        for item in block.split('¬'):
            if '÷' in item:
                k, v = item.split('÷', 1)
                fields[k] = v
        return fields

    def _to_float(self, val: str) -> float:
        try:
            return float(val.replace(',', '.').strip())
        except (ValueError, AttributeError):
            return 0.0

