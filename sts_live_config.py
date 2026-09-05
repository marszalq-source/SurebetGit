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

# Zbalansowane progi Indeksu Groźności (Danger Index - okna kroczące 10m i 5m):
MIN_DANGER_INDEX_1H = 55  # 1. połowa (min. 55% w oknie 10-min)
MIN_DANGER_INDEX_2H = 55  # 2. połowa i przerwa (HT/2H) (min. 55% w oknie 10-min)
MIN_DANGER_INDEX_5M = 60  # Wymóg naporu w oknie 5-minutowym (DI5 >= 60% dla 4⭐, >= 70% dla 5⭐)
MAX_FALLING_TREND = -15   # Maksymalny dopuszczalny spadek trendu (DI5 - DI10)

# Wymóg strzałów celnych (Shots on Target - wymóg świeżości):
MIN_SOT_1H = 3            # min. 3 celne strzały w meczu
MIN_SOT_2H = 3            # min. 3 celne strzały w meczu
MIN_SOT_10M_REQUIRED = 1  # Bezwzględny wymóg min. 1 strzału celnego w ostatnich 10 minutach

# Progi Wartości Oczekiwanej (Expected Value - EV):
MIN_EV_4_STAR = 0.03      # Twardy próg EV >= +3.0% dla oceny 4⭐
MIN_EV_5_STAR = 0.07      # Twardy próg EV >= +7.0% dla oceny 5⭐

# Żelazny warunek kursu (Value Bet floor ogólny):
MIN_ODDS = 1.40           # Dolny próg kursowy
MAX_ODDS = 2.65           # Maksymalny rozsądny kurs

# Dedykowane widełki kursowe per scenariusz (kontrolowany korytarz rynkowy):
SCENARIO_ODDS_RANGES = {
    'OVER_15_FT': (1.40, 2.00),      # Over 1.5 FT (Wczesna 2. połowa 46'-68')
    'POST_GOAL_FT': (1.45, 2.50),    # Over 2.5 FT / Podwyższenie po bramce
    'OVER_25_FT': (1.45, 2.50),      # Over 2.5 FT
    'OVER_15_HT': (1.70, 2.65),      # Over 1.5 HT
    'OVER_05_HT': (1.50, 2.15),      # Over 0.5 HT
    'OVER_05_2H': (1.45, 2.30),      # Over 0.5 2H
    'OVER_1H_TO_FT': (1.45, 2.20),   # 0:0 w 1H
}

# Selekcja gwiazdek i rynków:
MIN_STARS = 4             # Tylko pewniaki 4⭐ i 5⭐
ALLOWED_OVER_LINES = [0.5, 1.5, 2.5]  # Dozwolone linie meczowe FT (0.5, 1.5, 2.5 FT)

# TIER 1: Ligi o wysokiej płynności i bramkowości (bonus +1 pkt do scoringu)
LEAGUE_TIER_1_KEYWORDS = [
    'bundesliga', 'premier league', 'eredivisie', 'champions league', 'liga mistrzów',
    'europa league', 'liga europy', 'eliteserien', 'allsvenskan', 'superliga',
    'bundesliga austria', 'jupiler pro league', 'swiss super league'
]

# TIER 3: Ligi o niskiej bramkowości / underowe (kara -1 pkt do scoringu, wymagają wybitnych statystyk)
LEAGUE_TIER_3_KEYWORDS = [
    'egipt', 'egypt', 'division 2', '2. division',
    'kolumbia: liga kobiety', 'kolumbia: primera a', 'kolumbia: primera b',
    'colombia: primera a', 'colombia: primera b',
    'ekwador: serie b', 'ecuador: serie b',
    'iran: pro league', 'iran: league 1', 'iran',
    'morocco: botola', 'maroko: botola', 'maroko: 2. botola',
    'algeria: ligue 1', 'algeria: ligue 2', 'algieria',
    'greece: super league 2', 'grecja: super league 2',
    'argentina: primera b', 'argentina: torneo federal', 'argentina: primera nacional',
    'argentyna: primera b', 'argentyna: torneo federal', 'argentyna: primera c', 'argentina: primera c', 'primera c',
    'gruzja', 'georgia', 'erovnuli',
    'azerbejdżan', 'azerbejdzan', 'azerbaijan',
    'oman', 'omani',
    'south africa: premier league', 'rpa: premier league',
    'tunisia: ligue 1', 'tunezja',
    'venezuela: primera division', 'wenezuela', 'venezuela',
    'romania: liga 2', 'rumunia: liga 2',
    'uganda', 'tanzania', 'kenya', 'zambia', 'zimbabwe', 'rwanda'
]

