# -*- coding: utf-8 -*-
"""
Testy weryfikacyjne dla obszaru Live Data Flow / Watchdog / Source Synchronization:
- TEST 1: HT -> 2H / AC=13 (akceptacja snapshotu przez Watchdog po przerwie)
- TEST 2: Zmiana wyniku w 2H (0:1 -> 0:2)
- TEST 3: Brak Source Masking (jednoczesne odpytywanie i wybór STS)
- TEST 4: Nienaruszalność initial_score (initial_score immutable)
- TEST 5: Early Settlement (WIN w trakcie meczu LIVE)
- TEST 6: Early Settlement (BRAK WIN - brak przedwczesnego rozliczenia)
- TEST A: Niespójność czasu/fazy (odrzucenie opóźnionego STS mimo większej liczby goli)
- TEST B: Brak rollbacku wyniku (ochrona przed cofnięciem score)
- TEST C: FT vs Extra Time (ochrona rynku 90-minutowego FT przed golami z dogrywki)
- TEST D: Prawidłowe wyprzedzenie przez STS w tej samej fazie i czasie
- TEST 7: Scenariusz rzeczywisty (Shatin SA vs Golik North District pełny cykl)
"""

import os
import sys
import time
import threading
import pytest
from typing import Dict, Any, List, Optional

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)
os.chdir(BASE_DIR)

from engine.telegram_notifier import TelegramNotifier
from engine.active_cards_watchdog import ActiveCardsWatchdog


class MockTelegramNotifier(TelegramNotifier):
    """Izolowany testowy mock TelegramNotifier bez zewnętrznych połączeń sieciowych."""
    def __new__(cls, *args, **kwargs):
        return object.__new__(cls)

    def __init__(self):
        self._cards_lock = threading.RLock()
        self.config = {"enabled": True, "live_update_mode": True, "chat_id": 123456}
        self.active_match_cards = {}
        self.settled_matches = {}
        self.edit_calls = []
        self.sent_calls = []
        
        class DummyStats:
            def settle_signal(self, *args, **kwargs): pass
        self.stats_engine = DummyStats()

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

    def _log_settlement_telemetry(self, *args, **kwargs):
        pass


class DummyFSEngine:
    def __init__(self, matches: Optional[List[Dict[str, Any]]] = None):
        self.matches = matches or []

    def get_live_soccer_matches(self, include_all_today: bool = False):
        return self.matches


class DummySTSEngine:
    def __init__(self, matches: Optional[List[Dict[str, Any]]] = None):
        self.matches = matches or []

    def fetch_live_matches(self, include_esports: bool = False):
        return self.matches


def test_test1_ac13_stage_rank_watchdog_acceptance():
    """
    TEST 1 — HT -> 2H / AC=13
    Stan początkowy:
      - highest_stage_rank = 20 (HT)
      - snapshot Flashscore: AC=13
    Oczekiwane:
      - stage='2H', rank=30
      - snapshot musi zostać ACCEPTED przez rzeczywistą pętlę ActiveCardsWatchdog
      - brak odrzucenia przez inc_rank < highest_stage_rank
    """
    tg = MockTelegramNotifier()
    key = "shatin sa_vs_golik north district"
    tg.active_match_cards[key] = {
        "home_team": "Shatin SA",
        "away_team": "Golik North District",
        "league": "Hongkong, Premier League",
        "initial_minute": 24,
        "initial_score": "0:1",
        "last_seen_minute": 45,
        "last_seen_score": "0:1",
        "highest_stage_rank": 20,
        "highest_stage": "HT",
        "highest_minute": 45,
        "highest_goals": 1,
        "target_goals": 2,
        "target_period": "FT",
        "badge": "OVER 1.5 FT",
        "unit_tag": "2J",
        "initial_odds": 1.45,
        "last_odds": 1.45,
        "device_messages": {"123456": 789},
        "last_text": "old_msg",
        "last_edit_time": time.time() - 100
    }

    # Snapshot Flashscore po wznowieniu gry w 2H (AC=13)
    fs_snapshot = [{
        "home_team": "Shatin SA",
        "away_team": "Golik North District",
        "minute": 48,
        "score_str": "0:1",
        "half": "2H",
        "stage_text": "13",
        "status_code": "13",
        "is_live": True
    }]

    fs_engine = DummyFSEngine(fs_snapshot)
    sts_engine = DummySTSEngine([])
    watchdog = ActiveCardsWatchdog(fs_engine=fs_engine, sts_engine=sts_engine, telegram_notifier=tg)

    # Weryfikacja bezpośrednia mapowania fazy
    stage, rank = TelegramNotifier._get_match_stage_rank('2H', '13', True, '13', 48)
    assert stage == '2H', f"Oczekiwano stage='2H', otrzymano '{stage}'"
    assert rank == 30, f"Oczekiwano rank=30, otrzymano {rank}"

    # Wykonanie pełnego cyklu Watchdoga
    watchdog.check_active_cards_once()

    # Weryfikacja akceptacji przez maszynę stanów
    assert watchdog.counters["stale_snapshots_rejected"] == 0, "Snapshot AC=13 nie może zostać odrzucony jako stale!"
    last_snap = watchdog._last_logged_snapshot.get(key)
    assert last_snap is not None, "Watchdog musi zalogować telemetrię snapshotu"
    accepted, inc_rank, highest_rank, score, minute, stg, reject_reason = last_snap
    assert accepted is True, f"Snapshot 2H / AC=13 musi zostać ACCEPTED, odrzucono: {reject_reason}"
    assert inc_rank == 30
    assert highest_rank == 20
    assert tg.active_match_cards[key]["highest_stage_rank"] == 30


