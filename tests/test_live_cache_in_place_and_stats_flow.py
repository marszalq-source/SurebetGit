import unittest
import time
import threading
from unittest.mock import MagicMock, patch
from engine.sts_flashscore_aggregator import STSFlashscoreAggregator

class TestLiveCacheInPlaceAndStatsFlow(unittest.TestCase):
    """
    Testy weryfikujące poprawność natychmiastowej aktualizacji cache w locie,
    bezpieczeństwa współbieżności oraz ścisłej ochrony flagi is_estimated.
    """

    def setUp(self):
        # Inicjalizacja agregatora bez uruchamiania wątków w tle
        with patch.object(STSFlashscoreAggregator, 'start_background_scanner'):
            self.agg = STSFlashscoreAggregator()

    def test_stats_appear_in_scan_all_immediately_without_waiting_for_full_scan_completion(self):
        """
        REPRODUKCJA I WERYFIKACJA FIXA:
        Start: cached_results = fresh_baseline ze stats = {}
        Po pobraniu stats dla pojedynczego meczu rekord w scan_all() ma stats NATYCHMIAST,
        nawet gdy reszta meczów wciąż czeka lub pełny skan się nie zakończył.
        """
        # 1. Startowy stan fresh_baseline
        self.agg.cached_results = [
            {
                'id': 'fs_match_1',
                'home_team': 'Wisla Krakow',
                'away_team': 'Jagiellonia',
                'minute': 80,
                'half': '2H',
                'score_str': '1:0',
                'stats': {},
                'danger_index': 50,
                'apm': 0.8,
                'worth_reasons': ['⚪ Wczytywanie statystyk...'],
                'enrichment_status': 'PENDING'
            },
            {
                'id': 'fs_match_2',
                'home_team': 'Lech Poznan',
                'away_team': 'Legia Warszawa',
                'minute': 45,
                'half': '1H',
                'score_str': '0:0',
                'stats': {},
                'danger_index': 50,
                'apm': 0.8,
                'worth_reasons': ['⚪ Wczytywanie statystyk...'],
                'enrichment_status': 'PENDING'
            }
        ]

        # Weryfikacja stanu początkowego w scan_all()
        res_before = self.agg.scan_all()
        m1_before = next(m for m in res_before['matches'] if m['id'] == 'fs_match_1')
        self.assertEqual(m1_before['stats'], {})
        self.assertEqual(m1_before['enrichment_status'], 'PENDING')
        self.assertIn('⚪ Wczytywanie statystyk...', m1_before['worth_reasons'])

        # 2. Symulacja: pobrano realne statystyki z Goaloo/BeeSports dla match_1
        enriched_m1 = dict(m1_before)
        enriched_m1['stats'] = {
            'attacks_home': 94, 'attacks_away': 87,
            'dangerous_attacks_home': 68, 'dangerous_attacks_away': 38,
            'possession_home': 40, 'possession_away': 60,
            'shots_on_target_home': 5, 'shots_on_target_away': 1,
            'shots_total_home': 13, 'shots_total_away': 5, 'shots_total': 18,
            'corners_home': 8, 'corners_away': 0, 'corners_total': 8,
            'apm': 1.23, 'danger_index': 98,
            'is_estimated': False, 'source': 'BEESPORTS', 'has_stats': True
        }
        enriched_m1['danger_index'] = 98
        enriched_m1['apm'] = 1.23
        enriched_m1['worth_reasons'] = ['🔥 Bardzo wysoki napór na bramkę (98%, 1.23 APM)']
        enriched_m1['enrichment_status'] = 'COMPLETE'

        # Natychmiastowa aktualizacja w locie
        self.agg._update_cache_entry('fs_match_1', enriched_m1)

        # 3. Sprawdzenie scan_all() NATYCHMIAST:
        # Match 1 musi mieć już pełne dane, podczas gdy Match 2 nadal czeka w PENDING
        res_after = self.agg.scan_all()
        m1_after = next(m for m in res_after['matches'] if m['id'] == 'fs_match_1')
        m2_after = next(m for m in res_after['matches'] if m['id'] == 'fs_match_2')

        self.assertTrue(m1_after['stats'].get('has_stats'))
        self.assertEqual(m1_after['stats'].get('source'), 'BEESPORTS')
        self.assertEqual(m1_after['stats'].get('shots_total'), 18)
        self.assertEqual(m1_after['danger_index'], 98)
        self.assertEqual(m1_after['enrichment_status'], 'COMPLETE')
        self.assertNotIn('⚪ Wczytywanie statystyk...', m1_after['worth_reasons'])

        # Match 2 nie został naruszony
        self.assertEqual(m2_after['stats'], {})
        self.assertEqual(m2_after['enrichment_status'], 'PENDING')

    def test_concurrent_parallel_updates_no_data_loss(self):
        """
        Weryfikacja atomowości: 2 mecze aktualizowane równolegle przez różne wątki.
        Jeden kończy się szybciej, drugi później - cache nie traci żadnego rekordu.
        """
        self.agg.cached_results = [
            {'id': f'match_{i}', 'minute': 10, 'score_str': '0:0', 'stats': {}, 'enrichment_status': 'PENDING'}
            for i in range(10)
        ]

        def worker_update(match_id, delay, stats_source):
            time.sleep(delay)
            record = {
                'id': match_id,
                'minute': 10 + int(delay * 10),
                'score_str': '1:0',
                'stats': {'source': stats_source, 'has_stats': True, 'is_estimated': False},
                'enrichment_status': 'COMPLETE'
            }
            self.agg._update_cache_entry(match_id, record)

        threads = [
            threading.Thread(target=worker_update, args=('match_1', 0.05, 'GOALOO')),
            threading.Thread(target=worker_update, args=('match_2', 0.01, 'BEESPORTS')),
            threading.Thread(target=worker_update, args=('match_3', 0.03, 'GOALOO')),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        res = self.agg.scan_all()
        matches_dict = {m['id']: m for m in res['matches']}
        self.assertEqual(matches_dict['match_1']['stats']['source'], 'GOALOO')
        self.assertEqual(matches_dict['match_2']['stats']['source'], 'BEESPORTS')
        self.assertEqual(matches_dict['match_3']['stats']['source'], 'GOALOO')
        self.assertEqual(matches_dict['match_1']['enrichment_status'], 'COMPLETE')
        self.assertEqual(matches_dict['match_4']['enrichment_status'], 'PENDING')

    def test_monotonicity_protection(self):
        """
        Zasada: nowszy stan minuty / wyniku nie może zostać cofnięty przez starszy rekord.
        """
        self.agg.cached_results = [
            {
                'id': 'm1',
                'minute': 75,
                'stage_text': "75'",
                'score_str': '2:1',
                'home_score': 2,
                'away_score': 1,
                'live_markets': [{'market': 'OVER_35_FT', 'odds': 1.85}],
                'stats': {'has_stats': True}
            }
        ]

        # Próba nadpisania starszym stanem (np. min=70, score 0:0, brak rynków)
        stale_update = {
            'id': 'm1',
            'minute': 70,
            'stage_text': "70'",
            'score_str': '0:0',
            'home_score': 0,
            'away_score': 0,
            'live_markets': [],
            'stats': {'has_stats': True, 'updated': True}
        }
        self.agg._update_cache_entry('m1', stale_update)

        res = self.agg.scan_all()
        m = res['matches'][0]
        # Minuta, wynik i rynki pozostały monotonicznie zachowane z nowszego stanu
        self.assertEqual(m['minute'], 75)
        self.assertEqual(m['score_str'], '2:1')
        self.assertEqual(len(m['live_markets']), 1)
        self.assertTrue(m['stats'].get('updated'))

    def test_estimated_stats_never_generate_signals_regression(self):
        """
        Test regresyjny bezpieczeństwa is_estimated:
        - Real stats przechodzą kwalifikację kandydata
        - Estimated stats są bezwzględnie odrzucane z kwalifikacji i sygnałów
        """
        match_data = {
            'flashscore_id': 'test_cand',
            'minute': 25,
            'half': '1H',
            'home_score': 0,
            'away_score': 0,
            'score_str': '0:0',
            'league': 'Anglia, Premier League'
        }

        # 1. Realne statystyki (Goaloo/BeeSports) spełniające kryteria naporu
        real_stats = {
            'danger_index': 75,
            'apm': 1.10,
            'shots_on_target_total': 3,
            'dangerous_attacks_total': 30,
            'has_stats': True,
            'is_estimated': False,
            'xg_is_estimated': False
        }
        self.assertTrue(self.agg.triggers.is_candidate_eligible(match_data, real_stats))

        # 2. Estymowane statystyki radaru STS (is_estimated = True)
        est_stats = dict(real_stats)
        est_stats['is_estimated'] = True
        self.assertFalse(self.agg.triggers.is_candidate_eligible(match_data, est_stats),
                         "ZASADA 0: is_estimated = True NIE MOŻE kwalifikować się jako kandydat!")

        # 3. Weryfikacja w evaluate_match
        odds_sample = {'Over 0.5 HT': 1.80, 'live_markets': [{'market': 'OVER_05_HT', 'odds': 1.80}]}
        eval_est = self.agg.triggers.evaluate_match(match_data, est_stats, odds_sample)
        self.assertFalse(eval_est.get('has_signals'),
                         "ZASADA 0: Brak sygnału dla danych estymowanych!")

if __name__ == '__main__':
    unittest.main()
