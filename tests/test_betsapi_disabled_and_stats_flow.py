import unittest
from unittest.mock import patch, MagicMock
import time
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from engine.betsapi_engine import BetsAPIEngine
from engine.sts_flashscore_aggregator import STSFlashscoreAggregator

class TestBetsAPIDisabledAndStatsFlow(unittest.TestCase):
    def test_betsapi_engine_disabled_by_default(self):
        """Upewnij się, że BetsAPIEngine jest domyślnie wyłączony i nie wykonuje żądań."""
        engine = BetsAPIEngine()
        self.assertFalse(engine.enabled)
        self.assertIsNone(engine.api_token)

        # Wywołania metod przy enabled=False muszą natychmiast zwracać puste wyniki
        with patch('urllib.request.urlopen') as mock_urlopen:
            matches = engine.update_live_matches_list()
            self.assertEqual(matches, [])
            mock_urlopen.assert_not_called()

            stats = engine.get_live_stats('Team A', 'Team B', minute=25)
            self.assertIsNone(stats)
            mock_urlopen.assert_not_called()

    def test_betsapi_future_token_configuration_preserved(self):
        """Upewnij się, że kod i konfiguracja na przyszłość (dla tokenu) są zachowane."""
        engine = BetsAPIEngine(enabled=True, api_token="test_token_12345")
        self.assertTrue(engine.enabled)
        self.assertEqual(engine.api_token, "test_token_12345")

    def test_normal_scan_makes_zero_betsapi_requests(self):
        """Weryfikacja: podczas pełnego cyklu skanu wykonywanych jest DOKŁADNIE 0 zapytań do BetsAPI."""
        with patch.object(STSFlashscoreAggregator, 'start_background_scanner'):
            agg = STSFlashscoreAggregator()
            agg._scanner_running = True

            # Mockujemy fs_engine i sts_engine
            agg.fs_engine.get_live_soccer_matches = MagicMock(return_value=[
                {
                    'flashscore_id': 'match_1',
                    'home_team': 'Arsenal',
                    'away_team': 'Chelsea',
                    'league': 'Premier League',
                    'minute': 35,
                    'half': '1H',
                    'home_score': 1,
                    'away_score': 0,
                    'score_str': '1:0',
                    'is_live': True
                }
            ])
            agg.fs_engine.get_live_stats = MagicMock(return_value={})
            agg.sts_engine.fetch_live_matches = MagicMock(return_value=[
                {
                    'id': 'match_1',
                    'home_team': 'Arsenal',
                    'away_team': 'Chelsea',
                    'league': 'Premier League',
                    'minute': 35,
                    'half': '1H',
                    'home_score': 1,
                    'away_score': 0,
                    'score_str': '1:0',
                    'url': 'https://www.sts.pl/live/1',
                    'live_markets': [{'name': 'Over 1.5 FT', 'odds': 1.45}]
                }
            ])

            # Goaloo zwraca poprawne statystyki
            agg.goaloo.get_live_stats = MagicMock(return_value={
                'has_stats': True,
                'source': 'GOALOO',
                'shots_total': 8,
                'shots_on_target_total': 3,
                'shots_on_target_home': 2,
                'shots_on_target_away': 1,
                'shots_total_home': 5,
                'shots_total_away': 3,
                'corners_total': 4,
                'is_estimated': False,
                'apm': 1.1,
                'danger_index': 62
            })
            agg.goaloo.update_live_matches_list = MagicMock(return_value=[])

            # BetsAPI mock
            betsapi_mock_get = MagicMock(return_value=None)
            betsapi_mock_update = MagicMock(return_value=[])
            agg.betsapi.get_live_stats = betsapi_mock_get
            agg.betsapi.update_live_matches_list = betsapi_mock_update

            with patch('urllib.request.urlopen') as mock_urlopen:
                agg._execute_full_scan()

                # 1. Dokładnie 0 wywołań BetsAPI
                betsapi_mock_update.assert_not_called()
                betsapi_mock_get.assert_not_called()

                # 2. Brak zapytań sieciowych do betsapi
                for call in mock_urlopen.call_args_list:
                    args, _ = call
                    req = args[0]
                    url = req.full_url if hasattr(req, 'full_url') else str(req)
                    self.assertNotIn('betsapi.com', url)

                # 3. Mecz ma statystyki z GOALOO
                self.assertEqual(len(agg.cached_results), 1)
                entry = agg.cached_results[0]
                self.assertEqual(entry['stats']['source'], 'GOALOO')
                self.assertTrue(entry['stats']['has_stats'])
                self.assertFalse(entry['stats']['is_estimated'])
                self.assertEqual(entry['enrichment_status'], 'COMPLETE')

    def test_goaloo_primary_and_beesports_secondary(self):
        """Weryfikacja: Goaloo jest sprawdzane pierwsze, a BeeSports tylko w razie braku danych."""
        with patch.object(STSFlashscoreAggregator, 'start_background_scanner'):
            agg = STSFlashscoreAggregator()
            agg._scanner_running = True

            agg.fs_engine.get_live_soccer_matches = MagicMock(return_value=[
                {
                    'flashscore_id': 'match_1',
                    'home_team': 'Real Madrid',
                    'away_team': 'Barcelona',
                    'league': 'La Liga',
                    'minute': 20,
                    'half': '1H',
                    'home_score': 0,
                    'away_score': 0,
                    'score_str': '0:0',
                    'is_live': True
                }
            ])
            agg.fs_engine.get_live_stats = MagicMock(return_value={})
            agg.sts_engine.fetch_live_matches = MagicMock(return_value=[])

            # Goaloo zwraca poprawne statystyki
            agg.goaloo.get_live_stats = MagicMock(return_value={
                'has_stats': True,
                'source': 'GOALOO',
                'shots_total': 4,
                'shots_on_target_total': 2,
                'is_estimated': False
            })
            agg.goaloo.update_live_matches_list = MagicMock(return_value=[])

            agg.beesports.get_live_stats = MagicMock(return_value=None)

            agg._execute_full_scan()

            # Gdy Goaloo zwróci dane, BeeSports nie musi być wywołany
            agg.beesports.get_live_stats.assert_not_called()
            self.assertEqual(agg.cached_results[0]['stats']['source'], 'GOALOO')

    def test_fallback_to_beesports_when_goaloo_empty(self):
        """Weryfikacja: jeśli Goaloo nie ma danych, BeeSports jest wywoływany jako dodatkowe źródło."""
        with patch.object(STSFlashscoreAggregator, 'start_background_scanner'):
            agg = STSFlashscoreAggregator()
            agg._scanner_running = True

            agg.fs_engine.get_live_soccer_matches = MagicMock(return_value=[
                {
                    'flashscore_id': 'match_1',
                    'home_team': 'Wisla Krakow',
                    'away_team': 'Jagiellonia',
                    'league': 'Ekstraklasa',
                    'minute': 50,
                    'half': '2H',
                    'home_score': 1,
                    'away_score': 0,
                    'score_str': '1:0',
                    'is_live': True
                }
            ])
            agg.fs_engine.get_live_stats = MagicMock(return_value={})
            agg.sts_engine.fetch_live_matches = MagicMock(return_value=[])

            # Goaloo zwraca brak statystyk
            agg.goaloo.get_live_stats = MagicMock(return_value={'has_stats': False})
            agg.goaloo.update_live_matches_list = MagicMock(return_value=[])

            # BeeSports zwraca poprawne statystyki
            agg.beesports.get_live_stats = MagicMock(return_value={
                'has_stats': True,
                'source': 'BEESPORTS',
                'shots_total': 10,
                'shots_on_target_total': 3,
                'is_estimated': False
            })

            agg._execute_full_scan()

            # BeeSports został pomyślnie wywołany i jego dane trafiły do cache
            agg.beesports.get_live_stats.assert_called()
            self.assertEqual(agg.cached_results[0]['stats']['source'], 'BEESPORTS')

    def test_radar_fallback_has_is_estimated_true_and_blocks_signals(self):
        """Weryfikacja: gdy brak Goaloo i BeeSports, model radarowy ma is_estimated=True i blokuje sygnał."""
        with patch.object(STSFlashscoreAggregator, 'start_background_scanner'):
            agg = STSFlashscoreAggregator()
            agg._scanner_running = True

            agg.fs_engine.get_live_soccer_matches = MagicMock(return_value=[
                {
                    'flashscore_id': 'match_est',
                    'home_team': 'Team A',
                    'away_team': 'Team B',
                    'league': 'Liga Testowa',
                    'minute': 75,
                    'half': '2H',
                    'home_score': 0,
                    'away_score': 0,
                    'score_str': '0:0',
                    'is_live': True
                }
            ])
            agg.fs_engine.get_live_stats = MagicMock(return_value={})
            agg.sts_engine.fetch_live_matches = MagicMock(return_value=[
                {
                    'id': 'match_est',
                    'home_team': 'Team A',
                    'away_team': 'Team B',
                    'league': 'Liga Testowa',
                    'minute': 75,
                    'half': '2H',
                    'home_score': 0,
                    'away_score': 0,
                    'score_str': '0:0',
                    'url': 'https://www.sts.pl/live/est',
                    'odds_1': 2.10,
                    'odds_X': 3.10,
                    'odds_2': 3.20,
                    'live_markets': [{'name': 'Over 0.5 FT', 'odds': 1.85}]
                }
            ])

            agg.goaloo.get_live_stats = MagicMock(return_value={'has_stats': False})
            agg.goaloo.update_live_matches_list = MagicMock(return_value=[])
            agg.beesports.get_live_stats = MagicMock(return_value=None)

            agg._execute_full_scan()

            self.assertEqual(len(agg.cached_results), 1)
            entry = agg.cached_results[0]
            self.assertTrue(entry['stats']['is_estimated'])
            self.assertEqual(entry['enrichment_status'], 'ESTIMATED')
            # Zasada 0: is_estimated = True BEZWZGLĘDNIE blokuje generowanie sygnałów
            self.assertFalse(entry['has_signals'])
            self.assertEqual(entry['signals'], [])

if __name__ == '__main__':
    unittest.main()
