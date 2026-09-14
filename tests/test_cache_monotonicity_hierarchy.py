import unittest
import time
from engine.sts_flashscore_aggregator import STSFlashscoreAggregator, get_stats_tier, is_newer_or_equal_stats

class TestCacheMonotonicityHierarchy(unittest.TestCase):
    def setUp(self):
        # Inicjalizacja instancji bez uruchamiania wątków w tle
        self.agg = STSFlashscoreAggregator.__new__(STSFlashscoreAggregator)
        import threading
        self.agg._cache_lock = threading.Lock()
        self.agg.cached_results = []
        self.agg.last_scan_time = 0

    def test_tier_classification(self):
        real_stats = {'source': 'GOALOO', 'has_stats': True, 'is_estimated': False, 'shots_total': 10}
        est_stats = {'source': 'STS_RADAR', 'is_estimated': True, 'has_stats': True, 'shots_total': 5}
        empty_stats = {'has_detailed_stats': False, 'shots_total': 0}

        self.assertEqual(get_stats_tier(real_stats), 3)
        self.assertEqual(get_stats_tier(est_stats), 2)
        self.assertEqual(get_stats_tier(empty_stats), 1)
        self.assertEqual(get_stats_tier({}), 1)
        self.assertEqual(get_stats_tier(None), 1)

    def test_1_real_to_empty_preserves_real(self):
        # 1. REAL -> EMPTY = zachowaj REAL
        match_id = "test_m1"
        initial = {
            'id': match_id,
            'minute': 40,
            'score_str': '1:0',
            'stats': {'source': 'GOALOO', 'has_stats': True, 'shots_total': 8, 'shots_on_target_total': 4, 'timestamp': 1000.0},
            'danger_index': 85,
            'apm': 1.2,
            'enrichment_status': 'COMPLETE'
        }
        self.agg._update_cache_entry(match_id, initial)

        empty_update = {
            'id': match_id,
            'minute': 42,
            'score_str': '1:0',
            'stats': {'has_detailed_stats': False, 'shots_total': 0},
            'danger_index': 5,
            'apm': 0.1,
            'enrichment_status': 'BASIC'
        }
        self.agg._update_cache_entry(match_id, empty_update)

        res = self.agg.cached_results[0]
        self.assertEqual(res['minute'], 42)  # Minuta zaktualizowana
        self.assertEqual(res['stats']['source'], 'GOALOO')  # REAL_STATS zachowane!
        self.assertEqual(res['stats']['shots_total'], 8)
        self.assertEqual(res['danger_index'], 85)
        self.assertEqual(res['apm'], 1.2)
        self.assertEqual(res['enrichment_status'], 'COMPLETE')

    def test_2_real_to_estimated_preserves_real(self):
        # 2. REAL -> ESTIMATED = zachowaj REAL
        match_id = "test_m2"
        initial = {
            'id': match_id,
            'minute': 50,
            'score_str': '0:0',
            'stats': {'source': 'GOALOO', 'has_stats': True, 'shots_total': 12, 'timestamp': 1000.0},
            'danger_index': 78,
            'apm': 1.1,
            'enrichment_status': 'COMPLETE'
        }
        self.agg._update_cache_entry(match_id, initial)

        est_update = {
            'id': match_id,
            'minute': 52,
            'score_str': '0:0',
            'stats': {'source': 'STS_RADAR', 'is_estimated': True, 'has_stats': True, 'shots_total': 6},
            'danger_index': 50,
            'apm': 0.8,
            'enrichment_status': 'ESTIMATED'
        }
        self.agg._update_cache_entry(match_id, est_update)

        res = self.agg.cached_results[0]
        self.assertEqual(res['minute'], 52)
        self.assertEqual(res['stats']['source'], 'GOALOO')
        self.assertEqual(res['stats']['shots_total'], 12)
        self.assertEqual(res['danger_index'], 78)
        self.assertEqual(res['enrichment_status'], 'COMPLETE')

    def test_3_real_to_newer_real_updates(self):
        # 3. REAL -> REAL nowsze = aktualizuj
        match_id = "test_m3"
        initial = {
            'id': match_id,
            'minute': 60,
            'stats': {'source': 'GOALOO', 'has_stats': True, 'shots_total': 10, 'timestamp': 1000.0},
            'danger_index': 70,
            'apm': 1.0,
            'enrichment_status': 'COMPLETE'
        }
        self.agg._update_cache_entry(match_id, initial)

        newer_real = {
            'id': match_id,
            'minute': 65,
            'stats': {'source': 'GOALOO', 'has_stats': True, 'shots_total': 14, 'timestamp': 1010.0},
            'danger_index': 88,
            'apm': 1.4,
            'enrichment_status': 'COMPLETE'
        }
        self.agg._update_cache_entry(match_id, newer_real)

        res = self.agg.cached_results[0]
        self.assertEqual(res['minute'], 65)
        self.assertEqual(res['stats']['shots_total'], 14)
        self.assertEqual(res['danger_index'], 88)
        self.assertEqual(res['apm'], 1.4)

    def test_4_real_to_older_real_preserves_newer(self):
        # 4. REAL -> REAL starsze = zachowaj nowsze wg timestampu
        match_id = "test_m4"
        newer = {
            'id': match_id,
            'minute': 75,
            'stats': {'source': 'GOALOO', 'has_stats': True, 'shots_total': 16, 'timestamp': 2000.0},
            'danger_index': 90,
            'apm': 1.5,
            'enrichment_status': 'COMPLETE'
        }
        self.agg._update_cache_entry(match_id, newer)

        older = {
            'id': match_id,
            'minute': 70,
            'stats': {'source': 'GOALOO', 'has_stats': True, 'shots_total': 12, 'timestamp': 1900.0},
            'danger_index': 75,
            'apm': 1.1,
            'enrichment_status': 'COMPLETE'
        }
        self.agg._update_cache_entry(match_id, older)

        res = self.agg.cached_results[0]
        self.assertEqual(res['minute'], 75)
        self.assertEqual(res['stats']['shots_total'], 16)
        self.assertEqual(res['danger_index'], 90)

    def test_5_empty_to_real_saves_real(self):
        # 5. EMPTY -> REAL = zapisz REAL
        match_id = "test_m5"
        empty = {
            'id': match_id,
            'minute': 10,
            'stats': {},
            'danger_index': 50,
            'apm': 0.8,
            'enrichment_status': 'BASIC'
        }
        self.agg._update_cache_entry(match_id, empty)

        real = {
            'id': match_id,
            'minute': 15,
            'stats': {'source': 'BEESPORTS', 'has_stats': True, 'shots_total': 4, 'timestamp': 1000.0},
            'danger_index': 65,
            'apm': 0.9,
            'enrichment_status': 'COMPLETE'
        }
        self.agg._update_cache_entry(match_id, real)

        res = self.agg.cached_results[0]
        self.assertEqual(res['stats']['source'], 'BEESPORTS')
        self.assertEqual(res['stats']['shots_total'], 4)
        self.assertEqual(res['danger_index'], 65)
        self.assertEqual(res['enrichment_status'], 'COMPLETE')

    def test_6_estimated_to_real_saves_real(self):
        # 6. ESTIMATED -> REAL = zapisz REAL
        match_id = "test_m6"
        est = {
            'id': match_id,
            'minute': 20,
            'stats': {'source': 'STS_RADAR', 'is_estimated': True, 'shots_total': 2},
            'danger_index': 40,
            'enrichment_status': 'ESTIMATED'
        }
        self.agg._update_cache_entry(match_id, est)

        real = {
            'id': match_id,
            'minute': 22,
            'stats': {'source': 'GOALOO', 'has_stats': True, 'shots_total': 5, 'timestamp': 1000.0},
            'danger_index': 72,
            'enrichment_status': 'COMPLETE'
        }
        self.agg._update_cache_entry(match_id, real)

        res = self.agg.cached_results[0]
        self.assertEqual(res['stats']['source'], 'GOALOO')
        self.assertEqual(res['danger_index'], 72)
        self.assertEqual(res['enrichment_status'], 'COMPLETE')

    def test_7_score_and_minute_update_independently_of_stats(self):
        # 7. wynik/minuta mogą się aktualizować niezależnie od stats
        match_id = "test_m7"
        initial = {
            'id': match_id,
            'minute': 80,
            'half': '2H',
            'score_str': '1:1',
            'home_score': 1,
            'away_score': 1,
            'stats': {'source': 'GOALOO', 'has_stats': True, 'shots_total': 18},
            'danger_index': 92,
            'enrichment_status': 'COMPLETE'
        }
        self.agg._update_cache_entry(match_id, initial)

        # Przychodzi aktualizacja z golem w minucie 85, ale bez statystyk (np. szybki worker STS)
        goal_update = {
            'id': match_id,
            'minute': 85,
            'half': '2H',
            'score_str': '1:2',
            'home_score': 1,
            'away_score': 2,
            'stats': {},
            'danger_index': 5,
            'apm': 0.1,
            'enrichment_status': 'BASIC'
        }
        self.agg._update_cache_entry(match_id, goal_update)

        res = self.agg.cached_results[0]
        self.assertEqual(res['minute'], 85)
        self.assertEqual(res['score_str'], '1:2')
        self.assertEqual(res['home_score'], 1)
        self.assertEqual(res['away_score'], 2)
        self.assertEqual(res['stats']['source'], 'GOALOO')  # Statystyki nie zostały utracone
        self.assertEqual(res['stats']['shots_total'], 18)
        self.assertEqual(res['danger_index'], 92)

    def test_8_live_markets_preserved(self):
        # 8. live_markets nie są kasowane przez pusty rekord
        match_id = "test_m8"
        initial = {
            'id': match_id,
            'minute': 30,
            'live_markets': [{'name': 'Over 1.5 FT', 'odds': 1.85, 'source': 'STS_REAL'}]
        }
        self.agg._update_cache_entry(match_id, initial)

        update_no_markets = {
            'id': match_id,
            'minute': 32,
            'live_markets': []
        }
        self.agg._update_cache_entry(match_id, update_no_markets)

        res = self.agg.cached_results[0]
        self.assertEqual(len(res['live_markets']), 1)
        self.assertEqual(res['live_markets'][0]['name'], 'Over 1.5 FT')

    def test_9_signals_preserved(self):
        # 9. signals nie są kasowane przez pusty rekord
        match_id = "test_m9"
        initial = {
            'id': match_id,
            'minute': 70,
            'stats': {'source': 'GOALOO', 'has_stats': True, 'shots_total': 15},
            'danger_index': 88,
            'has_signals': True,
            'signals': [{'type': 'LATE_GOAL_2H', 'badge': 'OVER 2.5 FT', 'stars': 4}],
            'enrichment_status': 'COMPLETE'
        }
        self.agg._update_cache_entry(match_id, initial)

        empty_stats_update = {
            'id': match_id,
            'minute': 72,
            'stats': {},
            'danger_index': 5,
            'has_signals': False,
            'signals': [],
            'enrichment_status': 'BASIC'
        }
        self.agg._update_cache_entry(match_id, empty_stats_update)

        res = self.agg.cached_results[0]
        self.assertTrue(res['has_signals'])
        self.assertEqual(len(res['signals']), 1)
        self.assertEqual(res['signals'][0]['badge'], 'OVER 2.5 FT')

    def test_10_enrichment_status_complete_does_not_drop_to_basic(self):
        # 10. enrichment_status=COMPLETE nie może cofnąć się do BASIC
        match_id = "test_m10"
        initial = {
            'id': match_id,
            'minute': 50,
            'stats': {'source': 'GOALOO', 'has_stats': True, 'shots_total': 10},
            'enrichment_status': 'COMPLETE'
        }
        self.agg._update_cache_entry(match_id, initial)

        update_basic = {
            'id': match_id,
            'minute': 55,
            'stats': {},
            'enrichment_status': 'BASIC'
        }
        self.agg._update_cache_entry(match_id, update_basic)

        res = self.agg.cached_results[0]
        self.assertEqual(res['enrichment_status'], 'COMPLETE')

    def test_case_essendon_royals_vs_avondale(self):
        """
        Dokładny test dla przypadku:
        Essendon Royals SC K vs Avondale FC K:
        REAL_STATS: shots 8(2)-9(8), corners 4:5, DI 98, APM 1.35
        następnie: Goaloo=None, BeeSports=None, Flashscore stats empty.
        Oczekiwane: minute/score aktualne, stats nadal REAL_STATS, DI=98, APM=1.35, status=COMPLETE.
        """
        match_id = "OlXuZQfc"
        real_match = {
            'id': match_id,
            'home_team': 'Essendon Royals SC K',
            'away_team': 'Avondale FC K',
            'home_score': 1,
            'away_score': 2,
            'score_str': '1:2',
            'minute': 87,
            'half': '2H',
            'stage_text': "87'",
            'stats': {
                'source': 'GOALOO',
                'has_stats': True,
                'is_estimated': False,
                'shots_total_home': 8,
                'shots_total_away': 9,
                'shots_total': 17,
                'shots_on_target_home': 2,
                'shots_on_target_away': 8,
                'shots_on_target_total': 10,
                'corners_home': 4,
                'corners_away': 5,
                'corners_total': 9,
                'apm': 1.35,
                'danger_index': 98,
                'timestamp': 1000.0
            },
            'danger_index': 98,
            'danger_index_10': 98,
            'danger_index_5': 98,
            'apm': 1.35,
            'has_signals': False,
            'signals': [],
            'enrichment_status': 'COMPLETE'
        }
        self.agg._update_cache_entry(match_id, real_match)

        # Minuta 90: Goaloo=None, BeeSports=None, Flashscore zwraca szkielet zer
        zero_stats = {
            'xg_home': 0.0, 'xg_away': 0.0, 'xg_total': 0.0,
            'possession_home': 50, 'possession_away': 50,
            'shots_total_home': 0, 'shots_total_away': 0, 'shots_total': 0,
            'shots_on_target_home': 0, 'shots_on_target_away': 0, 'shots_on_target_total': 0,
            'shots_off_target_total': 0, 'blocked_shots_total': 0,
            'corners_home': 0, 'corners_away': 0, 'corners_total': 0,
            'attacks_home': 0, 'attacks_away': 0,
            'dangerous_attacks_home': 0, 'dangerous_attacks_away': 0, 'dangerous_attacks_total': 0,
            'yellow_cards_total': 0, 'red_cards_home': 0, 'red_cards_away': 0, 'red_cards_total': 0,
            'big_chances_total': 0, 'apm': 0.0, 'danger_index': 0.0,
            'has_detailed_stats': False, 'xg_is_estimated': False
        }
        empty_update = {
            'id': match_id,
            'home_team': 'Essendon Royals SC K',
            'away_team': 'Avondale FC K',
            'home_score': 1,
            'away_score': 2,
            'score_str': '1:2',
            'minute': 90,
            'half': '2H',
            'stage_text': "90'",
            'stats': zero_stats,
            'danger_index': 5,
            'danger_index_10': 5,
            'danger_index_5': 5,
            'apm': 0.1,
            'has_signals': False,
            'signals': [],
            'enrichment_status': 'BASIC'
        }
        self.agg._update_cache_entry(match_id, empty_update)

        res = self.agg.cached_results[0]
        # Weryfikacja:
        self.assertEqual(res['minute'], 90, "Minuta musi zaktualizować się do 90'")
        self.assertEqual(res['score_str'], '1:2', "Wynik 1:2 musi być zachowany")
        self.assertEqual(res['stage_text'], "90'", "Tekst etapu musi być zaktualizowany")
        self.assertEqual(res['stats']['source'], 'GOALOO', "Źródło statystyk musi pozostać GOALOO")
        self.assertEqual(res['stats']['shots_total_home'], 8, "Strzały gospodarzy muszą wynosić 8")
        self.assertEqual(res['stats']['shots_total_away'], 9, "Strzały gości muszą wynosić 9")
        self.assertEqual(res['stats']['shots_total'], 17, "Strzały ogółem muszą wynosić 17")
        self.assertEqual(res['stats']['corners_total'], 9, "Rzuty rożne ogółem muszą wynosić 9")
        self.assertEqual(res['danger_index'], 98, "Danger Index musi pozostać 98")
        self.assertEqual(res['apm'], 1.35, "APM musi pozostać 1.35")
        self.assertEqual(res['enrichment_status'], 'COMPLETE', "Status enrichment musi pozostać COMPLETE")


if __name__ == '__main__':
    unittest.main()
