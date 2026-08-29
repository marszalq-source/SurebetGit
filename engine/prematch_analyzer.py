"""
Moduł analizy przedmeczowej i kontekstu taktycznego (Prematch Analyzer).
Analizuje specyfikę ligi (Niemcy, Holandia, Puchary Europejskie), formę Dom/Wyjazd,
bramki do przerwy (Over 0.5/1.5 HT) oraz obciążenie kalendarza (Liga Mistrzów / Puchary).
"""
import urllib.request
import re
import time
from typing import Dict, Any, List, Optional

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36',
    'x-fsign': 'SW9D1eZo',
    'Origin': 'https://www.flashscore.pl',
    'Referer': 'https://www.flashscore.pl/',
}

# Profile ligowe i wagi overowości
LEAGUE_PROFILES = {
    # 🇦🇺 AUSTRALIA & NOWA ZELANDIA (Ultra wysoka średnia bramkowa 3.3 - 4.2 gola/mecz)
    'a-league': {'country': 'Australia', 'base_rating': 89, 'ht_over05_avg': 85, 'tag': '🇦🇺 Australia: A-League', 'tier': 'ULTRA'},
    'npl': {'country': 'Australia', 'base_rating': 92, 'ht_over05_avg': 88, 'tag': '🇦🇺 Australia: NPL (Stanowa)', 'tier': 'ULTRA'},
    'victoria': {'country': 'Australia', 'base_rating': 92, 'ht_over05_avg': 88, 'tag': '🇦🇺 Australia: NPL Victoria', 'tier': 'ULTRA'},
    'queensland': {'country': 'Australia', 'base_rating': 93, 'ht_over05_avg': 89, 'tag': '🇦🇺 Australia: NPL Queensland', 'tier': 'ULTRA'},
    'australia cup': {'country': 'Australia', 'base_rating': 90, 'ht_over05_avg': 86, 'tag': '🇦🇺 Australia: Australia Cup', 'tier': 'ULTRA'},
    'national league (nowa zelandia)': {'country': 'Nowa Zelandia', 'base_rating': 91, 'ht_over05_avg': 87, 'tag': '🇳🇿 Nowa Zelandia: National League', 'tier': 'ULTRA'},

    # 🇮🇳 INDIE & AZJA (Szybkie mecze, otwarte defensywy)
    'isl': {'country': 'Indie', 'base_rating': 84, 'ht_over05_avg': 81, 'tag': '🇮🇳 Indie: Super League (ISL)', 'tier': 'HIGH'},
    'indian super league': {'country': 'Indie', 'base_rating': 84, 'ht_over05_avg': 81, 'tag': '🇮🇳 Indie: ISL', 'tier': 'HIGH'},
    'i-league': {'country': 'Indie', 'base_rating': 83, 'ht_over05_avg': 80, 'tag': '🇮🇳 Indie: I-League', 'tier': 'HIGH'},
    'singapore': {'country': 'Singapur', 'base_rating': 94, 'ht_over05_avg': 90, 'tag': '🇸🇬 Singapur: Premier League (>4 goli/mecz!)', 'tier': 'ULTRA'},
    'singapur': {'country': 'Singapur', 'base_rating': 94, 'ht_over05_avg': 90, 'tag': '🇸🇬 Singapur: Premier League (>4 goli/mecz!)', 'tier': 'ULTRA'},
    'hongkong': {'country': 'Hongkong', 'base_rating': 88, 'ht_over05_avg': 85, 'tag': '🇭🇰 Hongkong: Premier League', 'tier': 'HIGH'},
    'j2 league': {'country': 'Japonia', 'base_rating': 79, 'ht_over05_avg': 76, 'tag': '🇯🇵 Japonia: J2 League', 'tier': 'MEDIUM'},

    # 🇳🇱 HOLANDIA (Klasyczne bramkowe eldorado)
    'eredivisie': {'country': 'Holandia', 'base_rating': 88, 'ht_over05_avg': 84, 'tag': '🇳🇱 Holandia: Eredivisie', 'tier': 'ULTRA'},
    'eerste divisie': {'country': 'Holandia', 'base_rating': 90, 'ht_over05_avg': 86, 'tag': '🇳🇱 Holandia: Eerste Divisie', 'tier': 'ULTRA'},
    'knvb beker': {'country': 'Holandia', 'base_rating': 88, 'ht_over05_avg': 84, 'tag': '🇳🇱 Holandia: Puchar KNVB', 'tier': 'ULTRA'},
    'puchar holandii': {'country': 'Holandia', 'base_rating': 88, 'ht_over05_avg': 84, 'tag': '🇳🇱 Holandia: Puchar KNVB', 'tier': 'ULTRA'},

    # 🇩🇪 NIEMCY (Ofensywna piłka i wysokie tempo)
    'bundesliga': {'country': 'Niemcy', 'base_rating': 86, 'ht_over05_avg': 83, 'tag': '🇩🇪 Niemcy: Bundesliga', 'tier': 'ULTRA'},
    '2. bundesliga': {'country': 'Niemcy', 'base_rating': 85, 'ht_over05_avg': 82, 'tag': '🇩🇪 Niemcy: 2. Bundesliga', 'tier': 'ULTRA'},
    '3. liga': {'country': 'Niemcy', 'base_rating': 79, 'ht_over05_avg': 76, 'tag': '🇩🇪 Niemcy: 3. Liga', 'tier': 'HIGH'},
    'dfb-pokal': {'country': 'Niemcy', 'base_rating': 87, 'ht_over05_avg': 85, 'tag': '🇩🇪 Niemcy: DFB Pokal (Puchar)', 'tier': 'ULTRA'},
    'dfb pokal': {'country': 'Niemcy', 'base_rating': 87, 'ht_over05_avg': 85, 'tag': '🇩🇪 Niemcy: DFB Pokal (Puchar)', 'tier': 'ULTRA'},
    'regionalliga': {'country': 'Niemcy', 'base_rating': 85, 'ht_over05_avg': 82, 'tag': '🇩🇪 Niemcy: Regionalliga', 'tier': 'HIGH'},

    # ❄️ SKANDYNAWIA & ISLANDIA (Kluczowe ligi letnie o wysokiej średniej goli)
    'besta deild': {'country': 'Islandia', 'base_rating': 91, 'ht_over05_avg': 87, 'tag': '🇮🇸 Islandia: Besta deild karla', 'tier': 'ULTRA'},
    'islandia': {'country': 'Islandia', 'base_rating': 90, 'ht_over05_avg': 86, 'tag': '🇮🇸 Islandia: Liga', 'tier': 'ULTRA'},
    'eliteserien': {'country': 'Norwegia', 'base_rating': 86, 'ht_over05_avg': 83, 'tag': '🇳🇴 Norwegia: Eliteserien', 'tier': 'ULTRA'},
    'obos-ligaen': {'country': 'Norwegia', 'base_rating': 87, 'ht_over05_avg': 84, 'tag': '🇳🇴 Norwegia: OBOS-ligaen', 'tier': 'ULTRA'},
    'allsvenskan': {'country': 'Szwecja', 'base_rating': 81, 'ht_over05_avg': 78, 'tag': '🇸🇪 Szwecja: Allsvenskan', 'tier': 'HIGH'},
    'veikkausliiga': {'country': 'Finlandia', 'base_rating': 80, 'ht_over05_avg': 77, 'tag': '🇫🇮 Finlandia: Veikkausliiga', 'tier': 'HIGH'},
    'meistriliiga': {'country': 'Estonia', 'base_rating': 87, 'ht_over05_avg': 84, 'tag': '🇪🇪 Estonia: Meistriliiga', 'tier': 'HIGH'},

    # 🇪🇺 PUCHARY EUROPEJSKIE
    'champions league': {'country': 'Europa', 'base_rating': 83, 'ht_over05_avg': 79, 'tag': '🇪🇺 Liga Mistrzów (UCL)', 'tier': 'HIGH'},
    'liga mistrzów': {'country': 'Europa', 'base_rating': 83, 'ht_over05_avg': 79, 'tag': '🇪🇺 Liga Mistrzów (UCL)', 'tier': 'HIGH'},
    'europa league': {'country': 'Europa', 'base_rating': 84, 'ht_over05_avg': 81, 'tag': '🇪🇺 Liga Europy (UEL)', 'tier': 'HIGH'},
    'liga europy': {'country': 'Europa', 'base_rating': 84, 'ht_over05_avg': 81, 'tag': '🇪🇺 Liga Europy (UEL)', 'tier': 'HIGH'},
    'conference league': {'country': 'Europa', 'base_rating': 85, 'ht_over05_avg': 82, 'tag': '🇪🇺 Liga Konferencji (UECL)', 'tier': 'HIGH'},
    'liga konferencji': {'country': 'Europa', 'base_rating': 85, 'ht_over05_avg': 82, 'tag': '🇪🇺 Liga Konferencji (UECL)', 'tier': 'HIGH'},

    # 🌍 INNE LIGI OFENSYWNE (Szwajcaria, Austria, USA, Walia)
    'super league (szwajcaria)': {'country': 'Szwajcaria', 'base_rating': 85, 'ht_over05_avg': 82, 'tag': '🇨🇭 Szwajcaria: Super League', 'tier': 'HIGH'},
    'szwajcaria': {'country': 'Szwajcaria', 'base_rating': 84, 'ht_over05_avg': 81, 'tag': '🇨🇭 Szwajcaria', 'tier': 'HIGH'},
    'austria': {'country': 'Austria', 'base_rating': 84, 'ht_over05_avg': 81, 'tag': '🇦🇹 Austria: Bundesliga', 'tier': 'HIGH'},
    'austrian bundesliga': {'country': 'Austria', 'base_rating': 84, 'ht_over05_avg': 81, 'tag': '🇦🇹 Austria: Bundesliga', 'tier': 'HIGH'},
    'major league soccer': {'country': 'USA', 'base_rating': 83, 'ht_over05_avg': 80, 'tag': '🇺🇸 USA: MLS', 'tier': 'HIGH'},
    'mls': {'country': 'USA', 'base_rating': 83, 'ht_over05_avg': 80, 'tag': '🇺🇸 USA: MLS', 'tier': 'HIGH'},
    'cymru premier': {'country': 'Walia', 'base_rating': 86, 'ht_over05_avg': 83, 'tag': '🏴󠁧󠁢󠁷󠁬󠁳󠁿 Walia: Cymru Premier', 'tier': 'HIGH'},
    'premier league': {'country': 'Anglia', 'base_rating': 81, 'ht_over05_avg': 77, 'tag': '🏴󠁧󠁢󠁥󠁮󠁧󠁿 Anglia: Premier League', 'tier': 'HIGH'},
    'ekstraklasa': {'country': 'Polska', 'base_rating': 73, 'ht_over05_avg': 70, 'tag': '🇵🇱 Polska: Ekstraklasa', 'tier': 'MEDIUM'},
}

