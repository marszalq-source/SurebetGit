"""
Engine dopasowania zdarzen (Team Matching Engine) dla SurebetGit.
Uzywa rapidfuzz do bezpiecznego laczenia tych samych meczow roznych bukmacherow.
"""
import re
from rapidfuzz import fuzz

CLEAN_PATTERNS = [
    r'\b(fc|cf|ks|ac|sc|cd|ud|sv|vfb|vfl|fk|sk|mks|gks|az|nk|hsk|bk|ik|gf|csd|rc|as|ss|spvg|afc|bsc|mcf)\b',
    r'\b(club|deportivo|real|sporting|atletico|atletica|inter|united|city|town|rovers|wanderers)\b'
]

def clean_team_name(name: str) -> str:
    """Normalizuje nazwe druzyny, usuwa zbędne skróty i znaki specjalne."""
    if not name:
        return ""
    name_clean = name.lower()
    # Usuwanie polskich/zagranicznych znaków lub zamiana
    replacements = {
        'ł': 'l', 'ą': 'a', 'ę': 'e', 'ć': 'c', 'ń': 'n', 'ó': 'o', 'ś': 's', 'ź': 'z', 'ż': 'z',
        'ü': 'u', 'ä': 'a', 'ö': 'o', 'ß': 'ss'
    }
    for char, repl in replacements.items():
        name_clean = name_clean.replace(char, repl)
        
    name_clean = re.sub(r'[^a-z0-9\s]', ' ', name_clean)
    
    # Czyszczenie fraz typu FC, CF itp.
    for pat in CLEAN_PATTERNS:
        name_clean = re.sub(pat, ' ', name_clean)
        
    # Usunięcie wielokrotnych spacji
    name_clean = ' '.join(name_clean.split())
    return name_clean.strip()


def calculate_match_similarity(home1: str, away1: str, home2: str, away2: str) -> float:
    """
    Oblicza podobieństwo między meczem 1 (home1 vs away1) i meczem 2 (home2 vs away2).
    Zwraca wynik od 0 do 100.
    """
    h1 = clean_team_name(home1)
    a1 = clean_team_name(away1)
    h2 = clean_team_name(home2)
    a2 = clean_team_name(away2)

    if not h1 or not a1 or not h2 or not a2:
        return 0.0

    # Dopasowanie proste
    home_sim = fuzz.token_set_ratio(h1, h2)
    away_sim = fuzz.token_set_ratio(a1, a2)

    direct_score = (home_sim + away_sim) / 2.0

    # Dopasowanie zamienione (np. w przypadku odwrócenia gospodarz/gosc)
    reverse_home_sim = fuzz.token_set_ratio(h1, a2)
    reverse_away_sim = fuzz.token_set_ratio(a1, h2)
    reverse_score = (reverse_home_sim + reverse_away_sim) / 2.0

    return max(direct_score, reverse_score)


def are_matches_equal(match1: dict, match2: dict, threshold: float = 75.0) -> bool:
    """
    Sprawdza czy dwa mecze sa tym samym wydarzeniem.
    match format: {'home_team': '...', 'away_team': '...', 'sport': '...', 'league': '...'}
    """
    if match1.get('sport') and match2.get('sport'):
        if match1['sport'].lower() != match2['sport'].lower():
            return False
            
    sim = calculate_match_similarity(
        match1.get('home_team', ''), match1.get('away_team', ''),
        match2.get('home_team', ''), match2.get('away_team', '')
    )
    return sim >= threshold
