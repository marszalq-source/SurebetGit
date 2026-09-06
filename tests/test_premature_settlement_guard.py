# -*- coding: utf-8 -*-
import os
import sys
import time
import threading

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from engine.telegram_notifier import TelegramNotifier

class MockTelegramNotifier(TelegramNotifier):
    def __new__(cls, *args, **kwargs):
        return object.__new__(cls)

    def __init__(self):
        self._cards_lock = threading.RLock()
        self.config = {'enabled': True, 'live_update_mode': True}
        self.active_match_cards = {}
        self.settled_matches = {}
        self.edit_calls = []
        self.sent_calls = []
        from engine.stats_engine import StatsEngine
        self.stats_engine = StatsEngine()
        class DummyBA:
            def settle_bet_async(self, m, o): pass
        self.ba_sync = DummyBA()

    def edit_message_all(self, device_messages, text, parse_mode='HTML'):
        self.edit_calls.append({'text': text, 'time': time.time(), 'devs': device_messages})
        return True

    def _save_cards(self):
        pass

def test_stage_rank_rejects_premature_ft():
    stage, rank = TelegramNotifier._get_match_stage_rank(
        half='FT', stage_text='Koniec', is_live=False, status_code='3', minute=40
    )
    assert rank < 40, f'Oczekiwano rank < 40 dla 40. minuty, otrzymano {rank} ({stage})'
    assert stage == '1H'

    stage_60, rank_60 = TelegramNotifier._get_match_stage_rank(
        half='FT', stage_text='Koniec', is_live=True, status_code='3', minute=60
    )
    assert rank_60 == 30, f'Oczekiwano 2H (30) dla 60. minuty, otrzymano {rank_60} ({stage_60})'
    assert stage_60 == '2H'

    stage_90, rank_90 = TelegramNotifier._get_match_stage_rank(
        half='FT', stage_text='Koniec', is_live=False, status_code='3', minute=90
    )
    assert rank_90 == 40
    assert stage_90 == 'FT'

    stage_void, rank_void = TelegramNotifier._get_match_stage_rank(
        half='1H', stage_text='Mecz przerwany', is_live=False, status_code='10', minute=30
    )
    assert rank_void == 40
    assert stage_void == 'FT'

def test_auto_settle_prioritizes_live_over_finished():
    tg = MockTelegramNotifier()
    key = 'everton_vs_catolica'
    now = time.time()

    tg.active_match_cards[key] = {
        'home_team': 'Everton',
        'away_team': 'Catolica',
        'league': 'Chile',
        'badge': 'OVER 2.5 FT',
        'unit_tag': '2J',
        'target_goals': 3,
        'target_period': 'FT',
        'initial_odds': 1.45,
        'last_odds': 1.45,
        'initial_minute': 35,
        'last_seen_minute': 38,
        'initial_score': '0:1',
        'last_seen_score': '0:1',
        'last_seen_half': '1H',
        'created_at': now - 300,
        'status': 'PENDING',
        'settled': False
    }

    live_match = [{
        'home_team': 'Everton', 'away_team': 'Catolica',
        'minute': 40, 'score_str': '0:1', 'half': '1H',
        'stage_text': "40'", 'is_live': True, 'status_code': '2'
    }]

    finished_match = [{
        'home_team': 'Everton', 'away_team': 'Catolica',
        'minute': 40, 'score_str': '0:1', 'half': 'FT',
        'stage_text': 'Koniec', 'is_live': False, 'status_code': '3'
    }]

    settled_cnt = tg.auto_settle_active_cards(live_matches=live_match, finished_matches=finished_match)
    assert settled_cnt == 0
    assert key in tg.active_match_cards
    assert tg.active_match_cards[key]['last_seen_minute'] == 40
    assert not tg.active_match_cards[key].get('settled')

def test_auto_settle_settles_real_ft_lost():
    tg = MockTelegramNotifier()
    key = 'everton_vs_catolica'
    now = time.time()

    tg.active_match_cards[key] = {
        'home_team': 'Everton',
        'away_team': 'Catolica',
        'league': 'Chile',
        'badge': 'OVER 2.5 FT',
        'unit_tag': '2J',
        'target_goals': 3,
        'target_period': 'FT',
        'initial_odds': 1.45,
        'last_odds': 1.45,
        'initial_minute': 35,
        'last_seen_minute': 88,
        'initial_score': '0:1',
        'last_seen_score': '0:1',
        'last_seen_half': '2H',
        'created_at': now - 3600,
        'status': 'PENDING',
        'settled': False
    }

    finished_match = [{
        'home_team': 'Everton', 'away_team': 'Catolica',
        'minute': 90, 'score_str': '0:1', 'half': 'FT',
        'stage_text': 'Koniec', 'is_live': False, 'status_code': '3'
    }]

    settled_cnt = tg.auto_settle_active_cards(live_matches=[], finished_matches=finished_match)
    assert settled_cnt == 1
    assert key not in tg.active_match_cards
    assert key in tg.settled_matches
    assert len(tg.edit_calls) >= 1
    assert 'PRZEGRANA' in tg.edit_calls[-1]['text']

def test_check_and_update_blocks_premature_ft_loss():
    tg = MockTelegramNotifier()
    key = 'test_match_ft'
    now = time.time()

    tg.active_match_cards[key] = {
        'home_team': 'Team X',
        'away_team': 'Team Y',
        'league': 'Test',
        'badge': 'OVER 2.5 FT',
        'target_goals': 3,
        'target_period': 'FT',
        'initial_minute': 30,
        'last_seen_minute': 42,
        'created_at': now - 600,
        'initial_score': '0:0',
        'last_seen_score': '0:0',
        'status': 'PENDING',
        'settled': False
    }

    bad_snapshot = {
        'home_team': 'Team X', 'away_team': 'Team Y',
        'minute': 42, 'score_str': '0:0', 'half': 'FT',
        'stage_text': 'Koniec', 'is_live': False, 'status_code': '3'
    }

    res = tg.check_and_update_match_status(bad_snapshot, card_key=key)
    assert not res
    assert not tg.active_match_cards[key].get('settled')
    assert key in tg.active_match_cards
