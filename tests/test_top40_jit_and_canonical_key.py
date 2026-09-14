import unittest
import time
from unittest.mock import MagicMock, patch
from engine.sts_flashscore_aggregator import STSFlashscoreAggregator
from engine.goal_triggers import GoalTriggersEngine
from engine.live_matcher import get_canonical_match_key


class TestTop40JITAndCanonicalKey(unittest.TestCase):
    def setUp(self):
        # Prevent background scanner thread from launching during tests
        STSFlashscoreAggregator.start_background_scanner = lambda self: None

    def test_canonical_key_generation(self):
        """Sprawdza poprawność i unifikację kanonicznego klucza meczu."""
        key1 = get_canonical_match_key("Caerau Ely FC", "Llanelli Town")
        key2 = get_canonical_match_key("Caerau Ely", "Llanelli Town")
        key3 = get_canonical_match_key("  caerau ely fc  ", "llanelli town ")
        self.assertEqual(key1, "caerau_ely_llanelli_town")
        self.assertEqual(key2, "caerau_ely_llanelli_town")
        self.assertEqual(key3, "caerau_ely_llanelli_town")
        self.assertEqual(key1, key2)
        self.assertEqual(key2, key3)

    def test_canonical_key_buffer_unification(self):
        """
        Sprawdza unifikację bufora _match_history w GoalTriggersEngine.
        Niezależnie od tego, czy zapytanie pochodzi z Flashscore ('AybnF7xo'),
        czy z STS ('sts_1981132748433250607'), używany jest jeden wspólny bufor.
        """
        triggers = GoalTriggersEngine()
        triggers._match_history.clear()

        fs_match = {
            'flashscore_id': 'AybnF7xo',
            'home_team': 'Caerau Ely FC',
            'away_team': 'Llanelli Town',
            'league': 'WALIA: Cymru South',
            'minute': 71,
            'half': '2H',
            'score_str': '1:0',
            'home_score': 1,
            'away_score': 0,
            'timestamp': 1000.0,
        }
        stats_t1 = {
            'has_stats': True,
            'is_estimated': False,
            'source': 'GOALOO',
            'has_da': True,
            'shots_total': 25,
            'shots_on_target_total': 9,
            'dangerous_attacks_total': 80,
            'corners_total': 6,
            'xg_total': 4.5,
            'danger_index': 60,
            'apm': 1.0,
            'shots_total_home': 22,
            'shots_total_away': 3,
            'shots_on_target_home': 8,
            'shots_on_target_away': 1,
            'dangerous_attacks_home': 70,
            'dangerous_attacks_away': 10,
            'xg_home': 4.0,
            'xg_away': 0.5,
            'corners_home': 5,
            'corners_away': 1,
        }

        # Krok 1: Snapshot z Flashscore
        triggers.evaluate_match(fs_match, stats_t1, {})

        canonical_key = "caerau_ely_llanelli_town"
        self.assertIn(canonical_key, triggers._match_history, "Bufor musi być zindeksowany kluczem kanonicznym")
        self.assertIs(triggers._match_history['aybnf7xo'], triggers._match_history[canonical_key], "Identyfikator 'aybnf7xo' musi wskazywać na ten sam obiekt bufora co klucz kanoniczny")
        self.assertEqual(len(triggers._match_history[canonical_key]['snapshots']), 1)

        # Krok 2: Snapshot z STS (inny format identyfikatora i lekko inna nazwa drużyny)
        sts_match = {
            'id': 'sts_1981132748433250607',
            'home_team': 'Caerau Ely',
            'away_team': 'Llanelli Town',
            'league': 'Walia, Cymru South',
            'minute': 75,
            'half': '2H',
            'score_str': '1:0',
            'home_score': 1,
            'away_score': 0,
            'timestamp': 1240.0,
        }
        stats_t2 = dict(stats_t1)
        stats_t2['shots_total'] = 28
        stats_t2['shots_on_target_total'] = 10
        stats_t2['dangerous_attacks_total'] = 90

        triggers.evaluate_match(sts_match, stats_t2, {})

        self.assertIs(triggers._match_history['sts_1981132748433250607'], triggers._match_history[canonical_key], "Identyfikator STS musi wskazywać na ten sam obiekt bufora co klucz kanoniczny")
        self.assertEqual(len(triggers._match_history[canonical_key]['snapshots']), 2,
                         "Oba snapshoty (FS i STS) muszą trafić do tego samego bufora!")

    def test_di_discrepancy_eliminated(self):
        """
        Weryfikuje usunięcie rozbieżności wskaźnika DI (np. 74% vs 21%).
        Dzięki wspólnemu buforowi snapshoty z FS i STS budują ciągłość serii czasowej,
        co gwarantuje, że delta SoT/ataków jest mierzona prawidłowo.
        """
        triggers = GoalTriggersEngine()
        triggers._match_history.clear()

        # T0 (minuta 64): Początek okresu obserwacji
        fs_t0 = {
            'flashscore_id': 'AybnF7xo',
            'home_team': 'Caerau Ely FC',
            'away_team': 'Llanelli Town',
            'minute': 64,
            'half': '2H',
            'home_score': 1,
            'away_score': 0,
            'score_str': '1:0',
            'timestamp': 1000.0,
        }
        stats_t0 = {
            'has_stats': True,
            'is_estimated': False,
            'source': 'GOALOO',
            'has_da': True,
            'shots_total': 24,
            'shots_on_target_total': 7,
            'dangerous_attacks_total': 75,
            'corners_total': 5,
            'xg_total': 4.0,
            'danger_index': 60,
            'apm': 0.9,
            'shots_total_home': 20,
            'shots_total_away': 4,
            'shots_on_target_home': 6,
            'shots_on_target_away': 1,
            'dangerous_attacks_home': 65,
            'dangerous_attacks_away': 10,
            'corners_home': 4,
            'corners_away': 1,
        }
        triggers.evaluate_match(fs_t0, stats_t0, {})

        # T1 (minuta 74): Snapshot zarejestrowany przez STS
        sts_t1 = {
            'id': 'sts_1981132748433250607',
            'home_team': 'Caerau Ely',
            'away_team': 'Llanelli Town',
            'minute': 74,
            'half': '2H',
            'home_score': 1,
            'away_score': 0,
            'score_str': '1:0',
            'timestamp': 1600.0,
        }
        stats_t1 = {
            'has_stats': True,
            'is_estimated': False,
            'source': 'GOALOO',
            'has_da': True,
            'shots_total': 29,
            'shots_on_target_total': 9,
            'dangerous_attacks_total': 90,
            'corners_total': 7,
            'xg_total': 4.8,
            'danger_index': 65,
            'apm': 1.0,
            'shots_total_home': 25,
            'shots_total_away': 4,
            'shots_on_target_home': 8,
            'shots_on_target_away': 1,
            'dangerous_attacks_home': 78,
            'dangerous_attacks_away': 12,
            'corners_home': 6,
            'corners_away': 1,
        }
        res_sts = triggers.evaluate_match(sts_t1, stats_t1, {})

        # T2 (minuta 78): Snapshot zarejestrowany przez FS
        fs_t2 = {
            'flashscore_id': 'AybnF7xo',
            'home_team': 'Caerau Ely FC',
            'away_team': 'Llanelli Town',
            'minute': 78,
            'half': '2H',
            'home_score': 1,
            'away_score': 0,
            'score_str': '1:0',
            'timestamp': 1840.0,
        }
        stats_t2 = {
            'has_stats': True,
            'is_estimated': False,
            'source': 'GOALOO',
            'has_da': True,
            'shots_total': 31,
            'shots_on_target_total': 11,
            'dangerous_attacks_total': 98,
            'corners_total': 8,
            'xg_total': 5.55,
            'danger_index': 74,
            'apm': 1.01,
            'shots_total_home': 27,
            'shots_total_away': 4,
            'shots_on_target_home': 10,
            'shots_on_target_away': 1,
            'dangerous_attacks_home': 86,
            'dangerous_attacks_away': 12,
            'corners_home': 7,
            'corners_away': 1,
        }
        res_fs = triggers.evaluate_match(fs_t2, stats_t2, {})

        # Sprawdzamy, czy w obu przypadkach (i w STS w minucie 74, i w FS w minucie 78)
        # danger_index_10 i danger_index_5 są wysokie (utrzymane tempo) i nie spadają do wartości zerowej (21%)
        self.assertGreater(res_sts['danger_index_10'], 50, "DI10 dla STS w minucie 74 powinno korzystać z historii z minuty 64")
        self.assertGreater(res_fs['danger_index_10'], 65, "DI10 dla FS w minucie 78 powinno wynosić ~70%+ dzięki ciągłości delty")

    def test_non_top40_candidate_gets_jit_and_emits_alert(self):
        """
        Weryfikuje, że mecz znajdujący się poza TOP 40 (np. na pozycji 45):
        1. Rozpoznaje spełnienie is_candidate_eligible,
        2. Uruchamia JIT Playwright (get_match_real_live_markets) tylko dla tego meczu,
        3. Wylicza sygnał i wysyła alert przez telegram.notify_goal_signal,
        4. Otrzymuje właściwy obiekt rekordu (nie processed_matches[-1]).
        """
        agg = STSFlashscoreAggregator()
        agg.prematch_analyzer.analyze_fixture = MagicMock(return_value={})
        agg.telegram.notify_goal_signal = MagicMock()
        agg.sts_engine.get_match_real_live_markets = MagicMock(return_value=[
            {'name': 'Over 1.5 FT', 'market': 'Over 1.5 FT', 'odds': 1.65, 'source': 'STS_REAL'}
        ])

        # Przygotujmy 41 meczów w TOP 40 (indeksy 0..40) - brak kandydatów
        fs_matches = []
        for i in range(41):
            fs_matches.append({
                'flashscore_id': f'fs_dummy_{i}',
                'league': 'Liga Dummy',
                'home_team': f'Gospodarz {i}',
                'away_team': f'Gosc {i}',
                'home_score': 0,
                'away_score': 0,
                'score_str': '0:0',
                'minute': 15,
                'half': '1H',
                'stage_text': "15'",
                'is_live': True
            })

        # Mecz 42 (indeks 41 w fs_matches, czyli fs_matches[40:]):
        # Kandydat: 78', 1:0, 2H, bogate REAL_STATS
        caerau_fs = {
            'flashscore_id': 'AybnF7xo',
            'league': 'WALIA: Cymru South',
            'home_team': 'Caerau Ely FC',
            'away_team': 'Llanelli Town',
            'home_score': 1,
            'away_score': 0,
            'score_str': '1:0',
            'minute': 78,
            'half': '2H',
            'stage_text': "78'",
            'is_live': True,
            'url': 'https://www.flashscore.pl/mecz/AybnF7xo/'
        }
        fs_matches.append(caerau_fs)

        # Mecz STS powiązany z Caerau
        caerau_sts = {
            'bookmaker': 'STS',
            'league': 'Walia, Cymru South',
            'home_team': 'Caerau Ely',
            'away_team': 'Llanelli Town',
            'score_str': '1:0',
            'home_score': 1,
            'away_score': 0,
            'minute': 78,
            'half': '2H',
            'stage_text': "78'",
            'goals_odds': {},
            'live_markets': [],
            'url': 'https://www.sts.pl/live/pilka-nozna/caerau-llanelli-m12345',
            'is_live': True
        }

        # Mock statystyk z Goaloo dla Caerau
        stats_caerau = {
            'has_stats': True,
            'is_estimated': False,
            'source': 'GOALOO',
            'has_da': True,
            'shots_total': 31,
            'shots_on_target_total': 11,
            'dangerous_attacks_total': 98,
            'corners_total': 4,
            'xg_total': 5.55,
            'danger_index': 74,
            'apm': 1.01,
            'shots_total_home': 27,
            'shots_total_away': 4,
            'shots_on_target_home': 10,
            'shots_on_target_away': 1,
            'dangerous_attacks_home': 86,
            'dangerous_attacks_away': 12,
            'corners_home': 3,
            'corners_away': 1,
            'xg_home': 4.8,
            'xg_away': 0.75,
            'possession_home': 62,
            'possession_away': 38,
        }

        agg.fs_engine.get_live_soccer_matches = MagicMock(return_value=fs_matches)
        agg.sts_engine.fetch_live_matches = MagicMock(return_value=[caerau_sts])
        agg.fs_engine.get_match_statistics = MagicMock(return_value={})

        def mock_goaloo_stats(home, away, minute=1, **kwargs):
            if "caerau" in home.lower():
                return dict(stats_caerau)
            return {}

        agg.goaloo.get_live_stats = MagicMock(side_effect=mock_goaloo_stats)
        agg.beesports.get_live_stats = MagicMock(return_value={})
        agg.betsapi.get_live_stats = MagicMock(return_value={})

        # Wcześniejsza historia w buforze dla delty SoT
        hist_key = get_canonical_match_key("Caerau Ely FC", "Llanelli Town")
        agg.triggers._match_history[hist_key] = {
            'last_seen': time.time() - 300,
            'last_score': (1, 0),
            'last_goal_minute': 40,
            'last_goal_time': time.time() - 2000,
            'snapshots': [
                {
                    'time': time.time() - 420,
                    'minute': 71,
                    'half': '2H',
                    'home_score': 1,
                    'away_score': 0,
                    'total_goals': 1,
                    'shots': 28,
                    'sot': 10,
                    'dangerous_attacks': 88,
                    'has_da': True,
                    'corners': 4,
                    'xg': 5.05,
                    'xg_home': 4.3,
                    'xg_away': 0.75,
                    'sot_home': 9,
                    'sot_away': 1,
                    'big_chances': 0,
                    'red_cards': 0,
                    'is_finished': False
                }
            ]
        }

        scan_res = agg._execute_full_scan()

        # 1. Weryfikacja, że JIT Playwright został wywołany dla URL meczu Caerau
        agg.sts_engine.get_match_real_live_markets.assert_called_with(
            'https://www.sts.pl/live/pilka-nozna/caerau-llanelli-m12345'
        )

        # 2. Weryfikacja, że Telegram wysłał alert dla Caerau
        self.assertGreaterEqual(agg.telegram.notify_goal_signal.call_count, 1,
                               "Alert Telegram musi zostać wysłany dla kwalifikującego się meczu poza TOP 40!")

        called_match_arg, called_sig_arg = agg.telegram.notify_goal_signal.call_args_list[0][0]
        self.assertTrue(called_match_arg['id'] == 'AybnF7xo' or str(called_match_arg['id']).startswith('sts_'))
        self.assertIn('caerau', called_match_arg['home_team'].lower())
        self.assertEqual(called_sig_arg['badge'], 'OVER 1.5 FT')
        self.assertEqual(called_sig_arg['odds'], 1.65)

    def test_non_top40_non_candidate_does_not_call_playwright(self):
        """
        Weryfikuje, że mecz poza TOP 40, który NIE jest kandydatem (np. 15 minuta, 0 strzałów),
        NIE uruchamia Playwright (get_match_real_live_markets) i nie obciąża procesora.
        """
        agg = STSFlashscoreAggregator()
        agg.prematch_analyzer.analyze_fixture = MagicMock(return_value={})
        agg.telegram.notify_goal_signal = MagicMock()
        agg.sts_engine.get_match_real_live_markets = MagicMock()

        # 42 mecze, żaden nie jest kandydatem (15 minuta, brak stats)
        fs_matches = []
        for i in range(42):
            fs_matches.append({
                'flashscore_id': f'fs_dummy_{i}',
                'league': 'Liga Dummy',
                'home_team': f'Gospodarz {i}',
                'away_team': f'Gosc {i}',
                'home_score': 0,
                'away_score': 0,
                'score_str': '0:0',
                'minute': 15,
                'half': '1H',
                'stage_text': "15'",
                'is_live': True
            })

        dummy_sts = {
            'bookmaker': 'STS',
            'league': 'Liga Dummy',
            'home_team': 'Gospodarz 41',
            'away_team': 'Gosc 41',
            'score_str': '0:0',
            'home_score': 0,
            'away_score': 0,
            'minute': 15,
            'half': '1H',
            'stage_text': "15'",
            'goals_odds': {},
            'live_markets': [],
            'url': 'https://www.sts.pl/live/pilka-nozna/dummy-41',
            'is_live': True
        }

        agg.fs_engine.get_live_soccer_matches = MagicMock(return_value=fs_matches)
        agg.sts_engine.fetch_live_matches = MagicMock(return_value=[dummy_sts])
        agg.fs_engine.get_match_statistics = MagicMock(return_value={})
        agg.goaloo.get_live_stats = MagicMock(return_value={})
        agg.beesports.get_live_stats = MagicMock(return_value={})
        agg.betsapi.get_live_stats = MagicMock(return_value={})

        scan_res = agg._execute_full_scan()

        # Sprawdzenie: Playwright NIE został wywołany ani razu
        agg.sts_engine.get_match_real_live_markets.assert_not_called()
        agg.telegram.notify_goal_signal.assert_not_called()

    def test_caerau_ely_regression_end_to_end(self):
        """
        Pełny test regresyjny przypadku Caerau Ely FC vs Llanelli Town:
        78', 1:0, Over 1.5 FT @ 1.65, DI 74%, EV +6.0%, Funnel 11_ACCEPTED, 4⭐ SILVER.
        Mecz poza TOP 40 musi przejść całą ścieżkę i wyemitować sygnał z poprawnym rynkiem.
        """
        agg = STSFlashscoreAggregator()
        agg.prematch_analyzer.analyze_fixture = MagicMock(return_value={})
        agg.telegram.notify_goal_signal = MagicMock()
        agg.sts_engine.get_match_real_live_markets = MagicMock(return_value=[
            {'name': 'Over 1.5 FT', 'market': 'Over 1.5 FT', 'odds': 1.65, 'source': 'STS_REAL'}
        ])

        # 40 meczów w TOP 40
        fs_matches = [
            {
                'flashscore_id': f'top40_{i}',
                'league': 'Liga Top',
                'home_team': f'Drużyna A {i}',
                'away_team': f'Drużyna B {i}',
                'home_score': 0,
                'away_score': 0,
                'score_str': '0:0',
                'minute': 20,
                'half': '1H',
                'stage_text': "20'",
                'is_live': True
            } for i in range(40)
        ]

        # Mecz Caerau na pozycji 41 (poza TOP 40)
        caerau = {
            'flashscore_id': 'AybnF7xo',
            'league': 'WALIA: Cymru South',
            'home_team': 'Caerau Ely FC',
            'away_team': 'Llanelli Town',
            'home_score': 1,
            'away_score': 0,
            'score_str': '1:0',
            'minute': 78,
            'half': '2H',
            'stage_text': "78'",
            'is_live': True,
            'url': 'https://www.flashscore.pl/mecz/AybnF7xo/'
        }
        fs_matches.append(caerau)

        caerau_sts = {
            'bookmaker': 'STS',
            'league': 'Walia, Cymru South',
            'home_team': 'Caerau Ely',
            'away_team': 'Llanelli Town',
            'score_str': '1:0',
            'home_score': 1,
            'away_score': 0,
            'minute': 78,
            'half': '2H',
            'stage_text': "78'",
            'goals_odds': {},
            'live_markets': [],
            'url': 'https://www.sts.pl/live/pilka-nozna/caerau-ely-llanelli-town-f123',
            'is_live': True
        }

        stats_caerau = {
            'has_stats': True,
            'is_estimated': False,
            'source': 'GOALOO',
            'has_da': True,
            'shots_total': 31,
            'shots_on_target_total': 11,
            'dangerous_attacks_total': 98,
            'corners_total': 4,
            'xg_total': 5.55,
            'danger_index': 74,
            'apm': 1.01,
            'shots_total_home': 27,
            'shots_total_away': 4,
            'shots_on_target_home': 10,
            'shots_on_target_away': 1,
            'dangerous_attacks_home': 86,
            'dangerous_attacks_away': 12,
            'corners_home': 3,
            'corners_away': 1,
            'xg_home': 4.8,
            'xg_away': 0.75,
            'possession_home': 62,
            'possession_away': 38,
        }

        agg.fs_engine.get_live_soccer_matches = MagicMock(return_value=fs_matches)
        agg.sts_engine.fetch_live_matches = MagicMock(return_value=[caerau_sts])
        agg.fs_engine.get_match_statistics = MagicMock(return_value={})

        def mock_stats(home, away, minute=1, **kwargs):
            if "caerau" in home.lower():
                return dict(stats_caerau)
            return {}

        agg.goaloo.get_live_stats = MagicMock(side_effect=mock_stats)
        agg.beesports.get_live_stats = MagicMock(return_value={})
        agg.betsapi.get_live_stats = MagicMock(return_value={})

        # Zapewnienie historii dla wysokiego DI10/DI5
        hist_key = get_canonical_match_key("Caerau Ely FC", "Llanelli Town")
        agg.triggers._match_history[hist_key] = {
            'last_seen': time.time() - 300,
            'last_score': (1, 0),
            'last_goal_minute': 40,
            'last_goal_time': time.time() - 2000,
            'snapshots': [
                {
                    'time': time.time() - 420,
                    'minute': 71,
                    'half': '2H',
                    'home_score': 1,
                    'away_score': 0,
                    'total_goals': 1,
                    'shots': 28,
                    'sot': 10,
                    'dangerous_attacks': 88,
                    'has_da': True,
                    'corners': 4,
                    'xg': 5.05,
                    'xg_home': 4.3,
                    'xg_away': 0.75,
                    'sot_home': 9,
                    'sot_away': 1,
                    'big_chances': 0,
                    'red_cards': 0,
                    'is_finished': False
                }
            ]
        }

        scan_res = agg._execute_full_scan()

        found_caerau = next((m for m in scan_res['matches'] if 'caerau' in m['home_team'].lower()), None)
        self.assertIsNotNone(found_caerau)
        self.assertTrue(found_caerau['has_signals'])
        self.assertGreaterEqual(len(found_caerau['signals']), 1)

        sig = found_caerau['signals'][0]
        self.assertEqual(sig['badge'], 'OVER 1.5 FT')
        self.assertEqual(sig['odds'], 1.65)
        self.assertGreaterEqual(sig.get('stars', 0), 4)

        # Telegram wywołany z właściwymi argumentami
        self.assertGreaterEqual(agg.telegram.notify_goal_signal.call_count, 1)
        match_arg, sig_arg = agg.telegram.notify_goal_signal.call_args[0]
        self.assertTrue(match_arg['id'] == 'AybnF7xo' or str(match_arg['id']).startswith('sts_'))
        self.assertEqual(sig_arg['badge'], 'OVER 1.5 FT')


if __name__ == '__main__':
    unittest.main()
