import sys
import os
import unittest
import math
sys.path.insert(0, os.path.abspath('.'))

from engine.goal_triggers import GoalTriggersEngine
from sts_live_config import POLISH_TAX_MULTIPLIER, MIN_EV_PL_4_STAR, MIN_ODDS


class TestLinePriorityAndPoissonEV(unittest.TestCase):
    def setUp(self):
        self.triggers = GoalTriggersEngine()
        self.base_stats = {
            'xg_total': 1.10,
            'shots_total': 9,
            'shots_on_target_total': 4,
            'dangerous_attacks_total': 35,
            'corners_total': 5,
            'big_chances_total': 1,
            'red_cards_total': 0
        }

    # -------------------------------------------------------------
    # 1. MATEMATYKA ROZKŁADU POISSONA ORAZ EV_PL (k=1 vs k=2)
    # -------------------------------------------------------------
    def test_poisson_math_k1_vs_k2(self):
        """Weryfikacja różnicy w prawdopodobieństwie i EV_PL dla k=1 vs k=2 goli."""
        # W 65. minucie meczu (25 min do końca) przy kursie 1.70:
        ev_1, p_1, imp_1, edge_1, ev_pl_1 = self.triggers._calculate_expected_value(
            xg=1.0, danger_index=65, apm=0.8, sot=3, minute=65, rem_mins=25, odds=1.70, goals_needed=1
        )
        ev_2, p_2, imp_2, edge_2, ev_pl_2 = self.triggers._calculate_expected_value(
            xg=1.0, danger_index=65, apm=0.8, sot=3, minute=65, rem_mins=25, odds=1.70, goals_needed=2
        )

        self.assertGreater(p_1, p_2, "Prawdopodobieństwo 1 gola musi być znacząco wyższe niż 2 goli")
        self.assertGreater(ev_1, ev_2, "Gross EV dla 1 gola musi być znacznie wyższe niż dla 2 goli")
        self.assertGreater(ev_pl_1, ev_pl_2, "EV_PL dla 1 gola musi być znacznie wyższe niż dla 2 goli")
        self.assertGreater(p_1, 0.40, "P(X >= 1) powinno wynosić około 45-50%")
        self.assertLess(p_2, 0.20, "P(X >= 2) przy 25 minutach powinno być poniżej 20%")
        self.assertLess(ev_pl_2, -0.60, "EV_PL dla 2 goli w końcówce musi być głęboko ujemne")

    # -------------------------------------------------------------
    # 2. BLOKADA PRZESKOKU NA OVER 2.5 FT PRZY STANIE 1:0 / 0:1
    # -------------------------------------------------------------
    def test_score_1_0_blocks_over_25_ft_only(self):
        """Gdy stan to 1:0, a w ofercie jest tylko Over 2.5 FT @ 1.75, system MUSI go zablokować."""
        match_data = {
            'minute': 30,
            'half': '1H',
            'home_score': 1,
            'away_score': 0,
            'league': 'La Liga',
            'is_started': True,
            'live_markets': [
                {'name': 'Over 2.5 FT', 'market': 'Over 2.5 FT', 'odds': 1.75, 'source': 'STS_REAL'}
            ]
        }
        res = self.triggers.evaluate_match(match_data, self.base_stats, {})
        self.assertFalse(res['has_signals'], "Over 2.5 FT przy 1:0 nie może wygenerować sygnału")
        self.assertIsNone(res['primary_signal'])

    def test_score_1_0_does_not_jump_to_over_25_when_over_15_odds_too_low(self):
        """Gdy stan to 1:0, Over 1.5 FT ma za niski kurs (1.25 < 1.48), a Over 2.5 FT ma 1.75.
        System NIE MOŻE przeskoczyć na Over 2.5 FT! Sygnał musi zostać wstrzymany."""
        match_data = {
            'minute': 30,
            'half': '1H',
            'home_score': 1,
            'away_score': 0,
            'league': 'La Liga',
            'is_started': True,
            'live_markets': [
                {'name': 'Over 1.5 FT', 'market': 'Over 1.5 FT', 'odds': 1.25, 'source': 'STS_REAL'},
                {'name': 'Over 2.5 FT', 'market': 'Over 2.5 FT', 'odds': 1.75, 'source': 'STS_REAL'},
            ]
        }
        res = self.triggers.evaluate_match(match_data, self.base_stats, {})
        self.assertFalse(res['has_signals'], "Brak sygnału: Over 1.5 czeka na kurs, a Over 2.5 jest zakazany")
        self.assertIsNone(res['primary_signal'])

    # -------------------------------------------------------------
    # 3. AKCEPTACJA OVER 1.5 FT PRZY 1:0 GDY KURS W KORYTARZU
    # -------------------------------------------------------------
    def test_score_1_0_accepts_over_15_ft_in_odds_corridor(self):
        """Gdy stan to 1:0 i Over 1.5 FT osiąga odpowiedni kurs w korytarzu (np. 1.75), sygnał jest akceptowany."""
        match_data = {
            'minute': 30,
            'half': '1H',
            'home_score': 1,
            'away_score': 0,
            'league': 'La Liga',
            'is_started': True,
            'live_markets': [
                {'name': 'Over 1.5 FT', 'market': 'Over 1.5 FT', 'odds': 1.75, 'source': 'STS_REAL'},
                {'name': 'Over 2.5 FT', 'market': 'Over 2.5 FT', 'odds': 2.45, 'source': 'STS_REAL'},
            ]
        }
        res = self.triggers.evaluate_match(match_data, self.base_stats, {})
        self.assertTrue(res['has_signals'], "Over 1.5 FT przy 1:0 i kursie 1.75 musi wygenerować sygnał")
        self.assertEqual(res['primary_signal']['badge'], 'OVER 1.5 FT')
        self.assertGreaterEqual(res['primary_signal']['stars'], 4)
        self.assertGreaterEqual(res['primary_signal']['ev_pl'], MIN_EV_PL_4_STAR)

    # -------------------------------------------------------------
    # 4. PRZY STANIE 1:1 OVER 2.5 FT JEST NAJBLIŻSZĄ LINIĄ (k=1)
    # -------------------------------------------------------------
    def test_score_1_1_accepts_over_25_ft(self):
        """Gdy stan to 1:1 (total_goals = 2), najbliższą linią jest Over 2.5 FT (k=1).
        System powinien ją zaakceptować zgodnie z regułą Next Goal."""
        match_data = {
            'minute': 30,
            'half': '1H',
            'home_score': 1,
            'away_score': 1,
            'league': 'La Liga',
            'is_started': True,
            'live_markets': [
                {'name': 'Over 2.5 FT', 'market': 'Over 2.5 FT', 'odds': 1.75, 'source': 'STS_REAL'}
            ]
        }
        res = self.triggers.evaluate_match(match_data, self.base_stats, {})
        self.assertTrue(res['has_signals'], "Over 2.5 FT przy 1:1 (k=1) musi być dozwolony")
        self.assertEqual(res['primary_signal']['badge'], 'OVER 2.5 FT')
        self.assertGreaterEqual(res['primary_signal']['stars'], 4)

    # -------------------------------------------------------------
    # 5. TESTY OPTYMALIZACJI POD POLSKI PODATEK 12% I PROGI KURSOWE
    # -------------------------------------------------------------
    def test_min_odds_148_rejection_and_waiting(self):
        """Kurs 1.38 (poniżej 1.48) musi zostać wstrzymany (brak sygnału) z powodu podatku 12%."""
        match_data = {
            'minute': 30,
            'half': '1H',
            'home_score': 1,
            'away_score': 0,
            'league': 'La Liga',
            'is_started': True,
            'live_markets': [
                {'name': 'Over 1.5 FT', 'market': 'Over 1.5 FT', 'odds': 1.38, 'source': 'STS_REAL'}
            ]
        }
        res = self.triggers.evaluate_match(match_data, self.base_stats, {})
        self.assertFalse(res['has_signals'], "Kurs 1.38 < 1.48 musi zostać odrzucony / wstrzymany")

    def test_high_odds_early_minute_accepted(self):
        """Wysoki kurs 1.95 na Over 1.5 FT przy stanie 1:0 w 30' minucie generuje wysokie EV_PL i jest akceptowany."""
        match_data = {
            'minute': 30,
            'half': '1H',
            'home_score': 1,
            'away_score': 0,
            'league': 'La Liga',
            'is_started': True,
            'live_markets': [
                {'name': 'Over 1.5 FT', 'market': 'Over 1.5 FT', 'odds': 1.95, 'source': 'STS_REAL'}
            ]
        }
        res = self.triggers.evaluate_match(match_data, self.base_stats, {})
        self.assertTrue(res['has_signals'], "Kurs 1.95 na Over 1.5 FT przy 1:0 musi być dozwolony")
        sig = res['primary_signal']
        self.assertEqual(sig['badge'], 'OVER 1.5 FT')
        self.assertGreaterEqual(sig['ev_pl'], 0.05, "EV_PL dla kursu 1.95 musi przekraczać +5%")

    def test_insufficient_ev_pl_rejected(self):
        """Gdy kurs i szansa na gola dają EV_PL < 0.05, sygnał musi zostać odrzucony."""
        ev, p, imp, edge, ev_pl = self.triggers._calculate_expected_value(
            xg=0.20, danger_index=55, apm=0.85, sot=2, minute=65, rem_mins=25, odds=1.50, goals_needed=1
        )
        self.assertLess(ev_pl, 0.05, f"Oczekiwano EV_PL < 0.05, otrzymano {ev_pl}")

    def test_apm_scoring_bonus(self):
        """APM >= 1.05 powinien dawać 2 punkty w scoringu filaru 4 (bonus +1 pkt), podnosząc ocenę do 5⭐."""
        logged = []
        self.triggers.shadow_logger.log_evaluation = lambda *a, **kw: logged.append(kw)

        stats_high_apm = dict(self.base_stats)
        stats_high_apm['dangerous_attacks_total'] = 55
        stats_high_apm['shots_total'] = 12
        stats_high_apm['shots_on_target_total'] = 5

        match_data = {
            'minute': 30,
            'half': '1H',
            'home_score': 1,
            'away_score': 0,
            'league': 'La Liga',
            'is_started': True,
            'live_markets': [
                {'name': 'Over 1.5 FT', 'market': 'Over 1.5 FT', 'odds': 1.75, 'source': 'STS_REAL'}
            ]
        }
        res = self.triggers.evaluate_match(match_data, stats_high_apm, {})
        self.assertTrue(len(logged) > 0)
        eval_item = logged[-1]
        self.assertGreaterEqual(eval_item.get('raw_score', 0), 7)
        self.assertEqual(eval_item.get('stars'), 5)


if __name__ == '__main__':
    unittest.main()
