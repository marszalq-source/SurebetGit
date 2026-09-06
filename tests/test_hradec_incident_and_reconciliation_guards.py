import sys
import os
sys.path.insert(0, os.path.abspath('.'))

import time
import pytest
from engine.sts_live_engine import STSLiveEngine
from engine.active_cards_watchdog import ActiveCardsWatchdog
from engine.telegram_notifier import TelegramNotifier


class MockTelegramNotifier(TelegramNotifier):
    def __new__(cls, *args, **kwargs):
        return object.__new__(cls)

    def __init__(self):
        import threading
        self._cards_lock = threading.RLock()
        self.config = {"enabled": True, "live_update_mode": True}
        self.active_match_cards = {}
        self.settled_matches = {}
        self.edit_calls = []
        from engine.stats_engine import StatsEngine
        self.stats_engine = StatsEngine()
        class DummyBA:
            def settle_bet_async(self, m, o): pass
        self.ba_sync = DummyBA()

    def edit_message_all(self, dev_msgs, text, parse_mode="HTML"):
        self.edit_calls.append({"dev_msgs": dev_msgs, "text": text})
        return True

    def _save_cards(self):
        pass

    def _log_settlement_telemetry(self, *args, **kwargs):
        pass


# ---------------------------------------------------------------------------
# 1. test_parse_sts_suspended_odds_does_not_create_1_2
# ---------------------------------------------------------------------------
def test_parse_sts_suspended_odds_does_not_create_1_2():
    engine = STSLiveEngine()
    lines = [
        "Piłka nożna",
        "Czechy, 2. liga - kobiety",
        "LIVE",
        "FC Hradec Kralove [K]",
        "FK Pardubice [K]",
        "2.połowa",
        "1",
        "X",
        "2",
        "Filtruj"
    ]
    matches = engine._parse_sts_lines(lines)
    assert len(matches) == 1
    m = matches[0]
    assert m["home_team"] == "FC Hradec Kralove [K]"
    assert m["away_team"] == "FK Pardubice [K]"
    assert m["score_str"] != "1:2"
    assert m["score_str"] is None


# ---------------------------------------------------------------------------
# 2. test_parse_sts_single_digit_labels_not_score
# ---------------------------------------------------------------------------
def test_parse_sts_single_digit_labels_not_score():
    engine = STSLiveEngine()
    lines = [
        "Piłka nożna",
        "Liga Testowa",
        "LIVE",
        "Team Alpha",
        "Team Beta",
        "1.połowa",
        "3",
        "5",
        "1",
        "2",
        "Filtruj"
    ]
    matches = engine._parse_sts_lines(lines)
    assert len(matches) == 1
    m = matches[0]
    # Pojedyncze cyfry w bloku nie mogą być sklejane jako wynik (np. 1:2 czy 3:5)
    assert m["score_str"] is None
    assert m["home_score"] is None
    assert m["away_score"] is None


# ---------------------------------------------------------------------------
# 3. test_live_settlement_requires_reconciliation
# ---------------------------------------------------------------------------
def test_live_settlement_requires_reconciliation():
    tg = MockTelegramNotifier()
    key = "hradec_vs_pardubice"
    tg.active_match_cards[key] = {
        "home_team": "FC Hradec Kralove [K]",
        "away_team": "FK Pardubice [K]",
        "league": "Czechy",
        "badge": "OVER 1.5 FT",
        "target_goals": 2,
        "target_period": "FT",
        "initial_score": "0:0",
        "last_seen_score": "0:0",
        "highest_goals": 0,
        "status": "PENDING",
        "settled": False
    }

    raw_unreconciled_live = [{
        "home_team": "FC Hradec Kralove [K]",
        "away_team": "FK Pardubice [K]",
        "score_str": "1:2",
        "minute": 85,
        "half": "2H",
        "stage_text": "85'",
        "is_live": True,
    }]

    settled_cnt = tg.auto_settle_active_cards(live_matches=raw_unreconciled_live, finished_matches=[])
    assert settled_cnt == 0
    assert key in tg.active_match_cards
    assert not tg.active_match_cards[key].get("settled")


