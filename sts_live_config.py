"""
Konfiguracja programu STS Live Goal Scanner (STS + Flashscore)
"""

# Ustawienia odświeżania
SCAN_INTERVAL_SECONDS = 15       # Częstotliwość odświeżania na żywo
AUTO_REFRESH_ENABLED = True      # Automatyczne odświeżanie

# Domyślne kryteria dla strategii bramkowych:
TRIGGERS_CONFIG = {
    # 1. OVER 0.5 HT (Złote Okno: 14' - 35' min, Odcięcie: 35. min, Min. 2 strzały celne)
    "OVER_05_HT": {
        "min_minute": 14,         # Złote okno start: 14. minuta
        "max_minute": 35,         # Odcięcie: 35. minuta (brak ryzykownych alertów w 36'-45')
        "max_score_sum": 0,       # Wynik musi być 0:0
        "min_odds": 1.50,
        "min_apm": 0.80,          # Min. 0.80 groźnego ataku na minutę
        "min_sot": 2,             # Twardy filtr: Min. 2 strzały celne w światło bramki
        "min_shots_total": 4,     # Min. 4 strzały łącznie
        "min_xg": 0.40,           # Jeśli xG jest dostępne
    },
    
    # 2. OVER 1.5 HT (Pierwsza połowa - Złote Okno: 14' - 35' min)
    "OVER_15_HT": {
        "min_minute": 14,
        "max_minute": 35,         # Odcięcie: 35. minuta
        "score_sum": 1,           # Dokładnie 1 bramka (1:0 lub 0:1)
        "min_odds": 1.65,
        "min_apm": 0.90,
        "min_sot": 2,             # Min. 2 celne strzały
        "min_shots_total": 5,
    },

    
    # 3. OVER 0.5 2H / LATE GOAL (Druga połowa)
    "OVER_05_2H": {
        "min_minute": 63,
        "max_minute": 83,
        "allowed_score_diff": 1,  # Wynik na styku (0:0, 1:0, 0:1, 2:1, 1:2)
        "min_odds": 1.65,
        "min_apm": 0.90,
        "min_sot": 3,
        "min_shots_total": 8,
    },
    
    # 4. OVER 1.5 FT (W drugiej połowie)
    "OVER_15_FT": {
        "min_minute": 46,
        "max_minute": 68,
        "max_score_sum": 1,       # Wynik 0:0 lub 1:0 / 0:1
        "min_odds": 1.70,
        "min_sot": 3,
        "min_shots_total": 7,
        "min_xg": 0.90,
    }
}

# Powiadomienia dźwiękowe
SOUND_ALERT_ENABLED = True
