"""
Inteligentny moduł dopasowywania meczów (Fuzzy Matcher) pomiędzy Flashscore a STS.
Zoptymalizowany pod kątem minimalnego czasu wykonania (LRU cache, C-level translation, pre-kompilowane regexy).
"""
import re
import difflib
import functools
from typing import Dict, Any, Optional, Tuple, List

# Słownik zamienników i normalizacji dla popularnych klubów i krajów
NAME_REPLACEMENTS = {
    'monachium': 'munich',
    'madryt': 'madrid',
    'rzym': 'roma',
    'londyn': 'london',
    'mediolan': 'milan',
    'lizbona': 'lisbon',
    'warszawa': 'warsaw',
    'krakow': 'cracow',
    'wieden': 'vienna',
    'praga': 'prague',
    'kijow': 'kyiv',
    'moskwa': 'moscow',
    'pogon': 'pogon',
    'slask': 'slask',
    'lech': 'lech',
    'legia': 'legia',
    'gornik': 'gornik',
    'cracovia': 'cracovia',
    'korona': 'korona',
    'widzew': 'widzew',
    'jagiellonia': 'jagiellonia',
    'manchester city': 'man city',
    'manchester united': 'man utd',
    'wolverhampton': 'wolves',
    'tottenham hotspur': 'tottenham',
    'newcastle united': 'newcastle',
    'west ham united': 'west ham',
    'paris sg': 'psg',
    'paris saint-germain': 'psg',
    'inter mediolan': 'inter',
    'ac milan': 'milan',
    'as roma': 'roma',
    'atletico madryt': 'atletico madrid',
    'athletic bilbao': 'athletic club',
    'sporting lizbona': 'sporting cp',
}

PL_TRANS = str.maketrans('ąćęłńóśźżĄĆĘŁŃÓŚŹŻ', 'acelnoszzACELNOSZZ')
RE_PARENS = re.compile(r'\s*(\([^)]*\)|\[[^\]]*\])')
RE_PREFIX = re.compile(r'\b(fc|cf|fk|sk|sc|ks|bk|afc|ssc|cd|ac|as|ca|tsv|sv|vfb|1\.)\b')
RE_NON_ALPHANUM = re.compile(r'[^a-z0-9\s]')
RE_SPACES = re.compile(r'\s+')

@functools.lru_cache(maxsize=4096)
def normalize_team_name(name: str) -> str:
    """Oczyszcza i normalizuje nazwę drużyny do porównania (błyskawiczna wersja z LRU cache)."""
    if not name:
        return ""
    name = name.lower().translate(PL_TRANS)
    name = RE_PARENS.sub('', name)
    name = RE_PREFIX.sub('', name)
    name = RE_NON_ALPHANUM.sub(' ', name)
    name = RE_SPACES.sub(' ', name).strip()

    for old, new in NAME_REPLACEMENTS.items():
        if old in name:
            name = name.replace(old, new)

    return name

@functools.lru_cache(maxsize=8192)
def match_teams_similarity(norm1: str, norm2: str) -> float:
    """Zwraca stopień podobieństwa dwóch znormalizowanych nazw drużyn (0.0 do 1.0)."""
    if not norm1 or not norm2:
        return 0.0

    if norm1 == norm2:
        return 1.0

    # Jeśli jedna zawiera drugą
    if norm1 in norm2 or norm2 in norm1:
        return 0.90

    # Token match
    tokens1 = set(norm1.split())
    tokens2 = set(norm2.split())
    if tokens1 and tokens2:
        intersection = tokens1.intersection(tokens2)
        if len(intersection) >= min(len(tokens1), len(tokens2)) and len(intersection) > 0:
            return 0.85

    # Sequence matcher (fallback)
    return difflib.SequenceMatcher(None, norm1, norm2).ratio()

class LiveMatcher:
    @staticmethod
    def pre_normalize_matches(matches: List[Dict[str, Any]]):
        """Wstępnie normalizuje nazwy w liście meczów dla natychmiastowego dopasowania O(1)."""
        for m in matches:
            if '_norm_home' not in m:
                m['_norm_home'] = normalize_team_name(m.get('home_team', ''))
            if '_norm_away' not in m:
                m['_norm_away'] = normalize_team_name(m.get('away_team', ''))

    @staticmethod
    def match_flashscore_with_sts(fs_match: Dict[str, Any], sts_matches: list) -> Optional[Dict[str, Any]]:
        """
        Znajduje odpowiadający mecz w STS dla danego meczu z Flashscore.
        Wykorzystuje wstępnie znormalizowane pola oraz szybkie ścieżki weryfikacji.
        """
        fs_home = fs_match.get('_norm_home')
        if fs_home is None:
            fs_home = normalize_team_name(fs_match.get('home_team', ''))
            fs_match['_norm_home'] = fs_home

        fs_away = fs_match.get('_norm_away')
        if fs_away is None:
            fs_away = normalize_team_name(fs_match.get('away_team', ''))
            fs_match['_norm_away'] = fs_away

        if not fs_home or not fs_away:
            return None

        best_sts = None
        best_score = 0.0

        for sts in sts_matches:
            sts_home = sts.get('_norm_home')
            if sts_home is None:
                sts_home = normalize_team_name(sts.get('home_team', ''))
                sts['_norm_home'] = sts_home

            sts_away = sts.get('_norm_away')
            if sts_away is None:
                sts_away = normalize_team_name(sts.get('away_team', ''))
                sts['_norm_away'] = sts_away

            score_home = match_teams_similarity(fs_home, sts_home)
            if score_home < 0.65:
                continue

            score_away = match_teams_similarity(fs_away, sts_away)
            if score_away < 0.65:
                continue

            avg_score = (score_home + score_away) / 2.0
            if avg_score > best_score:
                best_score = avg_score
                best_sts = sts
                if avg_score >= 0.95:  # Natychmiastowe perfekcyjne dopasowanie
                    break

        if best_score >= 0.70:
            return best_sts
        return None