# ---------------------------------------------------------------------------
# 4. test_live_sts_only_cannot_settle_without_validation
# ---------------------------------------------------------------------------
def test_live_sts_only_cannot_settle_without_validation():
    tg = MockTelegramNotifier()
    key = "solo_sts_match"
    tg.active_match_cards[key] = {
        "home_team": "Team STS",
        "away_team": "Team Guest",
        "league": "Test League",
        "badge": "OVER 1.5 FT",
        "target_goals": 2,
        "target_period": "FT",
        "initial_score": "0:0",
        "last_seen_score": "0:0",
        "highest_goals": 0,
        "status": "PENDING",
        "settled": False
    }

    watchdog = ActiveCardsWatchdog(telegram_notifier=tg)
    card = tg.active_match_cards[key]
    sts_match = {
        "home_team": "Team STS",
        "away_team": "Team Guest",
        "score_str": "1:2",
        "minute": 60,
        "half": "2H",
        "stage_text": "60'",
        "is_live": True
    }

    best_match, source, reason = watchdog._reconcile_match_sources(
        card_key=key,
        card=card,
        fs_match=None,
        sts_match=sts_match
    )

    assert source == "STS"
    assert best_match.get("_sts_only") is True
    assert best_match.get("_canonical_verified") is not True

    updated = tg.check_and_update_match_status(best_match, card_key=key)
    assert key in tg.active_match_cards
    assert not tg.active_match_cards[key].get("settled")


# ---------------------------------------------------------------------------
# 5. test_score_jump_0_0_to_1_2_unverified_is_rejected
# ---------------------------------------------------------------------------
def test_score_jump_0_0_to_1_2_unverified_is_rejected():
    tg = MockTelegramNotifier()
    key = "hradec_jump_test"
    tg.active_match_cards[key] = {
        "home_team": "FC Hradec Kralove [K]",
        "away_team": "FK Pardubice [K]",
        "league": "Czechy",
        "badge": "OVER 1.5 FT",
        "target_goals": 2,
        "target_period": "FT",
        "initial_score": "0:0",
        "last_seen_score": "0:0",
        "highest_goals": 0,
        "status": "PENDING",
        "settled": False
    }

    watchdog = ActiveCardsWatchdog(telegram_notifier=tg)
    card = tg.active_match_cards[key]

    fs_match = {
        "home_team": "FC Hradec Kralove [K]",
        "away_team": "FK Pardubice [K]",
        "score_str": "0:0",
        "minute": 85,
        "half": "2H",
        "stage_text": "85'",
        "is_live": True,
        "status_code": "13"
    }

    sts_match = {
        "home_team": "FC Hradec Kralove [K]",
        "away_team": "FK Pardubice [K]",
        "score_str": "1:2",
        "minute": 85,
        "half": "2H",
        "stage_text": "85'",
        "is_live": True
    }

    best_match, source, reason = watchdog._reconcile_match_sources(
        card_key=key,
        card=card,
        fs_match=fs_match,
        sts_match=sts_match
    )

    assert source == "FLASHSCORE"
    assert reason == "SCORE_JUMP_STS_UNVERIFIED_REJECTED"
    assert best_match["score_str"] == "0:0"


# ---------------------------------------------------------------------------
# 6. test_score_jump_0_0_to_1_2_confirmed_by_both_sources_is_accepted
# ---------------------------------------------------------------------------
def test_score_jump_0_0_to_1_2_confirmed_by_both_sources_is_accepted():
    tg = MockTelegramNotifier()
    key = "both_confirmed_jump"
    tg.active_match_cards[key] = {
        "home_team": "Team A",
        "away_team": "Team B",
        "league": "League",
        "badge": "OVER 1.5 FT",
        "target_goals": 2,
        "target_period": "FT",
        "initial_score": "0:0",
        "last_seen_score": "0:0",
        "highest_goals": 0,
        "status": "PENDING",
        "settled": False,
        "device_messages": {"1": 10}
    }

    watchdog = ActiveCardsWatchdog(telegram_notifier=tg)
    card = tg.active_match_cards[key]

    fs_match = {
        "home_team": "Team A",
        "away_team": "Team B",
        "score_str": "1:2",
        "minute": 70,
        "half": "2H",
        "stage_text": "70'",
        "is_live": True,
        "status_code": "13"
    }

    sts_match = {
        "home_team": "Team A",
        "away_team": "Team B",
        "score_str": "1:2",
        "minute": 70,
        "half": "2H",
        "stage_text": "70'",
        "is_live": True
    }

    best_match, source, reason = watchdog._reconcile_match_sources(
        card_key=key,
        card=card,
        fs_match=fs_match,
        sts_match=sts_match
    )

    assert best_match["score_str"] == "1:2"
    assert best_match.get("_canonical_verified") is True

    res = tg.check_and_update_match_status(best_match, card_key=key)
    assert res is True
    assert key not in tg.active_match_cards
    assert key in tg.settled_matches
    assert "WYGRANA" in tg.edit_calls[-1]["text"]


