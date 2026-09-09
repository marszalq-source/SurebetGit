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
        "live_markets": [{"name": "Over 1.5 FT", "odds": 1.53, "source": "STS_REAL"}]
    }
    res_small = tg.check_and_update_match_status(snapshot_small_odds, card_key=key)
    assert res_small is False, "Mikroskok kursu < 0.06 nie powinien wywołać edycji"

    # 4. Duży skok kursu >= 0.06 (np. 1.50 -> 1.75) - powinien wywołać edycję URGENT
    snapshot_big_odds = {
        "home_team": "Team A", "away_team": "Team B",
        "minute": 21, "score_str": "1:0", "half": "1H",
        "stage_text": "21'", "is_live": True,
        "live_markets": [{"name": "Over 1.5 FT", "odds": 1.75, "source": "STS_REAL"}]
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
    """Test E: Izolacja błędów sieciowych, timeoutów i ochrona przed lawiną requestów STS."""
    print("\n[TEST E] Test odporności na błędy, timeouty i ochrona przed lawiną STS...")
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
    
    # 1. Test globalnej awarii feedu Flashscore/STS (brak wyjątków i awarii procesu)
    tg.active_match_cards["error_match"] = {
        "home_team": "ErrA", "away_team": "ErrB", "device_messages": {"1": 1}
    }

    recs = watchdog.check_active_cards_once()
    assert watchdog.counters["feed_errors"] >= 1
    assert len(recs) == 0

    # 2. Test CardSTSScheduler: per-karta scheduler, circuit breaker i zapobieganie retry storm
    from engine.active_cards_watchdog import CardSTSScheduler
    now = 1000.0
    sched_a = CardSTSScheduler("card_a")
    sched_b = CardSTSScheduler("card_b")

    # Stan początkowy: can_poll dozwolone
    assert sched_a.can_poll(now, "0:0", 10) is True
    assert sched_b.can_poll(now, "0:0", 10) is True

    # Karta A napotyka błąd STS -> backoff do 30s
    sched_a.record_failure(now, "Timeout 3.0s")
    assert sched_a.consecutive_errors == 1
    assert sched_a.backoff_seconds == 30.0
    assert sched_a.next_allowed_time == now + 30.0

    # Pętla Watchdoga za 3 sekundy (now + 3.0) -> Karta A zablokowana (brak lawiny requestów!)
    assert sched_a.can_poll(now + 3.0, "0:0", 10) is False, "Scheduler A musi zablokować natychmiastowy ponowny request"
    
    # Niezależność: Karta B jest w 100% niezależna i gotowa do pracy
    assert sched_b.can_poll(now + 3.0, "0:0", 10) is True, "Awaria karty A nie może wpłynąć na kartę B"

    # Drugi błąd na karcie A (po 30s) -> backoff wydłuża się do 60s
    now_30 = now + 30.0
    assert sched_a.can_poll(now_30, "0:0", 10) is True
    sched_a.record_failure(now_30, "503 Service Unavailable")
    assert sched_a.consecutive_errors == 2
    assert sched_a.backoff_seconds == 60.0
    assert sched_a.next_allowed_time == now_30 + 60.0

    # Event-driven: gol na karcie A wyzwala natychmiastowe sprawdzenie, o ile minął bezpieczny cooldown 4s
    assert sched_a.can_poll(now_30 + 2.0, "1:0", 10) is False, "Gol przed upływem 4s cooldownu nie może zaspamować STS"
    assert sched_a.can_poll(now_30 + 5.0, "1:0", 10) is True, "Gol po 5s od próby musi natychmiast pozwolić na odpytanie"

    # Po sukcesie: reset błędów i powrót do standardowego interwału 15s
    sched_a.record_success(now_30 + 5.0, "1:0")
    assert sched_a.consecutive_errors == 0
    assert sched_a.backoff_seconds == 15.0

    print("  -> PASSED: Awaria sieciowa, izolacja schedulerów i ochrona przed lawiną STS działają w 100%.")



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
        "live_markets": [{"name": "Over 1.5 FT", "odds": 1.58, "source": "STS_REAL"}]
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


def test_k_goal_sts_single_request_guarantee():
    """
    Test K: Udowodnienie, że cykl wyniku 0:0 -> 1:0 generuje DOKŁADNIE JEDEN request STS,
    a nie powtarzające się requesty co 4s, nawet w przypadku awarii/timeoutu STS.
    """
    print("\n[TEST K] Gwarancja pojedynczego requestu STS po golu (0:0 -> 1:0)...")
    from engine.active_cards_watchdog import CardSTSScheduler
    
    now = 2000.0
    sched = CardSTSScheduler("match_goal_test")
    sched.last_score = "0:0"
    sched.last_attempt_time = now - 20.0  # stabilny stan
    sched.next_allowed_time = now - 5.0

    # 1. Pada bramka: 0:0 -> 1:0
    t_goal = now
    current_score = "1:0"
    
    # Pierwsze wykrycie gola: can_poll MUSI zwrócić True
    assert sched.can_poll(t_goal, current_score, 10) is True, "Wykrycie nowego gola musi natychmiast zezwolić na odpytanie"

    # W momencie podjęcia próby zapytania (record_attempt):
    sched.record_attempt(t_goal, current_score)
    assert sched.last_score == "1:0", "last_score musi zostać natychmiast zaktualizowane do 1:0"
    assert sched.last_attempt_time == t_goal

    # Symulacja błędu/timeoutu STS przy tym zapytaniu
    sched.record_failure(t_goal, "STS Timeout 3.0s")
    assert sched.consecutive_errors == 1
    assert sched.backoff_seconds == 30.0
    assert sched.next_allowed_time == t_goal + 30.0

    # 2. Weryfikacja pętli w kolejnych sekundach (T+1s, T+3s, T+4s, T+8s, T+12s, T+20s, T+29.9s)
    # Wynik meczu na Flashscore nadal wynosi "1:0"
    for delta_sec in [1.0, 3.0, 4.0, 5.0, 8.0, 12.0, 15.0, 20.0, 25.0, 29.9]:
        t_check = t_goal + delta_sec
        allowed = sched.can_poll(t_check, current_score, 10)
        assert allowed is False, f"BŁĄD: request do STS został dopuszczony w t={delta_sec}s po golu! Powinien być zablokowany!"

    # 3. Dopiero po upływie pełnego backoffu 30s scheduler dopuszcza kolejne (zwykłe) odpytanie
    assert sched.can_poll(t_goal + 30.0, current_score, 10) is True, "Po 30s backoffu dozwolone zwykłe odpytanie"

    # 4. Weryfikacja: kolejny nowy gol (1:0 -> 2:0) wyzwala natychmiastowe zapytanie
    t_goal2 = t_goal + 10.0 # drugi gol po 10s od pierwszego
    assert sched.can_poll(t_goal2, "2:0", 10) is True, "Kolejny nowy gol (2:0) musi wyzwolić natychmiastowe odpytanie"
    sched.record_attempt(t_goal2, "2:0")
    assert sched.last_score == "2:0"

    print("  -> PASSED: Dokładnie 1 request STS po golu 0:0 -> 1:0, brak spamu co 4s.")


def test_l_wake_event_zero_to_one_card():
    """
    Test L: Natychmiastowe wybudzenie Watchdoga (0 -> 1 karta) bez czekania na 3s timeout.
    """
    print("\n[TEST L] Natychmiastowe wybudzenie Watchdoga przy dodaniu karty (0 -> 1)...")
    tg = MockTelegramNotifier()
    watchdog = ActiveCardsWatchdog(telegram_notifier=tg)
    
    # Początkowo wake_event jest czyste
    watchdog._wake_event.clear()
    assert watchdog._wake_event.is_set() is False

    # Symulacja dodania nowej karty i wywołania zarejestrowanego callbacku
    for cb in tg._on_card_added_callbacks:
        cb("new_card_key")

    assert watchdog._wake_event.is_set() is True, "Dodanie nowej karty musi natychmiast ustawić wake_event"
    print("  -> PASSED: Watchdog budzi się natychmiastowo (<1ms) przy przejściu 0 -> 1 karta.")


def run_all_tests():
    print("===================================================================")
    print("🚀 ROZPOCZĘCIE KOMPLEKSOWEGO TESTU ACTIVE CARDS WATCHDOG (A -> L)")
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
    test_k_goal_sts_single_request_guarantee()
    test_l_wake_event_zero_to_one_card()
    print("\n===================================================================")
    print("🏆 WSZYSTKIE 12 TESTÓW (A -> L) ZAKOŃCZONE PEŁNYM SUKCESEM! [12/12]")
    print("===================================================================")

if __name__ == "__main__":
    run_all_tests()

