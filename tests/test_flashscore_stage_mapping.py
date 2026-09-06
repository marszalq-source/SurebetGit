import pytest
from engine.flashscore_engine import FlashscoreEngine
from engine.telegram_notifier import TelegramNotifier


def test_flashscore_stage_code_parsing_and_stage_rank():
    fs = FlashscoreEngine()

    # Symulacja surowych bloków Flashscore (format protokołu global.flashscore.ninja)
    raw_1h = "ZA÷Test League¬ZB÷Test Country¬~AA÷match_1h¬AD÷1788678000¬AB÷2¬AC÷12¬AE÷Home A¬AF÷Away A¬AG÷0¬AH÷0¬~"
    raw_ht_38 = "ZA÷Test League¬ZB÷Test Country¬~AA÷match_ht1¬AD÷1788678000¬AB÷2¬AC÷38¬AE÷Home B¬AF÷Away B¬AG÷1¬AH÷0¬~"
    raw_ht_46 = "ZA÷Test League¬ZB÷Test Country¬~AA÷match_ht2¬AD÷1788678000¬AB÷2¬AC÷46¬AE÷Home C¬AF÷Away C¬AG÷0¬AH÷1¬~"
    raw_2h_13 = "ZA÷Test League¬ZB÷Test Country¬~AA÷match_2h¬AD÷1788678000¬AB÷2¬AC÷13¬AE÷Home D¬AF÷Away D¬AG÷1¬AH÷1¬~"
    raw_ft_3 = "ZA÷Test League¬ZB÷Test Country¬~AA÷match_ft¬AD÷1788678000¬AB÷3¬AC÷3¬AE÷Home E¬AF÷Away E¬AG÷2¬AH÷1¬~"

    # 1. Test 1H (AC=12)
    fields_1h = fs._parse_feed_fields("AA÷match_1h¬AD÷1788678000¬AB÷2¬AC÷12¬AE÷Home A¬AF÷Away A¬AG÷0¬AH÷0¬")
    stage_1h, rank_1h = TelegramNotifier._get_match_stage_rank('1H', '20\'', True, '2', 20)
    assert rank_1h == 10
    assert stage_1h == '1H'

    # 2. Test HT (AC=38 i AC=46)
    stage_ht, rank_ht = TelegramNotifier._get_match_stage_rank('HT', 'Przerwa', True, '2', 45)
    assert rank_ht == 20
    assert stage_ht == 'HT'

    # 3. Test 2H (AC=13) -> ranga MUSI wynosić 30
    stage_2h, rank_2h = TelegramNotifier._get_match_stage_rank('2H', '59\'', True, '2', 59)
    assert rank_2h == 30
    assert stage_2h == '2H'

    # Monotoniczny awans HT (20) -> 2H (30)
    assert rank_2h > rank_ht

    # 4. Test FT (AC=3)
    stage_ft, rank_ft = TelegramNotifier._get_match_stage_rank('FT', 'Koniec', False, '3', 90)
    assert rank_ft == 40
    assert stage_ft == 'FT'
    assert rank_ft > rank_2h


def test_initial_score_display_in_card_and_settlement():
    class TestMockNotifier(TelegramNotifier):
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
            self.edit_calls.append(text)
            return True
        def _save_cards(self):
            pass
        def _log_settlement_telemetry(self, *args, **kwargs):
            pass

    notifier = TestMockNotifier()
    card_key = "shatin sa_vs_golik north district"
    notifier.active_match_cards[card_key] = {
        "device_messages": {"123": 456},
        "home_team": "Shatin SA",
        "away_team": "Golik North District",
        "league": "Hongkong, Premier League",
        "last_text": "old",
        "initial_odds": 1.45,
        "last_odds": 1.45,
        "initial_minute": 24,
        "initial_score": "0:1",
        "initial_goals": 1,
        "target_goals": 2,
        "target_period": "FT",
        "created_at": 1000,
        "last_edit_time": 1000,
        "badge": "OVER 1.5 FT",
        "unit_tag": "2J",
        "status": "PENDING",
        "settled": False,
        "is_silver": True,
        "is_golden": False,
        "last_rendered_minute": 24,
        "last_rendered_score": "0:1",
        "last_rendered_stage": "1H",
        "last_rendered_odds": 1.45,
    }

    # 1. Update in-place w trakcie meczu (np. 39' i aktualny kurs 1.20)
    match_update = {
        "home_team": "Shatin SA",
        "away_team": "Golik North District",
        "score_str": "0:1",
        "minute": 39,
        "half": "1H",
        "stage_text": "39'",
        "is_live": True,
        "danger_index": 66,
        "apm": 1.06,
        "live_markets": [{"name": "OVER 1.5 FT", "odds": 1.20}]
    }
    notifier.check_and_update_match_status(match_update, card_key=card_key)
    assert len(notifier.edit_calls) == 1
    assert "⏱️ <b>Czas:</b> 39' (Typ z: 24' [0:1])" in notifier.edit_calls[-1]

    # 2. Settle WON
    match_won = {
        "home_team": "Shatin SA",
        "away_team": "Golik North District",
        "score_str": "1:1",
        "minute": 55,
        "half": "2H",
        "stage_text": "55'",
        "is_live": True,
    }
    notifier.check_and_update_match_status(match_won, card_key=card_key)
    assert len(notifier.edit_calls) == 2
    assert "Typ z:</b> <b>24' min [0:1]</b>" in notifier.edit_calls[-1]