# ---------------------------------------------------------------------------
# 7. test_highest_goals_not_incremented_by_rejected_snapshot
# ---------------------------------------------------------------------------
def test_highest_goals_not_incremented_by_rejected_snapshot():
    tg = MockTelegramNotifier()
    key = "hg_protect_key"
    tg.active_match_cards[key] = {
        "home_team": "Team A",
        "away_team": "Team B",
        "league": "League",
        "badge": "OVER 1.5 FT",
        "target_goals": 2,
        "target_period": "FT",
        "initial_score": "0:0",
        "last_seen_score": "0:0",
        "highest_goals": 0,
        "status": "PENDING",
        "settled": False
    }

    unverified_snap = {
        "home_team": "Team A",
        "away_team": "Team B",
        "score_str": "1:2",
        "minute": 60,
        "half": "2H",
        "stage_text": "60'",
        "is_live": True,
        "_unverified_score_jump": True
    }

    tg.check_and_update_match_status(unverified_snap, card_key=key)
    card = tg.active_match_cards[key]
    assert card["highest_goals"] == 0
    assert card["last_seen_score"] == "0:0"


# ---------------------------------------------------------------------------
# 8. test_telegram_uses_canonical_current_score
# ---------------------------------------------------------------------------
def test_telegram_uses_canonical_current_score():
    tg = MockTelegramNotifier()
    key = "tg_canonical_key"
    tg.active_match_cards[key] = {
        "home_team": "Team A",
        "away_team": "Team B",
        "league": "League",
        "badge": "OVER 1.5 FT",
        "target_goals": 2,
        "target_period": "FT",
        "initial_score": "0:0",
        "last_seen_score": "0:0",
        "last_rendered_score": "0:0",
        "last_rendered_minute": 50,
        "highest_goals": 0,
        "status": "PENDING",
        "settled": False,
        "last_edit_time": time.time() - 100,
        "device_messages": {"1": 1}
    }

    unverified_snap = {
        "home_team": "Team A",
        "away_team": "Team B",
        "score_str": "1:2",
        "minute": 55,
        "half": "2H",
        "stage_text": "55'",
        "is_live": True,
        "_unverified_score_jump": True
    }

    tg.check_and_update_match_status(unverified_snap, card_key=key)
    for call in tg.edit_calls:
        assert "[1:2]" not in call["text"]
        assert "[0:0]" in call["text"]


# ---------------------------------------------------------------------------
# 9. test_live_disappearance_is_not_ft
# ---------------------------------------------------------------------------
def test_live_disappearance_is_not_ft():
    tg = MockTelegramNotifier()
    key = "disappearing_match"
    now = time.time()
    tg.active_match_cards[key] = {
        "home_team": "Team Disappear",
        "away_team": "Team Still There",
        "league": "League",
        "badge": "OVER 2.5 FT",
        "target_goals": 3,
        "target_period": "FT",
        "initial_score": "0:1",
        "last_seen_score": "0:1",
        "highest_goals": 1,
        "initial_minute": 88,
        "last_seen_minute": 88,
        "last_seen_time": now - 300,
        "created_at": now - 6000,
        "status": "PENDING",
        "settled": False
    }

    settled_cnt = tg.auto_settle_active_cards(live_matches=[], finished_matches=[])
    assert settled_cnt == 0
    assert key in tg.active_match_cards
    assert not tg.active_match_cards[key].get("settled")