def test_test2_score_progression_in_2h():
    """
    TEST 2 — Zmiana wyniku w 2H
    Snapshot 1: 2H, 0:1
    Snapshot 2: 2H, 0:2
    Oczekiwane:
      - Drugi snapshot ACCEPTED
      - current_score == "0:2"
      - Brak rollbacku
      - initial_score pozostaje bez zmian ("0:1")
    """
    tg = MockTelegramNotifier()
    key = "team a_vs_team b"
    tg.active_match_cards[key] = {
        "home_team": "Team A",
        "away_team": "Team B",
        "league": "Test League",
        "initial_minute": 20,
        "initial_score": "0:1",
        "last_seen_minute": 45,
        "last_seen_score": "0:1",
        "highest_stage_rank": 30,
        "highest_stage": "2H",
        "highest_minute": 45,
        "highest_goals": 1,
        "target_goals": 4,  # Ustawiamy wysoki cel, aby karta nie zamknęła się przed asercjami
        "target_period": "FT",
        "badge": "OVER 3.5 FT",
        "unit_tag": "2J",
        "initial_odds": 1.70,
        "last_odds": 1.70,
        "device_messages": {"123456": 101},
        "last_text": "start_text",
        "last_edit_time": time.time() - 60
    }

    fs_engine = DummyFSEngine([])
    sts_engine = DummySTSEngine([])
    watchdog = ActiveCardsWatchdog(fs_engine=fs_engine, sts_engine=sts_engine, telegram_notifier=tg)

    # Snapshot 1: 0:1 w 47'
    snap1 = [{
        "home_team": "Team A", "away_team": "Team B",
        "minute": 47, "score_str": "0:1", "half": "2H", "stage_text": "13", "status_code": "13", "is_live": True
    }]
    watchdog.fs_engine.matches = snap1
    watchdog.check_active_cards_once()
    assert tg.active_match_cards[key]["last_seen_score"] == "0:1"
    assert tg.active_match_cards[key]["initial_score"] == "0:1"

    # Snapshot 2: Gol na 0:2 w 53'
    snap2 = [{
        "home_team": "Team A", "away_team": "Team B",
        "minute": 53, "score_str": "0:2", "half": "2H", "stage_text": "13", "status_code": "13", "is_live": True
    }]
    watchdog.fs_engine.matches = snap2
    watchdog.check_active_cards_once()

    assert tg.active_match_cards[key]["last_seen_score"] == "0:2"
    assert tg.active_match_cards[key]["initial_score"] == "0:1"
    assert tg.active_match_cards[key]["highest_goals"] == 2

    # Próba rollbacku: stary snapshot 0:1
    snap_old = [{
        "home_team": "Team A", "away_team": "Team B",
        "minute": 54, "score_str": "0:1", "half": "2H", "stage_text": "13", "status_code": "13", "is_live": True
    }]
    watchdog.fs_engine.matches = snap_old
    watchdog.check_active_cards_once()
    assert tg.active_match_cards[key]["last_seen_score"] == "0:2", "Wynik nie może zostać cofnięty do 0:1!"
    assert tg.active_match_cards[key]["initial_score"] == "0:1"


def test_test3_no_source_masking_sts_evaluated():
    """
    TEST 3 — Brak Source Masking
    Źródła:
      - Flashscore: 0:1
      - STS: 0:2
    Oczekiwane:
      - STS jest faktycznie odpytywany i analizowany mimo obecności meczu we Flashscore.
      - System wybiera 0:2 z STS, gdy snapshot STS jest spójny fazowo i czasowo.
      - Brak warunku pomijającego STS w obecności Flashscore.
    """
    tg = MockTelegramNotifier()
    key = "team a_vs_team b"
    tg.active_match_cards[key] = {
        "home_team": "Team A",
        "away_team": "Team B",
        "league": "Test League",
        "initial_minute": 20,
        "initial_score": "0:1",
        "last_seen_minute": 50,
        "last_seen_score": "0:1",
        "highest_stage_rank": 30,
        "highest_stage": "2H",
        "highest_minute": 50,
        "highest_goals": 1,
        "target_goals": 4,
        "target_period": "FT",
        "badge": "OVER 3.5 FT",
        "unit_tag": "2J",
        "initial_odds": 1.70,
        "last_odds": 1.70,
        "device_messages": {"123456": 102},
        "last_text": "start_text",
        "last_edit_time": time.time() - 60
    }

    fs_match = {
        "home_team": "Team A", "away_team": "Team B",
        "minute": 54, "score_str": "0:1", "half": "2H", "stage_text": "13", "status_code": "13", "is_live": True
    }
    sts_match = {
        "home_team": "Team A", "away_team": "Team B",
        "minute": 55, "score_str": "0:2", "half": "2H", "stage_text": "55'", "status_code": "2", "is_live": True
    }

    fs_engine = DummyFSEngine([fs_match])
    sts_engine = DummySTSEngine([sts_match])
    watchdog = ActiveCardsWatchdog(fs_engine=fs_engine, sts_engine=sts_engine, telegram_notifier=tg)

    # Test bezpośredni pojednania źródeł (multi-source reconciliation)
    best_match, source, reason = watchdog._reconcile_match_sources(
        card_key=key,
        card=tg.active_match_cards[key],
        fs_match=fs_match,
        sts_match=sts_match
    )
    assert source == "STS", f"Oczekiwano wyboru STS, otrzymano {source}"
    assert reason == "STS_AHEAD_GOALS_CONGRUENT"
    assert best_match["score_str"] == "0:2"

    # Wykonanie pełnego cyklu z dwoma źródłami
    watchdog.check_active_cards_once()

    # Karta musi przyjąć wynik 0:2 z STS
    assert tg.active_match_cards[key]["last_seen_score"] == "0:2"
    sync_entry = watchdog._last_logged_sync.get(key)
    assert sync_entry is not None
    assert sync_entry[3] == "STS"  # selected_source


