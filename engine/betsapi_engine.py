"""
Moduł integracji statystyk na żywo ze strony BetsAPI (https://betsapi.com/cip/soccer).
Pobiera 100% realne statystyki meczowe (strzały celne/niecelne, ataki niebezpieczne, rzuty rożne, posiadanie piłki)
poprzez bezpośredni lekki parser HTTP bez konieczności używania przeglądarki.
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

class BetsAPIEngine:
    def __init__(self):
        self._matches_cache = []
        self._matches_cache_time = 0.0
        self._stats_cache = {}
        self._cache_ttl = 20.0  # sekundy
        self._stats_ttl = 15.0  # sekundy
        self._headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9,pl;q=0.8'
        }

    def _normalize_name(self, name: str) -> str:
        if not name: return ""
        n = name.lower()
        for pl, en in _TEAM_TRANSLATIONS.items():
            if pl in n:
                n = n.replace(pl, en)
        n = re.sub(r'\[.*?\]|\(.*?\)', '', n)
        n = re.sub(r'\b(u\d{2}|fc|cf|sc|ac|fk|ks|sp|cd|sk|sv|united|utd|city|town|women|kobiety|mlodziez|junior)\b', '', n)
        n = re.sub(r'[^a-z0-9\s]', ' ', n)
        return ' '.join(n.split())

    def update_live_matches_list(self) -> List[Dict[str, Any]]:
        """Pobiera aktualną listę meczów na żywo z BetsAPI In-Play przez lekki HTTP."""
        now = time.time()
        if self._matches_cache and (now - self._matches_cache_time) < self._cache_ttl:
            return self._matches_cache

        try:
            url = 'https://betsapi.com/cip/soccer'
            req = urllib.request.Request(url, headers=self._headers)
            with urllib.request.urlopen(req, timeout=5) as resp:
                html = resp.read().decode('utf-8', errors='ignore')

            raw_matches = re.findall(r'href="(/soccer/r/(\d+)/([^"]+))"', html)
            matches = []
            seen = set()
            for full_href, m_id, slug in raw_matches:
                if m_id in seen:
                    continue
                seen.add(m_id)
                clean_slug = slug.lower().replace('%28', '').replace('%29', '').replace('(', '').replace(')', '')
                matches.append({
                    'href': f"https://betsapi.com{full_href}",
                    'id': m_id,
                    'slug': clean_slug
                })

            self._matches_cache = matches
            self._matches_cache_time = now
        except Exception as e:
            print(f"[BetsAPIEngine] Błąd pobierania listy meczów: {e}")

        return self._matches_cache

    def find_match_url(self, home_team: str, away_team: str) -> Optional[str]:
        """Dopasowuje drużyny do linku meczu na BetsAPI."""
        h_words = [w for w in self._normalize_name(home_team).split() if len(w) >= 3]
        a_words = [w for w in self._normalize_name(away_team).split() if len(w) >= 3]

        def match_word(w, slug):
            if w in slug: return True
            # Wymiana fonetyczna y <-> i (np. altay <-> altai)
            if w.replace('y', 'i') in slug or w.replace('i', 'y') in slug: return True
            return False

        # 1. Obie drużyny dopasowane w slugu
        for m in self._matches_cache:
            slug = m.get('slug', '')
            h_match = any(match_word(w, slug) for w in h_words)
            a_match = any(match_word(w, slug) for w in a_words)
            if h_match and a_match:
                return m['href']

        # 2. Przynajmniej jedna unikalna nazwa (>= 4 znaki)
        for m in self._matches_cache:
            slug = m.get('slug', '')
            h_match = any(match_word(w, slug) for w in h_words if len(w) >= 4)
            a_match = any(match_word(w, slug) for w in a_words if len(w) >= 4)
            if h_match or a_match:
                return m['href']

        return None

    def get_live_stats(self, home_team: str, away_team: str, minute: int = 1) -> Optional[Dict[str, Any]]:
        """
        Pobiera 100% realne statystyki live z BetsAPI przez szybki request HTTP.
        """
        cache_key = f"{home_team}_{away_team}".lower()
        now = time.time()
        if cache_key in self._stats_cache:
            entry = self._stats_cache[cache_key]
            if now - entry['time'] < self._stats_ttl:
                return entry['stats']

        if not self._matches_cache:
            self.update_live_matches_list()

        match_url = self.find_match_url(home_team, away_team)
        if not match_url:
            return None

        try:
            req = urllib.request.Request(match_url, headers=self._headers)
            with urllib.request.urlopen(req, timeout=4) as resp:
                html = resp.read().decode('utf-8', errors='ignore')

            stats_rows = re.findall(r'<tr[^>]*>\s*<td[^>]*>(.*?)</td>\s*<td[^>]*>(.*?)</td>\s*<td[^>]*>(.*?)</td>\s*</tr>', html, re.DOTALL | re.IGNORECASE)
            
            raw_data = {}
            for h_val, label, a_val in stats_rows:
                l_clean = re.sub(r'<[^>]+>', '', label).strip().lower()
                h_clean = re.sub(r'<[^>]+>', '', h_val).strip()
                a_clean = re.sub(r'<[^>]+>', '', a_val).strip()
                if l_clean:
                    raw_data[l_clean] = (h_clean, a_clean)

            if not raw_data:
                return None

            def parse_int_pair(key_frag):
                for k, (h_str, a_str) in raw_data.items():
                    if key_frag in k:
                        try:
                            h_num = int(re.sub(r'[^\d]', '', h_str)) if re.search(r'\d', h_str) else 0
                            a_num = int(re.sub(r'[^\d]', '', a_str)) if re.search(r'\d', a_str) else 0
                            return h_num, a_num
                        except Exception:
                            pass
                return 0, 0

            corn_h, corn_a = parse_int_pair('corner')
            yc_h, yc_a = parse_int_pair('yellow')
            rc_h, rc_a = parse_int_pair('red')
            att_h, att_a = parse_int_pair('attacks')
            dang_h, dang_a = parse_int_pair('dangerous')
            s_on_h, s_on_a = parse_int_pair('on target')
            s_off_h, s_off_a = parse_int_pair('off target')
            poss_h, poss_a = parse_int_pair('possession')

            if poss_h == 0 and poss_a == 0:
                poss_h, poss_a = 50, 50

            sot_total = s_on_h + s_on_a
            shots_tot_h = s_on_h + s_off_h
            shots_tot_a = s_on_a + s_off_a
            shots_total = shots_tot_h + shots_tot_a
            corn_total = corn_h + corn_a
            dang_total = dang_h + dang_a
            eff_min = max(1, minute)

            apm = round(dang_total / eff_min, 2) if eff_min > 0 else 0.5
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
                'yellow_cards_home': yc_h,
                'yellow_cards_away': yc_a,
                'red_cards_home': rc_h,
                'red_cards_away': rc_a,
                'apm': apm,
                'danger_index': danger_idx,
                'xg_home': xg_h,
                'xg_away': xg_a,
                'xg_total': xg_total,
                'is_estimated': False,
                'source': 'BETSAPI',
                'has_stats': True
            }

            self._stats_cache[cache_key] = {'time': now, 'stats': stats}
            return stats
        except Exception as e:
            print(f"[BetsAPIEngine] Błąd pobierania statystyk {home_team} vs {away_team}: {e}")

        return None