# Flaga białej listy lig (True = przepuszczane tylko renomowane, profesjonalne ligi)
ENABLE_LEAGUE_WHITELIST = True

# -------------------------------------------------------------------------
# BIAŁA LISTA LIG (LEAGUE WHITELIST) - Filtr profesjonalnych lig
# -------------------------------------------------------------------------
LEAGUE_WHITELIST = [
    # Polska
    'ekstraklasa', '1. liga', '2. liga', 'puchar polski', 'superpuchar polski',

    # Anglia
    'premier league', 'championship', 'league one', 'league two', 'fa cup', 'efl cup', 'efl trophy', 'community shield',

    # Hiszpania
    'la liga', 'laliga', 'segunda division', 'laliga 2', 'copa del rey', 'supercopa de espana',

    # Włochy
    'serie a', 'serie b', 'serie c', 'coppa italia', 'supercoppa italiana',

    # Niemcy
    'bundesliga', '2. bundesliga', '3. liga', 'dfb pokal', 'supercup',

    # Francja
    'ligue 1', 'ligue 2', 'national', 'coupe de france', 'trophee des champions',

    # Holandia & Belgia
    'eredivisie', 'eerste divisie', 'knvb beker', 'jupiler pro league', 'challenger pro league', 'croky cup',

    # Portugalia
    'primeira liga', 'liga portugal', 'liga portugal 2', 'taca de portugal', 'taca da liga',

    # Szkocja
    'premiership', 'scottish premiership', 'scottish championship', 'scottish cup', 'league cup',

    # Skandynawia
    'superliga', '1. division', 'dbu pokalen', 'allsvenskan', 'superettan', 'svenska cupen',
    'eliteserien', 'obos-ligaen', 'nm cupen', 'veikkausliiga',

    # Kraje Alpejskie & Środkowa Europa
    'bundesliga austria', '2. liga austria', 'ofb cup', 'super league', 'challenge league',
    'chance liga', 'fortuna liga', 'fnl', 'mol cup', 'hnl', '1. hnl', 'superliga serbia',
    'superliga rumunia', 'liga 1 rumunia', 'nb i', 'nb 1', 'premier league ukraine', 'premier league ukraina', 'persha liga',

    # Turcja & Grecja
    'super lig', '1. lig', 'turkiye kupasi', 'super league greece', 'kypello elladas',

    # Puchary Europejskie & Światowe (UEFA / FIFA)
    'champions league', 'liga mistrzów', 'europa league', 'liga europy',
    'conference league', 'liga konferencji', 'uefa', 'nations league', 'liga narodów',
    'world cup', 'mistrzostwa świata', 'euro', 'copa america', 'afc champions league',
    'towarzyskie', 'friendly', 'club friendly',

    # Ameryka Północna & Południowa (Wysoka płynność)
    'mls', 'usl championship', 'liga mx', 'copa libertadores', 'copa sudamericana',
    'brasileirao', 'serie a brazil', 'serie b brazil', 'copa do brasil',
    'liga profesional argentina', 'copa de la liga profesional', 'copa argentina',
    'primera a kolumbia', 'primera division chile', 'liga pro ekwador',

    # Azja & Australia (Główne ligi)
    'a-league', 'j1 league', 'j2 league', 'k league 1', 'k league 2',
    'saudi pro league', 'qatar stars league', 'uae pro league'
]

# CZARNA LISTA KATEGORII (tylko rozgrywki wirtualne, esport i niestandardowe formaty)
LEAGUE_BLACKLIST_KEYWORDS = [
    'esport', 'cyber', 'virtual', 'futsal', 'simulated', 'short football', '7x7', '8x8', 'vr', 'penalty shoot'
]

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