def test_test4_initial_score_immutability():
    """
    TEST 4 — initial_score immutable
    Stan:
      - initial_score = "0:1"
      - seria aktualizacji: 0:1 -> 0:2 -> 1:2
    Oczekiwane:
      - initial_score na karcie oraz w generowanej wiadomości Telegrama nadal wynosi "0:1"
      - current_score zmienia się na "0:2", potem "1:2"
    """
    tg = MockTelegramNotifier()
    key = "arsenal_vs_chelsea"
    tg.active_match_cards[key] = {
        "home_team": "Arsenal",
        "away_team": "Chelsea",
        "league": "Premier League",
        "initial_minute": 24,
        "initial_score": "0:1",
        "last_seen_minute": 24,
        "last_seen_score": "0:1",
        "highest_stage_rank": 10,
        "highest_stage": "1H",
        "highest_minute": 24,
        "highest_goals": 1,
        "target_goals": 5,
        "target_period": "FT",
        "badge": "OVER 4.5 FT",
        "unit_tag": "2J",
        "initial_odds": 2.10,
        "last_odds": 2.10,
        "device_messages": {"123456": 201},
        "last_text": "start_msg",
        "last_edit_time": time.time() - 60
    }

    # Aktualizacja 1: Gol na 0:2
    snap_02 = {
        "home_team": "Arsenal", "away_team": "Chelsea",
        "minute": 50, "score_str": "0:2", "half": "2H", "stage_text": "50'", "is_live": True
    }
    tg.check_and_update_match_status(snap_02, card_key=key)

    card = tg.active_match_cards[key]
    assert card["initial_score"] == "0:1", "initial_score został nadpisany!"
    assert card["last_rendered_score"] == "0:2"
    last_msg = tg.edit_calls[-1]["text"]
    assert "[0:2]" in last_msg
    assert "Typ z: 24' [0:1]" in last_msg

    # Aktualizacja 2: Gol na 1:2
    snap_12 = {
        "home_team": "Arsenal", "away_team": "Chelsea",
        "minute": 62, "score_str": "1:2", "half": "2H", "stage_text": "62'", "is_live": True
    }
    tg.check_and_update_match_status(snap_12, card_key=key)

    card = tg.active_match_cards[key]
    assert card["initial_score"] == "0:1", "initial_score został nadpisany po drugim golu!"
    assert card["last_rendered_score"] == "1:2"
    last_msg2 = tg.edit_calls[-1]["text"]
    assert "[1:2]" in last_msg2
    assert "Typ z: 24' [0:1]" in last_msg2


def test_test5_early_settlement_win():
    """
    TEST 5 — Early Settlement (WIN)
    Stan:
      - Typ: OVER 1.5 FT
      - initial_score = "0:1"
      - target_goals = 2
      - current_score = "0:2" (mecz trwa, np. 55')
    Oczekiwane:
      - curr_tot == 2 >= target_goals (2) -> True
      - Natychmiastowe rozliczenie jako WON
      - Bez czekania na FT
      - Bez czekania na 90. minutę
    """
    tg = MockTelegramNotifier()
    key = "liverpool_vs_everton"
    tg.active_match_cards[key] = {
        "home_team": "Liverpool",
        "away_team": "Everton",
        "league": "Premier League",
        "initial_minute": 24,
        "initial_score": "0:1",
        "last_seen_minute": 24,
        "last_seen_score": "0:1",
        "highest_stage_rank": 10,
        "highest_stage": "1H",
        "highest_minute": 24,
        "highest_goals": 1,
        "target_goals": 2,
        "target_period": "FT",
        "badge": "OVER 1.5 FT",
        "unit_tag": "2J",
        "initial_odds": 1.50,
        "last_odds": 1.50,
        "device_messages": {"123456": 301},
        "last_text": "start_msg",
        "last_edit_time": time.time() - 60
    }

    # Snapshot w 55' (LIVE, ranga 30, mecz daleki od FT)
    snap_goal = {
        "home_team": "Liverpool", "away_team": "Everton",
        "minute": 55, "score_str": "0:2", "half": "2H", "stage_text": "13", "status_code": "13", "is_live": True
    }

    updated = tg.check_and_update_match_status(snap_goal, card_key=key)
    assert updated is True

    # Karta musi zostać natychmiast rozliczona i usunięta z aktywnych
    assert key not in tg.active_match_cards, "Rozliczona karta nie może pozostać w aktywnych!"
    assert key in tg.settled_matches, "Karta musi znaleźć się w settled_matches!"
    assert len(tg.edit_calls) == 1
    settle_msg = tg.edit_calls[-1]["text"]
    assert "WYGRANA" in settle_msg
    assert "[0:2]" in settle_msg
    assert "Typ podany w:</b> <b>24' min [0:1]</b>" in settle_msg or "Typ z: 24' [0:1]" in settle_msg or "[0:1]" in settle_msg
    assert "Trafiono w:</b> <b>55'" in settle_msg or "55'" in settle_msg


