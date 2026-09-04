"""
Konfiguracja programu STS Live Goal Scanner (STS + Flashscore)
"""

# Ustawienia odświeżania
SCAN_INTERVAL_SECONDS = 15       # Częstotliwość odświeżania na żywo
AUTO_REFRESH_ENABLED = True      # Automatyczne odświeżanie

# Tryb 24/7 (Skaner aktywny przez całą dobę bez przerw)
ACTIVE_HOURS_ENABLED = False     # False = praca non-stop 24h/dobę
ACTIVE_HOURS_START = 0
ACTIVE_HOURS_END = 24

# Pancerne progi Indeksu Groźności (Danger Index):
MIN_DANGER_INDEX_1H = 85  # 1. połowa (min. 85% - potężny napór)
MIN_DANGER_INDEX_2H = 90  # 2. połowa i przerwa (HT/2H) - min. 90% (ekstremalny napór)

# Wymóg strzałów celnych (Shots on Target):
MIN_SOT_1H = 3            # 1. połowa: min. 3 celne strzały
MIN_SOT_2H = 4            # 2. połowa: min. 4 celne strzały

# Selekcja gwiazdek i rynków:
MIN_STARS = 4             # Tylko pewniaki: 4⭐ i 5⭐ (odcięcie słabych 2⭐ i 3⭐)
ALLOWED_OVER_LINES = [0.5, 1.5, 2.5]  # Tylko bezpieczne linie meczowe FT: 0.5, 1.5, 2.5 FT
SIGNAL_RATE_LIMIT_MINUTES = 50        # Limit sygnałów: ok. 1 sygnał na godzinę (min. 50-60 min odstępu)

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
