import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import time
import re
from engine.active_cards_watchdog import CardSTSScheduler
from engine.telegram_notifier import TelegramNotifier
from engine.sts_live_engine import STSLiveEngine

def test_card_sts_scheduler_at_ht():
    """Weryfikacja: CardSTSScheduler zezwala na odpytywanie w przerwie meczu (HT, rank 20), blokuje tylko FT (rank 40)."""
    sched = CardSTSScheduler("test_match")
    now = 1000.0
    sched.next_allowed_time = 1000.0

    # HT (ranga 20) -> can_poll musi być True
    assert sched.can_poll(now, "0:1", stage_rank=20) is True, "Scheduler powinien zezwalać na odpytanie STS w przerwie meczu (HT)!"

    # FT (ranga 40) -> can_poll musi być False
    assert sched.can_poll(now, "0:1", stage_rank=40) is False, "Scheduler musi blokować odpytanie STS po zakończeniu meczu (FT)!"
    print("[OK] test_card_sts_scheduler_at_ht: PASSED")


def test_telegram_odds_disambiguation_and_source_isolation():
    """
    Weryfikacja scenariusza użytkownika (Fardu Ferghana vs Aral Nukus):
    - Badge to 'OVER 2.5 FT' z initial_odds = 1.63
    - Rynki syntetyczne (STS_LIVE) NIE MOGĄ aktualizować kursu na Telegramie
    - Rynki team totals (np. '1. drużyna - liczba goli' +0.5 = 1.75) NIE MOGĄ być przypisane do OVER 2.5 FT
    - Prawdziwy rynek STS_REAL 'Over 2.5 FT' o kursie 2.25 musi zaktualizować (Aktualny: 2.25)
    """
    notifier = TelegramNotifier()
    notifier.config["enabled"] = True
    notifier.config["live_update_mode"] = True

    key = "fardu_ferghana_vs_aral_nukus"
    card = {
        "home_team": "Fardu Ferghana",
        "away_team": "Aral Nukus",
        "league": "Uzbekistan Pro Liga",
        "badge": "OVER 2.5 FT",
        "unit_tag": "1J",
        "initial_odds": 1.63,
        "last_odds": 1.63,
        "initial_score": "0:1",
        "last_seen_score": "0:1",
        "last_rendered_score": "0:1",
        "initial_minute": 45,
        "last_seen_minute": 45,
        "last_rendered_minute": 45,
        "target_goals": 3,
        "target_period": "FT",
        "device_messages": {"123": 1},
        "last_text": "initial_text",
        "last_edit_time": time.time() - 100
    }
    notifier.active_match_cards[key] = card
    notifier.save_active_cards = lambda: None

    # Mock edit_message_all to record edits
    edited_messages = []
    notifier.edit_message_all = lambda dev_msgs, text: (edited_messages.append(text), True)[1]

    # Krok 1: Snapshot z rynkami syntetycznymi (STS_LIVE) - np. estymacja matematyczna 1.75
    synth_match = {
        "home_team": "Fardu Ferghana",
        "away_team": "Aral Nukus",
        "score_str": "0:1",
        "minute": 45,
        "half": "HT",
        "is_live": True,
        "live_markets": [
            {"name": "Over 2.5 FT", "odds": 1.75, "source": "STS_LIVE"}
        ]
    }
    notifier.check_and_update_match_status(synth_match, card_key=key)
    # Syntetyczny rynek nie może zostać ustawiony jako 'last_real_odds'
    assert card.get("last_real_odds") is None, "Syntetyczny rynek STS_LIVE nie może ustawić last_real_odds!"
    if edited_messages:
        assert "Aktualny: 1.75" not in edited_messages[-1], "Syntetyczny rynek STS_LIVE nie może być wyświetlony jako Aktualny!"

    # Krok 2: Snapshot z rynkiem innej linii (np. Over 0.5 FT = 1.75) z STS_REAL
    # Upewnij się, że Over 0.5 FT NIE jest przypisywany do karty OVER 2.5 FT!
    diff_line_match = {
        "home_team": "Fardu Ferghana",
        "away_team": "Aral Nukus",
        "score_str": "0:1",
        "minute": 45,
        "half": "HT",
        "is_live": True,
        "live_markets": [
            {"name": "Over 0.5 FT", "line": 0.5, "odds": 1.75, "period": "FT", "source": "STS_REAL"},
            {"name": "Over 1.5 FT", "line": 1.5, "odds": 1.95, "period": "FT", "source": "STS_REAL"}
        ]
    }
    notifier.check_and_update_match_status(diff_line_match, card_key=key)
    assert card.get("last_real_odds") is None, "Rynek Over 0.5 FT lub Over 1.5 FT nie może nadpisać kursu dla OVER 2.5 FT!"

    # Krok 3: Prawidłowy rynek z STS: 'Over 2.5 FT' = 2.25
    real_match = {
        "home_team": "Fardu Ferghana",
        "away_team": "Aral Nukus",
        "score_str": "0:1",
        "minute": 45,
        "half": "HT",
        "is_live": True,
        "live_markets": [
            {"name": "Over 0.5 FT", "line": 0.5, "odds": 1.10, "period": "FT", "source": "STS_REAL"},
            {"name": "Over 1.5 FT", "line": 1.5, "odds": 1.50, "period": "FT", "source": "STS_REAL"},
            {"name": "Over 2.5 FT", "line": 2.5, "odds": 2.25, "period": "FT", "source": "STS_REAL"},
            {"name": "Over 3.5 FT", "line": 3.5, "odds": 4.50, "period": "FT", "source": "STS_REAL"}
        ]
    }
    res = notifier.check_and_update_match_status(real_match, card_key=key)
    assert res is True, "Prawdziwy kurs STS powinien wywołać aktualizację karty"
    assert card.get("last_real_odds") == 2.25, f"Oczekiwano last_real_odds == 2.25, otrzymano: {card.get('last_real_odds')}"
    assert "Aktualny: 2.25" in edited_messages[-1], f"Wiadomość powinna zawierać '(Aktualny: 2.25)', treść: {edited_messages[-1]}"

    # Krok 4: Bukmacher zdejmuje linię z oferty (np. w 64. minucie zostaje tylko Over 1.5 FT)
    card["last_edit_time"] = time.time() - 100
    withdrawn_match = {
        "home_team": "Fardu Ferghana",
        "away_team": "Aral Nukus",
        "score_str": "0:1",
        "minute": 64,
        "half": "2H",
        "is_live": True,
        "live_markets": [
            {"name": "Over 1.5 FT", "line": 1.5, "odds": 1.47, "period": "FT", "source": "STS_REAL"},
            {"name": "Następny gol: Gosp.", "odds": 2.80, "period": "FT", "source": "STS_REAL"}
        ]
    }
    # 4a: Pierwsze wykrycie braku linii -> uruchamia grace period ~60s (bufor anty-zawieszeniowy)
    res_w_initial = notifier.check_and_update_match_status(withdrawn_match, card_key=key)
    assert card.get("withdrawn_first_seen") is not None, "Pierwszy brak linii musi zarejestrować timestamp withdrawn_first_seen!"
    assert card.get("is_market_withdrawn") is False, "W okresie karencji (<60s) rynek NIE może być oznaczony jako wycofany!"

    # 4b: Upływ 30s od zniknięcia linii -> nadal w buforze
    card["withdrawn_first_seen"] = time.time() - 30.0
    res_w_30s = notifier.check_and_update_match_status(withdrawn_match, card_key=key)
    assert card.get("is_market_withdrawn") is False, "Po 30s nadal obowiązuje bufor karencji (is_market_withdrawn=False)!"

    # 4c: Upływ 65s od zniknięcia linii (>60s) -> oficjalne wycofanie linii
    card["withdrawn_first_seen"] = time.time() - 65.0
    res_w = notifier.check_and_update_match_status(withdrawn_match, card_key=key)
    assert res_w is True, "Po przekroczeniu 60s zdjęcie linii z oferty powinno wyedytować wiadomość"
    assert card.get("is_market_withdrawn") is True, "Karta musi mieć flagę is_market_withdrawn = True po 60s"
    assert "Aktualny: 🔒 Wycofany" in edited_messages[-1], f"Wiadomość musi zawierać 'Aktualny: 🔒 Wycofany', treść: {edited_messages[-1]}"
    assert "Ostatni: 2.25" in edited_messages[-1], f"Wiadomość musi zachować ostatni REALNY kurs, treść: {edited_messages[-1]}"
    assert "1.75" not in edited_messages[-1], f"Błędny kurs 1.75 nie może się pojawić! Treść: {edited_messages[-1]}"
    assert "last_rendered_odds" not in card or card.get("last_rendered_odds") is None, "last_rendered_odds musi być wyczyszczone przy wycofaniu!"

    # 4d: Linia wraca do oferty -> reset flagi wycofania i withdrawn_first_seen
    notifier.check_and_update_match_status(real_match, card_key=key)
    assert card.get("is_market_withdrawn") is False, "Po powrocie linii flaga is_market_withdrawn musi znowu być False!"
    assert card.get("withdrawn_first_seen") is None, "withdrawn_first_seen musi zostać usunięte po powrocie linii!"

    if key in notifier.active_match_cards:
        del notifier.active_match_cards[key]
    print("[OK] test_telegram_odds_disambiguation_and_source_isolation: PASSED (including 60s Grace Period & Withdrawn Line)")


