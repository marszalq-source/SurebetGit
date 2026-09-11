#!/usr/bin/env python3
"""
Narzędzie do empirycznej analizy Out-of-Sample (OOS) z telegram_signals_history.json.
Zapewnia ścisłą separację:
  1. OOS TOTAL        - pełna historia rzeczywistych zagrań
  2. OOS CLEAN        - tylko czyste dane bez błędów parserów/feedów
  3. OOS DATA-ANOMALY - rekordy z wykrytymi anomaliami wejściowymi (parser_bug_detected)

Uruchomienie:
    python analyze_oos.py
"""

import os
import sys
import json
import math
from collections import defaultdict
from typing import List, Dict, Any, Optional, Tuple

if sys.stdout and hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
if sys.stderr and hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8')

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
HISTORY_FILE = os.path.join(BASE_DIR, "telegram_signals_history.json")


def calculate_wilson_ci(k: int, n: int, confidence: float = 0.95) -> Tuple[float, float]:
    """Oblicza dwustronny przedział ufności Wilsona dla proporcji sukcesów (skuteczności)."""
    if n == 0:
        return (0.0, 0.0)
    z = 1.95996  # 95% dwustronny
    p = k / n
    denom = 1 + z**2 / n
    centre = (p + z**2 / (2 * n)) / denom
    spread = z * math.sqrt(p * (1 - p) / n + z**2 / (4 * n**2)) / denom
    return (round(max(0.0, centre - spread) * 100, 1), round(min(1.0, centre + spread) * 100, 1))


