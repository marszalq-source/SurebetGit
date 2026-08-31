"""
Skaner Przedmeczowy (Prematch Scanner) dla piłki nożnej.
Wyszukuje nadchodzące mecze pod kątem bramek (Niemcy, Holandia, Puchary Europejskie),
analizuje statystyki dom/wyjazd, integruje ofertę STS (kursy 1X2 i bramkowe)
oraz zarządza Watchlistą (obserwowanymi meczami).
"""
import urllib.request
import urllib.parse
import re
import time
import json
import os
from typing import Dict, List, Any, Optional
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
from .prematch_analyzer import PrematchAnalyzer
from .live_matcher import LiveMatcher, normalize_team_name

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36',
    'x-fsign': 'SW9D1eZo',
    'Origin': 'https://www.flashscore.pl',
    'Referer': 'https://www.flashscore.pl/',
}

WATCHLIST_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "watchlist.json")

# Flagi państw dla akordeonów
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

# Słownik zamienników na oficjalne nazewnictwo STS (polska transkrypcja i konwencja bukmachera STS)
STS_TEAM_NAME_MAP = {
    'bayern munich': 'Bayern Monachium',
    'bayern': 'Bayern Monachium',
    'borussia dortmund': 'Borussia Dortmund',
    'bayer leverkusen': 'Bayer Leverkusen',
    'bayer 04 leverkusen': 'Bayer Leverkusen',
    'rb leipzig': 'RB Lipsk',
    'leipzig': 'RB Lipsk',
    'monchengladbach': 'Borussia M-gladbach',
    'borussia m.': 'Borussia M-gladbach',
    'borussia mönchengladbach': 'Borussia M-gladbach',
    'eintracht frankfurt': 'Eintracht Frankfurt',
    'vfb stuttgart': 'VfB Stuttgart',
    'vfl wolfsburg': 'VfL Wolfsburg',
    'sc freiburg': 'SC Freiburg',
    'werder bremen': 'Werder Brema',
    'mainz 05': 'FSV Mainz',
    'fsv mainz 05': 'FSV Mainz',
    'fc augsburg': 'FC Augsburg',
    'tsg hoffenheim': 'TSG Hoffenheim',
    '1. fc union berlin': 'Union Berlin',
    'union berlin': 'Union Berlin',
    'fc st. pauli': 'St. Pauli',
    'vfl bochum': 'VfL Bochum',
    '1. fc heidenheim': '1. FC Heidenheim',
    'holstein kiel': 'Holstein Kiel',
    'hertha bsc': 'Hertha Berlin',
    'schalke 04': 'Schalke 04',
    'hamburger sv': 'Hamburger SV',
    'hannover 96': 'Hannover 96',
    '1. fc nurnberg': '1. FC Norymberga',
    '1. fc koln': '1. FC Koeln',
    'fortuna dusseldorf': 'Fortuna Duesseldorf',
    
    # Holandia
    'ajax': 'Ajax Amsterdam',
    'psv': 'PSV Eindhoven',
    'feyenoord': 'Feyenoord Rotterdam',
    'az alkmaar': 'AZ Alkmaar',
    'fc twente': 'FC Twente',
    'fc utrecht': 'FC Utrecht',
    'sc heerenveen': 'SC Heerenveen',
    'fc groningen': 'FC Groningen',
    'nec nijmegen': 'NEC Nijmegen',
    'sparta rotterdam': 'Sparta Rotterdam',
    'go ahead eagles': 'Go Ahead Eagles',
    'fortuna sittard': 'Fortuna Sittard',
    'pec zwolle': 'PEC Zwolle',
    'heracles almelo': 'Heracles Almelo',
    'nac breda': 'NAC Breda',
    'willem ii': 'Willem II Tilburg',
    'rkc waalwijk': 'RKC Waalwijk',
    'almere city': 'Almere City',

    # Francja
    'paris sg': 'PSG',
    'paris saint-germain': 'PSG',
    'paris saint germain': 'PSG',
    'olympique lyonnais': 'Olympique Lyon',
    'lyon': 'Olympique Lyon',
    'olympique de marseille': 'Olympique Marsylia',
    'marseille': 'Olympique Marsylia',
    'as monaco': 'AS Monaco',
    'monaco': 'AS Monaco',
    'lille osc': 'Lille',
    'stade rennais': 'Rennes',
    'rc lens': 'RC Lens',
    'ogc nice': 'OGC Nice',

    # Włochy
    'inter': 'Inter Mediolan',
    'inter milan': 'Inter Mediolan',
    'ac milan': 'AC Milan',
    'milan': 'AC Milan',
    'as roma': 'AS Roma',
    'roma': 'AS Roma',
    'juventus': 'Juventus Turyn',
    'napoli': 'SSC Napoli',
    'ss lazio': 'Lazio Rzym',
    'lazio': 'Lazio Rzym',
    'atalanta': 'Atalanta Bergamo',
    'bologna': 'FC Bologna',
    'fiorentina': 'ACF Fiorentina',
    'torino': 'Torino FC',

    # Hiszpania
    'real madrid': 'Real Madryt',
    'atletico madrid': 'Atletico Madryt',
    'athletic club': 'Athletic Bilbao',
    'athletic bilbao': 'Athletic Bilbao',
    'fc barcelona': 'FC Barcelona',
    'barcelona': 'FC Barcelona',
    'real betis': 'Betis Sewilla',
    'sevilla': 'Sevilla FC',
    'real sociedad': 'Real Sociedad',
    'villarreal': 'Villarreal CF',
    'valencia': 'Valencia CF',
    'celta vigo': 'Celta Vigo',
    'rayo vallecano': 'Rayo Vallecano',

    # Anglia
    'manchester city': 'Man. City',
    'man city': 'Man. City',
    'manchester united': 'Man. United',
    'man utd': 'Man. United',
    'tottenham hotspur': 'Tottenham',
    'tottenham': 'Tottenham',
    'newcastle united': 'Newcastle',
    'newcastle': 'Newcastle',
    'west ham united': 'West Ham',
    'west ham': 'West Ham',
    'wolverhampton wanderers': 'Wolves',
    'wolverhampton': 'Wolves',
    'brighton & hove albion': 'Brighton',
    'aston villa': 'Aston Villa',
    'arsenal': 'Arsenal Londyn',
    'chelsea': 'Chelsea Londyn',
    'liverpool': 'Liverpool FC',

    # Portugalia
    'sporting cp': 'Sporting Lizbona',
    'sporting lisbon': 'Sporting Lizbona',
    'benfica': 'Benfica Lizbona',
    'fc porto': 'FC Porto',
    'braga': 'Sporting Braga',

    # Polska Ekstraklasa & I Liga
    'lech poznan': 'Lech Poznań',
    'lech poznań': 'Lech Poznań',
    'cracovia krakow': 'Cracovia',
    'cracovia': 'Cracovia',
    'legia warsaw': 'Legia Warszawa',
    'legia warszawa': 'Legia Warszawa',
    'pogon szczecin': 'Pogoń Szczecin',
    'pogoń szczecin': 'Pogoń Szczecin',
    'slask wroclaw': 'Śląsk Wrocław',
    'śląsk wrocław': 'Śląsk Wrocław',
    'gornik zabrze': 'Górnik Zabrze',
    'górnik zabrze': 'Górnik Zabrze',
    'widzew lodz': 'Widzew Łódź',
    'widzew łódź': 'Widzew Łódź',
    'rakow czestochowa': 'Raków Częstochowa',
    'raków częstochowa': 'Raków Częstochowa',
    'jagiellonia bialystok': 'Jagiellonia Białystok',
    'jagiellonia białystok': 'Jagiellonia Białystok',
    'zaglebie lubin': 'Zagłębie Lubin',
    'zagłębie lubin': 'Zagłębie Lubin',
    'radomiak radom': 'Radomiak',
    'korona kielce': 'Korona Kielce',
    'stal mielec': 'Stal Mielec',
    'piast gliwice': 'Piast Gliwice',
    'motor lublin': 'Motor Lublin',
    'gks katowice': 'GKS Katowice',
    'wisla krakow': 'Wisła Kraków',
    'wisła kraków': 'Wisła Kraków',
    'arka gdynia': 'Arka Gdynia',
    'ruch chorzow': 'Ruch Chorzów',
    'ruch chorzów': 'Ruch Chorzów',
    'lechia gdansk': 'Lechia Gdańsk',
    'lechia gdańsk': 'Lechia Gdańsk',
    'polonia warszawa': 'Polonia Warszawa',
    'gks tychy': 'GKS Tychy',
    'bruk-bet termalica': 'Termalica Nieciecza',
    'termalica': 'Termalica Nieciecza',

    # Inne kraje i puchary
    'copenhagen': 'FC Kopenhaga',
    'fc copenhagen': 'FC Kopenhaga',
    'slavia prague': 'Slavia Praga',
    'sparta prague': 'Sparta Praga',
    'dinamo zagreb': 'Dinamo Zagrzeb',
    'hajduk split': 'Hajduk Split',
    'red star belgrade': 'Crvena Zvezda Belgrad',
    'crvena zvezda': 'Crvena Zvezda Belgrad',
    'partizan': 'Partizan Belgrad',
    'fenerbahce': 'Fenerbahce Stambuł',
    'galatasaray': 'Galatasaray Stambuł',
    'besiktas': 'Besiktas Stambuł',
    'olympiacos': 'Olympiakos Pireus',
    'panathinaikos': 'Panathinaikos Ateny',
    'paok': 'PAOK Saloniki',
    'aek athens': 'AEK Ateny',
    'bodo/glimt': 'Bodo/Glimt',
    'young boys': 'Young Boys Berno',
    'red bull salzburg': 'RB Salzburg',
}

