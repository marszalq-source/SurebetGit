# -*- coding: utf-8 -*-
"""
Kompleksowy zestaw testów weryfikacyjnych dla modułu ActiveCardsWatchdog:
- Test A: Kompilacja i importy strukturalne
- Test B: Test jednostkowy logiki decyzyjnej (Urgent vs Regular vs Skip)
- Test C: Test cyklu życia (Lifecycle): 1H -> HT -> 2H -> FT
- Test D: Test Race Condition (równoległy Watchdog i FastSettlement)
- Test E: Test timeoutów i odporności na awarie sieciowe
- Test F: Test restartu / crash recovery
- Test G: Test Stale Snapshot Rejection (brak regresji minuty/fazy)
- Test H: Test FT -> Terminal Lock (trwałość stanu terminalnego)
- Test I: Test Duplicate Edit Prevention (ochrona przed spamem)
- Test J: Test Rate Limit & Hysteresis kursów
"""

import os
import sys
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)
os.chdir(BASE_DIR)

try:
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    if hasattr(sys.stderr, 'reconfigure'):
        sys.stderr.reconfigure(encoding='utf-8')
except Exception:
    pass

import time
import copy
import threading

from engine.telegram_notifier import TelegramNotifier
from engine.active_cards_watchdog import ActiveCardsWatchdog

class MockTelegramNotifier(TelegramNotifier):
    """Izolowany mock TelegramNotifier nie wysyłający realnych requestów sieciowych."""
    def __new__(cls, *args, **kwargs):
        return object.__new__(cls)

    def __init__(self):
        self._cards_lock = threading.RLock()
        self.config = {"enabled": True, "live_update_mode": True}
        self.active_match_cards = {}
        self.settled_matches = {}
        self.edit_calls = []
        self.sent_calls = []
        from engine.stats_engine import StatsEngine
        self.stats_engine = StatsEngine()
        class DummyBA:
            def settle_bet_async(self, m, o): pass
        self.ba_sync = DummyBA()

    def edit_message_all(self, device_messages, text, parse_mode="HTML"):
        self.edit_calls.append({"text": text, "time": time.time(), "devs": device_messages})
        return True

    def send_message_all(self, text, parse_mode="HTML"):
        self.sent_calls.append({"text": text, "time": time.time()})
        return {"123456": 999}

    def _save_cards(self):
        pass


def test_a_structure_and_imports():
    """Test A: Kompilacja i importy strukturalne."""
    print("\n[TEST A] Kompilacja i importy...")
    from engine.active_cards_watchdog import ActiveCardsWatchdog
    watchdog = ActiveCardsWatchdog()
    assert watchdog is not None
    assert isinstance(watchdog.counters, dict)
    assert watchdog.counters["watchdog_cycles"] == 0
    print("  -> PASSED: Moduł ActiveCardsWatchdog zaimportowany poprawnie.")