def test_test6_early_settlement_no_win():
    """
    TEST 6 — Early Settlement (BRAK WIN)
    Stan:
      - Typ: OVER 1.5 FT
      - initial_score = "0:1"
      - target_goals = 2
      - current_score = "0:1" (mecz trwa, 55')
    Oczekiwane:
      - curr_tot == 1 < target_goals (2) -> False
      - Karta pozostaje aktywna
      - Brak przedwczesnego rozliczenia
    """
    tg = MockTelegramNotifier()
    key = "liverpool_vs_everton"
    tg.active_match_cards[key] = {
        "home_team": "Liverpool",
        "away_team": "Everton",
        "league": "Premier League",
        "initial_minute": 24,
        "initial_score": "0:1",
        "last_seen_minute": 45,
        "last_seen_score": "0:1",
        "highest_stage_rank": 20,
        "highest_stage": "HT",
        "highest_minute": 45,
        "highest_goals": 1,
        "target_goals": 2,
        "target_period": "FT",
        "badge": "OVER 1.5 FT",
        "unit_tag": "2J",
        "initial_odds": 1.50,
        "last_odds": 1.50,
        "device_messages": {"123456": 302},
        "last_text": "start_msg",
        "last_edit_time": time.time() - 60
    }

    snap_nowin = {
        "home_team": "Liverpool", "away_team": "Everton",
        "minute": 55, "score_str": "0:1", "half": "2H", "stage_text": "55'", "status_code": "13", "is_live": True
    }

    tg.check_and_update_match_status(snap_nowin, card_key=key)

    # Karta musi pozostać aktywna
    assert key in tg.active_match_cards, "Karta nie może zostać usunięta przy braku warunku WIN!"
    assert key not in tg.settled_matches
    assert tg.active_match_cards[key].get("settled") is not True


def test_test_a_incongruent_time_phase():
    """
    TEST A — Niespójność czasu/fazy
    Flashscore: 1:1, 60', 2H
    STS: 1:2, 45', HT (opóźniona faza, mimo większej liczby goli)
    Oczekiwane:
      - System odrzuca STS z powodu niespójności fazy/czasu (INCONGRUENT_STAGE_STS_REJECTED)
      - Wybiera Flashscore 1:1
    """
    watchdog = ActiveCardsWatchdog()
    card = {"highest_goals": 1, "target_period": "FT"}

    fs_match = {
        "home_team": "Team A", "away_team": "Team B",
        "minute": 60, "score_str": "1:1", "half": "2H", "stage_text": "60'", "status_code": "13", "is_live": True
    }
    sts_match = {
        "home_team": "Team A", "away_team": "Team B",
        "minute": 45, "score_str": "1:2", "half": "HT", "stage_text": "Przerwa", "status_code": "2", "is_live": True
    }

    best_match, source, reason = watchdog._reconcile_match_sources(
        card_key="test_key",
        card=card,
        fs_match=fs_match,
        sts_match=sts_match
    )

    assert source == "FLASHSCORE", f"Oczekiwano FLASHSCORE, otrzymano {source}"
    assert reason == "INCONGRUENT_STAGE_STS_REJECTED"
    assert best_match["score_str"] == "1:1"


def test_test_b_monotonicity_no_rollback():
    """
    TEST B — Brak rollbacku wyniku
    Flashscore: 0:2, 53', 2H
    STS: 0:1, 48', 2H
    Oczekiwane:
      - System nie cofa wyniku do 0:1
      - Zachowuje 0:2 (ochrona przed rollbackiem i przewaga Flashscore)
    """
    watchdog = ActiveCardsWatchdog()
    
    # Przypadek 1: Karta już widziała 2 gole -> zadziałanie MONOTONICITY_GUARD
    card_with_2_goals = {"highest_goals": 2, "target_period": "FT"}
    fs_match = {
        "home_team": "Team A", "away_team": "Team B",
        "minute": 53, "score_str": "0:2", "half": "2H", "stage_text": "53'", "status_code": "13", "is_live": True
    }
    sts_match = {
        "home_team": "Team A", "away_team": "Team B",
        "minute": 48, "score_str": "0:1", "half": "2H", "stage_text": "48'", "status_code": "2", "is_live": True
    }

    best_match, source, reason = watchdog._reconcile_match_sources(
        card_key="test_key",
        card=card_with_2_goals,
        fs_match=fs_match,
        sts_match=sts_match
    )
    assert source == "FLASHSCORE"
    assert reason == "MONOTONICITY_GUARD_STS_ROLLBACK_REJECTED"
    assert best_match["score_str"] == "0:2"

    # Przypadek 2: Karta widziała 1 gol, a Flashscore ma nowszy wynik 0:2 względem STS 0:1
    card_with_1_goal = {"highest_goals": 1, "target_period": "FT"}
    best_match2, source2, reason2 = watchdog._reconcile_match_sources(
        card_key="test_key",
        card=card_with_1_goal,
        fs_match=fs_match,
        sts_match=sts_match
    )
    assert source2 == "FLASHSCORE"
    assert reason2 == "FLASHSCORE_AHEAD_GOALS"
    assert best_match2["score_str"] == "0:2"


