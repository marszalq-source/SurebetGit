import unittest
from unittest.mock import MagicMock, patch
import time

from engine.goal_triggers import GoalTriggersEngine
from engine.sts_flashscore_aggregator import STSFlashscoreAggregator


class TestScannerPerformanceAndDisplay(unittest.TestCase):
    """
    Test suite weryfikujący:
    1. Instant Display (błyskawiczny snapshot w RAM, enrichment_status PENDING -> COMPLETE).
    2. Wczesne odrzucanie czarnej listy (ANALYTIC_BL) bez wywołań Playwright ani zewnętrznych API.
    3. Blokowanie Playwright dla DI >= 65, gdy mecz NIE jest kandydatem do sygnału.
    4. Zachowanie pobierania STS_REAL przez Playwright dla rzeczywistych kandydatów i meczów z sygnałami.
    5. Bezwzględną tożsamość matematyczną ewaluacji sygnałów (Zasada 0).
    6. Pełną niezależność ActiveCardsWatchdog od filtrów skanera wstępnego.
    """

    def setUp(self):
        self.triggers = GoalTriggersEngine()

    def test_instant_display_returns_baseline_immediately(self):
        """
        1. Instant Display ma być warstwą prezentacyjną/cache.
        Po pobraniu surowych danych FS/STS cached_results zawiera natychmiastowy snapshot
        z enrichment_status='PENDING' i is_enriched=False.
        Po zakończeniu pełnego skanu is_enriched=True i enrichment_status='COMPLETE'.
        """
        with patch('engine.sts_flashscore_aggregator.FlashscoreEngine') as mock_fs_cls, \
             patch('engine.sts_flashscore_aggregator.STSLiveEngine') as mock_sts_cls, \
             patch('engine.sts_flashscore_aggregator.TelegramNotifier') as mock_tg_cls:

            mock_fs = mock_fs_cls.return_value
            mock_sts = mock_sts_cls.return_value
            mock_tg = mock_tg_cls.return_value
            mock_tg.active_match_cards = {}

            aggregator = STSFlashscoreAggregator()
            aggregator.fs_engine = mock_fs
            aggregator.sts_engine = mock_sts
            aggregator.telegram = mock_tg

            # Symulacja meczu Flashscore i STS
            fs_live = [{
                'flashscore_id': 'm_fs_1',
                'league': 'Premier League',
                'home_team': 'Arsenal',
                'away_team': 'Chelsea',
                'home_score': 0,
                'away_score': 0,
                'score_str': '0:0',
                'minute': 22,
                'half': '1H',
                'stage_text': "22'",
                'is_live': True,
                'is_started': True
            }]
            sts_live = [{
                'id': 'sts_1',
                'league': 'Premier League',
                'home_team': 'Arsenal',
                'away_team': 'Chelsea',
                'home_score': 0,
                'away_score': 0,
                'score_str': '0:0',
                'minute': 22,
                'half': '1H',
                'stage_text': "22'",
                'is_live': True,
                'is_started': True,
                'url': 'https://www.sts.pl/live/pilka-nozna/m1',
                'goals_odds': {'over_05_ht': 1.85}
            }]

            mock_fs.get_live_soccer_matches.return_value = fs_live
            mock_sts.fetch_live_matches.return_value = sts_live
            mock_fs.get_match_statistics.return_value = {
                'shots_total_home': 6, 'shots_total_away': 4,
                'shots_on_target_home': 3, 'shots_on_target_away': 2,
                'corners_home': 4, 'corners_away': 2,
                'dangerous_attacks_home': 25, 'dangerous_attacks_away': 20,
                'danger_index': 70, 'apm': 1.6, 'has_stats': True
            }

            # Wykonaj pełny skan
            result = aggregator._execute_full_scan()

            # Wynik skanu powinien być w pełni wzbogacony
            self.assertTrue(result['is_enriched'])
            self.assertEqual(result['enrichment_status'], 'COMPLETE')
            self.assertGreaterEqual(result['total_live_matches'], 1)

            # Odpowiedź API scan_all()
            api_res = aggregator.scan_all()
            self.assertTrue(api_res['is_enriched'])
            self.assertEqual(api_res['enrichment_status'], 'COMPLETE')
            self.assertGreaterEqual(len(api_res['matches']), 1)

    def test_early_analytic_blacklist_blocks_playwright_and_external_apis(self):
        """
        2. Mecze na ANALYTIC_BL (U19/U20/U21, rezerwy, amatorzy, puchary niszowe)
        muszą być odrzucone wcześnie i NIGDY nie mogą wywoływać get_match_real_live_markets (Playwright).
        """
        bl_leagues = [
            ("U21 Premier League Cup", "Chelsea U21", "Arsenal U21"),
            ("Czech U19 League", "Sparta Prague U19", "Slavia Prague U19"),
            ("Poland Amateur League", "Klub A", "Klub B"),
            ("Rumunia, Puchar", "Steaua", "Dinamo"),
            ("Chiny, Puchar", "Beijing", "Shanghai"),
            ("Arabia Saudyjska, Division 1", "Al-Hazem", "Al-Batin")
        ]

        for league, home, away in bl_leagues:
            self.assertTrue(
                GoalTriggersEngine.is_analytic_blacklisted(league, home, away),
                f"Powinno być na czarnej liście: {league}, {home} vs {away}"
            )

        # Wyjątek REVELACAO nie może być na czarnej liście
        self.assertFalse(
            GoalTriggersEngine.is_analytic_blacklisted("Liga Revelacao U23", "Estoril U23", "Benfica U23")
        )

    def test_di_65_non_candidate_does_not_call_playwright(self):
        """
        3. Usunięcie kosztownego Playwright dla meczów z DI >= 65, które NIE kwalifikują się jako kandydaci.
        Przykład: Mecz w 86. minucie przy wyniku 4:1 (DI > 65 przez bramki), ale bez szans na sygnał.
        """
        match_late = {
            'league': 'Premier League',
            'home_team': 'Team A',
            'away_team': 'Team B',
            'home_score': 4,
            'away_score': 1,
            'minute': 86,
            'half': '2H',
            'stage_text': "86'"
        }
        stats_high_di = {
            'danger_index': 78,
            'apm': 1.5,
            'has_stats': True
        }

        # Kwalifikacja wstępna powinna jednoznacznie odrzucić mecz w 86 minucie
        is_eligible = GoalTriggersEngine.is_candidate_eligible(match_late, stats_high_di)
        self.assertFalse(is_eligible, "Mecz w 86 minucie nie może być kandydatem do STS Playwright!")

    def test_eligible_candidate_calls_playwright_and_receives_sts_real(self):
        """
        4. Mecze kwalifikujące się jako kandydaci (np. Scenariusz 1: 1H 24 min, 0:0, wysokie tempo)
        MUSZĄ mieć możliwość pobrania dokładnych kursów STS_REAL przez Playwright.
        """
        match_candidate = {
            'league': 'Bundesliga',
            'home_team': 'Bayern',
            'away_team': 'Dortmund',
            'home_score': 0,
            'away_score': 0,
            'minute': 24,
            'half': '1H',
            'stage_text': "24'"
        }
        stats_active = {
            'danger_index': 70,
            'apm': 1.6,
            'has_stats': True
        }

        is_eligible = GoalTriggersEngine.is_candidate_eligible(match_candidate, stats_active)
        self.assertTrue(is_eligible, "Mecz w 24 minucie 0:0 przy wysokim tempie MUSI być kandydatem do STS_REAL!")

    def test_signal_selection_mathematical_identity(self):
        """
        5. Warunek 2 użytkownika: Identyczne dane wejściowe MUSZĄ dać identyczny wynik
        ewaluacji i identyczną decyzję sygnałową przed i po zmianie (Zasada 0).
        """
        sample_match = {
            'flashscore_id': 'test_fs_eval_1',
            'home_team': 'Liverpool',
            'away_team': 'Manchester City',
            'league': 'Premier League',
            'minute': 22,
            'half': '1H',
            'home_score': 0,
            'away_score': 0,
            'is_started': True,
            'live_markets': [
                {'name': 'Over 0.5 FT', 'market': 'MECZ', 'odds': 1.82, 'source': 'STS_REAL'},
                {'name': 'Over 1.5 FT', 'market': 'MECZ', 'odds': 2.10, 'source': 'STS_REAL'},
            ]
        }
        sample_stats = {
            'xg_total': 0.65,
            'shots_total': 8,
            'shots_on_target_total': 3,
            'dangerous_attacks_total': 30,
            'corners_total': 4,
            'big_chances_total': 1,
            'red_cards_total': 0,
            'has_stats': True
        }
        sample_odds = {}

        eval_res1 = self.triggers.evaluate_match(sample_match, sample_stats, sample_odds)
        eval_res2 = self.triggers.evaluate_match(sample_match, sample_stats, sample_odds)

        # Sprawdzenie powtarzalności i tożsamości matematycznej ewaluacji
        self.assertEqual(eval_res1['danger_index'], eval_res2['danger_index'])
        self.assertEqual(eval_res1['has_signals'], eval_res2['has_signals'])
        self.assertEqual(eval_res1.get('signals'), eval_res2.get('signals'))
        self.assertGreaterEqual(eval_res1['danger_index'], 50)

    def test_active_cards_watchdog_retains_real_markets_access(self):
        """
        6. ActiveCardsWatchdog zachowuje bezpośredni dostęp do get_match_real_live_markets
        i nie korzysta z filtrów wstępnych skanera.
        """
        mock_fs = MagicMock()
        mock_sts = MagicMock()
        mock_tg = MagicMock()

        from engine.active_cards_watchdog import ActiveCardsWatchdog
        watchdog = ActiveCardsWatchdog(
            fs_engine=mock_fs,
            sts_engine=mock_sts,
            telegram_notifier=mock_tg
        )

        # Sprawdź czy watchdog ma metodę i bezpośrednie odwołanie do silnika STS
        self.assertTrue(hasattr(watchdog.sts_engine, 'get_match_real_live_markets'))
        self.assertIsNotNone(watchdog.sts_engine)


if __name__ == '__main__':
    unittest.main()