def to_sts_team_name(raw_name: str) -> str:
    """Zwraca nazwę drużyny sformatowaną według standardu bukmachera STS."""
    if not raw_name:
        return ""
    name_clean = raw_name.strip()
    low = name_clean.lower()
    
    # 1. Dokładne lub prefiksowe dopasowanie w mapie STS
    for k, sts_name in STS_TEAM_NAME_MAP.items():
        if low == k or low.startswith(k + ' ') or low.endswith(' ' + k):
            return sts_name

    # 2. Oczyszczenie zbędnych dopisków
    name_clean = re.sub(r'\s*\b(FC|CF|FK|SK|KS|BK|SC)\b\s*', ' ', name_clean, flags=re.IGNORECASE).strip()
    name_clean = re.sub(r'\s+', ' ', name_clean).strip()
    return name_clean or raw_name

class PrematchScanner:
    def __init__(self):
        self.analyzer = PrematchAnalyzer()
        self.matcher = LiveMatcher()
        self.headers = HEADERS
        self.watchlist = self._load_watchlist()
        self._sts_cache_time = 0
        self._sts_cached_matches = []

    def scan_upcoming(self, country_filter: str = "ALL", day_offset: int = 0, time_filter: str = "ALL", min_rating: int = 40) -> Dict[str, Any]:
        """
        Skanuje WSZYSTKIE nadchodzące mecze ze wszystkich krajów i lig.
        Integruje ofertę bukmachera STS, priorytetyzuje nazewnictwo STS oraz wylicza kursy 1X2 i bramkowe.
        country_filter: 'ALL', 'GERMANY', 'NETHERLANDS', 'AUSTRALIA_ASIA', 'SCANDINAVIA', 'CHAMPIONS_LEAGUE', 'CONFERENCE_LEAGUE', 'EUROPA_LEAGUE', 'EUROPE', 'ULTRA_GOALS' lub konkretny kraj.
        day_offset: 0 = dzisiaj, 1 = jutro
        time_filter: 'ALL', '1H' (za 0-60 min), '2H' (za 0-120 min), '3H' (za 0-180 min)
        """
        start_time = time.time()
        feed_url = f"https://global.flashscore.ninja/3/x/feed/f_1_{day_offset}_2_pl_1"
        
        # 1. Pobierz mecze z Flashscore Feed
        raw_matches = self._fetch_feed_fixtures(feed_url, day_offset=day_offset)
        
        # 2. Pobierz aktualną ofertę przedmeczową STS (z cache RAM)
        sts_matches = self._get_cached_sts_prematch_matches()
        if sts_matches:
            self.matcher.pre_normalize_matches(sts_matches)
        
        # 3. Filtrowanie według czasu i kraju
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

            # 4. Dopasowanie do STS i priorytetyzacja nazw STS
            matched_sts = None
            if sts_matches:
                matched_sts = self.matcher.match_flashscore_with_sts(m, sts_matches)

            if matched_sts:
                m['home_team'] = matched_sts.get('home_team', m['home_team'])
                m['away_team'] = matched_sts.get('away_team', m['away_team'])
                m['matched_with_sts'] = True
                m['odds_1'] = matched_sts.get('odds_1')
                m['odds_X'] = matched_sts.get('odds_X')
                m['odds_2'] = matched_sts.get('odds_2')
                m['sts_url'] = matched_sts.get('url', f"https://www.sts.pl/szukaj?q={urllib.parse.quote(m['home_team'] + ' ' + m['away_team'])}")
            else:
                m['home_team'] = to_sts_team_name(m['home_team'])
                m['away_team'] = to_sts_team_name(m['away_team'])
                m['matched_with_sts'] = False
                m['odds_1'] = None
                m['odds_X'] = None
                m['odds_2'] = None
                m['sts_url'] = f"https://www.sts.pl/szukaj?q={urllib.parse.quote(m['home_team'] + ' ' + m['away_team'])}"

            filtered_fixtures.append(m)

        # 5. Równoległa analiza taktyczna i wyliczanie kursów STS dla wszystkich meczów
        analyzed_matches = []
        with ThreadPoolExecutor(max_workers=25) as executor:
            future_to_match = {
                executor.submit(
                    self.analyzer.analyze_fixture,
                    m['id'], m['league'], m['home_team'], m['away_team'],
                    fetch_h2h=False,
                    odds_1=m.get('odds_1'),
                    odds_X=m.get('odds_X'),
                    odds_2=m.get('odds_2')
                ): m for m in filtered_fixtures
            }

            for future in future_to_match:
                m_data = future_to_match[future]
                try:
                    analysis = future.result(timeout=4)
                except Exception:
                    analysis = self.analyzer.analyze_fixture(
                        m_data['id'], m_data['league'], m_data['home_team'], m_data['away_team'],
                        odds_1=m_data.get('odds_1'), odds_X=m_data.get('odds_X'), odds_2=m_data.get('odds_2')
                    )

                if analysis['prematch_goal_rating'] >= min_rating:
                    is_watched = m_data['id'] in self.watchlist
                    analyzed_matches.append({
                        **m_data,
                        'analysis': analysis,
                        'odds': analysis.get('odds', {}),
                        'is_watched': is_watched
                    })

        # Sortowanie meczów: wg godziny rozpoczęcia i potencjału bramkowego
        analyzed_matches.sort(key=lambda x: (x.get('mins_until', 9999), -x['analysis']['prematch_goal_rating']))

        # 6. Budowanie struktury akordeonu STS (Kraje & Ligi)
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

    def _get_cached_sts_prematch_matches(self) -> List[Dict[str, Any]]:
        """Pobiera mecze z oferty STS z wykorzystaniem buforowania w RAM (TTL: 120s)."""
        now = time.time()
        if self._sts_cached_matches and (now - self._sts_cache_time < 120):
            return self._sts_cached_matches

        matches = []
        try:
            from .sts_live_engine import _STSLiveWorker
            worker = _STSLiveWorker.get_instance()
            # Spróbuj pobrać bezpośrednio linie ze strony dzisiejszej oferty
            lines = worker.get_live_lines(timeout=4.0)
            if lines:
                matches = self._parse_sts_prematch_lines(lines)
        except Exception:
            pass

        if matches:
            self._sts_cached_matches = matches
            self._sts_cache_time = now

        return matches

    def _parse_sts_prematch_lines(self, lines: List[str]) -> List[Dict[str, Any]]:
        """Parsuje linie z kafelków meczowych STS na obiekty meczów."""
        matches = []
        n = len(lines)
        current_league = "Piłka Nożna – STS"
        i = 0

        while i < n:
            l = lines[i]

            if ',' in l and any(k in l for k in [
                'Anglia', 'Hiszpania', 'Niemcy', 'Włochy', 'Francja', 'Polska', 'Holandia',
                'Portugalia', 'Turcja', 'Dania', 'USA', 'Champions', 'Europa', 'Conference',
                'Międzynarodowe', 'Liga', 'Super', 'Puchar', 'Ekstraklasa', 'Brazylia', 'Argentyna'
            ]):
                current_league = l
                i += 1
                continue

            if ('dzisiaj' in l.lower() or 'jutro' in l.lower() or re.match(r'^\d{1,2}:\d{2}$', l)) and i >= 1:
                day_str = "dzisiaj"
                time_str = "20:00"
                
                if re.match(r'^\d{1,2}:\d{2}$', l):
                    time_str = l
                    j = i + 1
                else:
                    day_str = l.lower()
                    j = i + 1
                    if j < n and re.match(r'^\d{1,2}:\d{2}$', lines[j]):
                        time_str = lines[j]
                        j += 1

                home_team = ""
                prev_idx = i - 1
                while prev_idx >= 0:
                    p_l = lines[prev_idx]
                    if not p_l.startswith('+') and not p_l.isdigit() and p_l != current_league and len(p_l) > 1 and not p_l.startswith('Top'):
                        home_team = p_l
                        break
                    prev_idx -= 1

                away_team = ""
                while j < min(n, i + 6):
                    nxt = lines[j]
                    if nxt not in ['STS TV', 'Mecz', '1', 'X', '2', 'Zwycięzca meczu', 'Zwycięzca'] and not nxt.startswith('+') and not re.match(r'^\d+\.\d{2}$', nxt):
                        away_team = nxt
                        j += 1
                        break
                    j += 1

                o1, oX, o2 = 2.20, 3.20, 3.10
                k = j
                found_odds = False
                while k < min(n, j + 14):
                    if lines[k] == '1' and k + 1 < n and re.match(r'^\d+\.\d{2}$', lines[k+1]):
                        o1 = float(lines[k+1])
                        found_odds = True
                        k += 2
                        if k < n and lines[k] == 'X' and k + 1 < n and re.match(r'^\d+\.\d{2}$', lines[k+1]):
                            oX = float(lines[k+1])
                            k += 2
                        if k < n and lines[k] == '2' and k + 1 < n and re.match(r'^\d+\.\d{2}$', lines[k+1]):
                            o2 = float(lines[k+1])
                            k += 2
                        break
                    k += 1

                if home_team and away_team and home_team != away_team and found_odds:
                    if not any(k in home_team for k in ['[K]', 'Townsend', 'Wang', 'Sabalenka', 'Dzumhur', 'Alcaraz', 'Safiullin', 'Rybakina', 'Tiafoe', 'Gemy', 'Sety', 'Punkty']):
                        matches.append({
                            'league': current_league,
                            'home_team': home_team,
                            'away_team': away_team,
                            'time_str': time_str,
                            'day_str': day_str,
                            'odds_1': o1,
                            'odds_X': oX,
                            'odds_2': o2,
                            'bookmaker': 'STS',
                            'url': f"https://www.sts.pl/szukaj?q={urllib.parse.quote(home_team + ' ' + away_team)}"
                        })
                    i = max(i + 1, k)
                    continue

            i += 1

        return matches

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

                    time_str = "Dzisiaj"
                    mins_until = 9999

                    # Dokładne wyliczenie godziny meczu i minut do rozpoczęcia z timestampu UTC Flashscore
                    if ad_val and ad_val.isdigit():
                        try:
                            match_dt = datetime.fromtimestamp(int(ad_val))
                            if day_offset == 0:
                                time_str = match_dt.strftime('%H:%M')
                            elif day_offset == 1:
                                time_str = f"Jutro {match_dt.strftime('%H:%M')}"
                            else:
                                time_str = match_dt.strftime('%d.%m %H:%M')
                                
                            diff_secs = (match_dt - now).total_seconds()
                            mins_until = int(diff_secs / 60)
                        except Exception:
                            mins_until = 9999
                    else:
                        tm = re.search(r'(\d{2}):(\d{2})', time_raw)
                        if tm:
                            h, m = int(tm.group(1)), int(tm.group(2))
                            time_str = f"{h:02d}:{m:02d}"
                            if day_offset == 1:
                                time_str = f"Jutro {time_str}"
                            match_dt = now.replace(hour=h, minute=m, second=0, microsecond=0)
                            diff_secs = (match_dt - now).total_seconds()
                            mins_until = int(diff_secs / 60)

                    status_code = fields.get('AB', '1')

                    # W Skanerze Przedmeczowym pokazujemy TYLKO mecze nadchodzące (przed rozpoczęciem gry)
                    if day_offset == 0:
                        if status_code in ('2', '3', '4', '5', '10', '11', '13', '14', '15', '16', '17', '18') or mins_until < -5:
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