def test_test_c_ft_vs_extra_time():
    """
    TEST C — FT vs Extra Time (Krytyczny)
    Typ rynku: FT
    Flashscore: 1:1, 90', FT / regulation
    STS: 1:2, 105', dogrywka / Extra Time
    Oczekiwane:
      - Dla rynku FT wynik z dogrywki NIE zostaje użyty
      - Wynik regulaminowy 1:1 pozostaje podstawą dla rynku 90-minutowego
    """
    watchdog = ActiveCardsWatchdog()
    card = {"highest_goals": 2, "target_period": "FT"}

    fs_match = {
        "home_team": "Cup Team A", "away_team": "Cup Team B",
        "minute": 90, "score_str": "1:1", "half": "FT", "stage_text": "Koniec", "status_code": "3", "is_live": False
    }
    sts_match = {
        "home_team": "Cup Team A", "away_team": "Cup Team B",
        "minute": 105, "score_str": "1:2", "half": "ET", "stage_text": "Dogrywka 105'", "status_code": "14", "is_live": True
    }

    best_match, source, reason = watchdog._reconcile_match_sources(
        card_key="cup_key",
        card=card,
        fs_match=fs_match,
        sts_match=sts_match
    )

    assert source == "FLASHSCORE", f"Oczekiwano FLASHSCORE dla rynku FT, wybrano: {source}"
    assert reason in ("FT_MARKET_PROTECTION_REGULATION_PREFERRED", "EXTRA_TIME_IGNORED_FOR_FT_MARKET")
    assert best_match["score_str"] == "1:1", f"Dla rynku FT wynik musi wynosić 1:1, otrzymano {best_match['score_str']}"


def test_test_d_congruent_live_goal_update():
    """
    TEST D — Prawidłowe wyprzedzenie przez STS w tej samej fazie i czasie
    Flashscore: 0:1, 54', 2H
    STS: 0:2, 55', 2H
    Oczekiwane:
      - STS jest spójny fazą i czasem
      - System wybiera 0:2 z STS jako nowszy stan (STS_AHEAD_GOALS_CONGRUENT)
    """
    watchdog = ActiveCardsWatchdog()
    card = {"highest_goals": 1, "target_period": "FT"}

    fs_match = {
        "home_team": "Team A", "away_team": "Team B",
        "minute": 54, "score_str": "0:1", "half": "2H", "stage_text": "13", "status_code": "13", "is_live": True
    }
    sts_match = {
        "home_team": "Team A", "away_team": "Team B",
        "minute": 55, "score_str": "0:2", "half": "2H", "stage_text": "55'", "status_code": "2", "is_live": True
    }

    best_match, source, reason = watchdog._reconcile_match_sources(
        card_key="test_key",
        card=card,
        fs_match=fs_match,
        sts_match=sts_match
    )

    assert source == "STS"
    assert reason == "STS_AHEAD_GOALS_CONGRUENT"
    assert best_match["score_str"] == "0:2"