def test_sts_text_parser_isolation():
    """Weryfikacja: _parse_real_match_markets_text odcina rynki team totals i nie miesza ich z match total."""
    engine = STSLiveEngine.__new__(STSLiveEngine)
    dummy_text = """
Liczba goli
-2.5
1.52
+2.5
2.25
1. drużyna - liczba goli
-0.5
1.75
+0.5
1.85
2. drużyna - liczba goli
-0.5
1.60
+0.5
2.15
1. połowa - liczba goli
-0.5
1.40
+0.5
2.65
"""
    markets = engine._parse_real_match_markets_text(dummy_text, score_h=0, score_a=1)
    # Musi zawierać Over 2.5 FT z kursem 2.25
    over_25_ft = [m for m in markets if m.get('name') == 'Over 2.5 FT']
    assert len(over_25_ft) == 1, f"Oczekiwano 1 rynku Over 2.5 FT, znaleziono: {len(over_25_ft)}"
    assert over_25_ft[0]['odds'] == 2.25, f"Oczekiwano kursu 2.25, otrzymano: {over_25_ft[0]['odds']}"

    # NIE MOŻE zawierać Over 0.5 FT ze skażonego rynku 1. drużyna - liczba goli (1.85 / 1.75)!
    over_05_ft = [m for m in markets if m.get('name') == 'Over 0.5 FT']
    assert len(over_05_ft) == 0, f"Over 0.5 FT nie powinien istnieć w meczu total (pochodziłby z team total): {over_05_ft}"

    # Musi zawierać Over 0.5 HT z kursem 2.65
    over_05_ht = [m for m in markets if m.get('name') == 'Over 0.5 HT']
    assert len(over_05_ht) == 1, f"Oczekiwano 1 rynku Over 0.5 HT, znaleziono: {len(over_05_ht)}"
    assert over_05_ht[0]['odds'] == 2.65
    print("[OK] test_sts_text_parser_isolation: PASSED")


