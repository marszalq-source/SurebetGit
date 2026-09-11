import os
import json
import pytest
from engine.stats_engine import StatsEngine
from sts_live_config import POLISH_TAX_MULTIPLIER

def test_record_signal_emits_full_oos_telemetry(tmp_path):
    test_hist_file = tmp_path / "test_signals_history.json"
    test_hist_file.write_text("[]", encoding="utf-8")
    
    engine = StatsEngine(history_file=str(test_hist_file))
    
    match_data = {
        "home_team": "Team A",
        "away_team": "Team B",
        "league": "Test League",
        "minute": 45,
        "score_str": "0:1",
        "stats": {
            "shots_total": 14,
            "dangerous_attacks_total": 48,
            "corners_total": 6,
            "big_chances_total": 2,
            "xg_total": 1.82,
            "sot_10m": 1.4,
            "apm": 1.67,
            "source": "GOALOO"
        },
        "danger_index_10": 63.4,
        "danger_index_5": 71.2,
        "apm": 1.67
    }
    
    signal_data = {
        "badge": "OVER 2.5 FT",
        "odds": 1.73,
        "di10": 63.4,
        "di5": 71.2,
        "sot10m": 1.4,
        "apm": 1.67,
        "xg": 1.82,
        "shots": 14,
        "dangerous_attacks": 48,
        "corners": 6,
        "big_chances": 2,
        "tier": "SILVER",
        "is_silver": True
    }
    
    entry = engine.record_signal(match_data, signal_data, unit_tag="2J")
    
    assert entry["minute"] == 45
    assert entry["score"] == "0:1"
    assert entry["market"] == "OVER 2.5 FT"
    assert entry["odds"] == 1.73
    assert entry["di10"] == 63.4
    assert entry["di5"] == 71.2
    assert entry["sot10m"] == 1.4
    assert entry["apm"] == 1.67
    assert entry["xg"] == 1.82
    assert entry["xg_source"] == "DERIVED"
    assert entry["shots"] == 14
    assert entry["dangerous_attacks"] == 48
    assert entry["corners"] == 6
    assert entry["big_chances"] == 2
    assert entry["signal_type"] == "SILVER"
    assert entry["stats_provider"] == "GOALOO"
    assert entry["status"] == "PENDING"
    
    # Snapshot telemetrii decyzyjnej
    assert entry["decision_snapshot"]["shots"] == 14
    assert entry["decision_snapshot"]["xg"] == 1.82
    assert entry["decision_snapshot"]["xg_source"] == "DERIVED"
    assert entry["decision_snapshot"]["stats_provider"] == "GOALOO"
    assert entry["corrected_snapshot"] is None
    assert entry["data_quality"]["parser_bug_detected"] is False
    assert entry["data_quality"]["xg_source"] == "DERIVED"
    assert entry["data_quality"]["has_da"] is True
    assert entry["data_quality"]["has_bc"] is True
    
    engine.settle_signal("Team A", "Team B", "WON", "1:2", final_odds=1.73)
    updated_hist = engine.load_history()
    settled = updated_hist[0]
    
    assert settled["status"] == "WON"
    assert settled["di10"] == 63.4
    assert settled["di5"] == 71.2
    assert settled["sot10m"] == 1.4
    assert settled["apm"] == 1.67
    assert settled["xg"] == 1.82
    assert settled["xg_source"] == "DERIVED"
    assert settled["shots"] == 14
    assert settled["dangerous_attacks"] == 48
    assert settled["corners"] == 6
    assert settled["big_chances"] == 2
    assert settled["signal_type"] == "SILVER"
    assert settled["decision_snapshot"]["shots"] == 14
    assert settled["profit_units"] == round(2 * (1.73 * POLISH_TAX_MULTIPLIER - 1.0), 2)
    assert settled["profit_pln"] == round(4.0 * (1.73 * POLISH_TAX_MULTIPLIER - 1.0), 2)
    assert engine.get_stats()["profit_pln"] == settled["profit_pln"]


def test_record_signal_distinguishes_null_from_zero_for_unobserved_da(tmp_path):
    test_hist_file = tmp_path / "test_signals_history_null.json"
    test_hist_file.write_text("[]", encoding="utf-8")
    
    engine = StatsEngine(history_file=str(test_hist_file))
    
    # Feed bez statystyki groźnych ataków (DA) i bez big chances (np. Flashscore w niższej lidze)
    match_data = {
        "home_team": "Team C",
        "away_team": "Team D",
        "league": "Minor League",
        "minute": 30,
        "score_str": "0:0",
        "stats": {
            "shots_total": 8,
            "shots_on_target_total": 3,
            "corners_total": 2,
            "xg_total": 0.95
        },
        "danger_index_10": 58.0,
        "danger_index_5": 62.0,
        "apm": 0.8
    }
    
    signal_data = {
        "badge": "OVER 1.5 FT",
        "odds": 1.65,
        "di10": 58.0,
        "di5": 62.0,
        "sot10m": 1.0,
        "apm": 0.8,
        "xg": 0.95,
        "shots": 8,
        "dangerous_attacks": None,  # Nieobecne w feedzie
        "corners": 2,
        "big_chances": None,       # Nieobecne w feedzie
        "tier": "SILVER"
    }
    
    entry = engine.record_signal(match_data, signal_data, unit_tag="1J")
    
    # Kluczowa weryfikacja: brak danych nie może być zafałszowany jako 0!
    assert entry["dangerous_attacks"] is None
    assert entry["big_chances"] is None
    assert entry["shots"] == 8
    assert entry["corners"] == 2
    assert entry["decision_snapshot"]["dangerous_attacks"] is None
    assert entry["decision_snapshot"]["big_chances"] is None
    assert entry["data_quality"]["has_da"] is False
    assert entry["data_quality"]["has_bc"] is False
    assert entry["data_quality"]["xg_source"] == "DERIVED"


def test_record_signal_official_xg_from_flashscore(tmp_path):
    test_hist_file = tmp_path / "test_signals_history_official.json"
    test_hist_file.write_text("[]", encoding="utf-8")
    
    engine = StatsEngine(history_file=str(test_hist_file))
    
    match_data = {
        "home_team": "Arsenal",
        "away_team": "Chelsea",
        "league": "Premier League",
        "minute": 60,
        "score_str": "1:1",
        "stats": {
            "shots_total": 20,
            "corners_total": 5,
            "xg_total": 2.15,
            "xg_is_estimated": False,
            "source": "FLASHSCORE"
        }
    }
    
    signal_data = {
        "badge": "OVER 2.5 FT",
        "odds": 1.90,
        "xg": 2.15,
        "xg_is_estimated": False,
        "stats_provider": "FLASHSCORE",
        "tier": "GOLDEN"
    }
    
    entry = engine.record_signal(match_data, signal_data, unit_tag="3J")
    assert entry["xg_source"] == "OFFICIAL"
    assert entry["decision_snapshot"]["xg_source"] == "OFFICIAL"
    assert entry["data_quality"]["xg_source"] == "OFFICIAL"
