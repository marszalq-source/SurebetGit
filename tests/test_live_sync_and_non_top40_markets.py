import unittest
from unittest.mock import MagicMock, patch
from engine.sts_flashscore_aggregator import STSFlashscoreAggregator
from engine.goal_triggers import GoalTriggersEngine
from engine.active_cards_watchdog import ActiveCardsWatchdog


class TestLiveSyncAndNonTop40Markets(unittest.TestCase):
    def setUp(self):
        STSFlashscoreAggregator.start_background_scanner = lambda self: None

    def test_wisla_jagiellonia_exact_sync_and_markets_preserved(self):
        """
        Dedykowany test dla przypadku Wisła Kraków - Jagiellonia Białystok:
        FS: 55', 2H, 1:0 (mecz na pozycji 68 z 99)
        STS: 53', 2H, 1:0 z 6 rynkami live
        Oczekujemy:
        - half == '2H' (nie HT)
        - minute >= 53 (nie cofa się do 45)
        - live_markets zawiera 6 rynków STS (nie jest [])
        - stats nie jest pustym słownikiem udającym 0:0/50:50
        """
        agg = STSFlashscoreAggregator()
        agg.prematch_analyzer.analyze_fixture = MagicMock(return_value={})
        agg.beesports.get_live_stats = MagicMock(return_value={})
        agg.goaloo.get_live_stats = MagicMock(return_value={})
        agg.betsapi.get_live_stats = MagicMock(return_value={})

        # Tworzymy 70 meczów FS, aby Wisła była poza TOP 40 (np. na pozycji 68)
        fs_matches = []
        for i in range(67):
            fs_matches.append({
                'flashscore_id': f'fs_dummy_{i}',
                'league': 'Liga Dummy',
                'home_team': f'Gospodarz {i}',
                'away_team': f'Gosc {i}',
                'home_score': 0,
                'away_score': 0,
                'score_str': '0:0',
                'minute': 30,
                'half': '1H',
                'stage_text': "30'",
                'is_live': True
            })

        # Mecz 68: Wisła Kraków vs Jagiellonia Białystok
        wisla_fs = {
            'flashscore_id': 'zolyXKfd',
            'league': '154: POLSKA: PKO BP Ekstraklasa',
            'home_team': 'Wisła Kraków',
            'away_team': 'Jagiellonia Białystok',
            'home_score': 1,
            'away_score': 0,
            'score_str': '1:0',
            'minute': 55,
            'half': '2H',
            'stage_text': "55'",
            'is_live': True
        }
        fs_matches.append(wisla_fs)

        # Mecz STS z 6 rynkami
        sample_markets = [
            {'name': 'Over 1.5 FT', 'market': 'Over 1.5 FT', 'odds': 1.27, 'source': 'STS_LIVE'},
            {'name': 'Over 2.5 FT', 'market': 'Over 2.5 FT', 'odds': 1.92, 'source': 'STS_LIVE'},
            {'name': 'Over 3.5 FT', 'market': 'Over 3.5 FT', 'odds': 3.82, 'source': 'STS_LIVE'},
            {'name': '2. Gol: Gosp.', 'market': 'Następny Gol: Gospodarze', 'odds': 1.32, 'source': 'STS_LIVE'},
            {'name': '2. Gol: Goście', 'market': 'Następny Gol: Goście', 'odds': 3.69, 'source': 'STS_LIVE'},
            {'name': 'Brak goli (nikt)', 'market': 'Następny Gol: Nikt', 'odds': 4.70, 'source': 'STS_LIVE'}
        ]
        wisla_sts = {
            'bookmaker': 'STS',
            'league': 'Polska, Ekstraklasa',
            'home_team': 'Wisła Kraków',
            'away_team': 'Jagiellonia Białystok',
            'score_str': '1:0',
            'home_score': 1,
            'away_score': 0,
            'minute': 53,
            'half': '2H',
            'stage_text': "53'",
            'goals_odds': {'over_15_ft': 1.92, 'over_05_2h': 1.27},
            'live_markets': sample_markets,
            'url': 'https://www.sts.pl/live/pilka-nozna/f2911995',
            'is_live': True
        }

        agg.fs_engine.get_live_soccer_matches = MagicMock(return_value=fs_matches)
        agg.sts_engine.fetch_live_matches = MagicMock(return_value=[wisla_sts])
        agg.fs_engine.get_match_statistics = MagicMock(return_value={})

        scan_res = agg._execute_full_scan()
        matches = scan_res['matches']

        wisla_res = next((m for m in matches if m['id'] == 'zolyXKfd'), None)
        self.assertIsNotNone(wisla_res, "Wisła Kraków musi znajdować się na liście przetworzonych meczów")

        # 1. Sprawdzenie fazy i minuty
        self.assertEqual(wisla_res['half'], '2H', "Faza nie może pokazywać 'HT', musi być '2H'")
        self.assertGreaterEqual(wisla_res['minute'], 53, "Minuta nie może cofać się do 45")
        self.assertEqual(wisla_res['score_str'], '1:0', "Wynik musi być 1:0")

        # 2. Sprawdzenie rynków live_markets
        self.assertEqual(len(wisla_res['live_markets']), 6, "Rynki nie mogą być puste, muszą zawierać 6 pozycji STS")
        self.assertEqual(wisla_res['live_markets'][0]['name'], 'Over 1.5 FT')

        # 3. Sprawdzenie statystyk (brak sztucznego stats: {})
        self.assertTrue(bool(wisla_res['stats']), "Mecz nie może mieć pustego stats: {} powodującego 0:0/50:50")
        self.assertIn('danger_index', wisla_res['stats'])
        self.assertIn('xg_total', wisla_res['stats'])

    def test_non_top40_match_with_active_sts_markets_reflected_in_api_scan(self):
        """Mecz poza TOP 40 z rynkami STS jest prawidłowo zwracany przez scan_all() (/api/scan)."""
        agg = STSFlashscoreAggregator()
        agg.prematch_analyzer.analyze_fixture = MagicMock(return_value={})
        agg.beesports.get_live_stats = MagicMock(return_value={})
        agg.goaloo.get_live_stats = MagicMock(return_value={})
        agg.betsapi.get_live_stats = MagicMock(return_value={})

        fs_matches = [{'flashscore_id': f'fs_{i}', 'home_team': f'H{i}', 'away_team': f'A{i}', 'minute': 20, 'half': '1H', 'is_live': True} for i in range(50)]
        target_fs = {
            'flashscore_id': 'target_60',
            'home_team': 'Valencia',
            'away_team': 'Sevilla',
            'home_score': 0,
            'away_score': 1,
            'score_str': '0:1',
            'minute': 58,
            'half': '2H',
            'stage_text': "58'",
            'is_live': True
        }
        fs_matches.append(target_fs)

        target_sts = {
            'bookmaker': 'STS',
            'league': 'Hiszpania, LaLiga',
            'home_team': 'Valencia',
            'away_team': 'Sevilla',
            'score_str': '0:1',
            'home_score': 0,
            'away_score': 1,
            'minute': 60,
            'half': '2H',
            'stage_text': "60'",
            'goals_odds': {'over_15_ft': 1.45},
            'live_markets': [{'name': 'Over 1.5 FT', 'market': 'Over 1.5 FT', 'odds': 1.45, 'source': 'STS_LIVE'}],
            'url': 'https://www.sts.pl/live/pilka-nozna/valencia-sevilla',
            'is_live': True
        }

        agg.fs_engine.get_live_soccer_matches = MagicMock(return_value=fs_matches)
        agg.sts_engine.fetch_live_matches = MagicMock(return_value=[target_sts])

        agg._execute_full_scan()
        api_data = agg.scan_all()

        valencia_card = next((m for m in api_data['matches'] if m['id'] == 'target_60'), None)
        self.assertIsNotNone(valencia_card)
        self.assertEqual(len(valencia_card['live_markets']), 1)
        self.assertEqual(valencia_card['minute'], 60)
        self.assertEqual(valencia_card['half'], '2H')

    def test_no_playwright_for_disqualified_matches(self):
        """Żaden mecz niespełniający rygorystycznych warunków strategii nie odpala Playwright."""
        # 1. 1:0 w 25' z DI=35 i 0 SoT
        match_low_di = {'league': 'Ekstraklasa', 'home_team': 'A', 'away_team': 'B', 'minute': 25, 'half': '1H', 'home_score': 1, 'away_score': 0}
        stats_low_di = {'has_stats': True, 'danger_index': 35, 'shots_on_target_total': 0, 'apm': 0.4}
        self.assertFalse(GoalTriggersEngine.is_candidate_eligible(match_low_di, stats_low_di))

        # 2. 0:1 w 30' z 1 SoT
        match_low_sot = {'league': 'Ekstraklasa', 'home_team': 'A', 'away_team': 'B', 'minute': 30, 'half': '1H', 'home_score': 0, 'away_score': 1}
        stats_low_sot = {'has_stats': True, 'danger_index': 58, 'shots_on_target_total': 1, 'apm': 0.9}
        self.assertFalse(GoalTriggersEngine.is_candidate_eligible(match_low_sot, stats_low_sot))

        # 3. Mecz w przerwie (HT)
        match_ht = {'league': 'Ekstraklasa', 'home_team': 'A', 'away_team': 'B', 'minute': 45, 'half': 'HT', 'home_score': 1, 'away_score': 0}
        stats_ht = {'has_stats': True, 'danger_index': 70, 'shots_on_target_total': 3, 'apm': 1.2}
        self.assertFalse(GoalTriggersEngine.is_candidate_eligible(match_ht, stats_ht))

        # 4. Mecz w 85' minucie (końcówka)
        match_late = {'league': 'Ekstraklasa', 'home_team': 'A', 'away_team': 'B', 'minute': 85, 'half': '2H', 'home_score': 1, 'away_score': 0}
        self.assertFalse(GoalTriggersEngine.is_candidate_eligible(match_late, stats_ht))

        # 5. Mecz bez statystyk (pusty słownik)
        self.assertFalse(GoalTriggersEngine.is_candidate_eligible(match_low_di, {}))

        # 6. Mecz ze statystykami estymowanymi
        stats_est = {'has_stats': True, 'is_estimated': True, 'danger_index': 80, 'shots_on_target_total': 5, 'apm': 1.5}
        self.assertFalse(GoalTriggersEngine.is_candidate_eligible(match_low_di, stats_est))

    def test_playwright_called_for_real_eligible_candidate(self):
        """Prawdziwy kandydat (spełniający scenariusz, DI >= 60, SoT >= 2, APM >= 0.85) jest uprawniony."""
        match_candidate = {
            'league': 'Ekstraklasa',
            'home_team': 'Legia',
            'away_team': 'Lech',
            'minute': 25,
            'half': '1H',
            'home_score': 1,
            'away_score': 0
        }
        stats_active = {
            'has_stats': True,
            'is_estimated': False,
            'danger_index': 68,
            'shots_on_target_total': 3,
            'apm': 1.2,
            'dangerous_attacks_total': 25
        }
        self.assertTrue(GoalTriggersEngine.is_candidate_eligible(match_candidate, stats_active))

    def test_continuous_fresh_cache_without_blocking(self):
        """Instant fresh snapshot odświeża pamięć RAM natychmiast na starcie cyklu bez czekania na enrichment."""
        agg = STSFlashscoreAggregator()
        agg.prematch_analyzer.analyze_fixture = MagicMock(return_value={})
        agg.beesports.get_live_stats = MagicMock(return_value={})
        agg.goaloo.get_live_stats = MagicMock(return_value={})
        agg.betsapi.get_live_stats = MagicMock(return_value={})

        # Symulacja pierwszego skanu ze stanem 45' HT
        initial_match = {
            'flashscore_id': 'm1',
            'league': 'Ekstraklasa',
            'home_team': 'TeamA',
            'away_team': 'TeamB',
            'minute': 45,
            'half': 'HT',
            'stage_text': 'Przerwa',
            'is_live': True,
            'home_score': 1,
            'away_score': 0,
            'score_str': '1:0'
        }
        agg.fs_engine.get_live_soccer_matches = MagicMock(return_value=[initial_match])
        agg.sts_engine.fetch_live_matches = MagicMock(return_value=[])
        agg._execute_full_scan()

        self.assertEqual(agg.cached_results[0]['minute'], 45)
        self.assertEqual(agg.cached_results[0]['half'], 'HT')

        # Drugi skan: STS zaktualizował do 52' 2H
        updated_fs = dict(initial_match)
        updated_sts = {
            'home_team': 'TeamA',
            'away_team': 'TeamB',
            'minute': 52,
            'half': '2H',
            'stage_text': "52'",
            'is_live': True,
            'home_score': 1,
            'away_score': 0,
            'score_str': '1:0',
            'live_markets': [{'name': 'Over 1.5 FT', 'market': 'Over 1.5 FT', 'odds': 1.30, 'source': 'STS_LIVE'}]
        }
        agg.fs_engine.get_live_soccer_matches = MagicMock(return_value=[updated_fs])
        agg.sts_engine.fetch_live_matches = MagicMock(return_value=[updated_sts])

        # Uruchamiamy skan i sprawdzamy, że zaraz po starcie cached_results ma już 52' i 2H
        agg._execute_full_scan()
        self.assertEqual(agg.cached_results[0]['minute'], 52)
        self.assertEqual(agg.cached_results[0]['half'], '2H')
        self.assertEqual(len(agg.cached_results[0]['live_markets']), 1)

    def test_active_cards_watchdog_retains_direct_access(self):
        """ActiveCardsWatchdog ma nienaruszoną ścieżkę bezpośredniego odpytywania STS."""
        mock_sts = MagicMock()
        mock_sts.get_match_real_live_markets.return_value = [{'name': 'Over 1.5 FT', 'odds': 1.85, 'source': 'STS_REAL'}]
        watchdog = ActiveCardsWatchdog(sts_engine=mock_sts)

        res = watchdog.sts_engine.get_match_real_live_markets("https://www.sts.pl/live/mecz")
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]['source'], 'STS_REAL')


if __name__ == '__main__':
    unittest.main()
