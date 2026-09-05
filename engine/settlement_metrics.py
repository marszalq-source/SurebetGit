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
            
    min_lats = [float(r['latency_min_sec']) for r in records if r.get('latency_min_sec') is not None]
    max_lats = [float(r['latency_max_sec']) for r in records if r.get('latency_max_sec') is not None]
    est_lats = [float(r['latency_est_sec']) for r in records if r.get('latency_est_sec') is not None]

    med_min = statistics.median(min_lats) if min_lats else med_latency
    p95_min = calculate_percentile(min_lats, 95) if min_lats else p95_latency
    med_max = statistics.median(max_lats) if max_lats else med_latency
    p95_max = calculate_percentile(max_lats, 95) if max_lats else p95_latency
    med_est = statistics.median(est_lats) if est_lats else med_latency
    p95_est = calculate_percentile(est_lats, 95) if est_lats else p95_latency

    windows = [float(r['uncertainty_window_sec']) for r in records if r.get('uncertainty_window_sec') is not None and float(r['uncertainty_window_sec']) > 0]
    med_window = statistics.median(windows) if windows else 0.0
    p95_window = calculate_percentile(windows, 95) if windows else 0.0

    transitions = {}
    for r in records:
        t_type = r.get('transition_type', 'N/A')
        transitions[t_type] = transitions.get(t_type, 0) + 1
    
    trans_lines = "\n".join([f"  • {k}: {v}" for k, v in sorted(transitions.items(), key=lambda x: -x[1])[:6]])

    report = (
        f"Fast Settlement Performance (N={n_settled})\n"
        "─────────────────────────────────────────────\n"
        "1. Reakcja systemu po detekcji (t_detected → t_telegram):\n"
        f"   • Median latency_min:    {med_min:.2f} s\n"
        f"   • P95 latency_min:       {p95_min:.2f} s\n"
        "\n"
        "2. Konserwatywne opóźnienie użytkownika (t_last_live → t_telegram):\n"
        f"   • Median latency_max:    {med_max:.2f} s\n"
        f"   • P95 latency_max:       {p95_max:.2f} s\n"
        f"   • Est midpoint:          {med_est:.2f} s\n"
        "\n"
        "3. Fizyczne okno niepewności pollingu (Δt = t_detected - t_last_live):\n"
        f"   • Median window:         {med_window:.2f} s\n"
        f"   • P95 window:            {p95_window:.2f} s\n"
        "\n"
        "4. Stabilność API Telegrama:\n"
        f"   • Median edit:           {tg_med:.2f} s\n"
        f"   • P95 edit:              {tg_p95:.2f} s\n"
        "\n"
        "5. Niezawodność i spójność:\n"
        f"   • Duplicates:            {duplicates}\n"
        f"   • Missed settlements:    {missed_settlements}\n"
        "\n"
        "Zaobserwowane przejścia statusu (Flashscore):\n"
        f"{trans_lines}\n"
    )
    return report

if __name__ == '__main__':
    print(generate_performance_report())
