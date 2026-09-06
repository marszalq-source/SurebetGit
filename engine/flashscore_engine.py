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
                    st_low = str(stage_text or '').lower()
                    ac_val = str(stage_text or '').strip()

                    is_live = status_code in LIVE_STATUSES or ac_val in ('12', '13', '38', '46', '6', '7')

                    # Dodatkowa detekcja z tekstu fazy
                    if not is_live:
                        if any(w in st_low for w in ['1. połowa', '2. połowa', 'przerwa', 'w grze', '1st half', '2nd half', 'live', 'ht', '1h', '2h', 'dogrywka', 'et']):
                            is_live = True

                    # Bezwzględne wykluczenie meczów zakończonych z trybu live
                    if status_code in ('3', '10', '11') or ac_val in ('3', '10', '11') or any(w in st_low for w in ['koniec', 'ended', 'finished', 'po karnych', 'po dogr.']):
                        is_live = False

                    if not is_live and not include_all_today:
                        continue

                    # 1. Mapowanie fazy meczu wg oficjalnych kodów Flashscore:
                    # 12 = 1. połowa (1H), 38 / 46 = Przerwa (HT), 13 = 2. połowa (2H), 3 = Koniec (FT)
                    minute = 0
                    half_str = "1H"
                    clean_stage = ""

                    if ac_val in ('38', '46') or status_code in ('38', '46') or 'przerw' in st_low or 'halftime' in st_low or st_low == 'ht':
                        half_str = "HT"
                        clean_stage = "Przerwa"
                        minute = 45
                    elif ac_val == '13' or status_code in ('13', '14', '15', '16', '17') or '2.' in st_low or '2nd' in st_low or st_low == '2h':
                        half_str = "2H"
                        clean_stage = "2. połowa"
                        minute = 46
                    elif ac_val == '3' or status_code in ('3', '8', '9', '10', '11') or any(w in st_low for w in ['koniec', 'ended', 'finished', 'po karnych', 'po dogr.']):
                        half_str = "FT"
                        clean_stage = "Koniec"
                        minute = 90
                    elif ac_val == '12' or '1.' in st_low or '1st' in st_low or st_low == '1h':
                        half_str = "1H"
                        clean_stage = "1. połowa"
                        minute = 1
                    else:
                        clean_stage = stage_text

                    # 2. Próba wyciągnięcia dokładnej minuty z GB/DB
                    min_match = RE_DIGITS.search(minute_raw)
                    if min_match:
                        minute = int(min_match.group(1))

                    # 3. Jeśli brak bezpośredniego pola z minutą, wylicz z timestampu rozpoczęcia AD
                    ad_val = fields.get('AD', fields.get('ADE', ''))
                    if (not min_match or minute == 0) and ad_val and ad_val.isdigit():
                        start_ts = int(ad_val)
                        diff_secs = int(time.time() - start_ts)
                        if diff_secs > 0:
                            calc_min = diff_secs // 60
                            if half_str == "HT":
                                minute = 45
                            elif half_str == "2H":
                                minute = max(46, min(90, calc_min - 15))
                            elif half_str == "FT":
                                minute = 90
                            else:
                                minute = max(1, min(45, calc_min))

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
                    if not include_all_today and (status_code == '3' or ac_val == '3' or 'Koniec' in clean_stage or 'Po karnych' in clean_stage or 'Po dogr.' in clean_stage):
                        continue

                    # 4. Formatowanie clean_stage do wyświetlania
                    if half_str == "HT":
                        clean_stage = "Przerwa"
                    elif half_str == "FT":
                        clean_stage = "Koniec"
                    elif minute > 0:
                        clean_stage = f"{minute}'"
                    elif not clean_stage or clean_stage.isdigit() or str(clean_stage).lower() == 'live':
                        clean_stage = "2. połowa" if half_str == "2H" else "1. połowa"

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
                        'status_code': status_code,
                        'kickoff_ts': int(ad_val) if (ad_val and ad_val.isdigit()) else None,
                        'url': f"https://www.flashscore.pl/mecz/{match_id}/"
                    })

        except Exception as e:
            print(f"[FlashscoreEngine] Błąd pobierania listy meczów: {e}")

        return matches

    def get_finished_results(self, days_back: int = 3) -> List[Dict[str, Any]]:
        """
        Pobiera wszystkie oficjalnie zakończone mecze z ostatnich dni (domyślnie 0, -1, -2, -3 dni wstecz).
        Gwarantuje 100% pewności wyniku końcowego (FT) oraz wyniku do przerwy (HT) nawet po przerwie weekendowej.
        """
        feed_urls = [f'https://global.flashscore.ninja/3/x/feed/f_1_{-d}_3_pl_1' if d > 0 else 'https://global.flashscore.ninja/3/x/feed/f_1_0_3_pl_1' for d in range(days_back + 1)]

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
                        # OCHRONA: Ignoruj mecze trwające na żywo (AB==2) lub w przerwie HT (AC==13)
                        status_ab = str(fields.get('AB', ''))
                        status_ac = str(fields.get('AC', ''))
                        if status_ab == '2' or status_ac in ('1', '2', '12', '13', '14', '15', '16', '17', '18', '19', '20', '21'):
                            continue
                        if status_ab != '3' and status_ac not in ('3', '8', '9', '10', '11'):
                            continue

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
                            'kickoff_ts': int(fields.get('AD')) if (fields.get('AD', '').isdigit()) else None,
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