def test_al_ahed_dom_isolation_and_team_rejection():
    """
    Weryfikacja przypadku ze zrzutu ekranu (Al-Ahed FC vs Akhaa Ahli Aley):
    - Kafelek 'Liczba goli' zawiera linię -2.5 (1.75) oraz +2.5 (1.95 / 2.05).
    - Kafelek '1. drużyna - liczba goli' zawiera linię +1.5 (1.57, wcześniej 1.53).
    - Kafelek '2. drużyna - liczba goli' zawiera linię +0.5 (1.80).
    - Kafelek '1. połowa - liczba goli' zawiera linię +0.5 (1.40).
    - Kafelek 'Następny gol' zawiera 1 (1.65), nikt (3.20), 2 (4.50).
    
    Wymagania:
    1. Parser DOM (GET_SUBPAGE_MARKETS_JS) zwraca WYŁĄCZNIE Over 2.5 FT (1.95) jako rynek meczowy FT (is_match_total=True).
    2. Parser DOM BEZWZGLĘDNIE ODRZUCA Over 1.5 FT (1.57 / 1.53) z kafelka 1. drużyna.
    3. Parser DOM ignoruje wszystkie kafelki drużynowe i połów.
    4. Parser DOM pobiera Następny gol z flagą is_match_total=False.
    """
    from playwright.sync_api import sync_playwright
    from engine.sts_live_engine import GET_SUBPAGE_MARKETS_JS

    html_content = """<!DOCTYPE html>
    <html>
    <head><meta charset="utf-8"></head>
    <body>
        <!-- Główny kafelek meczowy: Liczba goli -->
        <div class="market-tile">
            <div class="market-tile-header">Liczba goli</div>
            <div class="market-tile-content">
                <button class="odds-button">
                    <span class="odds-button__label">-2.5</span>
                    <span class="odds-button__odd-value">1.75</span>
                </button>
                <button class="odds-button">
                    <span class="odds-button__label">+2.5</span>
                    <span class="odds-button__odd-value">1.95</span>
                </button>
            </div>
        </div>

        <!-- Wyciek: 1. drużyna - liczba goli (kurs 1.57 / 1.53) -->
        <div class="market-tile">
            <div class="market-tile-header">1. drużyna - liczba goli</div>
            <div class="market-tile-content">
                <button class="odds-button">
                    <span class="odds-button__label">-1.5</span>
                    <span class="odds-button__odd-value">2.20</span>
                </button>
                <button class="odds-button">
                    <span class="odds-button__label">+1.5</span>
                    <span class="odds-button__odd-value">1.57</span>
                </button>
            </div>
        </div>

        <!-- Wyciek: 2. drużyna - liczba goli -->
        <div class="market-tile">
            <div class="market-tile-header">2. drużyna - liczba goli</div>
            <div class="market-tile-content">
                <button class="odds-button">
                    <span class="odds-button__label">+0.5</span>
                    <span class="odds-button__odd-value">1.80</span>
                </button>
            </div>
        </div>

        <!-- Rynek połowy: 1. połowa - liczba goli -->
        <div class="market-tile">
            <div class="market-tile-header">1. połowa - liczba goli</div>
            <div class="market-tile-content">
                <button class="odds-button">
                    <span class="odds-button__label">+0.5</span>
                    <span class="odds-button__odd-value">1.40</span>
                </button>
            </div>
        </div>

        <!-- Następny gol -->
        <div class="market-tile">
            <div class="market-tile-header">Następny gol</div>
            <div class="market-tile-content">
                <button class="odds-button">
                    <span class="odds-button__label">1</span>
                    <span class="odds-button__odd-value">1.65</span>
                </button>
                <button class="odds-button">
                    <span class="odds-button__label">nikt</span>
                    <span class="odds-button__odd-value">3.20</span>
                </button>
                <button class="odds-button">
                    <span class="odds-button__label">2</span>
                    <span class="odds-button__odd-value">4.50</span>
                </button>
            </div>
        </div>
    </body>
    </html>"""

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content(html_content)
        parsed_markets = page.evaluate(GET_SUBPAGE_MARKETS_JS)
        browser.close()

    # 1. Sprawdź, czy Over 2.5 FT został prawidłowo sparsowany
    over_25 = [m for m in parsed_markets if m.get("market") == "Over 2.5 FT" or m.get("name") == "Over 2.5 FT"]
    assert len(over_25) == 1, f"Oczekiwano 1 rynku Over 2.5 FT, znaleziono: {len(over_25)}"
    assert over_25[0]["odds"] == 1.95, f"Oczekiwano kursu 1.95, otrzymano {over_25[0]['odds']}"
    assert over_25[0]["is_match_total"] is True, "Over 2.5 FT musi mieć is_match_total=True"
    assert over_25[0]["market_header"] == "Liczba goli", "Over 2.5 FT musi pochodzić z nagłówka 'Liczba goli'"

    # 2. Bezwzględny zakaz: Over 1.5 FT z '1. drużyna - liczba goli' NIE MOŻE istnieć!
    over_15 = [m for m in parsed_markets if "1.5" in m.get("name", "") or m.get("odds") == 1.57 or m.get("odds") == 1.53]
    assert len(over_15) == 0, f"BŁĄD KRYTYCZNY! Rynek Over 1.5 FT wyciekł z 1. drużyna: {over_15}"

    # 3. Zakaz: brak rynków z 2. drużyna ani z 1. połowa
    team_or_ht = [m for m in parsed_markets if "drużyn" in m.get("market_header", "").lower() or "połow" in m.get("market_header", "").lower()]
    assert len(team_or_ht) == 0, f"BŁĄD! Wyciekły rynki drużynowe/połowy: {team_or_ht}"

    # 4. Następny gol jest dozwolony, ale z is_match_total=False
    next_goals = [m for m in parsed_markets if "Następny gol" in m.get("name", "")]
    assert len(next_goals) == 3, f"Oczekiwano 3 rynków następnego gola, znaleziono: {len(next_goals)}"
    for ng in next_goals:
        assert ng["is_match_total"] is False, "Następny gol nie może mieć is_match_total=True"

    print("[OK] test_al_ahed_dom_isolation_and_team_rejection (Playwright): PASSED")


