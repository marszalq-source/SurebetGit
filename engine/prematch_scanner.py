"""
Skaner Przedmeczowy (Prematch Scanner) dla piłki nożnej.
Wyszukuje nadchodzące mecze pod kątem bramek (Niemcy, Holandia, Puchary Europejskie),
analizuje statystyki dom/wyjazd i zarządza Watchlistą (obserwowanymi meczami).
"""
import urllib.request
import re
import time
import json
import os
from typing import Dict, List, Any, Optional
from concurrent.futures import ThreadPoolExecutor
from .prematch_analyzer import PrematchAnalyzer

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36',
    'x-fsign': 'SW9D1eZo',
    'Origin': 'https://www.flashscore.pl',
    'Referer': 'https://www.flashscore.pl/',
}

WATCHLIST_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "watchlist.json")

COUNTRY_FLAGS = {
    'POLSKA': '🇵🇱', 'POLAND': '🇵🇱',
    'ANGLIA': '🏴󠁧󠁢󠁥󠁮󠁧󠁿', 'ENGLAND': '🏴󠁧󠁢󠁥󠁮󠁧󠁿',
    'NIEMCY': '🇩🇪', 'GERMANY': '🇩🇪',
    'HISZPANIA': '🇪🇸', 'SPAIN': '🇪🇸',
    'WŁOCHY': '🇮🇹', 'ITALY': '🇮🇹',
    'FRANCJA': '🇫🇷', 'FRANCE': '🇫🇷',
    'HOLANDIA': '🇳🇱', 'NETHERLANDS': '🇳🇱',
    'PORTUGALIA': '🇵🇹', 'PORTUGAL': '🇵🇹',
    'BELGIA': '🇧🇪', 'BELGIUM': '🇧🇪',
    'TURCJA': '🇹🇷', 'TURKEY': '🇹🇷',
    'NORWEGIA': '🇳🇴', 'NORWAY': '🇳🇴',
    'SZWECJA': '🇸🇪', 'SWEDEN': '🇸🇪',
    'DANIA': '🇩🇰', 'DENMARK': '🇩🇰',
    'ISLANDIA': '🇮🇸', 'ICELAND': '🇮🇸',
    'FINLANDIA': '🇫🇮', 'FINLAND': '🇫🇮',
    'AUSTRALIA': '🇦🇺',
    'INDIE': '🇮🇳', 'INDIA': '🇮🇳',
    'JAPONIA': '🇯🇵', 'JAPAN': '🇯🇵',
    'ARABIA SAUDYJSKA': '🇸🇦', 'SAUDI ARABIA': '🇸🇦',
    'BRAZYLIA': '🇧🇷', 'BRAZIL': '🇧🇷',
    'ARGENTYNA': '🇦🇷', 'ARGENTINA': '🇦🇷',
    'CZECHY': '🇨🇿', 'CZECH REPUBLIC': '🇨🇿',
    'WĘGRY': '🇭🇺', 'HUNGARY': '🇭🇺',
    'CHORWACJA': '🇭🇷', 'CROATIA': '🇭🇷',
    'SZWAJCARIA': '🇨🇭', 'SWITZERLAND': '🇨🇭',
    'AUSTRIA': '🇦🇹',
    'EUROPA': '🇪🇺', 'EUROPE': '🇪🇺',
    'ŚWIAT': '🌐', 'WORLD': '🌐',
    'MIĘDZYNARODOWE': '🌐',
    'AMERYKA POŁUDNIOWA': '🌎',
    'AMERYKA PÓŁNOCNA I CENTRALNA': '🌎',
    'AZJA': '🌏', 'AFRYKA': '🌍',
    'UKRAINA': '🇺🇦', 'RUMUNIA': '🇷🇴', 'GRECJA': '🇬🇷', 'SZKOCJA': '🏴󠁧󠁢󠁳󠁣󠁴󠁿', 'SERBIA': '🇷🇸', 'SŁOWACJA': '🇸🇰', 'IRLANDIA': '🇮🇪', 'USA': '🇺🇸', 'MEKSYK': '🇲🇽'
}

