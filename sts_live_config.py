"""
Konfiguracja programu STS Live Goal Scanner (STS + Flashscore)
"""

# Ustawienia odświeżania
SCAN_INTERVAL_SECONDS = 15       # Częstotliwość odświeżania na żywo
AUTO_REFRESH_ENABLED = True      # Automatyczne odświeżanie

# Złote Okno Godzinowe (16:00 - 06:00)
# W godzinach 16:00 - 06:00 generowane są sygnały dla najlepszych lig europejskich i południowoamerykańskich
ACTIVE_HOURS_ENABLED = True
ACTIVE_HOURS_START = 16   # Od 16:00 po południu
ACTIVE_HOURS_END = 6      # Do 06:00 rano

# Minimalne progi Indeksu Groźności (Danger Index):
MIN_DANGER_INDEX_1H = 65  # 1. połowa (Złote Okno 14'-32' ma 92.3% WR)
MIN_DANGER_INDEX_2H = 85  # 2. połowa i przerwa (HT/2H) - wymagany ekstremalny napór min. 85%

# Domyślne kryteria dla strategii bramkowych:
TRIGGERS_CONFIG = {
    # 1. OVER 0.5 HT (WYŁĄCZONY NA BAZIE DANYCH STATYSTYCZNYCH - 40% WR, JEDYNY RYNEK NA MINUSIE)
    "OVER_05_HT": {
        "enabled": False,
        "min_minute": 14,
        "max_minute": 35,
        "max_score_sum": 0,
        "min_odds": 1.45,
        "max_odds": 2.45,
        "min_apm": 0.85,
        "min_sot": 2,
        "min_shots_total": 5,
        "min_xg": 0.45,
    },
    
    # 2. OVER 1.5 HT (Pierwsza połowa - Złote Okno: 14' - 35' min)
    "OVER_15_HT": {
        "enabled": True,
        "min_minute": 14,
        "max_minute": 34,         # Odcięcie: 34. minuta
        "score_sum": 1,           # Dokładnie 1 bramka (1:0 lub 0:1)
        "min_odds": 1.45,
        "max_odds": 2.45,
        "min_apm": 0.90,
        "min_sot": 2,             # Min. 2 celne strzały
        "min_shots_total": 5,
    },

    # 3. OVER 0.5 2H / LATE GOAL (Druga połowa - MAKSYMALNIE DO 75. MINUTY)
    "OVER_05_2H": {
        "enabled": True,
        "min_minute": 63,
        "max_minute": 75,         # TWARDY LIMIT: 75. minuta (eliminacja loterii w końcówce)
        "allowed_score_diff": 1,  # Wynik na styku (0:0, 1:0, 0:1, 2:1, 1:2)
        "min_odds": 1.40,
        "max_odds": 2.45,
        "min_apm": 0.90,
        "min_sot": 3,
        "min_shots_total": 8,
    },
    
    # 4. OVER 1.5 FT (W drugiej połowie)
    "OVER_15_FT": {
        "enabled": True,
        "min_minute": 46,
        "max_minute": 68,
        "max_score_sum": 1,       # Wynik 0:0 lub 1:0 / 0:1
        "min_odds": 1.45,
        "max_odds": 2.45,
        "min_sot": 3,
        "min_shots_total": 7,
        "min_xg": 0.85,
    }
}

# Powiadomienia dźwiękowe
SOUND_ALERT_ENABLED = True