# ---------------------------------------------------------------------------
# 10. test_ft_confirmed_snapshot_can_settle
# ---------------------------------------------------------------------------
def test_ft_confirmed_snapshot_can_settle():
    tg = MockTelegramNotifier()
    key = "ft_confirmed_match"
    now = time.time()
    tg.active_match_cards[key] = {
        "home_team": "Team End1",
        "away_team": "Team End2",
        "league": "League",
        "badge": "OVER 2.5 FT",
        "target_goals": 3,
        "target_period": "FT",
        "initial_score": "0:1",
        "last_seen_score": "0:1",
        "highest_goals": 1,
        "initial_minute": 50,
        "last_seen_minute": 90,
        "created_at": now - 3600,
        "status": "PENDING",
        "settled": False,
        "device_messages": {"1": 200}
    }

    fin_matches = [{
        "home_team": "Team End1",
        "away_team": "Team End2",
        "score_str": "0:1",
        "minute": 90,
        "half": "FT",
        "stage_text": "Koniec meczu",
        "is_live": False,
        "status_code": "3"
    }]

    settled_cnt = tg.auto_settle_active_cards(live_matches=[], finished_matches=fin_matches)
    assert settled_cnt == 1
    assert key not in tg.active_match_cards
    assert key in tg.settled_matches
    assert "PRZEGRANA" in tg.edit_calls[-1]["text"]


# ---------------------------------------------------------------------------
# 11. test_initial_score_remains_immutable
# ---------------------------------------------------------------------------
def test_initial_score_remains_immutable():
    tg = MockTelegramNotifier()
    key = "immutability_key"
    tg.active_match_cards[key] = {
        "home_team": "Team Immut",
        "away_team": "Team Stable",
        "league": "League",
        "badge": "OVER 2.5 FT",
        "target_goals": 3,
        "target_period": "FT",
        "initial_score": "0:0",
        "last_seen_score": "0:0",
        "highest_goals": 0,
        "status": "PENDING",
        "settled": False,
        "device_messages": {"1": 100}
    }

    tg.check_and_update_match_status({
        "home_team": "Team Immut", "away_team": "Team Stable",
        "score_str": "1:0", "minute": 30, "half": "1H", "is_live": True
    }, card_key=key)
    assert tg.active_match_cards[key]["initial_score"] == "0:0"

    tg.check_and_update_match_status({
        "home_team": "Team Immut", "away_team": "Team Stable",
        "score_str": "1:1", "minute": 50, "half": "2H", "is_live": True
    }, card_key=key)
    assert tg.active_match_cards[key]["initial_score"] == "0:0"


# ---------------------------------------------------------------------------
# 12. test_real_shatin_early_settlement_still_works
# ---------------------------------------------------------------------------
def test_real_shatin_early_settlement_still_works():
    tg = MockTelegramNotifier()
    key = "shatin_sa_vs_golik_north_district"
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
        "status": "PENDING",
        "settled": False
    }

    watchdog = ActiveCardsWatchdog(telegram_notifier=tg)
    card = tg.active_match_cards[key]

    fs_snap = {
        "home_team": "Shatin SA",
        "away_team": "Golik North District",
        "score_str": "0:2",
        "minute": 71,
        "half": "2H",
        "stage_text": "13",
        "status_code": "13",
        "is_live": True
    }

    sts_snap = {
        "home_team": "Shatin SA",
        "away_team": "Golik North District",
        "score_str": "0:2",
        "minute": 71,
        "half": "2H",
        "stage_text": "71'",
        "is_live": True
    }

    best_match, source, reason = watchdog._reconcile_match_sources(
        card_key=key,
        card=card,
        fs_match=fs_snap,
        sts_match=sts_snap
    )

    assert best_match["score_str"] == "0:2"
    assert best_match.get("_canonical_verified") is True

    updated = tg.check_and_update_match_status(best_match, card_key=key)
    assert updated is True
    assert key not in tg.active_match_cards
    assert key in tg.settled_matches
    assert "WYGRANA" in tg.edit_calls[-1]["text"]


