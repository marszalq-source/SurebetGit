"""
Moduł integracji statystyk meczowych na żywo ze strony BeeSports (https://www.beesports.com/pl/live).
Zapewnia priorytetowe pobieranie statystyk (ataki niebezpieczne, strzały celne, posiadanie piłki, rożne)
z automatycznym fallbackiem do Flashscore i STS.
"""

import re
import time
from typing import Dict, Any, List, Optional
from engine.live_matcher import LiveMatcher

class BeeSportsEngine:
    def __init__(self):
        self.matcher = LiveMatcher()
        self._matches_cache = []
        self._matches_cache_time = 0.0
        self._stats_cache = {}
        self._cache_ttl = 15.0 # sekundy dla listy meczów
        self._stats_ttl = 12.0  # sekundy dla statystyk meczu

    def _normalize_name(self, name: str) -> str:
        if not name: return ""
        n = name.lower()
        n = re.sub(r'\b(u\d{2}|fc|cf|sc|ac|fk|ks|sp|cd|sk|sv|united|utd|city|town)\b', '', n)
        n = re.sub(r'[^a-z0-9]', '', n)
        return n

    def fetch_live_matches_list(self, page) -> List[Dict[str, Any]]:
        """Pobiera aktualną listę meczów na żywo z BeeSports."""
        now = time.time()
        if self._matches_cache and (now - self._matches_cache_time) < self._cache_ttl:
            return self._matches_cache

        matches = []
        try:
            page.goto('https://www.beesports.com/pl/live', timeout=15000, wait_until='domcontentloaded')
            page.wait_for_timeout(1000)

            raw_items = page.evaluate("""() => {
                const results = [];
                const seen = new Set();
                
                document.querySelectorAll('a[href*="/match/"]').forEach(a => {
                    const href = a.href;
                    if (!href || seen.has(href)) return;
                    seen.add(href);
                    
                    const txt = a.innerText.trim();
                    results.push({
                        href: href,
                        full_text: txt
                    });
                });
                return results;
            }""")

            for item in raw_items:
                href = item.get('href', '')
                slug = href.split('/match/')[-1] if '/match/' in href else ''
                parts = slug.split('-')
                if len(parts) >= 3:
                    match_id = parts[-1]
                    name_slug = '-'.join(parts[:-1]).lower()
                    matches.append({
                        'href': href,
                        'id': match_id,
                        'slug': name_slug,
                        'raw_text': item.get('full_text', '')
                    })

            self._matches_cache = matches
            self._matches_cache_time = now
        except Exception as e:
            print(f"[BeeSportsEngine] Błąd pobierania listy meczów: {e}")

        return self._matches_cache

    def find_match_url(self, page, home_team: str, away_team: str) -> Optional[str]:
        """Dopasowuje drużyny do linku meczu na BeeSports."""
        matches = self.fetch_live_matches_list(page)
        h_norm = self._normalize_name(home_team)
        a_norm = self._normalize_name(away_team)

        for m in matches:
            slug = m['slug']
            slug_norm = re.sub(r'[^a-z0-9]', '', slug)
            if (h_norm and h_norm in slug_norm) or (a_norm and a_norm in slug_norm):
                return m['href']
            
            raw = m['raw_text'].lower()
            if (home_team.lower() in raw) or (away_team.lower() in raw):
                return m['href']

        return None

    def get_live_stats(self, page, home_team: str, away_team: str, minute: int = 1) -> Optional[Dict[str, Any]]:
        """
        Pobiera pełne statystyki live z BeeSports dla wskazanego meczu.
        Zwraca słownik statystyk kompatybilny z Flashscore / STS Live Scanner.
        """
        cache_key = f"{home_team}_{away_team}".lower()
        now = time.time()
        if cache_key in self._stats_cache:
            entry = self._stats_cache[cache_key]
            if now - entry['time'] < self._stats_ttl:
                return entry['stats']

        match_url = self.find_match_url(page, home_team, away_team)
        if not match_url:
            return None

        try:
            page.goto(match_url, timeout=12000, wait_until='domcontentloaded')
            page.wait_for_timeout(800)

            stats_data = page.evaluate("""() => {
                const bodyTxt = document.body ? document.body.innerText : '';
                return bodyTxt.split('\\n').map(s => s.trim()).filter(Boolean);
            }""")

            stats = self._parse_stats_stream(stats_data, minute)
            if stats and stats.get('has_stats'):
                self._stats_cache[cache_key] = {'time': now, 'stats': stats}
                return stats
        except Exception as e:
            print(f"[BeeSportsEngine] Błąd pobierania statystyk meczu {home_team} vs {away_team}: {e}")

        return None

    def _parse_stats_stream(self, parts: List[str], minute: int) -> Dict[str, Any]:
        """Ekstrahuje kluczowe parametry telemetryczne z tekstu BeeSports."""
        att_h, att_a = 0, 0
        dang_h, dang_a = 0, 0
        poss_h, poss_a = 50, 50
        sot_h, sot_a = 0, 0
        soff_h, soff_a = 0, 0
        corn_h, corn_a = 0, 0
        has_stats = False

        for i, p in enumerate(parts):
            p_clean = p.strip()
            if p_clean == 'Ataki' and i + 2 < len(parts):
                try:
                    att_h = int(parts[i+1])
                    att_a = int(parts[i+2])
                    has_stats = True
                except ValueError: pass
            elif p_clean == 'Ataki niebezpieczne' and i + 2 < len(parts):
                try:
                    dang_h = int(parts[i+1])
                    dang_a = int(parts[i+2])
                    has_stats = True
                except ValueError: pass
            elif p_clean == 'Posiadanie piłki' and i + 2 < len(parts):
                try:
                    poss_h = int(parts[i+1].replace('%', ''))
                    poss_a = int(parts[i+2].replace('%', ''))
                    has_stats = True
                except ValueError: pass
            elif p_clean == 'Strzały na/poza cel' and i + 9 < len(parts):
                try:
                    sub_nums = []
                    for idx in range(i+1, min(i+15, len(parts))):
                        if parts[idx].isdigit():
                            sub_nums.append(int(parts[idx]))
                    if len(sub_nums) >= 2:
                        sot_h = sub_nums[0]
                        sot_a = sub_nums[1]
                        has_stats = True
                except Exception: pass
            elif 'Rzuty rożne' in p_clean and i + 2 < len(parts):
                try:
                    corn_h = int(parts[i+1])
                    corn_a = int(parts[i+2])
                    has_stats = True
                except ValueError: pass

        if not has_stats:
            return {'has_stats': False}

        eff_min = max(1, minute)
        dang_total = dang_h + dang_a
        apm = round(dang_total / eff_min, 2) if eff_min > 0 else 0.5
        sot_total = sot_h + sot_a
        shots_total = sot_total + soff_h + soff_a

        # Danger Index z realnych ataków niebezpiecznych BeeSports
        danger_idx = min(98, max(15, int(apm * 50 + sot_total * 4.5 + (corn_h + corn_a) * 2)))

        # xG wyliczane z celnych strzałów i naporu bramkowego
        xg_total = round(max(0.1, sot_total * 0.28 + dang_total * 0.015), 2)
        xg_h = round(max(0.05, sot_h * 0.28 + dang_h * 0.015), 2)
        xg_a = round(max(0.05, sot_a * 0.28 + dang_a * 0.015), 2)

        return {
            'attacks_home': att_h,
            'attacks_away': att_a,
            'dangerous_attacks_home': dang_h,
            'dangerous_attacks_away': dang_a,
            'possession_home': poss_h,
            'possession_away': poss_a,
            'shots_on_target_home': sot_h,
            'shots_on_target_away': sot_a,
            'shots_on_target_total': sot_total,
            'shots_off_target_home': soff_h,
            'shots_off_target_away': soff_a,
            'shots_total_home': sot_h + soff_h,
            'shots_total_away': sot_a + soff_a,
            'corners_home': corn_h,
            'corners_away': corn_a,
            'corners_total': corn_h + corn_a,
            'apm': apm,
            'danger_index': danger_idx,
            'xg_home': xg_h,
            'xg_away': xg_a,
            'xg_total': xg_total,
            'is_estimated': False,
            'source': 'BEESPORTS',
            'has_stats': True
        }
