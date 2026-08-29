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
    def test_strategy_1_over_05_ht_golden_window(self):
        """Strategia 1: Over 0.5 HT w Złotym Oknie (14'-34', wynik 0:0)."""
        match_data = {
            'minute': 22,
            'half': '1H',
            'home_score': 0,
            'away_score': 0,
            'league': 'Niemcy: Bundesliga',
            'is_started': True,
            'live_markets': [
                {'name': 'Over 0.5 HT', 'market': 'Over 0.5 HT', 'odds': 1.80},
                {'name': 'Over 0.5 FT', 'market': 'Over 0.5 FT', 'odds': 1.14},
                {'name': 'Over 1.5 FT', 'market': 'Over 1.5 FT', 'odds': 1.50},
            ]
        }
        stats = {
            'xg_total': 0.60,
            'shots_total': 6,
            'shots_on_target_total': 3,
            'dangerous_attacks_total': 24,
            'corners_total': 3,
            'big_chances_total': 1,
            'red_cards_total': 0
        }
        res = self.triggers.evaluate_match(match_data, stats, {})
        self.assertTrue(res['has_signals'])
        sig = res['primary_signal']
        self.assertEqual(sig['type'], 'OVER_05_HT')
        self.assertEqual(sig['badge'], 'OVER 0.5 HT')
        self.assertGreaterEqual(sig['stars'], 2)
        self.assertGreaterEqual(sig['ev'], -0.05)

    def test_strategy_2_over_15_ht_golden_window(self):
        """Strategia 2: Over 1.5 HT w Złotym Oknie (14'-32', wynik 1:0)."""
        match_data = {
            'minute': 23,
            'half': '1H',
            'home_score': 1,
            'away_score': 0,
            'league': 'Anglia: Premier League',
            'is_started': True,
            'live_markets': [
                {'name': 'Over 1.5 HT', 'market': 'Over 1.5 HT', 'odds': 2.15},
                {'name': 'Over 1.5 FT', 'market': 'Over 1.5 FT', 'odds': 1.25},
                {'name': 'Over 2.5 FT', 'market': 'Over 2.5 FT', 'odds': 1.75},
            ]
        }
        stats = {
            'xg_total': 0.85,
            'shots_total': 7,
            'shots_on_target_total': 4,
            'dangerous_attacks_total': 26,
            'corners_total': 4,
            'big_chances_total': 1,
            'red_cards_total': 0
        }
        res = self.triggers.evaluate_match(match_data, stats, {})
        self.assertTrue(res['has_signals'])
        sig = res['primary_signal']
        self.assertEqual(sig['type'], 'OVER_15_HT')
        self.assertEqual(sig['badge'], 'OVER 1.5 HT')
        self.assertGreaterEqual(sig['stars'], 2)

    def test_strategy_3_post_goal_ft(self):
        """Strategia 3: Błyskawiczna reakcja po bramce (POST_GOAL_FT)."""
        match_data = {
            'minute': 34,
            'half': '1H',
            'home_score': 1,
            'away_score': 1,
            'league': 'Hiszpania: LaLiga',
            'is_started': True,
            'live_markets': [
                {'name': 'Over 2.5 FT', 'market': 'Over 2.5 FT', 'odds': 1.65},
                {'name': 'Over 3.5 FT', 'market': 'Over 3.5 FT', 'odds': 2.70},
            ]
        }
        stats = {
            'xg_total': 1.15,
            'shots_total': 9,
            'shots_on_target_total': 4,
            'dangerous_attacks_total': 36,
            'corners_total': 5,
            'red_cards_total': 0
        }
        res = self.triggers.evaluate_match(match_data, stats, {})
        self.assertTrue(res['has_signals'])
        sig = res['primary_signal']
        self.assertEqual(sig['type'], 'POST_GOAL_FT')
        self.assertEqual(sig['badge'], 'OVER 2.5 FT')

    def test_strategy_4_over_15_ft_early_2h(self):
        """Strategia 4: Over 1.5 FT we wczesnej 2. połowie (46'-68')."""
        match_data = {
            'minute': 52,
            'half': '2H',
            'home_score': 0,
            'away_score': 1,
            'league': 'Holandia: Eredivisie',
            'is_started': True,
            'live_markets': [
                {'name': 'Over 1.5 FT', 'market': 'Over 1.5 FT', 'odds': 1.70},
                {'name': 'Over 2.5 FT', 'market': 'Over 2.5 FT', 'odds': 2.80},
            ]
        }
        stats = {
            'xg_total': 1.30,
            'shots_total': 12,
            'shots_on_target_total': 5,
            'dangerous_attacks_total': 56,
            'corners_total': 6,
            'red_cards_total': 0
        }
        res = self.triggers.evaluate_match(match_data, stats, {})
        self.assertTrue(res['has_signals'])
        sig = res['primary_signal']
        self.assertIn(sig['type'], ('OVER_15_FT', 'POST_GOAL_FT'))
        self.assertEqual(sig['badge'], 'OVER 1.5 FT')

    def test_strategy_5_over_05_2h_late_goal(self):
        """Strategia 5: Late Goal w końcówce (63'-85')."""
        match_data = {
            'minute': 72,
            'half': '2H',
            'home_score': 1,
            'away_score': 1,
            'league': 'Włochy: Serie A',
            'is_started': True,
            'live_markets': [
                {'name': 'Over 2.5 FT', 'market': 'Over 2.5 FT', 'odds': 2.10},
                {'name': 'Over 3.5 FT', 'market': 'Over 3.5 FT', 'odds': 5.50},
            ]
        }
        stats = {
            'xg_total': 2.05,
            'shots_total': 17,
            'shots_on_target_total': 8,
            'dangerous_attacks_total': 80,
            'corners_total': 8,
            'red_cards_total': 0
        }
        res = self.triggers.evaluate_match(match_data, stats, {})
        self.assertTrue(res['has_signals'])
        sig = res['primary_signal']
        self.assertEqual(sig['badge'], 'OVER 2.5 FT')
        self.assertGreaterEqual(sig['stars'], 2)

    # -------------------------------------------------------------
    # 2. SWEET SPOTY KURSOWE I VALUE BET
    # -------------------------------------------------------------
    def test_odds_sweet_spot_acceptance_and_rejection(self):
        """Test akceptacji kursów w Sweet Spocie i odrzucania nieopłacalnych."""
        stats = {'xg_total': 0.70, 'shots_total': 6, 'shots_on_target_total': 3, 'dangerous_attacks_total': 25, 'corners_total': 3}

        # Zbyt niski kurs (np. 1.25 dla Over 0.5 HT) - brak Value
        mkt_low = {'minute': 20, 'half': '1H', 'home_score': 0, 'away_score': 0, 'league': 'Polska: Ekstraklasa', 'is_started': True,
                   'live_markets': [{'name': 'Over 0.5 HT', 'market': 'Over 0.5 HT', 'odds': 1.25}]}
        res_low = self.triggers.evaluate_match(mkt_low, stats, {})
        self.assertFalse(res_low['has_signals'])

        # Kurs idealny w Sweet Spocie (1.80)
        mkt_ideal = {'minute': 20, 'half': '1H', 'home_score': 0, 'away_score': 0, 'league': 'Polska: Ekstraklasa', 'is_started': True,
                     'live_markets': [{'name': 'Over 0.5 HT', 'market': 'Over 0.5 HT', 'odds': 1.80}]}
        res_ideal = self.triggers.evaluate_match(mkt_ideal, stats, {})
        self.assertTrue(res_ideal['has_signals'])

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

if __name__ == '__main__':
    unittest.main()