# ---------------------------------------------------------------------------
# 13. test_sts_only_step_by_step_updates_telegram_but_blocks_early_settle
# ---------------------------------------------------------------------------
def test_sts_only_step_by_step_updates_telegram_but_blocks_early_settle():
    """
    Test A:
    STS-only: 0:0 -> 0:1 -> 0:2
    OVER 1.5 FT, target_goals=2
    Oczekiwane:
    - 0:1 może aktualizować Telegram
    - 0:2 może aktualizować Telegram
    - highest_goals może dojść do 2
    - Early Settlement MUSI pozostać zablokowany
    - karta pozostaje PENDING
    """
    tg = MockTelegramNotifier()
    key = "solo_step_match"
    now = time.time()
    tg.active_match_cards[key] = {
        "home_team": "Team A",
        "away_team": "Team B",
        "league": "Test League",
        "badge": "OVER 1.5 FT",
        "target_goals": 2,
        "target_period": "FT",
        "initial_score": "0:0",
        "initial_minute": 10,
        "last_seen_score": "0:0",
        "last_seen_minute": 10,
        "highest_goals": 0,
        "last_rendered_score": "0:0",
        "last_rendered_minute": 10,
        "last_rendered_stage": "1H",
        "last_edit_time": now - 100,
        "device_messages": {"123": 456},
        "last_text": "initial_text",
        "status": "PENDING",
        "settled": False
    }

    watchdog = ActiveCardsWatchdog(telegram_notifier=tg)
    card = tg.active_match_cards[key]

    # Krok 1: 0:0 -> 0:1 w 30' (pojedynczy gol, brak jump anomaly)
    sts_1 = {
        "home_team": "Team A",
        "away_team": "Team B",
        "score_str": "0:1",
        "minute": 30,
        "half": "1H",
        "stage_text": "30'",
        "is_live": True
    }
    m1, src1, reason1 = watchdog._reconcile_match_sources(key, card, fs_match=None, sts_match=sts_1)
    assert src1 == "STS"
    assert m1.get("_sts_only") is True
    assert m1.get("_canonical_verified") is False
    assert m1.get("_unverified_score_jump") is not True

    # Check update
    tg.check_and_update_match_status(m1, card_key=key)
    assert card["highest_goals"] == 1
    assert card["last_seen_score"] == "0:1"
    assert not card.get("settled")
    assert card["status"] == "PENDING"
    assert key in tg.active_match_cards
    assert any("[0:1]" in call["text"] for call in tg.edit_calls)

    # Krok 2: 0:1 -> 0:2 w 55' (kolejny pojedynczy gol, target_goals=2 osiągnięte)
    card["last_edit_time"] = now - 100
    sts_2 = {
        "home_team": "Team A",
        "away_team": "Team B",
        "score_str": "0:2",
        "minute": 55,
        "half": "2H",
        "stage_text": "55'",
        "is_live": True
    }
    m2, src2, reason2 = watchdog._reconcile_match_sources(key, card, fs_match=None, sts_match=sts_2)
    assert src2 == "STS"
    assert m2.get("_sts_only") is True
    assert m2.get("_canonical_verified") is False
    assert m2.get("_unverified_score_jump") is not True

    tg.check_and_update_match_status(m2, card_key=key)
    assert card["highest_goals"] == 2
    assert card["last_seen_score"] == "0:2"
    # Early Settlement MUSI pozostać zablokowany!
    assert not card.get("settled")
    assert card["status"] == "PENDING"
    assert key in tg.active_match_cards
    assert key not in tg.settled_matches
    assert any("[0:2]" in call["text"] for call in tg.edit_calls)