def calculate_metrics(items: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    settled = [r for r in items if r.get('status') in ('WON', 'LOST', 'VOID')]
    total = len(settled)
    if total == 0:
        return None

    wins = sum(1 for r in settled if r.get('status') == 'WON')
    losses = sum(1 for r in settled if r.get('status') == 'LOST')
    voids = sum(1 for r in settled if r.get('status') == 'VOID')
    decided = wins + losses

    profit_u = sum(float(r.get('profit_units', 0.0)) for r in settled)
    profit_pln = sum(float(r.get('profit_pln', 0.0)) for r in settled)
    stake_u = sum(float(r.get('units', 1.0)) for r in settled)

    hr = (wins / decided * 100) if decided > 0 else 0.0
    ci_95 = calculate_wilson_ci(wins, decided) if decided > 0 else (0.0, 0.0)
    roi = (profit_u / stake_u * 100) if stake_u > 0 else 0.0

    odds_list = [float(r.get('odds', 0.0)) for r in settled if float(r.get('odds', 0.0)) > 1.0]
    avg_odds = (sum(odds_list) / len(odds_list)) if odds_list else 0.0

    cum = 0.0
    peak = 0.0
    max_dd = 0.0
    streak = 0
    max_loss_streak = 0
    gross_win = 0.0
    gross_loss = 0.0

    for r in settled:
        p = float(r.get('profit_units', 0.0))
        cum += p
        if cum > peak:
            peak = cum
        dd = peak - cum
        if dd > max_dd:
            max_dd = dd

        if r.get('status') == 'LOST':
            streak += 1
            if streak > max_loss_streak:
                max_loss_streak = streak
            gross_loss += abs(p)
        elif r.get('status') == 'WON':
            streak = 0
            gross_win += p

    pf = (gross_win / gross_loss) if gross_loss > 0 else (99.9 if gross_win > 0 else 0.0)

    return {
        'total': total,
        'wins': wins,
        'losses': losses,
        'voids': voids,
        'decided': decided,
        'profit_u': round(profit_u, 2),
        'profit_pln': round(profit_pln, 2),
        'hr': round(hr, 1),
        'ci_95': ci_95,
        'roi': round(roi, 1),
        'avg_odds': round(avg_odds, 2),
        'profit_factor': round(pf, 2),
        'max_dd': round(max_dd, 2),
        'max_loss_streak': max_loss_streak
    }


def print_metrics_row(label: str, m: Optional[Dict[str, Any]]):
    if not m:
        print(f"| {label:<24} | {'BRAK DANYCH':<66} |")
        return
    ci_str = f"[{m['ci_95'][0]:>4.1f}% - {m['ci_95'][1]:>4.1f}%]"
    pf_str = f"{m['profit_factor']:>4.2f}" if m['profit_factor'] < 90 else "  INF"
    print(
        f"| {label:<24} | N={m['total']:>3} (W:{m['wins']:>2} L:{m['losses']:>2}) | "
        f"HR: {m['hr']:>5.1f}% {ci_str:<15} | "
        f"ROI: {m['roi']:>+6.1f}% | P: {m['profit_u']:>+6.2f}J | PF: {pf_str} | DD: {m['max_dd']:>4.1f}J |"
    )


def load_history() -> List[Dict[str, Any]]:
    if not os.path.exists(HISTORY_FILE):
        return []
    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def run_oos_analysis():
    history = load_history()
    if not history:
        print("Brak historii sygnałów w telegram_signals_history.json.")
        return

    total_records = history
    clean_records = []
    anomaly_records = []

    for r in history:
        dq = r.get('data_quality') or {}
        if dq.get('parser_bug_detected'):
            anomaly_records.append(r)
        else:
            clean_records.append(r)

    print("=" * 115)
    print("                    AUDYT I ANALIZA OUT-OF-SAMPLE (OOS) - PROFILE SKUTECZNOŚCI")
    print("=" * 115)
    print(f"  • Łącznie zarejestrowanych sygnałów: {len(history)}")
    print(f"  • Czyste rekordy (OOS CLEAN):        {len(clean_records)}")
    print(f"  • Zgłoszone anomalie (DATA-ANOMALY): {len(anomaly_records)}")
    print("-" * 115)

    m_total = calculate_metrics(total_records)
    m_clean = calculate_metrics(clean_records)
    m_anom = calculate_metrics(anomaly_records)

    print_metrics_row("OOS TOTAL", m_total)
    print_metrics_row("OOS CLEAN", m_clean)
    print_metrics_row("OOS DATA-ANOMALY", m_anom)
    print("-" * 115)

    # Rozbicie wg rynków wewnątrz OOS CLEAN
    print("\n" + "=" * 115)
    print("                       SUB-PROFILE RYNKOWE (W RAMACH OOS CLEAN)")
    print("=" * 115)

    markets = defaultdict(list)
    for r in clean_records:
        mkt = r.get('market', 'INNY')
        markets[mkt].append(r)

    for mkt, recs in sorted(markets.items(), key=lambda x: len(x[1]), reverse=True):
        m = calculate_metrics(recs)
        print_metrics_row(mkt, m)

    # Rozbicie wg Tierów / SigType
    print("\n" + "=" * 115)
    print("                       TIERY SYGNAŁÓW (W RAMACH OOS CLEAN)")
    print("=" * 115)

    tiers = defaultdict(list)
    for r in clean_records:
        t = r.get('signal_type', 'SILVER')
        tiers[t].append(r)

    for t, recs in sorted(tiers.items(), key=lambda x: len(x[1]), reverse=True):
        m = calculate_metrics(recs)
        print_metrics_row(t, m)

    # Rozbicie wg dostawców statystyk
    print("\n" + "=" * 115)
    print("                  DOSTAWCY STATYSTYK (PROWIDENCY W OOS CLEAN)")
    print("=" * 115)

    providers = defaultdict(list)
    for r in clean_records:
        p = r.get('stats_provider', 'FLASHSCORE')
        providers[p].append(r)

    for p, recs in sorted(providers.items(), key=lambda x: len(x[1]), reverse=True):
        m = calculate_metrics(recs)
        print_metrics_row(p, m)

    print("=" * 115 + "\n")


if __name__ == "__main__":
    run_oos_analysis()
