import unittest
from unittest.mock import MagicMock, patch
import time

from engine.goal_triggers import GoalTriggersEngine, get_canonical_match_key
from engine.sts_flashscore_aggregator import STSFlashscoreAggregator

class TestTop40RealMarketsJIT(unittest.TestCase):
    """
    Test weryfikujacy minimalna poprawnosc buga technicznego w TOP 40:
    - wyeliminowanie blokady 'not fs_m.get(\"live_markets\")'
    - caly lancuch: candidate -> JIT STS_REAL -> reevaluacja -> has_signals -> notify_goal_signal
    - testy A, B, C
    - zabezpieczenie przed nadpisywaniem STS_REAL przez STS_LIVE w cache
    """

    def setUp(self):
        self.triggers = GoalTriggersEngine()

    def test_root_cause_confirmed(self):
        """
        Potwierdzenie root cause: obecnosc syntetycznych rynkow STS_LIVE powodowala,
        ze stary warunek 'not fs_m.get(\"live_markets\")' zwracal False,
        calkowicie blokujac odpytanie o STS_REAL.
        """
        fs_m = {
            'flashscore_id': 'top40_match_1',
            'home_team': 'Arsenal',
            'away_team': 'Chelsea',
            'league': 'Anglia, Premier League',
            'minute': 70,
            'half': '2H',
            'home_score': 1,
            'away_score': 0,
            'score_str': '1:0',
            # 6 ogolnych rynkow fallback STS_LIVE przypisanych w linii 618
            'live_markets': [
                {'name': 'Over 1.5 FT', 'odds': 1.15, 'source': 'STS_LIVE'},
                {'name': 'Over 2.5 FT', 'odds': 1.45, 'source': 'STS_LIVE'}
            ]
        }
        stats = {
            'has_stats': True,
            'shots_total_home': 14,
            'shots_total_away': 4,
            'shots_on_target_home': 6,
            'shots_on_target_away': 2,
            'corners_home': 7,
            'corners_away': 1,
            'dangerous_attacks_home': 55,
            'dangerous_attacks_away': 20,
            'possession_home': 65,
            'possession_away': 35,
            'danger_index': 72,
            'danger_index_10': 72,
            'danger_index_5': 72,
            'apm': 1.15,
            'source': 'GOALOO_REAL'
        }
        
        is_cand = self.triggers.is_candidate_eligible(fs_m, stats)
        self.assertTrue(is_cand, "Mecz powinien byc poprawnym kandydatem analitycznym")

        # STARY WARUNEK z linii 682:
        old_condition = is_cand and not fs_m.get('live_markets')
        self.assertFalse(old_condition, "Stary warunek blokowal pobranie STS_REAL (wynik: False)")

        # NOWY WARUNEK:
        new_condition = is_cand and not any(m.get('source') == 'STS_REAL' for m in fs_m.get('live_markets', []))
        self.assertTrue(new_condition, "Nowy warunek odblokowuje pobranie STS_REAL (wynik: True)")

    @patch('engine.sts_flashscore_aggregator.GoalooEngine')
    @patch('engine.sts_flashscore_aggregator.BeeSportsEngine')
    @patch('engine.sts_flashscore_aggregator.FlashscoreEngine')
    @patch('engine.sts_flashscore_aggregator.STSLiveEngine')
    @patch('engine.sts_flashscore_aggregator.TelegramNotifier')
    def test_scenario_a_top40_fetches_real_and_emits_alert(self, mock_tg_cls, mock_sts_cls, mock_fs_cls, mock_bs_cls, mock_g_cls):
        """
        Scenariusz A: Mecz w TOP 40 z rynkami STS_LIVE i spelnionymi warunkami strategii:
        1. candidate_eligible == True
        2. brak STS_REAL (tylko STS_LIVE)
        3. should_fetch_real == True
        4. pobiera STS_REAL z Playwright
        5. re-ewaluuje
        6. evaluate_match zwraca has_signals == True
        7. wywoluje notify_goal_signal()
        """
        mock_tg = mock_tg_cls.return_value
        mock_sts = mock_sts_cls.return_value
        mock_fs = mock_fs_cls.return_value
        mock_bs = mock_bs_cls.return_value
        mock_g = mock_g_cls.return_value

        agg = STSFlashscoreAggregator()

        # Rynki autentyczne STS_REAL, ktore zwroci Playwright z podstrony
        real_markets = [
            {
                'market': 'Over 1.5 FT',
                'name': 'Over 1.5 FT',
                'line': 1.5,
                'odds': 1.65,
                'source': 'STS_REAL',
                'is_match_total': True,
                'market_header': 'Liczba goli'
            }
        ]
        mock_sts.get_match_real_live_markets.return_value = real_markets

        top_match = {
            'flashscore_id': 'arsenal_chelsea_top40',
            'home_team': 'Arsenal FC',
            'away_team': 'Chelsea FC',
            'league': 'Anglia, Premier League',
            'minute': 78,
            'half': '2H',
            'home_score': 1,
            'away_score': 0,
            'score_str': '1:0',
            'url': 'https://www.flashscore.pl/mecz/arsenal_chelsea_top40/',
            'is_live': True
        }

        mock_fs.get_live_soccer_matches.return_value = [top_match]

        sts_m = {
            'home_team': 'Arsenal',
            'away_team': 'Chelsea',
            'league': 'Piłka Nożna – Premier League',
            'url': 'https://www.sts.pl/live/pilka-nozna/f123456',
            'minute': 78,
            'half': '2H',
            'home_score': 1,
            'away_score': 0,
            'score_str': '1:0',
            'goals_odds': {},
            'live_markets': [
                {'name': 'Over 1.5 FT', 'odds': 1.15, 'source': 'STS_LIVE'}
            ]
        }
        mock_sts.fetch_live_matches.return_value = [sts_m]

        real_stats = {
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
        mock_g.get_live_stats.return_value = real_stats

        # Inicjalizacja bufora kroczącego z próbką sprzed 7 minut (minuta 71), aby wyliczyć delty
        canonical_k = get_canonical_match_key('Arsenal FC', 'Chelsea FC')
        agg.triggers._match_history[canonical_k] = {
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

        res = agg._execute_full_scan()

        # Weryfikacja: Playwright zostal wywolany
        self.assertEqual(mock_sts.get_match_real_live_markets.call_count, 1)
        mock_sts.get_match_real_live_markets.assert_called_with('https://www.sts.pl/live/pilka-nozna/f123456')

        # Weryfikacja: rynki zostaly zapisane z STS_REAL
        self.assertTrue(len(res['matches']) >= 1)
        match_res = next(m for m in res['matches'] if m['id'] == 'arsenal_chelsea_top40')
        self.assertTrue(any(mk.get('source') == 'STS_REAL' for mk in match_res['live_markets']))
        self.assertEqual(match_res['live_markets'][0]['odds'], 1.65)

        # Weryfikacja: wyemitowano sygnał i wysłano alert na Telegram
        self.assertTrue(match_res['has_signals'])
        self.assertTrue(mock_tg.notify_goal_signal.called)

    @patch('engine.sts_flashscore_aggregator.GoalooEngine')
    @patch('engine.sts_flashscore_aggregator.BeeSportsEngine')
    @patch('engine.sts_flashscore_aggregator.FlashscoreEngine')
    @patch('engine.sts_flashscore_aggregator.STSLiveEngine')
    @patch('engine.sts_flashscore_aggregator.TelegramNotifier')
    def test_scenario_b_top40_with_existing_real_does_not_refetch(self, mock_tg_cls, mock_sts_cls, mock_fs_cls, mock_bs_cls, mock_g_cls):
        """
        Scenariusz B: Mecz w TOP 40, ktory w poprzednim cyklu POBRAL juz STS_REAL
        i ma go w pamieci podręcznej (cache):
        1. should_fetch_real == False
        2. Playwright NIE jest ponownie uruchamiany bez potrzeby (call_count == 0)
        3. rynki STS_REAL sa zachowane w rekordzie meczu
        """
        mock_sts = mock_sts_cls.return_value
        mock_fs = mock_fs_cls.return_value
        mock_g = mock_g_cls.return_value

        agg = STSFlashscoreAggregator()

        top_match = {
            'flashscore_id': 'match_already_has_real',
            'home_team': 'Bayern',
            'away_team': 'Dortmund',
            'league': 'Niemcy, Bundesliga',
            'minute': 73,
            'half': '2H',
            'home_score': 1,
            'away_score': 0,
            'score_str': '1:0',
            'url': 'https://www.flashscore.pl/mecz/match_already_has_real/',
            'is_live': True
        }
        mock_fs.get_live_soccer_matches.return_value = [top_match]

        sts_m = {
            'home_team': 'Bayern',
            'away_team': 'Dortmund',
            'league': 'Piłka Nożna – Bundesliga',
            'url': 'https://www.sts.pl/live/pilka-nozna/f999999',
            'minute': 73,
            'half': '2H',
            'home_score': 1,
            'away_score': 0,
            'score_str': '1:0',
            'goals_odds': {},
            # STS Live feed podaje tylko ogolne rynki STS_LIVE
            'live_markets': [
                {'name': 'Over 1.5 FT', 'odds': 1.15, 'source': 'STS_LIVE'}
            ]
        }
        mock_sts.fetch_live_matches.return_value = [sts_m]

        real_stats = {
            'has_stats': True,
            'is_estimated': False,
            'shots_total_home': 16,
            'shots_total_away': 5,
            'shots_on_target_home': 7,
            'shots_on_target_away': 2,
            'corners_home': 8,
            'corners_away': 2,
            'possession_home': 60,
            'possession_away': 40,
            'danger_index': 74,
            'apm': 1.10,
            'source': 'GOALOO_REAL'
        }
        mock_g.get_live_stats.return_value = real_stats

        # Wstepne zasilenie cache rynkami STS_REAL z poprzedniego cyklu
        existing_real_markets = [
            {
                'market': 'Over 1.5 FT',
                'name': 'Over 1.5 FT',
                'line': 1.5,
                'odds': 1.70,
                'source': 'STS_REAL',
                'is_match_total': True
            }
        ]
        agg.cached_results = [
            {
                'id': 'match_already_has_real',
                'home_team': 'Bayern',
                'away_team': 'Dortmund',
                'minute': 72,
                'half': '2H',
                'home_score': 1,
                'away_score': 0,
                'score_str': '1:0',
                'stats': real_stats,
                'live_markets': existing_real_markets
            }
        ]

        res = agg._execute_full_scan()

        # Playwright NIE powinien byc wywolany, bo STS_REAL jest juz w pamieci
        self.assertEqual(mock_sts.get_match_real_live_markets.call_count, 0)

        # STS_REAL zachowany w rekordzie wynikowym
        match_res = next(m for m in res['matches'] if m['id'] == 'match_already_has_real')
        self.assertTrue(any(mk.get('source') == 'STS_REAL' for mk in match_res['live_markets']))
        self.assertEqual(match_res['live_markets'][0]['odds'], 1.70)

    @patch('engine.sts_flashscore_aggregator.GoalooEngine')
    @patch('engine.sts_flashscore_aggregator.BeeSportsEngine')
    @patch('engine.sts_flashscore_aggregator.FlashscoreEngine')
    @patch('engine.sts_flashscore_aggregator.STSLiveEngine')
    @patch('engine.sts_flashscore_aggregator.TelegramNotifier')
    def test_scenario_c_tail_41plus_still_works_properly(self, mock_tg_cls, mock_sts_cls, mock_fs_cls, mock_bs_cls, mock_g_cls):
        """
        Scenariusz C: Mecz poza TOP 40 (41+) z rynkami STS_LIVE:
        - zachowuje istniejaca poprawna logike JIT wdrożona rano
        - Playwright pobiera STS_REAL
        """
        mock_sts = mock_sts_cls.return_value
        mock_fs = mock_fs_cls.return_value
        mock_g = mock_g_cls.return_value

        agg = STSFlashscoreAggregator()

        # Generujemy 40 dummy meczow w TOP 40 bez sygnałów
        dummy_matches = [
            {
                'flashscore_id': f'dummy_{i}',
                'home_team': f'Home {i}',
                'away_team': f'Away {i}',
                'league': 'Polska, Ekstraklasa',
                'minute': 10,
                'half': '1H',
                'home_score': 0,
                'away_score': 0,
                'score_str': '0:0',
                'url': f'https://www.flashscore.pl/mecz/dummy_{i}/',
                'is_live': True
            } for i in range(40)
        ]

        # 41. mecz - kandydat w ogonie
        tail_candidate = {
            'flashscore_id': 'tail_cand_41',
            'home_team': 'Caerau Ely',
            'away_team': 'Llanelli Town',
            'league': 'Walia, Cymru South',
            'minute': 78,
            'half': '2H',
            'home_score': 1,
            'away_score': 0,
            'score_str': '1:0',
            'url': 'https://www.flashscore.pl/mecz/tail_cand_41/',
            'is_live': True
        }

        mock_fs.get_live_soccer_matches.return_value = dummy_matches + [tail_candidate]

        mock_sts.fetch_live_matches.return_value = [
            {
                'home_team': 'Caerau Ely',
                'away_team': 'Llanelli Town',
                'league': 'Piłka Nożna – Walia',
                'url': 'https://www.sts.pl/live/pilka-nozna/f777777',
                'minute': 78,
                'half': '2H',
                'home_score': 1,
                'away_score': 0,
                'score_str': '1:0',
                'goals_odds': {},
                'live_markets': [{'name': 'Over 1.5 FT', 'odds': 1.15, 'source': 'STS_LIVE'}]
            }
        ]

        real_stats = {
            'has_stats': True,
            'is_estimated': False,
            'shots_total_home': 15,
            'shots_total_away': 4,
            'shots_on_target_home': 6,
            'shots_on_target_away': 2,
            'corners_home': 7,
            'corners_away': 1,
            'possession_home': 65,
            'possession_away': 35,
            'danger_index': 74,
            'danger_index_10': 74,
            'danger_index_5': 74,
            'apm': 1.05,
            'source': 'GOALOO_REAL'
        }
        mock_g.get_live_stats.return_value = real_stats

        mock_sts.get_match_real_live_markets.return_value = [
            {
                'market': 'Over 1.5 FT',
                'name': 'Over 1.5 FT',
                'line': 1.5,
                'odds': 1.65,
                'source': 'STS_REAL',
                'is_match_total': True
            }
        ]

        res = agg._execute_full_scan()

        # Playwright wywolany dokladnie dla kandydata z ogona
        self.assertEqual(mock_sts.get_match_real_live_markets.call_count, 1)
        mock_sts.get_match_real_live_markets.assert_called_with('https://www.sts.pl/live/pilka-nozna/f777777')

        match_res = next(m for m in res['matches'] if m['id'] == 'tail_cand_41')
        self.assertTrue(any(mk.get('source') == 'STS_REAL' for mk in match_res['live_markets']))

    def test_cache_entry_does_not_downgrade_real_to_live(self):
        """
        Weryfikacja ochrony pamięci cache w _update_cache_entry:
        jesli stary wpis w cache posiadal STS_REAL, a nowa aktualizacja niesie tylko STS_LIVE,
        rynki STS_REAL NIE moga zostac utracone.
        """
        agg = STSFlashscoreAggregator()
        agg.cached_results = [
            {
                'id': 'test_protect_real',
                'home_team': 'Real Madrid',
                'away_team': 'Barcelona',
                'minute': 75,
                'half': '2H',
                'home_score': 1,
                'away_score': 0,
                'score_str': '1:0',
                'live_markets': [
                    {'name': 'Over 1.5 FT', 'odds': 1.65, 'source': 'STS_REAL'}
                ]
            }
        ]

        # Aktualizacja niosaca rynki fallback STS_LIVE (np. z szybkiego cyklu baseline)
        new_entry = {
            'id': 'test_protect_real',
            'home_team': 'Real Madrid',
            'away_team': 'Barcelona',
            'minute': 76,
            'half': '2H',
            'home_score': 1,
            'away_score': 0,
            'score_str': '1:0',
            'live_markets': [
                {'name': 'Over 1.5 FT', 'odds': 1.10, 'source': 'STS_LIVE'}
            ]
        }

        agg._update_cache_entry('test_protect_real', new_entry)

        cached_item = agg.cached_results[0]
        # Minuta powinna ulec zwiekszeniu
        self.assertEqual(cached_item['minute'], 76)
        # Ale rynki musza zachowac autentyczne STS_REAL!
        self.assertTrue(any(m.get('source') == 'STS_REAL' for m in cached_item['live_markets']))
        self.assertEqual(cached_item['live_markets'][0]['odds'], 1.65)

if __name__ == '__main__':
    unittest.main()
