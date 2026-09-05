import os
import sys
import json
import statistics
from typing import List, Dict, Any

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

TELEMETRY_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'settlement_telemetry.jsonl')
CARDS_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'telegram_active_cards.json')

def load_telemetry() -> List[Dict[str, Any]]:
    records = []
    if os.path.exists(TELEMETRY_FILE):
        with open(TELEMETRY_FILE, 'r', encoding='utf-8') as f:
            for line in f:
                line_s = line.strip()
                if line_s:
                    try:
                        records.append(json.loads(line_s))
                    except Exception:
                        pass
    return records

def calculate_percentile(data: List[float], p: float) -> float:
    if not data:
        return 0.0
    sorted_data = sorted(data)
    k = (len(sorted_data) - 1) * (p / 100.0)
    f = int(k)
    c = min(f + 1, len(sorted_data) - 1)
    d = k - f
    return round(sorted_data[f] + d * (sorted_data[c] - sorted_data[f]), 2)

def generate_performance_report() -> str:
    records = load_telemetry()
    n_settled = len(records)
    
    if n_settled == 0:
        return (
            "Fast Settlement Performance\n"
            "────────────────────────────\n"
            "N settled:                0\n"
            "Status: Zbieranie pierwszych zdarzeń telemetrycznych w toku.\n"
        )
    
    e2e_latencies = [float(r.get('e2e_latency_sec', r.get('cycle_latency_sec', 0.0))) for r in records]
    tg_edit_ms = [float(r.get('telegram_edit_ms', 0.0)) for r in records]
    tg_edit_sec = [ms / 1000.0 for ms in tg_edit_ms]
    
    med_latency = statistics.median(e2e_latencies) if e2e_latencies else 0.0
    p90_latency = calculate_percentile(e2e_latencies, 90)
    p95_latency = calculate_percentile(e2e_latencies, 95)
    max_latency = max(e2e_latencies) if e2e_latencies else 0.0
    
    tg_med = statistics.median(tg_edit_sec) if tg_edit_sec else 0.0
    tg_p95 = calculate_percentile(tg_edit_sec, 95)
    
    # Weryfikacja duplikatów
    seen_fixtures = set()
    duplicates = 0
    for r in records:
        fixture_key = f"{r.get('home')}_{r.get('away')}".lower().strip()
        if fixture_key in seen_fixtures:
            duplicates += 1
        else:
            seen_fixtures.add(fixture_key)
            
    # Weryfikacja pominiętych rozliczeń
    missed_settlements = 0
    if os.path.exists(CARDS_FILE):
        try:
            with open(CARDS_FILE, 'r', encoding='utf-8') as f:
                cards_data = json.load(f)
                active = {k: v for k, v in cards_data.items() if k != '__settled_matches__'}
                for k, c in active.items():
                    if c.get('status') in ('WON', 'LOST', 'VOID'):
                        missed_settlements += 1
        except Exception:
            pass
            
    lags = [float(r['lag_from_last_live_sec']) for r in records if r.get('lag_from_last_live_sec') is not None]
    lag_line = ""
    if lags:
        med_lag = statistics.median(lags)
        p95_lag = calculate_percentile(lags, 95)
        lag_line = (
            f"\nLag from Last Live:\n"
            f"Median:                  {med_lag:.1f} s\n"
            f"P95:                     {p95_lag:.1f} s\n"
        )

    report = (
        "Fast Settlement Performance\n"
        "────────────────────────────\n"
        f"N settled:              {n_settled}\n"
        f"Median latency:          {med_latency:.1f} s\n"
        f"P90 latency:             {p90_latency:.1f} s\n"
        f"P95 latency:             {p95_latency:.1f} s\n"
        f"Max latency:             {max_latency:.1f} s\n"
        "\n"
        "Telegram update:\n"
        f"Median:                  {tg_med:.1f} s\n"
        f"P95:                     {tg_p95:.1f} s\n"
        f"{lag_line}"
        f"Duplicates:                {duplicates}\n"
        f"Missed settlements:        {missed_settlements}\n"
    )
    return report

if __name__ == '__main__':
    print(generate_performance_report())
