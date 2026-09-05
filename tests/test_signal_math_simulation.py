import sys
import os
sys.path.insert(0, os.path.abspath('.'))

import unittest
import math
from engine.goal_triggers import GoalTriggersEngine
from engine.sts_live_engine import STSLiveEngine

class TestComprehensiveSignalSimulation(unittest.TestCase):
    def setUp(self):
        self.triggers = GoalTriggersEngine()
        self.sts_engine = STSLiveEngine()

    # -------------------------------------------------------------
    # 1. STRATEGIE BRAMKOWE (5 STRATEGII)
    # -------------------------------------------------------------
    def test_strategy_1_over_ft_golden_window(self):
        """Strategia 1: Over 0.5/1.5 FT w Złotym Oknie 1H (14'-32', wynik 0:0)."""
        match_data = {
            'minute': 22,
            'half': '1H',
            'home_score': 0,
            'away_score': 0,
            'league': 'Bundesliga',
            'is_started': True,
            'live_markets': [
                {'name': 'Over 0.5 FT', 'market': 'MECZ', 'odds': 1.68, 'source': 'STS_REAL'},
                {'name': 'Over 1.5 FT', 'market': 'MECZ', 'odds': 2.10, 'source': 'STS_REAL'},
            ]
        }
        stats = {
            'xg_total': 1.25,
            'shots_total': 10,
            'shots_on_target_total': 4,
            'dangerous_attacks_total': 35,
            'corners_total': 5,
            'big_chances_total': 2,
            'red_cards_total': 0
        }
        res = self.triggers.evaluate_match(match_data, stats, {})
        self.assertTrue(res['has_signals'])
        sig = res['primary_signal']
        self.assertEqual(sig['type'], 'OVER_1H_TO_FT')
        self.assertEqual(sig['badge'], 'OVER 0.5 FT')
        self.assertGreaterEqual(sig['stars'], 4)
        self.assertGreaterEqual(sig['ev'], 0.02)

    def test_strategy_2_over_05_ht_disabled(self):
        """Strategia 2: Wyłączenie Over 0.5 HT (jako rynku o ujemnym ROI) i akceptacja tylko linii FT."""
        match_data = {
            'minute': 23,
            'half': '1H',
            'home_score': 0,
            'away_score': 0,
            'league': 'Premier League',
            'is_started': True,
            'live_markets': [
                {'name': 'Over 0.5 HT', 'market': 'Over 0.5 HT', 'odds': 1.85},
            ]
        }
        stats = {
            'xg_total': 1.2,
            'shots_total': 9,
            'shots_on_target_total': 4,
            'dangerous_attacks_total': 35,
            'corners_total': 4,
            'big_chances_total': 1,
            'red_cards_total': 0
        }
        res = self.triggers.evaluate_match(match_data, stats, {})
        # Rynki HT są wyłączone zgodnie z regułą dozwolonych linii 0.5, 1.5, 2.5 FT
        self.assertFalse(res['has_signals'])

    def test_strategy_3_post_goal_ft(self):
        """Strategia 3: Błyskawiczna reakcja po bramce (POST_GOAL_FT)."""
        match_data = {
            'minute': 30,
            'half': '1H',
            'home_score': 1,
            'away_score': 1,
            'league': 'La Liga',
            'is_started': True,
            'live_markets': [
                {'name': 'Over 2.5 FT', 'market': 'Over 2.5 FT', 'odds': 1.85, 'source': 'STS_REAL'},
                {'name': 'Over 3.5 FT', 'market': 'Over 3.5 FT', 'odds': 3.10, 'source': 'STS_REAL'},
            ]
        }
        stats = {
            'xg_total': 1.60,
            'shots_total': 12,
            'shots_on_target_total': 5,
            'dangerous_attacks_total': 45,
            'corners_total': 6,
            'big_chances_total': 2,
            'red_cards_total': 0
        }
        res = self.triggers.evaluate_match(match_data, stats, {})
        self.assertTrue(res['has_signals'])
        sig = res['primary_signal']
        self.assertEqual(sig['type'], 'POST_GOAL_FT')
        self.assertEqual(sig['badge'], 'OVER 2.5 FT')
        self.assertGreaterEqual(sig['stars'], 4)

    def test_strategy_4_over_15_ft_early_2h(self):
        """Strategia 4: Over 1.5 FT we wczesnej 2. połowie (46'-68')."""
        match_data = {
            'minute': 52,
            'half': '2H',
            'home_score': 0,
            'away_score': 1,
            'league': 'Eredivisie',
            'is_started': True,
            'live_markets': [
                {'name': 'Over 1.5 FT', 'market': 'Over 1.5 FT', 'odds': 1.70, 'source': 'STS_REAL'},
                {'name': 'Over 2.5 FT', 'market': 'Over 2.5 FT', 'odds': 2.80, 'source': 'STS_REAL'},
            ]
        }
        stats = {
            'xg_total': 2.40,
            'shots_total': 20,
            'shots_on_target_total': 8,
            'dangerous_attacks_total': 75,
            'corners_total': 8,
            'big_chances_total': 3,
            'red_cards_total': 0
        }
        res = self.triggers.evaluate_match(match_data, stats, {})
        self.assertTrue(res['has_signals'])
        sig = res['primary_signal']
        self.assertIn(sig['type'], ('OVER_15_FT', 'POST_GOAL_FT'))
        self.assertEqual(sig['badge'], 'OVER 1.5 FT')
        self.assertGreaterEqual(sig['stars'], 4)

    def test_strategy_5_over_05_2h_late_goal(self):
        """Strategia 5: Late Goal w końcówce (63'-75')."""
        match_data = {
            'minute': 68,
            'half': '2H',
            'home_score': 1,
            'away_score': 1,
            'league': 'Serie A',
            'is_started': True,
            'live_markets': [
                {'name': 'Over 2.5 FT', 'market': 'Over 2.5 FT', 'odds': 2.10, 'source': 'STS_REAL'},
                {'name': 'Over 3.5 FT', 'market': 'Over 3.5 FT', 'odds': 5.50, 'source': 'STS_REAL'},
            ]
        }
        stats = {
            'xg_total': 2.80,
            'shots_total': 24,
            'shots_on_target_total': 10,
            'dangerous_attacks_total': 85,
            'corners_total': 9,
            'big_chances_total': 3,
            'red_cards_total': 0
        }
        res = self.triggers.evaluate_match(match_data, stats, {})
        self.assertTrue(res['has_signals'])
        sig = res['primary_signal']
        self.assertEqual(sig['badge'], 'OVER 2.5 FT')
        self.assertGreaterEqual(sig['stars'], 4)

    # -------------------------------------------------------------
    # 2. SWEET SPOTY KURSOWE I VALUE BET
    # -------------------------------------------------------------
    def test_odds_sweet_spot_acceptance_and_rejection(self):
        """Test akceptacji kursów w Sweet Spocie (>= 1.60) i odrzucania zabitych kursów (< 1.60)."""
        stats = {
            'xg_total': 1.30,
            'shots_total': 10,
            'shots_on_target_total': 4,
            'dangerous_attacks_total': 36,
            'corners_total': 5,
            'big_chances_total': 2,
            'red_cards_total': 0
        }

        # Zbyt niski kurs (np. 1.35) - odrzucony przez twardy próg MIN_ODDS = 1.60
        mkt_low = {'minute': 20, 'half': '1H', 'home_score': 0, 'away_score': 0, 'league': 'Ekstraklasa', 'is_started': True,
                   'live_markets': [{'name': 'Over 0.5 FT', 'market': 'MECZ', 'odds': 1.35, 'source': 'STS_REAL'}]}
        res_low = self.triggers.evaluate_match(mkt_low, stats, {})
        self.assertFalse(res_low['has_signals'])

        # Kurs idealny w Sweet Spocie (1.80 >= 1.60)
        mkt_ideal = {'minute': 20, 'half': '1H', 'home_score': 0, 'away_score': 0, 'league': 'Ekstraklasa', 'is_started': True,
                     'live_markets': [{'name': 'Over 0.5 FT', 'market': 'MECZ', 'odds': 1.80, 'source': 'STS_REAL'}]}
        res_ideal = self.triggers.evaluate_match(mkt_ideal, stats, {})
        self.assertTrue(res_ideal['has_signals'])
        self.assertGreaterEqual(res_ideal['primary_signal']['stars'], 4)

    # -------------------------------------------------------------
    # 3. FILTROWANIE LIG ANTY-BRAMKOWYCH I ANOMALII
    # -------------------------------------------------------------
    def test_anti_goal_leagues(self):
        """Weryfikacja odrzucania lig o skrajnie niskiej bramkowości."""
        anti_leagues = [
            'Egipt: Division 2',
            'Iran: Pro League',
            'Maroko: Botola Pro',
            'Algieria: Ligue 1',
            'Grecja: Super League 2',
            'Argentyna: Primera B',
            'Kolumbia: Primera A'
        ]
        stats = {'xg_total': 0.60, 'shots_total': 6, 'shots_on_target_total': 3, 'dangerous_attacks_total': 25, 'corners_total': 3}
        for alg in anti_leagues:
            m_data = {
                'minute': 22, 'half': '1H', 'home_score': 0, 'away_score': 0,
                'league': alg, 'is_started': True,
                'live_markets': [{'name': 'Over 0.5 HT', 'market': 'Over 0.5 HT', 'odds': 1.80}]
            }
            res = self.triggers.evaluate_match(m_data, stats, {})
            self.assertFalse(res['has_signals'], f"Liga {alg} nie powinna generować sygnału Over 0.5 HT")

    def test_sterile_possession_anomaly(self):
        """Odrzucanie meczów z dużą liczbą ataków, ale 0 strzałów celnych."""
        m_data = {
            'minute': 27, 'half': '1H', 'home_score': 0, 'away_score': 0,
            'league': 'Niemcy: Bundesliga', 'is_started': True,
            'live_markets': [{'name': 'Over 0.5 HT', 'market': 'Over 0.5 HT', 'odds': 1.95}]
        }
        stats = {'xg_total': 0.05, 'shots_total': 1, 'shots_on_target_total': 0, 'dangerous_attacks_total': 40, 'corners_total': 1}
        res = self.triggers.evaluate_match(m_data, stats, {})
        self.assertFalse(res['has_signals'])

    def test_blowout_game_anomaly(self):
        """Odrzucanie meczów rozstrzygniętych (np. 5:0 w 70')."""
        m_data = {
            'minute': 70, 'half': '2H', 'home_score': 5, 'away_score': 0,
            'league': 'Anglia: Premier League', 'is_started': True,
            'live_markets': [{'name': 'Over 5.5 FT', 'market': 'Over 5.5 FT', 'odds': 2.05}]
        }
        stats = {'xg_total': 3.50, 'shots_total': 15, 'shots_on_target_total': 7, 'dangerous_attacks_total': 65, 'corners_total': 6}
        res = self.triggers.evaluate_match(m_data, stats, {})
        self.assertFalse(res['has_signals'])

    # -------------------------------------------------------------
    # 4. BRZEGOWE PRZYPADKI I DZIELENIE PRZEZ ZERO
    # -------------------------------------------------------------
    def test_boundary_and_zero_division(self):
        """Test odporności na wartości skrajne (minuty ujemne, doliczone, zerowe dane)."""
        # Ujemna minuta
        r1 = self.triggers.evaluate_match({'minute': -10, 'is_started': False}, {}, {})
        self.assertFalse(r1['has_signals'])

        # Minuta 0
        r2 = self.triggers.evaluate_match({'minute': 0, 'is_started': True, 'half': 'PRE'}, {}, {})
        self.assertFalse(r2['has_signals'])

        # Doliczony czas 95'
        m_95 = {'minute': 95, 'half': '2H', 'home_score': 0, 'away_score': 0, 'is_started': True,
                'live_markets': [{'name': 'Over 0.5 FT', 'market': 'Over 0.5 FT', 'odds': 4.50}]}
        r3 = self.triggers.evaluate_match(m_95, {'shots_total': 6, 'shots_on_target_total': 3}, {})
        self.assertFalse(r3['has_signals'])

        # Brak rynków live
        m_no_mkt = {'minute': 22, 'half': '1H', 'home_score': 0, 'away_score': 0, 'is_started': True, 'live_markets': []}
        r4 = self.triggers.evaluate_match(m_no_mkt, {'shots_total': 6, 'shots_on_target_total': 3}, {})
        self.assertFalse(r4['has_signals'])

    # -------------------------------------------------------------
    # 5. DYNAMICZNE RYNKI STS
    # -------------------------------------------------------------
    def test_dynamic_live_markets_sts(self):
        """Weryfikacja kalkulatora rynków na żywo STS."""
        # 1H 0:0, min 20
        mkts_1h = self.sts_engine.calculate_dynamic_live_markets(0, 0, 20, '1H')
        self.assertTrue(any(m['name'] == 'Over 0.5 HT' for m in mkts_1h))
        self.assertTrue(any('1. Gol: Gosp.' in m['name'] for m in mkts_1h))

        # 1H 1:0, min 25 -> Over 1.5 HT i 2. Gol
        mkts_1h_10 = self.sts_engine.calculate_dynamic_live_markets(1, 0, 25, '1H')
        self.assertTrue(any(m['name'] == 'Over 1.5 HT' for m in mkts_1h_10))
        self.assertTrue(any('2. Gol: Gosp.' in m['name'] for m in mkts_1h_10))

        # 2H 2:1, min 65 -> Over 3.5 FT i 4. Gol
        mkts_2h = self.sts_engine.calculate_dynamic_live_markets(2, 1, 65, '2H')
        self.assertTrue(any(m['name'] == 'Over 3.5 FT' for m in mkts_2h))
        self.assertTrue(any('4. Gol: Gosp.' in m['name'] for m in mkts_2h))

    # -------------------------------------------------------------
    # 6. SPÓJNOŚĆ STAWKOWANIA (1J, 2J, 3J)
    # -------------------------------------------------------------
    def test_stake_units_mapping(self):
        """Weryfikacja przypisania stawek (1J, 2J, 3J) do gwiazdek (2-5)."""
        from engine.stats_engine import StatsEngine
        stats_eng = StatsEngine()
        
        match_sample = {'home_team': 'Bayern', 'away_team': 'Dortmund', 'score_str': '0:0', 'minute': 20, 'league': 'Bundesliga'}
        
        # 2 gwiazdki -> 1J (2 zł)
        sig_1j = stats_eng.record_signal(match_sample, {'badge': 'OVER 0.5 HT', 'odds': 1.80, 'stars': 2}, unit_tag="1J")
        self.assertEqual(sig_1j['units'], 1)
        self.assertEqual(sig_1j['stake_pln'], 2.0)
        
        # 3 gwiazdki -> 2J (4 zł)
        sig_2j = stats_eng.record_signal(match_sample, {'badge': 'OVER 0.5 HT', 'odds': 1.95, 'stars': 3}, unit_tag="2J")
        self.assertEqual(sig_2j['units'], 2)
        self.assertEqual(sig_2j['stake_pln'], 4.0)

        # 4 gwiazdki -> 3J (6 zł - MAX)
        sig_3j = stats_eng.record_signal(match_sample, {'badge': 'OVER 0.5 HT', 'odds': 2.10, 'stars': 4}, unit_tag="3J")
        self.assertEqual(sig_3j['units'], 3)
        self.assertEqual(sig_3j['stake_pln'], 6.0)

    # -------------------------------------------------------------
    # 7. SERIA CZASOWA, INERCJA W 2H I COOL-DOWN PO BRAMCE
    # -------------------------------------------------------------
    def test_2h_storm_after_60_mins_of_boredom(self):
        """Weryfikacja eliminacji inercji: nagły napór w 70' po 60 minutach nudy wyzwala sygnał."""
        import time
        t0 = time.time() - 600

        m60 = {
            'flashscore_id': 'test_storm_2h',
            'league': 'La Liga',
            'home_team': 'Real Madrid',
            'away_team': 'Sevilla',
            'minute': 60,
            'half': '2H',
            'is_started': True,
            'home_score': 0,
            'away_score': 0,
            'timestamp': t0,
            'live_markets': [{'name': 'OVER 0.5 FT', 'market': 'MECZ', 'odds': 1.85, 'source': 'STS_REAL'}]
        }
        s60 = {'shots_total': 2, 'shots_on_target_total': 0, 'dangerous_attacks_total': 10, 'corners_total': 1, 'xg_total': 0.10, 'red_cards_total': 0, 'big_chances_total': 0}
        r60 = self.triggers.evaluate_match(m60, s60, {})
        self.assertFalse(r60['has_signals'])

        # Minuta 70: 10 minut później, nagła nawałnica (+5 strzałów, +3 celne, +18 ataków)
        m70 = dict(m60)
        m70['minute'] = 70
        m70['timestamp'] = t0 + 600
        s70 = {'shots_total': 7, 'shots_on_target_total': 3, 'dangerous_attacks_total': 28, 'corners_total': 3, 'xg_total': 0.55, 'red_cards_total': 0, 'big_chances_total': 1}
        r70 = self.triggers.evaluate_match(m70, s70, {})
        self.assertTrue(r70['has_signals'])
        self.assertGreaterEqual(r70['danger_index'], 80)
        self.assertEqual(r70['primary_signal']['badge'], 'OVER 0.5 FT')
        self.assertGreaterEqual(r70['primary_signal']['stars'], 4)

    def test_goal_cooldown_protection(self):
        """Weryfikacja blokady cool-down po bramce (5 minut kwarantanny na reset rynku)."""
        import time
        t_base = time.time() - 360

        m_base = {
            'flashscore_id': 'test_cd_fixture',
            'league': 'Premier League',
            'home_team': 'Arsenal',
            'away_team': 'Chelsea',
            'minute': 50,
            'half': '2H',
            'is_started': True,
            'home_score': 0,
            'away_score': 0,
            'timestamp': t_base,
            'live_markets': [{'name': 'OVER 1.5 FT', 'market': 'MECZ', 'odds': 1.80, 'source': 'STS_REAL'}]
        }
        s_base = {'shots_total': 10, 'shots_on_target_total': 4, 'dangerous_attacks_total': 35, 'corners_total': 4, 'xg_total': 1.2, 'red_cards_total': 0, 'big_chances_total': 1}
        self.triggers.evaluate_match(m_base, s_base, {})

        # Minuta 52: Padła bramka (1:0)
        m_goal = dict(m_base)
        m_goal['minute'] = 52
        m_goal['home_score'] = 1
        m_goal['timestamp'] = t_base + 120
        r_goal = self.triggers.evaluate_match(m_goal, s_base, {})
        self.assertFalse(r_goal['has_signals'])
        self.assertIn('Cool-down po bramce', r_goal.get('top_recommendation', ''))

        # Minuta 54: 2 minuty po bramce -> nadal cool-down
        m_cd = dict(m_goal)
        m_cd['minute'] = 54
        m_cd['timestamp'] = t_base + 240
        r_cd = self.triggers.evaluate_match(m_cd, s_base, {})
        self.assertFalse(r_cd['has_signals'])
        self.assertIn('Cool-down po bramce', r_cd.get('top_recommendation', ''))

    def test_multicollinearity_isolated_shot(self):
        """Pojedynczy strzał celny nie może sztucznie pompować Danger Index do 90%."""
        m = {
            'flashscore_id': 'test_single_shot',
            'minute': 20,
            'half': '1H',
            'home_team': 'Team X',
            'away_team': 'Team Y',
            'home_score': 0,
            'away_score': 0,
            'league': 'Ekstraklasa',
            'is_started': True,
            'live_markets': [{'name': 'Over 0.5 FT', 'market': 'MECZ', 'odds': 1.80, 'source': 'STS_REAL'}]
        }
        # Tylko 1 celny strzał, brak naporu terytorialnego
        stats = {'xg_total': 0.10, 'shots_total': 1, 'shots_on_target_total': 1, 'dangerous_attacks_total': 6, 'corners_total': 0, 'big_chances_total': 0, 'red_cards_total': 0}
        res = self.triggers.evaluate_match(m, stats, {})
        self.assertFalse(res['has_signals'])
        self.assertLess(res['danger_index'], 50)

    # -------------------------------------------------------------
    # 6. TESTY CZYSZCZENIA PAMIĘCI RAM I OCHRONY PRZED WYCIEKAMI (24/7)
    # -------------------------------------------------------------
    def test_ram_buffer_cleanup_finished_matches(self):
        """Mecze ze statusem FT są poprawnie usuwane z bufora pamięci RAM po 300s."""
        m_id = 'test_match_finished'
        m = {
            'flashscore_id': m_id,
            'minute': 90,
            'half': '2H',
            'home_team': 'Team A',
            'away_team': 'Team B',
            'home_score': 2,
            'away_score': 1,
            'stage_text': 'FT',
            'is_finished': True,
            'is_started': True
        }
        t0 = 1000000.0
        m['timestamp'] = t0
        self.triggers.evaluate_match(m, {'shots_total': 10}, {})
        self.assertIn(m_id, self.triggers._match_history)

        # Przed upływem 300s mecz nadal jest w pamięci
        self.triggers._last_cleanup_ts = 0.0
        self.triggers._cleanup_old_history(t0 + 200.0)
        self.assertIn(m_id, self.triggers._match_history)

        # Po 350s (ponad 300s) zakończony mecz FT zostaje bezwzględnie usunięty z RAM
        self.triggers._last_cleanup_ts = 0.0
        self.triggers._cleanup_old_history(t0 + 350.0)
        self.assertNotIn(m_id, self.triggers._match_history)

    def test_ram_buffer_cleanup_unseen_matches(self):
        """Mecze, które zniknęły z feedu live, są czyszczone metodą cleanup_unseen_matches."""
        m1 = 'active_match_1'
        m2 = 'dropped_match_2'
        t0 = 1000000.0
        self.triggers._record_snapshot_and_get_deltas(m1, {'time': t0, 'minute': 30, 'half': '1H', 'home_score': 0, 'away_score': 0, 'total_goals': 0, 'shots': 5, 'sot': 2, 'dangerous_attacks': 20, 'corners': 2, 'xg': 0.4, 'big_chances': 0, 'is_finished': False})
        self.triggers._record_snapshot_and_get_deltas(m2, {'time': t0, 'minute': 30, 'half': '1H', 'home_score': 0, 'away_score': 0, 'total_goals': 0, 'shots': 5, 'sot': 2, 'dangerous_attacks': 20, 'corners': 2, 'xg': 0.4, 'big_chances': 0, 'is_finished': False})
        
        self.assertIn(m1, self.triggers._match_history)
        self.assertIn(m2, self.triggers._match_history)

        # Feed zawiera tylko m1; m2 nie było widziane od 700s (>600s)
        self.triggers.cleanup_unseen_matches(active_live_keys={m1}, now=t0 + 700.0)
        self.assertIn(m1, self.triggers._match_history)
        self.assertNotIn(m2, self.triggers._match_history)

if __name__ == '__main__':
    unittest.main()