def test_pre_send_validation_rejection():
    """
    Weryfikacja Pre-Send Validation w goal_triggers.py i telegram_notifier.py:
    - Próba wysłania alertu z badge zawierającym FT, ale z market_header != 'liczba goli' lub is_match_total=False
    - Musi zostać zablokowana z komunikatem REJECTED_TEAM_MARKET_LEAK
    """
    from engine.goal_triggers import GoalTriggersEngine as GoalTriggers
    from engine.telegram_notifier import TelegramNotifier

    # 1. Test w TelegramNotifier: notify_goal_signal
    notifier = TelegramNotifier()
    notifier.config["enabled"] = True
    notifier.save_active_cards = lambda: None
    notifier._save_cards = lambda: None
    notifier.active_match_cards = {}
    sent_messages = []
    notifier.send_message_all = lambda text, reply_markup=None: (sent_messages.append(text), {"dev1": 101})[1]

    # Próba A: Wyciek z 1. drużyna (market_header niepoprawny)
    leaked_signal = {
        "badge": "OVER 1.5 FT",
        "odds": 1.53,
        "stars": 4,
        "market_obj": {
            "name": "Over 1.5 FT",
            "odds": 1.53,
            "market_header": "1. drużyna - liczba goli",
            "is_match_total": False,
            "source": "STS_REAL"
        }
    }
    match_data = {
        "home_team": "Mock FC",
        "away_team": "Test City",
        "score_str": "0:0",
        "minute": 27,
        "half": "1H",
        "league": "Test League"
    }

    res_a = notifier.notify_goal_signal(match_data, leaked_signal)
    assert res_a is False, "Pre-send validation w telegram_notifier MUSI odrzucić wyciek z 1. drużyna!"
    assert len(sent_messages) == 0, "Żadna wiadomość nie może zostać wysłana na Telegram przy wycieku!"

    # Próba B: Prawidłowy sygnał meczowy (Liczba goli, is_match_total=True, source=STS_REAL)
    valid_signal = {
        "badge": "OVER 2.5 FT",
        "odds": 1.95,
        "stars": 4,
        "market_obj": {
            "name": "Over 2.5 FT",
            "odds": 1.95,
            "market_header": "Liczba goli",
            "is_match_total": True,
            "source": "STS_REAL"
        }
    }
    res_b = notifier.notify_goal_signal(match_data, valid_signal)
    assert res_b is True, "Prawidłowy sygnał meczowy powinien przejść Pre-Send Validation!"
    assert len(sent_messages) == 1, "Prawidłowy sygnał powinien wysłać wiadomość na Telegram!"
    assert "OVER 2.5 FT" in sent_messages[0]

    # Posprzątaj dodaną kartę z pamięci notyfikatora
    card_key = "mock fc_vs_test city"
    if card_key in notifier.active_match_cards:
        del notifier.active_match_cards[card_key]

    # Próba C: Brak statusu STS_REAL (np. rynek syntetyczny STS_LIVE) -> BEZWZGLĘDNY DROP
    synth_signal = {
        "badge": "OVER 2.5 FT",
        "odds": 1.95,
        "stars": 4,
        "market_obj": {
            "name": "Over 2.5 FT",
            "odds": 1.95,
            "market_header": "Liczba goli",
            "is_match_total": True,
            "source": "STS_LIVE"
        }
    }
    res_c = notifier.notify_goal_signal(match_data, synth_signal)
    assert res_c is False, "Pre-send Gatekeeper MUSI bezwzględnie odrzucić sygnał ze statusem innym niż STS_REAL!"
    assert len(sent_messages) == 1, "Żadna wiadomość nie może zostać wysłana dla syntetycznego rynku!"

    # Próba D: Linia nie istnieje w bieżących rynkach meczu (live_markets bez linii 2.5) -> DROP
    match_data_missing_line = {
        "home_team": "Gamma Warriors",
        "away_team": "Delta Rangers",
        "score_str": "0:0",
        "minute": 27,
        "half": "1H",
        "league": "Test League",
        "live_markets": [
            {"name": "Over 1.5 FT", "line": 1.5, "odds": 1.40, "period": "FT", "source": "STS_REAL"}
        ]
    }
    res_d = notifier.notify_goal_signal(match_data_missing_line, valid_signal)
    assert res_d is False, "Pre-send Gatekeeper MUSI odrzucić sygnał, jeśli linia nie istnieje w bieżącej ofercie STS_REAL meczu!"
    assert len(sent_messages) == 1, "Brak wysyłki przy braku linii w STS_REAL!"

    # Próba E: Linia istnieje w live_markets z STS_REAL -> AKCEPTACJA
    match_data_with_line = {
        "home_team": "Alpha United",
        "away_team": "Omega Rovers",
        "score_str": "0:0",
        "minute": 27,
        "half": "1H",
        "league": "Test League",
        "live_markets": [
            {"name": "Over 1.5 FT", "line": 1.5, "odds": 1.40, "period": "FT", "source": "STS_REAL"},
            {"name": "Over 2.5 FT", "line": 2.5, "odds": 1.95, "period": "FT", "source": "STS_REAL"}
        ]
    }
    res_e = notifier.notify_goal_signal(match_data_with_line, valid_signal)
    assert res_e is True, "Pre-send Gatekeeper akceptuje sygnał, gdy linia istnieje w ofercie STS_REAL!"
    assert len(sent_messages) == 2, "Wiadomość powinna zostać wysłana!"

    card_key_e = "alpha united_vs_omega rovers"
    if card_key_e in notifier.active_match_cards:
        del notifier.active_match_cards[card_key_e]

    # 2. Test w GoalTriggers: evaluate_match_for_triggers
    gt = GoalTriggers()
    match_with_leak = {
        "home_team": "Al-Ahed FC",
        "away_team": "Akhaa Ahli Aley",
        "score": "0:0",
        "score_str": "0:0",
        "score_h": 0,
        "score_a": 0,
        "minute": 27,
        "half": "1H",
        "is_live": True,
        "league": "Liban, Puchar",
        "danger_index": 65,
        "danger_index_10": 65,
        "apm": 0.85,
        "sot_10m": 2.0,
        "live_markets": [
            {
                "name": "Over 1.5 FT",
                "market": "Over 1.5 FT",
                "odds": 1.53,
                "period": "FT",
                "market_header": "1. drużyna - liczba goli",
                "is_match_total": False,
                "source": "STS_REAL"
            },
            {
                "name": "Over 2.5 FT",
                "market": "Over 2.5 FT",
                "odds": 1.95,
                "period": "FT",
                "market_header": "Liczba goli",
                "is_match_total": True,
                "source": "STS_REAL"
            }
        ]
    }

    # available_over_ft w evaluate_match nie może dopuścić wycieku Over 1.5 FT
    stats = {
        "danger_index": 65,
        "apm": 0.85,
        "shots_on_target": 3,
        "dangerous_attacks": 35,
        "shots_total": 8,
        "corners": 4
    }
    eval_res = gt.evaluate_match(match_with_leak, stats, {})
    signals = eval_res.get("signals", [])
    for sig in signals:
        assert sig.get("badge") != "OVER 1.5 FT", f"Sygnał nie może wybrać wyciekającego Over 1.5 FT: {sig}"
        if "market_obj" in sig and "FT" in sig.get("badge", ""):
            assert sig["market_obj"].get("is_match_total") is True
            assert sig["market_obj"].get("market_header") in ("Liczba goli", "liczba goli", "liczba goli w meczu")

    print("[OK] test_pre_send_validation_rejection: PASSED")


if __name__ == "__main__":
    test_card_sts_scheduler_at_ht()
    test_telegram_odds_disambiguation_and_source_isolation()
    test_sts_text_parser_isolation()
    test_al_ahed_dom_isolation_and_team_rejection()
    test_pre_send_validation_rejection()
    print("\nALL STS ODDS DISAMBIGUATION & DOM ISOLATION TESTS PASSED SUCCESSFULLY!")