# ---------------------------------------------------------------------------
# 14. test_sts_only_confirmed_later_by_flashscore_triggers_early_settlement
# ---------------------------------------------------------------------------
def test_sts_only_confirmed_later_by_flashscore_triggers_early_settlement():
    """
    Test B:
    STS-only: 0:0 -> 0:1 -> 0:2
    następnie Flashscore pojawia się z 0:2
    Oczekiwane:
    - reconciliation potwierdza 0:2
    - _canonical_verified=True
    - _reconciled=True
    - jeśli mecz nadal LIVE i target został osiągnięty, Early Settlement może nastąpić natychmiast
    - initial_score pozostaje niezmieniony
    """
    tg = MockTelegramNotifier()
    key = "delayed_fs_match"
    now = time.time()
    tg.active_match_cards[key] = {
        "home_team": "Team A",
        "away_team": "Team B",
        "league": "Test League",
        "badge": "OVER 1.5 FT",
        "target_goals": 2,
        "target_period": "FT",
        "initial_score": "0:0",
        "initial_minute": 15,
        "last_seen_score": "0:0",
        "last_seen_minute": 15,
        "highest_goals": 0,
        "last_rendered_score": "0:0",
        "last_rendered_minute": 15,
        "last_rendered_stage": "1H",
        "last_edit_time": now - 100,
        "device_messages": {"123": 456},
        "last_text": "init",
        "status": "PENDING",
        "settled": False
    }

    watchdog = ActiveCardsWatchdog(telegram_notifier=tg)
    card = tg.active_match_cards[key]

    # STS prowadzi 0:1
    sts_1 = {"home_team": "Team A", "away_team": "Team B", "score_str": "0:1", "minute": 30, "half": "1H", "stage_text": "30'", "is_live": True}
    m1, _, _ = watchdog._reconcile_match_sources(key, card, fs_match=None, sts_match=sts_1)
    tg.check_and_update_match_status(m1, card_key=key)

    # STS prowadzi 0:2 (Early Settle zablokowane)
    card["last_edit_time"] = now - 100
    sts_2 = {"home_team": "Team A", "away_team": "Team B", "score_str": "0:2", "minute": 55, "half": "2H", "stage_text": "55'", "is_live": True}
    m2, _, _ = watchdog._reconcile_match_sources(key, card, fs_match=None, sts_match=sts_2)
    tg.check_and_update_match_status(m2, card_key=key)
    assert not card.get("settled")

    # Flashscore pojawia się z 0:2
    fs_2 = {"home_team": "Team A", "away_team": "Team B", "score_str": "0:2", "minute": 56, "half": "2H", "stage_text": "13", "status_code": "13", "is_live": True}
    m_confirmed, src, reason = watchdog._reconcile_match_sources(key, card, fs_match=fs_2, sts_match=sts_2)
    assert m_confirmed.get("_canonical_verified") is True
    assert m_confirmed.get("_reconciled") is True
    assert m_confirmed["score_str"] == "0:2"

    settled_ok = tg.check_and_update_match_status(m_confirmed, card_key=key)
    assert settled_ok is True
    assert key not in tg.active_match_cards
    assert key in tg.settled_matches
    assert card["initial_score"] == "0:0"
    assert "WYGRANA" in tg.edit_calls[-1]["text"]


# ---------------------------------------------------------------------------
# 15. test_ac_10_after_extra_time_is_confirmed_ft_and_not_void
# ---------------------------------------------------------------------------
def test_ac_10_after_extra_time_is_confirmed_ft_and_not_void():
    """
    Test C:
    AC=10: status_code=10, stage_text odpowiadający After Extra Time / Po dogr.
    Oczekiwane:
    - finished=True
    - is_live=False
    - VOID=False
    - status może zostać rozliczony jako wynik końcowy
    """
    tg = MockTelegramNotifier()
    key = "cup_match_aet"
    now = time.time()
    tg.active_match_cards[key] = {
        "home_team": "Club A",
        "away_team": "Club B",
        "league": "Cup",
        "badge": "OVER 1.5 FT",
        "target_goals": 2,
        "target_period": "FT",
        "initial_score": "0:0",
        "initial_minute": 20,
        "last_seen_score": "1:1",
        "last_seen_minute": 90,
        "highest_goals": 2,
        "last_rendered_score": "1:1",
        "device_messages": {"123": 456},
        "last_text": "old",
        "status": "PENDING",
        "settled": False
    }

    stage, rank = tg._get_match_stage_rank(
        half="FT",
        stage_text="Po dogr.",
        is_live=False,
        status_code="10",
        minute=120
    )
    assert rank == 40
    assert stage == "FT"

    fin_match = {
        "home_team": "Club A",
        "away_team": "Club B",
        "score_str": "2:1",
        "minute": 120,
        "half": "FT",
        "stage_text": "Po dogr.",
        "status_code": "10",
        "is_live": False
    }

    settled_cnt = tg.auto_settle_active_cards(live_matches=[], finished_matches=[fin_match])
    assert settled_cnt == 1
    assert key not in tg.active_match_cards
    assert key in tg.settled_matches
    last_msg = tg.edit_calls[-1]["text"]
    assert "WYGRANA" in last_msg
    assert "ZWROT" not in last_msg
    assert "VOID" not in last_msg