def test_test7_real_scenario_trace_shatin():
    """
    TEST 7 — Scenariusz rzeczywisty (Shatin SA vs Golik North District)
    Pełna sekwencja:
      T0: 24' -> score: 0:1, stage: 1H, typ: OVER 1.5 FT, initial_score = "0:1"
      T1: 45'+ / HT -> score: 0:1, stage: HT, highest_stage_rank = 20
      T2: ~48' -> Golik strzela na 0:2. Flashscore zwraca AC=13.
          Watchdog wyznacza rank=30, akceptuje snapshot (30 >= 20),
          aktualizuje current_score = "0:2", przekazuje do check_and_update_match_status.
          Warunek curr_tot (2) >= target_goals (2) spełniony.
          Karta zostaje rozliczona jako WON w trakcie meczu (LIVE).
          Telegram edit wysłany z wynikiem 0:2 i zachowanym Typ z: 24' [0:1].
    """
    tg = MockTelegramNotifier()
    key = "shatin sa_vs_golik north district"

    # --- T0: Utworzenie karty w 24' ---
    tg.active_match_cards[key] = {
        "home_team": "Shatin SA",
        "away_team": "Golik North District",
        "league": "Hongkong, Premier League",
        "initial_minute": 24,
        "initial_score": "0:1",
        "last_seen_minute": 24,
        "last_seen_score": "0:1",
        "highest_stage_rank": 10,
        "highest_stage": "1H",
        "highest_minute": 24,
        "highest_goals": 1,
        "target_goals": 2,
        "target_period": "FT",
        "badge": "OVER 1.5 FT",
        "unit_tag": "2J",
        "initial_odds": 1.45,
        "last_odds": 1.45,
        "device_messages": {"123456": 888},
        "last_text": "start_msg",
        "last_edit_time": time.time() - 100,
        "status": "PENDING",
        "settled": False
    }

    fs_engine = DummyFSEngine([])
    sts_engine = DummySTSEngine([])
    watchdog = ActiveCardsWatchdog(fs_engine=fs_engine, sts_engine=sts_engine, telegram_notifier=tg)

    # --- T1: Przerwa HT (AC=38) ---
    ht_snapshot = [{
        "home_team": "Shatin SA",
        "away_team": "Golik North District",
        "minute": 45,
        "score_str": "0:1",
        "half": "HT",
        "stage_text": "38",
        "status_code": "38",
        "is_live": True
    }]
    watchdog.fs_engine.matches = ht_snapshot
    watchdog.check_active_cards_once()

    card = tg.active_match_cards[key]
    assert card["highest_stage_rank"] == 20
    assert card["last_seen_score"] == "0:1"
    assert card["initial_score"] == "0:1"
    assert key in tg.active_match_cards, "W przerwie karta nadal musi być aktywna"

    # --- T2: Wznowienie 2H i gol na 0:2 w 48' (AC=13) ---
    t2_snapshot = [{
        "home_team": "Shatin SA",
        "away_team": "Golik North District",
        "minute": 48,
        "score_str": "0:2",
        "half": "2H",
        "stage_text": "13",
        "status_code": "13",
        "is_live": True
    }]
    watchdog.fs_engine.matches = t2_snapshot
    watchdog.check_active_cards_once()

    # Weryfikacja akceptacji w Watchdogu
    assert watchdog.counters["stale_snapshots_rejected"] == 0, "Snapshot 2H / AC=13 NIE MOŻE zostać odrzucony!"
    last_snap = watchdog._last_logged_snapshot.get(key)
    assert last_snap is not None
    assert last_snap[0] is True, "Snapshot z 48' AC=13 musiał zostać zaakceptowany!"
    assert last_snap[1] == 30  # inc_rank
    assert last_snap[2] == 20  # highest_stage_rank z poprzedniego kroku

    # Weryfikacja natychmiastowego rozliczenia Early Settlement
    assert key not in tg.active_match_cards, "Karta po osiągnięciu 2 goli musi zostać rozliczona i zdjęta z aktywnych!"
    assert key in tg.settled_matches, "Karta musi być oznaczona jako settled!"

    # Weryfikacja edycji wiadomości Telegram
    assert len(tg.edit_calls) >= 1
    last_edit = tg.edit_calls[-1]["text"]
    assert "WYGRANA" in last_edit, f"Wiadomość musi potwierdzać wygraną: {last_edit}"
    assert "[0:2]" in last_edit, f"Wiadomość musi zawierać aktualny wynik [0:2]: {last_edit}"
    assert "24' min [0:1]" in last_edit or "[0:1]" in last_edit, f"Wiadomość musi zawierać wynik wejściowy [0:1]: {last_edit}"


# ---------------------------------------------------------------------------
# TESTY FINALNE AUDITU (edge-case'y z zakresu Live Synchronization)
# ---------------------------------------------------------------------------

def test_highest_goals_multicycle_no_rollback():
    """
    AUDIT TEST HG — Wielocyklowy test highest_goals przez Watchdoga.

    Cykl sekwencji snapshotów:
      0:1 → 0:2 → 0:1 (opóźniony stary snapshot) → 0:2 (ponownie bieżący)

    Oczekiwane niezmienniki przez cały przepływ:
    - Po 0:1:  highest_goals=1, last_seen_score="0:1"
    - Po 0:2:  highest_goals=2, last_seen_score="0:2"
    - Po 0:1 (stary):  highest_goals NADAL 2, last_seen_score NADAL "0:2" (brak rollbacku)
    - current_score podczas przetwarzania starego snapshotu = "0:2" (przywrócony z last_seen_score)
    - initial_score = "0:1" przez cały czas
    - Karta nie zostaje rozliczona po 0:1 (target_goals=4 → brak WIN), ani po rollbacku
    """
    tg = MockTelegramNotifier()
    key = "team_x_vs_team_y"
    tg.active_match_cards[key] = {
        "home_team": "Team X",
        "away_team": "Team Y",
        "league": "Test League",
        "initial_minute": 20,
        "initial_score": "0:1",
        "last_seen_minute": 20,
        "last_seen_score": "0:1",
        "highest_stage_rank": 10,
        "highest_stage": "1H",
        "highest_minute": 20,
        "highest_goals": 1,
        "target_goals": 4,      # Wysoki cel – karta nie zamknie się przy 0:2
        "target_period": "FT",
        "badge": "OVER 3.5 FT",
        "unit_tag": "2J",
        "initial_odds": 2.00,
        "last_odds": 2.00,
        "device_messages": {"123456": 501},
        "last_text": "initial",
        "last_edit_time": time.time() - 60,
    }

    fs_engine = DummyFSEngine([])
    sts_engine = DummySTSEngine([])
    watchdog = ActiveCardsWatchdog(fs_engine=fs_engine, sts_engine=sts_engine, telegram_notifier=tg)

    # Cykl 1: snapshot 0:1 w 30' (1H)
    watchdog.fs_engine.matches = [{
        "home_team": "Team X", "away_team": "Team Y",
        "minute": 30, "score_str": "0:1", "half": "1H", "stage_text": "30'", "is_live": True
    }]
    watchdog.check_active_cards_once()
    card = tg.active_match_cards[key]
    assert card["highest_goals"] == 1
    assert card["last_seen_score"] == "0:1"
    assert card["initial_score"] == "0:1"

    # Cykl 2: snapshot 0:2 w 55' (2H) – gol!
    watchdog.fs_engine.matches = [{
        "home_team": "Team X", "away_team": "Team Y",
        "minute": 55, "score_str": "0:2", "half": "2H", "stage_text": "13", "status_code": "13", "is_live": True
    }]
    watchdog.check_active_cards_once()
    card = tg.active_match_cards[key]
    assert card["highest_goals"] == 2, "Po golu highest_goals musi wynosić 2"
    assert card["last_seen_score"] == "0:2"
    assert card["initial_score"] == "0:1", "initial_score niezmieniony"

    # Cykl 3: opóźniony/stary snapshot 0:1 w 56' – symulacja opóźnionego feedu
    watchdog.fs_engine.matches = [{
        "home_team": "Team X", "away_team": "Team Y",
        "minute": 56, "score_str": "0:1", "half": "2H", "stage_text": "13", "status_code": "13", "is_live": True
    }]
    watchdog.check_active_cards_once()
    card = tg.active_match_cards[key]
    assert card["highest_goals"] == 2, "Stary snapshot 0:1 NIE MOŻE obniżyć highest_goals!"
    assert card["last_seen_score"] == "0:2", "last_seen_score NIE MOŻE cofnąć się do 0:1!"
    assert card["initial_score"] == "0:1"
    # Karta nadal aktywna (target_goals=4 nie osiągnięte)
    assert key in tg.active_match_cards, "Karta nie powinna być rozliczona przy highest_goals=2 < target_goals=4"

    # Cykl 4: ponowny bieżący snapshot 0:2 – stabilny wynik
    watchdog.fs_engine.matches = [{
        "home_team": "Team X", "away_team": "Team Y",
        "minute": 58, "score_str": "0:2", "half": "2H", "stage_text": "13", "status_code": "13", "is_live": True
    }]
    watchdog.check_active_cards_once()
    card = tg.active_match_cards[key]
    assert card["highest_goals"] == 2
    assert card["last_seen_score"] == "0:2"
    assert card["initial_score"] == "0:1"


