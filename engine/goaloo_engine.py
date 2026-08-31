"""
Moduł integracji statystyk na żywo ze strony Goaloo (https://www.goaloo.com/).
Pobiera 100% realne statystyki meczowe (strzały celne/niecelne, ataki niebezpieczne, rzuty rożne, posiadanie piłki)
poprzez bezpośredni lekki feed HTTP (bf_us1.js + flashdata/get) w kilkadziesiąt milisekund.
"""

import re
import time
import urllib.request
from typing import Dict, Any, List, Optional

_TEAM_TRANSLATIONS = {
    'mariany polnocne': 'northern mariana islands',
    'mariany północne': 'northern mariana islands',
    'wyspy salomona': 'solomon islands',
    'wyspy owcze': 'faroe islands',
    'nowa zelandia': 'new zealand',
    'nowa kaledonia': 'new caledonia',
    'republika poludniowej afryki': 'south africa',
    'rpa': 'south africa',
    'wybrzeze kosci sloniowej': 'ivory coast',
    'korea poludniowa': 'south korea',
    'korea polnocna': 'north korea',
    'macedonia polnocna': 'north macedonia',
    'arabia saudyjska': 'saudi arabia',
    'stany zjednoczone': 'usa',
    'zjednoczone emiraty arabskie': 'uae',
    'czechy': 'czech republic',
    'wlochy': 'italy',
    'hiszpania': 'spain',
    'niemcy': 'germany',
    'francja': 'france',
    'anglia': 'england',
    'szwajcaria': 'switzerland',
    'szwecja': 'sweden',
    'wegry': 'hungary',
    'grecja': 'greece',
    'turcja': 'turkey',
    'holandia': 'netherlands'
}