def test_b_unit_urgency_decision():
    """Test B: Test jednostkowy logiki decyzyjnej (Urgent vs Regular vs Skip)."""
    print("\n[TEST B] Klasyfikacja zdarzeń (Urgent vs Regular vs Skip)...")
    tg = MockTelegramNotifier()
    key = "test_team_a_vs_test_team_b"
    now = time.time()

    tg.active_match_cards[key] = {
        "home_team": "Team A",
        "away_team": "Team B",
        "league": "Test League",
        "badge": "OVER 1.5 FT",
        "unit_tag": "2J",
        "initial_odds": 1.50,
        "last_odds": 1.50,
        "initial_minute": 20,
        "last_seen_minute": 20,
        "last_rendered_minute": 20,
        "initial_score": "0:0",
        "last_seen_score": "0:0",
        "last_rendered_score": "0:0",
        "last_seen_half": "1H",
        "last_rendered_stage": "1H",
        "highest_stage_rank": 10,
        "target_goals": 2,
        "target_period": "FT",
        "last_edit_time": now - 10,  # 10s temu
        "last_text": "old_text",
        "device_messages": {"123": 1}
    }

    # 1. Zwykła zmiana minuty po zaledwie 10s (powinna zostać zignorowana przez rate limit 35s)
    snapshot_min_fast = {
        "home_team": "Team A", "away_team": "Team B",
        "minute": 21, "score_str": "0:0", "half": "1H",
        "stage_text": "21'", "is_live": True
    }
    res = tg.check_and_update_match_status(snapshot_min_fast, card_key=key)
    assert res is False, "Zwykła minuta po 10s nie powinna wywołać edycji (rate limit)"
    assert len(tg.edit_calls) == 0

    # 2. GOL (powinien wywołać edycję NATYCHMIAST bez względu na czas)
    snapshot_goal = {
        "home_team": "Team A", "away_team": "Team B",
        "minute": 21, "score_str": "1:0", "half": "1H",
        "stage_text": "21'", "is_live": True
    }
    res_goal = tg.check_and_update_match_status(snapshot_goal, card_key=key)
    assert res_goal is True, "Gol musi wywołać edycję natychmiast"
    assert len(tg.edit_calls) == 1
    assert "[1:0]" in tg.edit_calls[-1]["text"]
    assert tg.active_match_cards[key]["last_rendered_score"] == "1:0"

    # 3. Skok kursu < 0.06 (np. 1.50 -> 1.53) - szum rynkowy (powinien zostać zignorowany)
    snapshot_small_odds = {
        "home_team": "Team A", "away_team": "Team B",
        "minute": 21, "score_str": "1:0", "half": "1H",
        "stage_text": "21'", "is_live": True,
        "live_markets": [{"name": "Over 1.5 FT", "odds": 1.53}]
    }
    res_small = tg.check_and_update_match_status(snapshot_small_odds, card_key=key)
    assert res_small is False, "Mikroskok kursu < 0.06 nie powinien wywołać edycji"

    # 4. Duży skok kursu >= 0.06 (np. 1.50 -> 1.75) - powinien wywołać edycję URGENT
    snapshot_big_odds = {
        "home_team": "Team A", "away_team": "Team B",
        "minute": 21, "score_str": "1:0", "half": "1H",
        "stage_text": "21'", "is_live": True,
        "live_markets": [{"name": "Over 1.5 FT", "odds": 1.75}]
    }
    res_big = tg.check_and_update_match_status(snapshot_big_odds, card_key=key)
    assert res_big is True, "Duży skok kursu >= 0.06 musi wywołać natychmiastową edycję"
    assert "1.75" in tg.edit_calls[-1]["text"]

    print("  -> PASSED: Prawidłowe rozróżnienie zdarzeń pilnych, zwykłych i szumu.")


