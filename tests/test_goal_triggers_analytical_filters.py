import sys
import os
import unittest
sys.path.insert(0, os.path.abspath('.'))

from engine.goal_triggers import GoalTriggersEngine

class TestGoalTriggersAnalyticalFilters(unittest.TestCase):
    def setUp(self):
        self.triggers = GoalTriggersEngine()
        self.base_stats = {
            'xg_total': 0.86,
            'shots_total': 7,
            'shots_on_target_total': 3,
            'dangerous_attacks_total': 25,
            'corners_total': 3,
            'big_chances_total': 0,
            'red_cards_total': 0
        }

    # -----------------------------------------------------------------
    # 1. FILTR STANU 0:0
    # -----------------------------------------------------------------
    def test_zero_zero_over_25_ft_rejected_after_21(self):
        """OVER 2.5 FT przy stanie 0:0 i minucie >= 21 musi byc bezwzglednie odrzucony."""
        match_data = {
            'minute': 25,
            'half': '1H',
            'home_score': 0,
            'away_score': 0,
            'league': 'Ekstraklasa',
            'is_started': True,
            'live_markets': [
                {'name': 'Over 2.5 FT', 'market': 'MECZ', 'odds': 1.85, 'source': 'STS_REAL'}
            ]
        }
        res = self.triggers.evaluate_match(match_data, self.base_stats, {})
        self.assertFalse(res['has_signals'])

    def test_zero_zero_over_15_ft_rejected_after_60(self):
        """OVER 1.5 FT przy stanie 0:0 i minucie >= 60 musi byc bezwzglednie odrzucony."""
        match_data = {
            'minute': 62,
            'half': '2H',
            'home_score': 0,
            'away_score': 0,
            'league': 'Ekstraklasa',
            'is_started': True,
            'live_markets': [
                {'name': 'Over 1.5 FT', 'market': 'MECZ', 'odds': 1.85, 'source': 'STS_REAL'}
            ]
        }
        res = self.triggers.evaluate_match(match_data, self.base_stats, {})
        self.assertFalse(res['has_signals'])

    def test_nonzero_over_25_ft_allowed_in_window_36_45(self):
        """W oknie 36-45' mecze z wynikiem roznym od 0:0 (np. 1:0, 0:1, 1:1) NIE MOGA byc blokowane przez filtr 0:0."""
        match_data = {
            'minute': 38,
            'half': '1H',
            'home_score': 1,
            'away_score': 0,
            'league': 'Ekstraklasa',
            'is_started': True,
            'live_markets': [
                {'name': 'Over 2.5 FT', 'market': 'MECZ', 'odds': 1.75, 'source': 'STS_REAL'}
            ]
        }
        res = self.triggers.evaluate_match(match_data, self.base_stats, {})
        # Upewniamy sie, ze mecz NIE zostal odrzucony przez filtr 0:0 ani koncowek
        self.assertNotIn('ZERO_ZERO', str(res.get('top_recommendation', '')))
        self.assertNotIn('wykluczony analitycznie', str(res.get('top_recommendation', '')))

    # -----------------------------------------------------------------
    # 2. FILTR KONCOWEK MECZU (MINUTA 61+)
    # -----------------------------------------------------------------
    def test_high_line_rejected_after_60(self):
        """Wysokie linie (Over 2.5, 3.5, 4.5 FT) odrzucane przy minucie >= 61."""
        match_data = {
            'minute': 65,
            'half': '2H',
            'home_score': 1,
            'away_score': 1,
            'league': 'La Liga',
            'is_started': True,
            'live_markets': [
                {'name': 'Over 2.5 FT', 'market': 'MECZ', 'odds': 1.75, 'source': 'STS_REAL'}
            ]
        }
        res = self.triggers.evaluate_match(match_data, self.base_stats, {})
        self.assertFalse(res['has_signals'])

    def test_low_line_allowed_after_60(self):
        """Niskie linie (np. Over 1.5 FT przy 0:1 w 65') NIE sa blokowane przez filtr wysokich linii."""
        match_data = {
            'minute': 65,
            'half': '2H',
            'home_score': 0,
            'away_score': 1,
            'league': 'La Liga',
            'is_started': True,
            'live_markets': [
                {'name': 'Over 1.5 FT', 'market': 'MECZ', 'odds': 1.75, 'source': 'STS_REAL'}
            ]
        }
        res = self.triggers.evaluate_match(match_data, self.base_stats, {})
        self.assertNotIn('LATE_GAME_HIGH_LINE', str(res.get('top_recommendation', '')))

    # -----------------------------------------------------------------
    # 3. CZARNA LISTA LIG ORAZ WYJATEK REVELACAO
    # -----------------------------------------------------------------
    def test_blacklist_leagues_rejected(self):
        """Ligi U21, U20, U19, U18, U17, AMATEUR, DEVELOPMENT, RESERVE oraz puchary Rumunia/Chiny/Arabia odrzucane."""
        blacklisted_examples = [
            ('Anglia Amatorzy, Professional Development League', 'Team A', 'Team B'),
            ('Szkocja, Reserve League', 'Team A', 'Team B'),
            ('Wlochy, Campionato Primavera 1 U21', 'Team A', 'Team B'),
            ('Hiszpania, Division de Honor Juvenil', 'CA Osasuna U19', 'UD Valle de Aranguren'),
            ('Brazylia, Paulista U20', 'Palmeiras U20', 'Corinthians U20'),
            ('Niemcy, U17 Bundesliga', 'Bayern U17', 'Dortmund U17'),
            ('Polska, Centralna Liga Juniorow U18', 'Legia U18', 'Lech U18'),
            ('Rumunia, Puchar', 'Team A', 'Team B'),
            ('Chiny, Puchar', 'Team A', 'Team B'),
            ('Arabia Saudyjska, Division 1', 'Team A', 'Team B')
        ]
        for lg, h_team, a_team in blacklisted_examples:
            match_data = {
                'minute': 22,
                'half': '1H',
                'home_score': 1,
                'away_score': 0,
                'league': lg,
                'home_team': h_team,
                'away_team': a_team,
                'is_started': True,
                'live_markets': [
                    {'name': 'Over 1.5 FT', 'market': 'MECZ', 'odds': 1.75, 'source': 'STS_REAL'}
                ]
            }
            res = self.triggers.evaluate_match(match_data, self.base_stats, {})
            self.assertFalse(res['has_signals'], f"Mecz/liga '{lg}' ({h_team} vs {a_team}) powinna byc odrzucona przez czarna liste!")
            self.assertIn('wykluczony analitycznie', str(res.get('top_recommendation', '')))

    def test_revelacao_exception_allowed(self):
        """Portugalia Liga Revelacao U23 NIE MOZE byc odrzucona (wyjatek)."""
        match_data = {
            'minute': 22,
            'half': '1H',
            'home_score': 1,
            'away_score': 0,
            'league': 'Portugalia, Liga Revelacao U23',
            'is_started': True,
            'live_markets': [
                {'name': 'Over 1.5 FT', 'market': 'MECZ', 'odds': 1.75, 'source': 'STS_REAL'}
            ]
        }
        res = self.triggers.evaluate_match(match_data, self.base_stats, {})
        self.assertGreaterEqual(res['danger_index'], 50)
        self.assertNotIn('wykluczony analitycznie', res.get('top_recommendation', ''))

if __name__ == '__main__':
    unittest.main()
