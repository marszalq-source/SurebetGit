import unittest
from unittest.mock import patch, MagicMock
from engine.goaloo_engine import GoalooEngine

class TestGoalooParserAccuracy(unittest.TestCase):
    def setUp(self):
        self.engine = GoalooEngine()

    def test_parse_match_details_cuenca_vinotinto_case(self):
        """
        Weryfikacja przypadku Cuenca Juniors vs Vinotinto FC:
        flashdata:
        sections[8]  = "57,5,52" (att=57, shots_total=5, poss=52)
        sections[9]  = "48,5,48" (att=48, shots_total=5, poss=48)
        sections[10] = "37,2"    (dang=37, shots_on_target=2)
        sections[11] = "26,3"    (dang=26, shots_on_target=3)
        sections[12] = "4"       (yellow_cards_h=4)
        sections[13] = "0"       (red_cards_h=0)
        sections[14] = "2"       (corners_h=2)
        sections[15] = "2"       (yellow_cards_a=2)
        sections[16] = "0"       (red_cards_a=0)
        sections[17] = "4"       (corners_a=4)
        """
        sections = [""] * 20
        sections[8] = "57,5,52"
        sections[9] = "48,5,48"
        sections[10] = "37,2"
        sections[11] = "26,3"
        sections[12] = "4"
        sections[13] = "0"
        sections[14] = "2"
        sections[15] = "2"
        sections[16] = "0"
        sections[17] = "4"

        raw_flashdata = "^".join(sections)

        mock_resp = MagicMock()
        mock_resp.read.return_value = raw_flashdata.encode('utf-8')
        mock_resp.__enter__.return_value = mock_resp

        with patch.object(self.engine, 'find_match_id', return_value='12345'), \
             patch('urllib.request.urlopen', return_value=mock_resp):
            stats = self.engine.get_live_stats(home_team="Cuenca Juniors", away_team="Vinotinto FC", minute=55)

        self.assertIsNotNone(stats)
        # Strzały: NIE MOGĄ być sztucznie zawyżone do 7 i 8!
        self.assertEqual(stats['shots_total_home'], 5)
        self.assertEqual(stats['shots_on_target_home'], 2)
        self.assertEqual(stats['shots_off_target_home'], 3)

        self.assertEqual(stats['shots_total_away'], 5)
        self.assertEqual(stats['shots_on_target_away'], 3)
        self.assertEqual(stats['shots_off_target_away'], 2)

        self.assertEqual(stats['shots_total'], 10)
        self.assertEqual(stats['shots_on_target_total'], 5)

        # Rzuty rożne
        self.assertEqual(stats['corners_home'], 2)
        self.assertEqual(stats['corners_away'], 4)
        self.assertEqual(stats['corners_total'], 6)

        # Kartki
        self.assertEqual(stats['yellow_cards_home'], 4)
        self.assertEqual(stats['yellow_cards_away'], 2)
        self.assertEqual(stats['red_cards_home'], 0)
        self.assertEqual(stats['red_cards_away'], 0)

        # Ataki i posiadanie
        self.assertEqual(stats['attacks_home'], 57)
        self.assertEqual(stats['attacks_away'], 48)
        self.assertEqual(stats['dangerous_attacks_home'], 37)
        self.assertEqual(stats['dangerous_attacks_away'], 26)
        self.assertEqual(stats['possession_home'], 52)
        self.assertEqual(stats['possession_away'], 48)

    def test_shots_clamping_when_total_less_than_on_target(self):
        """
        Jeśli źródło podało shots_total < shots_on_target z powodu opóźnienia,
        shots_total powinno być zrównane do shots_on_target, a off_target = 0.
        """
        sections = [""] * 20
        sections[8] = "10,1,50"
        sections[9] = "10,0,50"
        sections[10] = "5,3"
        sections[11] = "5,1"

        raw_flashdata = "^".join(sections)

        mock_resp = MagicMock()
        mock_resp.read.return_value = raw_flashdata.encode('utf-8')
        mock_resp.__enter__.return_value = mock_resp

        with patch.object(self.engine, 'find_match_id', return_value='99999'), \
             patch('urllib.request.urlopen', return_value=mock_resp):
            stats = self.engine.get_live_stats(home_team="Team A", away_team="Team B", minute=30)

        self.assertEqual(stats['shots_on_target_home'], 3)
        self.assertEqual(stats['shots_total_home'], 3)
        self.assertEqual(stats['shots_off_target_home'], 0)

        self.assertEqual(stats['shots_on_target_away'], 1)
        self.assertEqual(stats['shots_total_away'], 1)
        self.assertEqual(stats['shots_off_target_away'], 0)

if __name__ == '__main__':
    unittest.main()