def test_c_lifecycle_progression():
    """Test C: Test cyklu życia (Lifecycle): 1H -> HT -> 2H -> FT."""
    print("\n[TEST C] Pełny cykl życia karty (1H -> HT -> 2H -> FT)...")
    tg = MockTelegramNotifier()
    key = "real_vs_barca"
    now = time.time()

    tg.active_match_cards[key] = {
        "home_team": "Real", "away_team": "Barca", "league": "La Liga",
        "badge": "OVER 2.5 FT", "unit_tag": "2J", "initial_odds": 1.80, "last_odds": 1.80,
        "initial_minute": 25, "last_seen_minute": 25, "last_rendered_minute": 25,
        "initial_score": "0:0", "last_seen_score": "0:0", "last_rendered_score": "0:0",
        "last_seen_half": "1H", "last_rendered_stage": "1H", "highest_stage_rank": 10,
        "target_goals": 3, "target_period": "FT", "last_edit_time": now - 40,
        "last_text": "start", "device_messages": {"1": 100}
    }

    # Krok 1: Minuta po 40s -> REGULAR update
    snap_min = {"home_team": "Real", "away_team": "Barca", "minute": 26, "score_str": "0:0", "half": "1H", "stage_text": "26'", "is_live": True}
    res = tg.check_and_update_match_status(snap_min, card_key=key)
    assert res is True
    assert "26'" in tg.edit_calls[-1]["text"]

    # Krok 2: Przerwa HT -> URGENT update
    snap_ht = {"home_team": "Real", "away_team": "Barca", "minute": 45, "score_str": "1:0", "half": "HT", "stage_text": "Przerwa", "is_live": True}
    res = tg.check_and_update_match_status(snap_ht, card_key=key)
    assert res is True
    assert "Przerwa" in tg.edit_calls[-1]["text"]
    assert tg.active_match_cards[key]["highest_stage_rank"] == 20

    # Krok 3: Start 2H -> URGENT update
    snap_2h = {"home_team": "Real", "away_team": "Barca", "minute": 46, "score_str": "1:0", "half": "2H", "stage_text": "46'", "is_live": True}
    res = tg.check_and_update_match_status(snap_2h, card_key=key)
    assert res is True
    assert tg.active_match_cards[key]["highest_stage_rank"] == 30

    # Krok 4: Trafienie celu (3 gole -> WON) -> natychmiastowe rozliczenie
    snap_won = {"home_team": "Real", "away_team": "Barca", "minute": 70, "score_str": "2:1", "half": "2H", "stage_text": "70'", "is_live": True}
    res = tg.check_and_update_match_status(snap_won, card_key=key)
    assert res is True
    assert key not in tg.active_match_cards, "Karta po wygranej musi zostać usunięta z aktywnych"
    assert key in tg.settled_matches, "Karta musi znaleźć się w settled_matches"
    assert "WYGRANA" in tg.edit_calls[-1]["text"]

    print("  -> PASSED: Pełny cykl życia 1H -> HT -> 2H -> WON zrealizowany bezbłędnie.")


def test_d_race_condition_protection():
    """Test D: Ochrona przed wyścigiem (Race Condition) przy współbieżnym dostępie."""
    print("\n[TEST D] Test współbieżności i Race Condition...")
    tg = MockTelegramNotifier()
    key = "race_team_a_vs_race_team_b"

    tg.active_match_cards[key] = {
        "home_team": "Team A", "away_team": "Team B", "league": "Liga",
        "badge": "OVER 1.5 FT", "unit_tag": "1J", "initial_odds": 1.50,
        "initial_minute": 88, "last_seen_minute": 88, "initial_score": "0:0",
        "last_seen_score": "0:0", "target_goals": 2, "target_period": "FT",
        "highest_stage_rank": 30, "last_edit_time": time.time() - 50,
        "device_messages": {"1": 1}
    }

    # Dwa wątki symulujące równoległe rozliczenie FT (Watchdog vs FastSettlementWorker)
    results = []
    def worker_settle(worker_id):
        snap_ft = {"home_team": "Team A", "away_team": "Team B", "minute": 90, "score_str": "0:1", "half": "FT", "stage_text": "Koniec", "is_live": False}
        with tg._cards_lock:
            res = tg.check_and_update_match_status(snap_ft, card_key=key)
            results.append((worker_id, res))

    t1 = threading.Thread(target=worker_settle, args=(1,))
    t2 = threading.Thread(target=worker_settle, args=(2,))
    t1.start(); t2.start()
    t1.join(); t2.join()

    # Dokładnie jeden worker powinien rozliczyć, drugi powinien otrzymać False / pominąć
    settled_true_count = sum(1 for wid, res in results if res is True)
    assert settled_true_count == 1, f"Oczekiwano dokładnie 1 rozliczenia, uzyskano: {settled_true_count}"
    assert key not in tg.active_match_cards
    print("  -> PASSED: Brak kolizji (idempotencja i blokada race condition działają w 100%).")