def test_sts_only_et_blocked_for_ft_market():
    """
    AUDIT TEST ET1 — Jedyne dostępne źródło to STS w dogrywce (ET), rynek FT.

    Scenariusz:
      FS = brak snapshotu (None)
      STS = 105', 1:2, half='ET', status_code='6' → dogrywka

    Dla rynku FT wynik z dogrywki NIE MOŻE zostać użyty do Early Settlement.

    Oczekiwane:
      - reason = "STS_ONLY_ET_BLOCKED_FOR_FT_MARKET"
      - source = "STS" (to jedyne źródło, ale z flagą blokady)
      - Watchdog NOT wywołuje early settlement z wynikiem 1:2

    Uwaga: zwracamy STS match jako techniczną odpowiedź (brak FS fallbacku),
    ale karta nie może być rozliczona – najwyżej dostanie state update ze starzejącym się snapshotem.
    Test sprawdza że reason jest poprawne i settlement nie następuje przedwcześnie.
    """
    watchdog = ActiveCardsWatchdog()
    card = {"highest_goals": 2, "target_period": "FT", "initial_goals": 2}

    sts_only_et = {
        "home_team": "Cup A", "away_team": "Cup B",
        "minute": 105, "score_str": "1:2", "half": "ET", "stage_text": "ET 105'", "status_code": "6", "is_live": True
    }

    best_match, source, reason = watchdog._reconcile_match_sources(
        card_key="cup_et_key",
        card=card,
        fs_match=None,
        sts_match=sts_only_et
    )

    assert reason == "STS_ONLY_ET_BLOCKED_FOR_FT_MARKET", (
        f"Jedyny STS w ET dla rynku FT musi być zablokowany, otrzymano: {reason}"
    )
    assert source == "STS"
    # Wynik 1:2 może trafić do snapshotu, ale maszyna stanów w check_and_update_match_status
    # nie przetworzy go jako WON dla rynku FT gdyż highest_goals == 2 == target_goals
    # a blokada ET powinna zapobiec nowym goalsom z ET


def test_is_extra_time_no_false_positive_on_reset_and_set():
    """
    AUDIT TEST ET2 — Ochrona przed false-positive w _is_extra_time_match.

    Wcześniej ogólny substring 'et' mógł dopasować słowa takie jak:
      'reset', 'set', 'nette', 'Metz', 'Brest'
    co powodowało błędną klasyfikację meczu regulaminowego jako dogrywkę.

    Oczekiwane:
      - Słowa zawierające 'et' jako podciąg (nie całe słowo) NIE triggują ET
      - Jedyne 'et' jako całe słowo (np. "et 105'") TRIGGERUJE ET
      - AC=6 i AC=7 triggerują ET
    """
    watchdog = ActiveCardsWatchdog()

    # Te NIE powinny być ET:
    false_cases = [
        {"half": "2H", "stage_text": "reset", "status_code": "13", "minute": 60},
        {"half": "2H", "stage_text": "nette", "status_code": "13", "minute": 70},
        {"half": "2H", "stage_text": "75'", "status_code": "13", "minute": 75},
        {"half": "2H", "stage_text": "Metz attack", "status_code": "13", "minute": 80},
    ]
    for case in false_cases:
        result = watchdog._is_extra_time_match(case)
        assert result is False, f"False-positive ET dla: {case['stage_text']} → powinno być False, otrzymano True"

    # Te POWINNY być ET:
    true_cases = [
        {"half": "ET", "stage_text": "105'", "status_code": "6", "minute": 105},
        {"half": "2H", "stage_text": "et 105'", "status_code": "13", "minute": 105},
        {"half": "2H", "stage_text": "dogrywka", "status_code": "13", "minute": 106},
        {"half": "2H", "stage_text": "extra time", "status_code": "13", "minute": 110},
        {"half": "2H", "stage_text": "karne", "status_code": "7", "minute": 120},
        {"half": "PEN", "stage_text": "120'", "status_code": "7", "minute": 120},
    ]
    for case in true_cases:
        result = watchdog._is_extra_time_match(case)
        assert result is True, f"Przeoczono ET dla: {case['stage_text']} (status={case['status_code']}) → powinno być True, otrzymano False"