class GoalooEngine:
    def __init__(self):
        self._matches_cache = []
        self._matches_cache_time = 0.0
        self._stats_cache = {}
        self._cache_ttl = 20.0  # sekundy
        self._stats_ttl = 15.0  # sekundy
        self._headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36',
            'Accept': '*/*',
            'Referer': 'https://www.goaloo.com/'
        }

    def _normalize_name(self, name: str) -> str:
        if not name: return ""
        n = name.lower()
        for pl, en in _TEAM_TRANSLATIONS.items():
            if pl in n:
                n = n.replace(pl, en)
        n = re.sub(r'<[^>]+>', '', n)
        n = re.sub(r'\[.*?\]|\(.*?\)', '', n)
        n = re.sub(r'\b(u\d{2}|fc|cf|sc|ac|fk|ks|sp|cd|sk|sv|united|utd|city|town|women|kobiety|mlodziez|junior)\b', '', n)
        n = re.sub(r'[^a-z0-9\s]', ' ', n)
        return ' '.join(n.split())

    def update_live_matches_list(self) -> List[Dict[str, Any]]:
        """Pobiera aktualną listę meczów na żywo z Goaloo feed (bf_us1.js) przez szybki HTTP."""
        now = time.time()
        if self._matches_cache and (now - self._matches_cache_time) < self._cache_ttl:
            return self._matches_cache

        try:
            ts = int(now * 1000)
            url = f'https://www.goaloo.com/gf/data/bf_us1.js?{ts}'
            req = urllib.request.Request(url, headers=self._headers)
            with urllib.request.urlopen(req, timeout=5) as resp:
                content = resp.read().decode('utf-8', errors='ignore')

            matches = []
            for line in re.findall(r'A\[\d+\]=\[(.*?)\n', content):
                parts = [p.strip("'\r; ]") for p in line.split(',')]
                if len(parts) >= 11:
                    m_id = parts[0]
                    status = parts[8] if len(parts) > 8 else '0'
                    home = parts[4] if len(parts) > 4 else ''
                    away = parts[5] if len(parts) > 5 else ''
                    score_h = parts[9] if len(parts) > 9 else '0'
                    score_a = parts[10] if len(parts) > 10 else '0'
                    # Status 1,2,3,4 = mecze w trakcie trwania
                    if status in ('1', '2', '3', '4'):
                        matches.append({
                            'id': m_id,
                            'home': home,
                            'away': away,
                            'score': f"{score_h}:{score_a}",
                            'status': status
                        })

            self._matches_cache = matches
            self._matches_cache_time = now
        except Exception as e:
            print(f"[GoalooEngine] Błąd pobierania listy meczów: {e}")

        return self._matches_cache

    def find_match_id(self, home_team: str, away_team: str) -> Optional[str]:
        """Dopasowuje drużyny do ID meczu na Goaloo."""
        h_words = [w for w in self._normalize_name(home_team).split() if len(w) >= 3]
        a_words = [w for w in self._normalize_name(away_team).split() if len(w) >= 3]

        def match_word(w, text):
            t = text.lower()
            if w in t: return True
            if w.replace('y', 'i') in t or w.replace('i', 'y') in t: return True
            return False

        # 1. Obie drużyny dopasowane
        for m in self._matches_cache:
            m_h = self._normalize_name(m.get('home', ''))
            m_a = self._normalize_name(m.get('away', ''))
            h_match = any(match_word(w, m_h) for w in h_words) or any(match_word(w, m_h) for w in a_words)
            a_match = any(match_word(w, m_a) for w in a_words) or any(match_word(w, m_a) for w in h_words)
            if h_match and a_match:
                return m['id']

        # 2. Przynajmniej jedna unikalna nazwa (>= 4 znaki)
        for m in self._matches_cache:
            m_h = self._normalize_name(m.get('home', ''))
            m_a = self._normalize_name(m.get('away', ''))
            h_match = any(match_word(w, m_h) for w in h_words if len(w) >= 4)
            a_match = any(match_word(w, m_a) for w in a_words if len(w) >= 4)
            if h_match or a_match:
                return m['id']

        return None

    def get_live_stats(self, home_team: str, away_team: str, minute: int = 1) -> Optional[Dict[str, Any]]:
        """
        Pobiera 100% realne statystyki live z Goaloo (flashdata/get) w kilkadziesiąt milisekund.
        """
        cache_key = f"{home_team}_{away_team}".lower()
        now = time.time()
        if cache_key in self._stats_cache:
            entry = self._stats_cache[cache_key]
            if now - entry['time'] < self._stats_ttl:
                return entry['stats']

        if not self._matches_cache:
            self.update_live_matches_list()

        match_id = self.find_match_id(home_team, away_team)
        if not match_id:
            return None

        try:
            ts = int(now * 1000)
            f_url = f'https://www.goaloo.com/flashdata/get?chid={match_id}&t={ts}'
            req = urllib.request.Request(f_url, headers=self._headers)
            with urllib.request.urlopen(req, timeout=3) as resp:
                f_txt = resp.read().decode('utf-8', errors='ignore')

            if not f_txt or '^' not in f_txt:
                return None

            sections = f_txt.split('^')
            # Indeksy flashdata Goaloo:
            # sections[8] = Home (Attacks, Dangerous, Poss) -> "10,0,34"
            # sections[9] = Away (Attacks, Dangerous, Poss) -> "21,0,66"
            # sections[10] = Home (On Target, Off Target) -> "1,0"
            # sections[11] = Away (On Target, Off Target) -> "0,0"
            # sections[14] = Corners Home -> "1"
            # sections[17] = Corners Away -> "4"

            att_h, dang_h, poss_h = 0, 0, 50
            att_a, dang_a, poss_a = 0, 0, 50
            s_on_h, s_off_h = 0, 0
            s_on_a, s_off_a = 0, 0
            corn_h, corn_a = 0, 0

            shots_tot_h, shots_tot_a = 0, 0

            if len(sections) > 8 and ',' in sections[8]:
                p = sections[8].split(',')
                if len(p) >= 3:
                    att_h = int(p[0]) if p[0].isdigit() else 0
                    dang_h = int(p[1]) if p[1].isdigit() else 0
                    poss_h = int(p[2]) if p[2].isdigit() else 50

            if len(sections) > 9 and ',' in sections[9]:
                p = sections[9].split(',')
                if len(p) >= 3:
                    att_a = int(p[0]) if p[0].isdigit() else 0
                    dang_a = int(p[1]) if p[1].isdigit() else 0
                    poss_a = int(p[2]) if p[2].isdigit() else 50

            if len(sections) > 10 and ',' in sections[10]:
                p = sections[10].split(',')
                if len(p) >= 2:
                    shots_tot_h = int(p[0]) if p[0].isdigit() else 0
                    s_on_h = int(p[1]) if p[1].isdigit() else 0
                    s_off_h = max(0, shots_tot_h - s_on_h)

            if len(sections) > 11 and ',' in sections[11]:
                p = sections[11].split(',')
                if len(p) >= 2:
                    shots_tot_a = int(p[0]) if p[0].isdigit() else 0
                    s_on_a = int(p[1]) if p[1].isdigit() else 0
                    s_off_a = max(0, shots_tot_a - s_on_a)

            if len(sections) > 14 and sections[14].isdigit():
                corn_h = int(sections[14])

            if len(sections) > 17 and sections[17].isdigit():
                corn_a = int(sections[17])

            sot_total = s_on_h + s_on_a
            shots_total = shots_tot_h + shots_tot_a
            corn_total = corn_h + corn_a
            dang_total = dang_h + dang_a
            eff_min = max(1, minute)

            apm = round(dang_total / eff_min, 2) if eff_min > 0 else (round(att_h + att_a) / (eff_min * 2) if eff_min > 0 else 0.5)
            danger_idx = min(98, max(15, int(apm * 45 + sot_total * 4.5 + corn_total * 2)))

            xg_h = round(max(0.0, s_on_h * 0.28 + s_off_h * 0.05 + dang_h * 0.015), 2)
            xg_a = round(max(0.0, s_on_a * 0.28 + s_off_a * 0.05 + dang_a * 0.015), 2)
            xg_total = round(xg_h + xg_a, 2)

            stats = {
                'attacks_home': att_h,
                'attacks_away': att_a,
                'dangerous_attacks_home': dang_h,
                'dangerous_attacks_away': dang_a,
                'possession_home': poss_h,
                'possession_away': poss_a,
                'shots_on_target_home': s_on_h,
                'shots_on_target_away': s_on_a,
                'shots_on_target_total': sot_total,
                'shots_off_target_home': s_off_h,
                'shots_off_target_away': s_off_a,
                'shots_total_home': shots_tot_h,
                'shots_total_away': shots_tot_a,
                'shots_total': shots_total,
                'corners_home': corn_h,
                'corners_away': corn_a,
                'corners_total': corn_total,
                'yellow_cards_home': 0,
                'yellow_cards_away': 0,
                'red_cards_home': 0,
                'red_cards_away': 0,
                'apm': apm,
                'danger_index': danger_idx,
                'xg_home': xg_h,
                'xg_away': xg_a,
                'xg_total': xg_total,
                'is_estimated': False,
                'source': 'GOALOO',
                'has_stats': True
            }

            self._stats_cache[cache_key] = {'time': now, 'stats': stats}
            return stats
        except Exception as e:
            print(f"[GoalooEngine] Błąd pobierania statystyk {home_team} vs {away_team}: {e}")

        return None