def test_e_network_timeout_isolation():
    """Test E: Izolacja błędów sieciowych i timeoutów."""
    print("\n[TEST E] Test odporności na błędy i timeouty sieciowe...")
    tg = MockTelegramNotifier()
    
    class FailingFS:
        def get_live_soccer_matches(self, include_all_today=False):
            raise TimeoutError("Connection timed out after 3000ms")

    class FailingSTS:
        def fetch_live_matches(self, include_esports=False):
            raise RuntimeError("STS Down")
        def get_match_real_live_markets(self, url):
            raise TimeoutError("DOM Timeout")

    watchdog = ActiveCardsWatchdog(fs_engine=FailingFS(), sts_engine=FailingSTS(), telegram_notifier=tg)
    
    # Dodaj kartę do RAM
    tg.active_match_cards["error_match"] = {
        "home_team": "ErrA", "away_team": "ErrB", "device_messages": {"1": 1}
    }

    # Uruchomienie pojedynczego sprawdzenia z symulowaną awarią obu feedów
    recs = watchdog.check_active_cards_once()
    assert watchdog.counters["feed_errors"] >= 1
    assert len(recs) == 0
    print("  -> PASSED: Awaria sieciowa została bezpiecznie obsłużona, brak unhandled exceptions.")


def test_f_restart_recovery():
    """Test F: Test zachowania po restarcie systemu (Cold Start Recovery)."""
    print("\n[TEST F] Test odzyskiwania stanu po restarcie...")
    tg = MockTelegramNotifier()
    key = "restart_home_vs_restart_away"
    tg.active_match_cards[key] = {
        "home_team": "Home", "away_team": "Away", "league": "Recovery League",
        "badge": "OVER 1.5 FT", "unit_tag": "2J", "initial_odds": 1.60,
        "initial_minute": 50, "last_rendered_minute": 50, "last_seen_minute": 50,
        "initial_score": "0:1", "last_rendered_score": "0:1", "last_seen_score": "0:1",
        "highest_stage_rank": 30, "last_seen_half": "2H", "last_rendered_stage": "2H",
        "target_goals": 2, "target_period": "FT", "device_messages": {"1": 50}
    }

    watchdog = ActiveCardsWatchdog(telegram_notifier=tg)
    # Symulacja pierwszego cyklu po starcie demona
    mock_live = [{
        "home_team": "Home", "away_team": "Away", "minute": 52, "score_str": "0:1",
        "half": "2H", "stage_text": "52'", "is_live": True
    }]
    recs = watchdog.check_active_cards_once(live_matches=mock_live)
    assert key in tg.active_match_cards
    assert tg.active_match_cards[key]["last_seen_minute"] == 52
    print("  -> PASSED: Monitorowanie natychmiast podjęte po starcie.")


def test_g_stale_snapshot_rejection():
    """Test G: Odrzucanie opóźnionych snapshotów (ochrona przed cofaniem stanu)."""
    print("\n[TEST G] Test ochrony przed cofnięciem stanu (Stale Snapshot Rejection)...")
    tg = MockTelegramNotifier()
    key = "stale_home_vs_stale_away"
    tg.active_match_cards[key] = {
        "home_team": "StaleH", "away_team": "StaleA", "league": "Stale L",
        "highest_stage_rank": 30, "last_seen_half": "2H", "last_rendered_stage": "2H",
        "highest_minute": 60, "last_seen_minute": 60, "last_rendered_minute": 60,
        "device_messages": {"1": 1}
    }

    watchdog = ActiveCardsWatchdog(telegram_notifier=tg)

    # Symulacja nadejścia starej odpowiedzi (np. opóźniony pakiet z 1H, 45')
    stale_match = [{
        "home_team": "StaleH", "away_team": "StaleA", "minute": 45, "score_str": "0:0",
        "half": "1H", "stage_text": "45'", "is_live": True
    }]
    watchdog.check_active_cards_once(live_matches=stale_match)

    assert watchdog.counters["stale_snapshots_rejected"] >= 1
    assert tg.active_match_cards[key]["highest_stage_rank"] == 30, "Stage rank nie może spaść z 30 do 10"
    assert tg.active_match_cards[key]["last_seen_minute"] == 60, "Minuta nie może cofnąć się z 60 do 45"
    print("  -> PASSED: Stary snapshot został wykryty i odrzucony.")