class PrematchScanner:
    def __init__(self):
        self.analyzer = PrematchAnalyzer()
        self.headers = HEADERS
        self.watchlist = self._load_watchlist()

    def scan_upcoming(self, country_filter: str = "ALL", day_offset: int = 0, time_filter: str = "ALL", min_rating: int = 40) -> Dict[str, Any]:
        """
        Skanuje WSZYSTKIE nadchodzące mecze ze wszystkich krajów i lig.
        country_filter: 'ALL', 'GERMANY', 'NETHERLANDS', 'AUSTRALIA_ASIA', 'SCANDINAVIA', 'CHAMPIONS_LEAGUE', 'CONFERENCE_LEAGUE', 'EUROPA_LEAGUE', 'EUROPE', 'ULTRA_GOALS' lub konkretny kraj.
        day_offset: 0 = dzisiaj, 1 = jutro
        time_filter: 'ALL', '1H' (za 0-60 min), '2H' (za 0-120 min), '3H' (za 0-180 min)
        """
        start_time = time.time()
        feed_url = f"https://global.flashscore.ninja/3/x/feed/f_1_{day_offset}_2_pl_1"
        
        raw_matches = self._fetch_feed_fixtures(feed_url, day_offset=day_offset)
        
        # Filtrowanie według czasu i kraju
        filtered_fixtures = []
        for m in raw_matches:
            c_tag = m['country'].lower()
            l_tag = m['league'].lower()
            mins = m.get('mins_until', 9999)

            # Filtry czasowe: 1h, 2h, 3h
            if time_filter == "1H":
                if mins < -10 or mins > 65:
                    continue
            elif time_filter == "2H":
                if mins < -10 or mins > 125:
                    continue
            elif time_filter == "3H":
                if mins < -10 or mins > 185:
                    continue

            # Filtry ligowe / krajowe
            if country_filter == "GERMANY":
                if not any(k in c_tag or k in l_tag for k in ['niemcy', 'germany', 'bundesliga', 'dfb', 'regionalliga', 'oberliga']):
                    continue
            elif country_filter == "NETHERLANDS":
                if not any(k in c_tag or k in l_tag for k in ['holandia', 'netherlands', 'eredivisie', 'eerste', 'knvb']):
                    continue
            elif country_filter == "AUSTRALIA_ASIA":
                if not any(k in c_tag or k in l_tag for k in ['australia', 'a-league', 'indie', 'india', 'isl', 'i-league', 'durand', 'singapur', 'singapore', 'hongkong', 'zelandia', 'japon']):
                    continue
            elif country_filter == "SCANDINAVIA":
                if not any(k in c_tag or k in l_tag for k in ['norwegia', 'islandia', 'szwecja', 'finlandia', 'estonia', 'dania', 'eliteserien', 'obos', 'besta']):
                    continue
            elif country_filter == "CHAMPIONS_LEAGUE":
                if not any(k in l_tag or k in c_tag for k in ['champions', 'mistrz', 'ucl']):
                    continue
            elif country_filter == "CONFERENCE_LEAGUE":
                if not any(k in l_tag or k in c_tag for k in ['konferenc', 'conference', 'uecl']):
                    continue
            elif country_filter == "EUROPA_LEAGUE":
                if not any(k in l_tag or k in c_tag for k in ['europa league', 'liga europy', 'uel']):
                    continue
            elif country_filter == "EUROPE":
                if not any(k in l_tag or k in c_tag for k in ['champions', 'mistrz', 'europa', 'konferenc', 'conference', 'uefa']):
                    continue
            elif country_filter == "ULTRA_GOALS":
                ultra_keys = ['australia', 'a-league', 'singapur', 'singapore', 'islandia', 'besta', 'holandia', 'eredivisie', 'eerste', 'bundesliga', 'norwegia', 'eliteserien', 'obos', 'austria', 'szwajcaria', 'zelandia', 'npl']
                if not any(k in c_tag or k in l_tag for k in ultra_keys):
                    continue
            elif country_filter != "ALL":
                # Filtrowanie po dokładnej nazwie kraju
                if country_filter.lower() not in c_tag:
                    continue

            filtered_fixtures.append(m)

        # Równoległa analiza taktyczna dla wszystkich wyfiltrowanych meczów
        analyzed_matches = []
        with ThreadPoolExecutor(max_workers=25) as executor:
            future_to_match = {
                executor.submit(
                    self.analyzer.analyze_fixture,
                    m['id'], m['league'], m['home_team'], m['away_team']
                ): m for m in filtered_fixtures
            }

            for future in future_to_match:
                m_data = future_to_match[future]
                try:
                    analysis = future.result(timeout=4)
                except Exception:
                    analysis = self.analyzer.analyze_fixture(m_data['id'], m_data['league'], m_data['home_team'], m_data['away_team'])

                if analysis['prematch_goal_rating'] >= min_rating:
                    is_watched = m_data['id'] in self.watchlist
                    analyzed_matches.append({
                        **m_data,
                        'analysis': analysis,
                        'is_watched': is_watched
                    })

        # Sortowanie meczów: wg godziny rozpoczęcia i potencjału bramkowego
        analyzed_matches.sort(key=lambda x: (x.get('mins_until', 9999), -x['analysis']['prematch_goal_rating']))

        # Budowanie struktury akordeonu STS (Kraje & Ligi)
        country_groups_map = {}
        for m in analyzed_matches:
            c_name = m['country'].upper()
            flag = COUNTRY_FLAGS.get(c_name, '⚽')
            
            if c_name not in country_groups_map:
                country_groups_map[c_name] = {
                    'country': c_name,
                    'flag': flag,
                    'count': 0,
                    'top_rating': 0,
                    'matches': []
                }
            
            country_groups_map[c_name]['count'] += 1
            country_groups_map[c_name]['matches'].append(m)
            country_groups_map[c_name]['top_rating'] = max(country_groups_map[c_name]['top_rating'], m['analysis']['prematch_goal_rating'])

        # Sortowanie grup krajów: kraje priorytetowe (Puchary, Niemcy, Anglia, Holandia, Norwegia, Australia) na górze, reszta alfabetycznie
        priority_countries = ['EUROPA', 'MIĘDZYNARODOWE', 'ANGLIA', 'NIEMCY', 'HISZPANIA', 'WŁOCHY', 'FRANCJA', 'HOLANDIA', 'NORWEGIA', 'AUSTRALIA', 'POLSKA', 'BELGIA', 'PORTUGALIA', 'TURCJA', 'ISLANDIA', 'SZWECJA', 'INDIE', 'BRAZYLIA']
        
        country_groups = list(country_groups_map.values())
        country_groups.sort(key=lambda g: (
            priority_countries.index(g['country']) if g['country'] in priority_countries else 999,
            -g['top_rating'],
            g['country']
        ))

        return {
            'timestamp': time.strftime('%H:%M:%S'),
            'total_found': len(raw_matches),
            'filtered_count': len(analyzed_matches),
            'country_groups_count': len(country_groups),
            'country_groups': country_groups,
            'duration_sec': round(time.time() - start_time, 2),
            'matches': analyzed_matches
        }

    def toggle_watchlist(self, match_id: str, match_info: Optional[Dict[str, Any]] = None) -> bool:
        """Dodaje lub usuwa mecz z listy obserwowanych (Watchlista)."""
        if match_id in self.watchlist:
            del self.watchlist[match_id]
            self._save_watchlist()
            return False
        else:
            if match_info:
                self.watchlist[match_id] = match_info
            else:
                self.watchlist[match_id] = {'id': match_id, 'added_at': time.time()}
            self._save_watchlist()
            return True

    def get_watchlist_matches(self) -> List[Dict[str, Any]]:
        """Zwraca wszystkie mecze z Watchlisty."""
        return list(self.watchlist.values())

    def _fetch_feed_fixtures(self, url: str, day_offset: int = 0) -> List[Dict[str, Any]]:
        fixtures = []
        try:
            from datetime import datetime
            now = datetime.now()

            req = urllib.request.Request(url, headers=self.headers)
            with urllib.request.urlopen(req, timeout=8) as resp:
                raw = resp.read().decode('utf-8', errors='ignore')

            current_country = "Świat"
            current_league = "Piłka Nożna"

            blocks = raw.split('~')
            for b in blocks:
                if not b:
                    continue
                if b.startswith('ZA÷'):
                    za_val = ""
                    for item in b.split('¬'):
                        if item.startswith('ZA÷'):
                            za_val = item.split('÷', 1)[1]
                    if ':' in za_val:
                        parts = za_val.split(':', 1)
                        current_country = parts[0].strip()
                        current_league = parts[1].strip()
                    else:
                        current_country = za_val.strip()
                        current_league = za_val.strip()
                    continue

                if b.startswith('AA÷'):
                    fields = self._parse_fields(b)
                    mid = fields.get('AA', '')
                    home = fields.get('AE', '').strip()
                    away = fields.get('AF', '').strip()
                    time_raw = fields.get('DC', fields.get('GB', ''))
                    ad_val = fields.get('AD', fields.get('ADE', ''))

                    # Wyciągnięcie godziny meczu i minut do rozpoczęcia z timestampu AD
                    time_str = "Dzisiaj"
                    mins_until = 9999

                    if ad_val and ad_val.isdigit():
                        try:
                            match_dt = datetime.fromtimestamp(int(ad_val))
                            time_str = match_dt.strftime('%H:%M')
                            diff_secs = (match_dt - now).total_seconds()
                            mins_until = int(diff_secs / 60)
                        except Exception:
                            mins_until = 9999
                    else:
                        tm = re.search(r'(\d{2}):(\d{2})', time_raw)
                        if tm:
                            h, m = int(tm.group(1)), int(tm.group(2))
                            time_str = f"{h:02d}:{m:02d}"
                            match_dt = now.replace(hour=h, minute=m, second=0, microsecond=0)
                            diff_secs = (match_dt - now).total_seconds()
                            mins_until = int(diff_secs / 60)

                    status_code = fields.get('AB', '1')

                    # W Skanerze Przedmeczowym pokazujemy TYLKO mecze nadchodzące (przed pierwszym gwizdkiem)
                    # Mecze trwające na żywo (status_code != '1' lub mins_until < -5) należą do Live Scannera
                    if day_offset == 0:
                        if status_code in ('2', '3', '13', '14', '15', '16', '17', '18') or mins_until < -5:
                            continue

                    if not home or not away or not mid:
                        continue

                    c_upper = current_country.upper()
                    flag = COUNTRY_FLAGS.get(c_upper, '⚽')

                    fixtures.append({
                        'id': mid,
                        'league': current_league,
                        'country': current_country,
                        'country_flag': flag,
                        'home_team': home,
                        'away_team': away,
                        'time_str': time_str,
                        'mins_until': mins_until,
                        'status_code': status_code,
                        'url': f"https://www.flashscore.pl/mecz/{mid}/"
                    })
        except Exception as e:
            print(f"[PrematchScanner] Błąd pobierania feedu: {e}")

        return fixtures

    def _parse_fields(self, block: str) -> Dict[str, str]:
        fields = {}
        for item in block.split('¬'):
            if '÷' in item:
                k, v = item.split('÷', 1)
                fields[k] = v
        return fields

    def _load_watchlist(self) -> Dict[str, Any]:
        try:
            if os.path.exists(WATCHLIST_FILE):
                with open(WATCHLIST_FILE, 'r', encoding='utf-8') as f:
                    return json.load(f)
        except Exception:
            pass
        return {}

    def _save_watchlist(self):
        try:
            with open(WATCHLIST_FILE, 'w', encoding='utf-8') as f:
                json.dump(self.watchlist, f, ensure_ascii=False, indent=2)
        except Exception:
            pass