class PrematchAnalyzer:
    def __init__(self):
        self.headers = HEADERS
        self.h2h_cache = {}

    def analyze_fixture(self, match_id: str, league_name: str, home_team: str, away_team: str, fetch_h2h: bool = False) -> Dict[str, Any]:
        """
        Kompleksowa ocena przedmeczowa: profil ligowy, statystyki H2H, dom/wyjazd,
        oraz detekcja zbliżających się meczów pucharowych (Liga Mistrzów itp.).
        """
        # 1. Rozpoznanie profilu ligowego
        league_info = self._get_league_profile(league_name)
        
        # 2. Pobranie i analiza H2H (opcjonalnie lub z cache)
        if fetch_h2h or match_id in self.h2h_cache:
            h2h_data = self._fetch_and_parse_h2h(match_id)
        else:
            h2h_data = {
                'home_stats': {'avg_goals': 3.1, 'ht_over05_pct': league_info['ht_over05_avg'], 'ht_over15_pct': 42.0, 'goals_scored_avg': 1.8},
                'away_stats': {'avg_goals': 2.9, 'ht_over05_pct': league_info['ht_over05_avg'], 'ht_over15_pct': 38.0, 'goals_scored_avg': 1.4},
                'next_fixtures': []
            }

        # 3. Wykrywanie ryzyka rotacji / kalendarza
        congestion = self._check_calendar_congestion(h2h_data, home_team, away_team)

        # 4. Obliczenie wskaźników bramkowych Dom / Wyjazd
        home_stats = h2h_data.get('home_stats', {})
        away_stats = h2h_data.get('away_stats', {})

        # Średnia goli i % Over 0.5 HT
        ht_over05_pct = round((home_stats.get('ht_over05_pct', league_info['ht_over05_avg']) +
                               away_stats.get('ht_over05_pct', league_info['ht_over05_avg'])) / 2.0, 1)

        ht_over15_pct = round((home_stats.get('ht_over15_pct', 42) +
                               away_stats.get('ht_over15_pct', 40)) / 2.0, 1)

        avg_total_goals = round((home_stats.get('avg_goals', 3.1) + away_stats.get('avg_goals', 2.9)) / 2.0, 2)

        # 5. Wyznaczenie ostatecznego Pre-Match Goal Rating (0 - 100%)
        goal_rating = league_info['base_rating']
        
        # Korekty na podstawie formy
        if ht_over05_pct >= 85:
            goal_rating += 6
        elif ht_over05_pct <= 60:
            goal_rating -= 8

        if avg_total_goals >= 3.3:
            goal_rating += 5
        elif avg_total_goals <= 2.2:
            goal_rating -= 7

        # Wpływ rotacji pucharowej
        if congestion['has_european_soon']:
            # Mecz w LM za 2-3 dni oznacza mniejszą dyscyplinę taktyczną lub rotacje obrońców (często więcej bramek!)
            goal_rating += 3

        goal_rating = max(25, min(98, goal_rating))

        # Ocena słowna
        if goal_rating >= 85:
            verdict = "🔥 MUST WATCH (Ultra-ofensywny potencjał)"
            verdict_color = "#00E676"
        elif goal_rating >= 75:
            verdict = "🟢 BARDZO DOBRY KANDYDAT NA GOLE"
            verdict_color = "#22C55E"
        elif goal_rating >= 65:
            verdict = "🟡 DOBRY / SOLIDNY"
            verdict_color = "#EAB308"
        else:
            verdict = "⚠️ NISKI POTENCJAŁ / ZACHOWAJ OSTROŻNOŚĆ"
            verdict_color = "#94A3B8"

        from .stats_comparator import StatsComparator
        comparison = StatsComparator.generate_multi_source_comparison(
            home_team, away_team, league_name, h2h_data, match_id=match_id, fetch_h2h=fetch_h2h
        )


        return {
            'league_tag': league_info['tag'],
            'country': league_info['country'],
            'prematch_goal_rating': goal_rating,
            'verdict': verdict,
            'verdict_color': verdict_color,
            'ht_over05_pct': ht_over05_pct,
            'ht_over15_pct': ht_over15_pct,
            'avg_total_goals': avg_total_goals,
            'home_goals_home': home_stats.get('goals_scored_avg', 1.8),
            'away_goals_away': away_stats.get('goals_scored_avg', 1.4),
            'congestion': congestion,
            'tactical_notes': self._generate_tactical_notes(league_info, ht_over05_pct, avg_total_goals, congestion),
            'comparison': comparison
        }

    def _get_league_profile(self, league_name: str) -> Dict[str, Any]:
        ln = league_name.lower()
        for key, profile in LEAGUE_PROFILES.items():
            if key in ln:
                return profile

        # Domyślny profil dla nieznanych lig
        return {
            'country': 'Inne',
            'base_rating': 70,
            'ht_over05_avg': 72,
            'tag': f"⚽ {league_name}"
        }

    def _fetch_and_parse_h2h(self, match_id: str) -> Dict[str, Any]:
        """Pobiera i paruje ostatnie mecze gospodarza, gościa i H2H."""
        if match_id in self.h2h_cache:
            return self.h2h_cache[match_id]

        url = f"https://3.flashscore.ninja/3/x/feed/df_hh_1_{match_id}"
        h2h_res = {
            'home_stats': {'avg_goals': 3.1, 'ht_over05_pct': 80.0, 'ht_over15_pct': 42.0, 'goals_scored_avg': 1.8},
            'away_stats': {'avg_goals': 2.9, 'ht_over05_pct': 78.0, 'ht_over15_pct': 38.0, 'goals_scored_avg': 1.4},
            'next_fixtures': []
        }

        try:
            req = urllib.request.Request(url, headers=self.headers)
            with urllib.request.urlopen(req, timeout=3.5) as resp:
                raw = resp.read().decode('utf-8', errors='ignore')

            # Wyszukiwanie wyników meczów (format KL÷X¬KM÷Y)
            # KL: gole 1. połowy home, KM: gole 1. połowy away
            ht_scores = re.findall(r'KL÷(\d+)¬KM÷(\d+)', raw)
            ft_scores = re.findall(r'KU÷(\d+)¬KV÷(\d+)', raw)

            if ht_scores:
                ht_goals = [int(h) + int(a) for h, a in ht_scores]
                total_m = len(ht_goals)
                if total_m > 0:
                    over05_count = sum(1 for g in ht_goals if g >= 1)
                    over15_count = sum(1 for g in ht_goals if g >= 2)
                    
                    h2h_res['home_stats']['ht_over05_pct'] = round((over05_count / total_m) * 100, 1)
                    h2h_res['away_stats']['ht_over05_pct'] = round((over05_count / total_m) * 100, 1)
                    h2h_res['home_stats']['ht_over15_pct'] = round((over15_count / total_m) * 100, 1)

            # Wyszukiwanie przyszłych meczów w pucharach (Liga Mistrzów, Liga Europy)
            next_competitions = re.findall(r'KF÷([^¬]+)¬', raw)
            h2h_res['next_fixtures'] = next_competitions

        except Exception:
            pass

        self.h2h_cache[match_id] = h2h_res
        return h2h_res

    def _check_calendar_congestion(self, h2h_data: Dict[str, Any], home_team: str, away_team: str) -> Dict[str, Any]:
        """Sprawdza czy któraś z drużyn gra wkrótce w pucharach europejskich."""
        fixtures = " ".join(h2h_data.get('next_fixtures', [])).lower()
        
        has_ucl = 'liga mistrz' in fixtures or 'champions league' in fixtures
        has_uel = 'liga europ' in fixtures or 'europa league' in fixtures
        has_uecl = 'liga konferenc' in fixtures or 'conference league' in fixtures

        has_european = has_ucl or has_uel or has_uecl
        alert_text = None

        if has_ucl:
            alert_text = "🇪🇺 UWAGA: Drużyna gra wkrótce w Lidze Mistrzów (możliwa rotacja składu / otwarta gra)"
        elif has_uel or has_uecl:
            alert_text = "🇪🇺 UWAGA: Zbliża się mecz w Lidze Europy/Konferencji (zagęszczenie kalendarza)"

        return {
            'has_european_soon': has_european,
            'is_ucl': has_ucl,
            'is_uel': has_uel,
            'alert_text': alert_text
        }

    def _generate_tactical_notes(self, league_info: Dict[str, Any], ht_over05: float, avg_goals: float, congestion: Dict[str, Any]) -> List[str]:
        notes = []
        if league_info['country'] in ['Niemcy', 'Holandia']:
            notes.append(f"Wysoki profil ligowy ({league_info['tag']}) – preferowany agresywny styl gry do przodu.")
        
        if ht_over05 >= 80:
            notes.append(f"Bardzo wysoka historyczna frekwencja bramek do przerwy ({ht_over05}% Over 0.5 HT).")
        
        if avg_goals >= 3.0:
            notes.append(f"Wysoka średnia bramkowa spotkań (średnio {avg_goals} gola na mecz).")

        if congestion['has_european_soon'] and congestion['alert_text']:
            notes.append(congestion['alert_text'])

        return notes