# ---------------------------------------------------------------------------
# 16. test_ac_11_after_penalties_is_confirmed_ft_and_not_void
# ---------------------------------------------------------------------------
def test_ac_11_after_penalties_is_confirmed_ft_and_not_void():
    """
    Test D:
    AC=11: status_code=11, stage_text odpowiadający After Penalties / Po karnych
    Oczekiwane:
    - finished=True
    - is_live=False
    - VOID=False
    """
    tg = MockTelegramNotifier()
    key = "cup_match_pen"
    now = time.time()
    tg.active_match_cards[key] = {
        "home_team": "Club C",
        "away_team": "Club D",
        "league": "Cup",
        "badge": "OVER 2.5 FT",
        "target_goals": 3,
        "target_period": "FT",
        "initial_score": "0:0",
        "initial_minute": 20,
        "last_seen_score": "1:1",
        "last_seen_minute": 90,
        "highest_goals": 2,
        "last_rendered_score": "1:1",
        "device_messages": {"123": 456},
        "last_text": "old",
        "status": "PENDING",
        "settled": False
    }

    stage, rank = tg._get_match_stage_rank(
        half="FT",
        stage_text="Po karnych",
        is_live=False,
        status_code="11",
        minute=120
    )
    assert rank == 40
    assert stage == "FT"

    fin_match = {
        "home_team": "Club C",
        "away_team": "Club D",
        "score_str": "1:1",
        "minute": 120,
        "half": "FT",
        "stage_text": "Po karnych",
        "status_code": "11",
        "is_live": False
    }

    settled_cnt = tg.auto_settle_active_cards(live_matches=[], finished_matches=[fin_match])
    assert settled_cnt == 1
    assert key not in tg.active_match_cards
    assert key in tg.settled_matches
    last_msg = tg.edit_calls[-1]["text"]
    assert "PRZEGRANA" in last_msg or "STATUS:</b> <b>PRZEGRANA" in last_msg or "🔴" in last_msg
    assert "ZWROT" not in last_msg
    assert "VOID" not in last_msg


# ---------------------------------------------------------------------------
# 17. test_ac_36_interrupted_routes_to_void_and_cannot_settle_normal_ft
# ---------------------------------------------------------------------------
def test_ac_36_interrupted_routes_to_void_and_cannot_settle_normal_ft():
    """
    Test E:
    AC=36: status_code=36, stage_text=Przerwany
    Oczekiwane:
    - nie jest traktowany jako normalny FT
    - przechodzi do odpowiedniej ścieżki VOID/przerwania
    - nie może wygenerować fałszywego WON/LOST jako zwykły FT
    """
    tg = MockTelegramNotifier()
    key = "interrupted_match"
    now = time.time()
    tg.active_match_cards[key] = {
        "home_team": "Team Interrupted 1",
        "away_team": "Team Interrupted 2",
        "league": "League",
        "badge": "OVER 1.5 FT",
        "target_goals": 2,
        "target_period": "FT",
        "initial_score": "0:0",
        "initial_minute": 20,
        "last_seen_score": "0:0",
        "last_seen_minute": 35,
        "highest_goals": 0,
        "last_rendered_score": "0:0",
        "device_messages": {"123": 456},
        "last_text": "old",
        "status": "PENDING",
        "settled": False
    }

    stage, rank = tg._get_match_stage_rank(
        half="1H",
        stage_text="Przerwany",
        is_live=False,
        status_code="36",
        minute=35
    )
    assert rank == 40

    interrupted_match = {
        "home_team": "Team Interrupted 1",
        "away_team": "Team Interrupted 2",
        "score_str": "0:0",
        "minute": 35,
        "half": "1H",
        "stage_text": "Przerwany",
        "status_code": "36",
        "is_live": False
    }

    settled_cnt = tg.auto_settle_active_cards(live_matches=[], finished_matches=[interrupted_match])
    assert settled_cnt == 1
    assert key not in tg.active_match_cards
    assert key in tg.settled_matches
    last_msg = tg.edit_calls[-1]["text"]
    assert "ZWROT (VOID)" in last_msg
    assert "WYGRANA" not in last_msg
    assert "PRZEGRANA" not in last_msg