def test_h_ft_terminal_lock():
    """Test H: Terminal Lock - po FT żadna późniejsza odpowiedź nie może cofnąć meczu."""
    print("\n[TEST H] Test Terminal Lock (FT = stan ostateczny)...")
    tg = MockTelegramNotifier()
    key = "terminal_home_vs_terminal_away"
    tg.active_match_cards[key] = {
        "home_team": "TermH", "away_team": "TermA", "league": "Term L",
        "badge": "OVER 1.5 FT", "target_goals": 2, "target_period": "FT",
        "initial_score": "0:0", "last_seen_score": "0:0", "highest_stage_rank": 30,
        "device_messages": {"1": 1}
    }

    # 1. Koniec meczu FT -> status LOST
    snap_ft = {"home_team": "TermH", "away_team": "TermA", "minute": 90, "score_str": "0:1", "half": "FT", "stage_text": "Koniec", "is_live": False}
    res = tg.check_and_update_match_status(snap_ft, card_key=key)
    assert res is True
    assert key in tg.settled_matches

    # 2. Próba przesłania rzekomego snapshota "Live 89'" po rozliczeniu FT
    snap_rev = {"home_team": "TermH", "away_team": "TermA", "minute": 89, "score_str": "0:1", "half": "2H", "stage_text": "89'", "is_live": True}
    res_rev = tg.check_and_update_match_status(snap_rev, card_key=key)
    assert res_rev is False, "Rozliczony mecz nie może przyjąć snapshota live"
    assert key not in tg.active_match_cards
    print("  -> PASSED: Terminal lock nienaruszony, mecz zakończony nie może powrócić do gry.")


def test_i_duplicate_edit_prevention():
    """Test I: Ochrona przed niepotrzebnymi edycjami Telegrama (Duplicate Edit Prevention)."""
    print("\n[TEST I] Ochrona przed duplikatami i spamem edycji...")
    tg = MockTelegramNotifier()
    key = "dup_home_vs_dup_away"
    now = time.time()

    tg.active_match_cards[key] = {
        "home_team": "DupH", "away_team": "DupA", "league": "Dup L",
        "badge": "OVER 1.5 FT", "unit_tag": "1J", "initial_odds": 1.50, "last_odds": 1.50,
        "initial_minute": 30, "last_seen_minute": 30, "last_rendered_minute": 30,
        "initial_score": "0:0", "last_seen_score": "0:0", "last_rendered_score": "0:0",
        "last_seen_half": "1H", "last_rendered_stage": "1H", "highest_stage_rank": 10,
        "target_goals": 2, "target_period": "FT", "last_edit_time": now,
        "last_text": "⚽️ <b>DupH vs DupA</b>  <code>[0:0]</code>\n🏆 <b>Liga:</b> Dup L\n⏱️ <b>Czas:</b> 30'\n\n🎯 <code>OVER 1.5 FT</code>\n💰 <b>Stawka:</b> <code>1J</code>\n📈 <b>Kurs:</b> <b>1.50</b>\n🔥 <b>85%</b> (APM: 0.9)",
        "device_messages": {"1": 1}
    }

    # Wywołanie z identycznymi danymi
    snap_same = {"home_team": "DupH", "away_team": "DupA", "minute": 30, "score_str": "0:0", "half": "1H", "stage_text": "30'", "is_live": True}
    res = tg.check_and_update_match_status(snap_same, card_key=key)
    assert res is False, "Brak zmian w meczu nie może powodować zapytania do Telegram API"
    assert len(tg.edit_calls) == 0
    print("  -> PASSED: Zerowa liczba edycji przy braku zmian.")


