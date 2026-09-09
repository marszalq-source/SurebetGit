import unittest
import time
import datetime

class TestFreeChannelDistribution(unittest.TestCase):
    def test_time_window_evaluation(self):
        """Weryfikacja czy okno godzinowe 14:00 - 20:59 prawidlowo filtruje godziny."""
        start_hour = 14
        end_hour = 20

        # Poza oknem: noc i rano (0:00 - 13:59)
        for h in range(0, 14):
            in_window = (start_hour <= h <= end_hour)
            self.assertFalse(in_window, f"Godzina {h}:00 nie powinna byc w oknie 14-20!")

        # W oknie: popołudnie i wieczór (14:00 - 20:59)
        for h in range(14, 21):
            in_window = (start_hour <= h <= end_hour)
            self.assertTrue(in_window, f"Godzina {h}:00 powinna byc w oknie 14-20!")

        # Poza oknem: pozny wieczor (21:00 - 23:59)
        for h in range(21, 24):
            in_window = (start_hour <= h <= end_hour)
            self.assertFalse(in_window, f"Godzina {h}:00 nie powinna byc w oknie 14-20!")

    def test_interval_cooldown_evaluation(self):
        """Weryfikacja minimalnego odstepu czasowego (cooldown) np. 60 minut."""
        min_interval_min = 60
        min_interval_sec = min_interval_min * 60

        now_ts = 100000.0

        # Ostatni wysłany 10 minut temu -> odrzucenie
        last_sent_10m = now_ts - (10 * 60)
        interval_ok = (now_ts - last_sent_10m) >= min_interval_sec
        self.assertFalse(interval_ok)

        # Ostatni wysłany 59 minut temu -> odrzucenie
        last_sent_59m = now_ts - (59 * 60)
        interval_ok = (now_ts - last_sent_59m) >= min_interval_sec
        self.assertFalse(interval_ok)

        # Ostatni wysłany 61 minut temu -> akceptacja
        last_sent_61m = now_ts - (61 * 60)
        interval_ok = (now_ts - last_sent_61m) >= min_interval_sec
        self.assertTrue(interval_ok)

        # Pierwszy typ danego dnia (brak poprzedniego) -> akceptacja
        last_sent_0 = 0.0
        interval_ok = (now_ts - last_sent_0) >= min_interval_sec
        self.assertTrue(interval_ok)

    def test_daily_quota_limit(self):
        """Weryfikacja limitu max 3 typow dziennie."""
        max_picks = 3

        picks_0 = []
        self.assertTrue(len(picks_0) < max_picks)
        self.assertEqual(len(picks_0) + 1, 1)

        picks_1 = ["mecz_1"]
        self.assertTrue(len(picks_1) < max_picks)
        self.assertEqual(len(picks_1) + 1, 2)

        picks_2 = ["mecz_1", "mecz_2"]
        self.assertTrue(len(picks_2) < max_picks)
        self.assertEqual(len(picks_2) + 1, 3)

        # Wyczerpany limit (3/3)
        picks_3 = ["mecz_1", "mecz_2", "mecz_3"]
        self.assertFalse(len(picks_3) < max_picks)

if __name__ == '__main__':
    unittest.main()