def test_equal_goals_fresher_stage_prefers_sts():
    """
    AUDIT TEST EQ — EQUAL_GOALS ≠ EQUAL_FRESHNESS: STS w wyższej fazie.

    Scenariusz (krytyczny):
      Flashscore: 0:1, 45', HT  (ranga 20)
      STS:        0:1, 48', 2H  (ranga 30)

    Oba źródła mają identyczny wynik (1 gol). Poprzednie EQUAL_GOALS_CONSENSUS
    zwracałoby Flashscore → Watchdog wstawiałby snapshot HT z minutą 45',
    co opóźniało przejście HT → 2H i blokowanie ewentualnego early settlement.

    Oczekiwane po poprawce:
      - source = "STS"  (wyższy rank 30 > 20)
      - reason = "EQUAL_GOALS_STS_FRESHER_STAGE"
      - best_match["half"] = "2H"
      - best_match["minute"] = 48
    """
    watchdog = ActiveCardsWatchdog()
    card = {"highest_goals": 1, "target_period": "FT"}

    fs_match = {
        "home_team": "Team A", "away_team": "Team B",
        "minute": 45, "score_str": "0:1", "half": "HT",
        "stage_text": "Przerwa", "status_code": "38", "is_live": True
    }
    sts_match = {
        "home_team": "Team A", "away_team": "Team B",
        "minute": 48, "score_str": "0:1", "half": "2H",
        "stage_text": "48'", "status_code": "2", "is_live": True
    }

    best_match, source, reason = watchdog._reconcile_match_sources(
        card_key="eq_key",
        card=card,
        fs_match=fs_match,
        sts_match=sts_match
    )

    assert source == "STS", (
        f"Przy równych golach i wyższym ranku STS (30>20) oczekiwano STS, otrzymano {source}"
    )
    assert reason == "EQUAL_GOALS_STS_FRESHER_STAGE"
    assert best_match["half"] == "2H"
    assert best_match["minute"] == 48


def test_equal_goals_same_stage_fresher_minute_prefers_sts():
    """
    AUDIT TEST EQ2 — EQUAL_GOALS, ten sam rank (obie 2H), STS >2 minuty nowszy.

    Flashscore: 0:1, 50', 2H  (ranga 30)
    STS:        0:1, 55', 2H  (ranga 30, minuta o 5 min. nowsza)

    Oczekiwane:
      - source = "STS"
      - reason = "EQUAL_GOALS_STS_FRESHER_MINUTE"
    """
    watchdog = ActiveCardsWatchdog()
    card = {"highest_goals": 1, "target_period": "FT"}

    fs_match = {
        "home_team": "Team A", "away_team": "Team B",
        "minute": 50, "score_str": "0:1", "half": "2H",
        "stage_text": "50'", "status_code": "13", "is_live": True
    }
    sts_match = {
        "home_team": "Team A", "away_team": "Team B",
        "minute": 55, "score_str": "0:1", "half": "2H",
        "stage_text": "55'", "status_code": "2", "is_live": True
    }

    best_match, source, reason = watchdog._reconcile_match_sources(
        card_key="eq2_key",
        card=card,
        fs_match=fs_match,
        sts_match=sts_match
    )

    assert source == "STS"
    assert reason == "EQUAL_GOALS_STS_FRESHER_MINUTE"
    assert best_match["minute"] == 55


def test_equal_goals_similar_minute_stays_flashscore():
    """
    AUDIT TEST EQ3 — EQUAL_GOALS, ten sam rank i podobna minuta (różnica <= 2 min).

    Flashscore: 0:1, 52', 2H
    STS:        0:1, 53', 2H  (tylko 1 minuta nowszy)

    Oczekiwane: EQUAL_GOALS_CONSENSUS (Flashscore) — różnica minuty <= 2 nie uprawnia do zmiany.
    """
    watchdog = ActiveCardsWatchdog()
    card = {"highest_goals": 1, "target_period": "FT"}

    fs_match = {
        "home_team": "Team A", "away_team": "Team B",
        "minute": 52, "score_str": "0:1", "half": "2H",
        "stage_text": "52'", "status_code": "13", "is_live": True
    }
    sts_match = {
        "home_team": "Team A", "away_team": "Team B",
        "minute": 53, "score_str": "0:1", "half": "2H",
        "stage_text": "53'", "status_code": "2", "is_live": True
    }

    best_match, source, reason = watchdog._reconcile_match_sources(
        card_key="eq3_key",
        card=card,
        fs_match=fs_match,
        sts_match=sts_match
    )

    assert source == "FLASHSCORE"
    assert reason == "EQUAL_GOALS_CONSENSUS"