def test_j_rate_limit_and_odds_hysteresis():
    """Test J: Weryfikacja rate limitu minutowego (35s) i histerezy kursowej (0.06)."""
    print("\n[TEST J] Test histerezy kursowej i rate limitu minut...")
    tg = MockTelegramNotifier()
    key = "hyst_home_vs_hyst_away"
    now = time.time()

    tg.active_match_cards[key] = {
        "home_team": "HystH", "away_team": "HystA", "league": "Hyst L",
        "badge": "OVER 1.5 FT", "unit_tag": "1J", "initial_odds": 1.50, "last_odds": 1.50,
        "last_rendered_odds": 1.50,
        "initial_minute": 60, "last_seen_minute": 60, "last_rendered_minute": 60,
        "initial_score": "0:0", "last_seen_score": "0:0", "last_rendered_score": "0:0",
        "last_seen_half": "2H", "last_rendered_stage": "2H", "highest_stage_rank": 30,
        "target_goals": 2, "target_period": "FT", "last_edit_time": now - 15, # 15s temu
        "last_odds_edit_time": now - 5, # kurs zmieniany 5s temu
        "last_text": "initial",
        "device_messages": {"1": 1}
    }

    # 1. Kurs skacze o 0.08, ale minęło tylko 5s od poprzedniej zmiany kursu -> DEBOUNCE BLOCK
    snap_debounce = {
        "home_team": "HystH", "away_team": "HystA", "minute": 60, "score_str": "0:0",
        "half": "2H", "stage_text": "60'", "is_live": True,
        "live_markets": [{"name": "Over 1.5 FT", "odds": 1.58}]
    }
    res_db = tg.check_and_update_match_status(snap_debounce, card_key=key)
    assert res_db is False, "Debounce kursowy musi zablokować edycję przed upływem 15s"

    # 2. Kurs skacze o 0.08 po upływie 16s -> POWINIEN PRZEJŚĆ
    tg.active_match_cards[key]["last_odds_edit_time"] = now - 16
    res_pass = tg.check_and_update_match_status(snap_debounce, card_key=key)
    assert res_pass is True, "Histereza kursowa >= 0.06 po 16s musi wywołać edycję"
    assert tg.active_match_cards[key]["last_rendered_odds"] == 1.58

    # 3. Minuta 61 po 20s -> ZABLOKOWANA (min. 35s dla zwykłych minut)
    tg.active_match_cards[key]["last_edit_time"] = now - 20
    snap_min_blocked = {
        "home_team": "HystH", "away_team": "HystA", "minute": 61, "score_str": "0:0",
        "half": "2H", "stage_text": "61'", "is_live": True
    }
    res_mb = tg.check_and_update_match_status(snap_min_blocked, card_key=key)
    assert res_mb is False, "Zwykła minuta po 20s musi poczekać do min. 35s"

    # 4. Minuta 61 po 36s -> POWINNA PRZEJŚĆ PŁYNNIE
    tg.active_match_cards[key]["last_edit_time"] = now - 36
    res_mp = tg.check_and_update_match_status(snap_min_blocked, card_key=key)
    assert res_mp is True, "Zwykła minuta po 36s musi przejść płynnie"
    assert tg.active_match_cards[key]["last_rendered_minute"] == 61

    print("  -> PASSED: Histereza kursów (0.06 / 15s) i rate limit minut (35s) działają perfekcyjnie.")


def run_all_tests():
    print("===================================================================")
    print("🚀 ROZPOCZĘCIE KOMPLEKSOWEGO TESTU ACTIVE CARDS WATCHDOG (A -> J)")
    print("===================================================================")
    test_a_structure_and_imports()
    test_b_unit_urgency_decision()
    test_c_lifecycle_progression()
    test_d_race_condition_protection()
    test_e_network_timeout_isolation()
    test_f_restart_recovery()
    test_g_stale_snapshot_rejection()
    test_h_ft_terminal_lock()
    test_i_duplicate_edit_prevention()
    test_j_rate_limit_and_odds_hysteresis()
    print("\n===================================================================")
    print("🏆 WSZYSTKIE 10 TESTÓW (A -> J) ZAKOŃCZONE PEŁNYM SUKCESEM! [10/10]")
    print("===================================================================")

if __name__ == "__main__":
    run_all_tests()
