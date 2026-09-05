#!/usr/bin/env python3
"""
Narzędzie analityczne do empirycznej weryfikacji danych z telegram_shadow_log.jsonl
oraz kontrfaktycznej symulacji strategii (Backtest / Co by było gdyby?).

Uruchomienie:
    python analyze_shadow.py
    python analyze_shadow.py --settle   (automatycznie pobiera ostatnie wyniki z Flashscore i rozlicza bazę)
"""
import os
import sys
import json
import math
from collections import defaultdict

if sys.stdout and hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
if sys.stderr and hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8')

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_FILE = os.path.join(BASE_DIR, "telegram_shadow_log.jsonl")

# Skalibrowane korytarze kursowe
ODDS_CORRIDORS = {
    'OVER_15_FT': (1.40, 2.00),
    'POST_GOAL_FT': (1.45, 2.50),
    'OVER_25_FT': (1.45, 2.50),
    'OVER_15_HT': (1.70, 2.65),
    'OVER_05_HT': (1.50, 2.15),
    'OVER_05_2H': (1.45, 2.30),
    'OVER_1H_TO_FT': (1.45, 2.20),
}

def settle_from_flashscore():
    print("⏳ Pobieranie wyników zakończonych meczów z Flashscore...")
    try:
        from engine.flashscore_engine import FlashscoreEngine
        from engine.shadow_logger import ShadowLogger
        fs = FlashscoreEngine()
        finished = fs.get_finished_results(days_back=1)
        print(f"  • Pobrano {len(finished)} meczów z Flashscore. Rozliczanie bazy...")
        ShadowLogger().update_settled_matches_batch(finished)
        print("  ✅ Baza Shadow Tracker została zaktualizowana.")
    except Exception as e:
        print(f"  ❌ Błąd podczas rozliczania: {e}")

def calculate_wilson_ci(k, n, confidence=0.95):
    """Oblicza 95% przedział ufności Wilsona dla proporcji sukcesów (skuteczności)."""
    if n == 0:
        return (0.0, 0.0)
    z = 1.95996  # 95% dwustronny
    p = k / n
    denom = 1 + z**2 / n
    centre = (p + z**2 / (2 * n)) / denom
    spread = z * math.sqrt(p * (1 - p) / n + z**2 / (4 * n**2)) / denom
    return (round(max(0.0, centre - spread) * 100, 1), round(min(1.0, centre + spread) * 100, 1))

def calculate_metrics(items):
    total = len(items)
    if total == 0:
        return None
    wins = sum(1 for r in items if r.get('result') == 'WIN')
    losses = sum(1 for r in items if r.get('result') == 'LOSS')
    profit = sum(float(r.get('profit_units', 0.0)) for r in items)
    hr = (wins / total) * 100
    ci_95 = calculate_wilson_ci(wins, total)
    roi = (profit / total) * 100
    odds_list = [float(r.get('entry_odds', 0.0)) for r in items]
    avg_odds = sum(odds_list) / total
    sorted_odds = sorted(odds_list)
    median_odds = sorted_odds[total // 2]
    
    cum = 0.0
    peak = 0.0
    max_dd = 0.0
    streak = 0
    max_loss_streak = 0
    gross_win = 0.0
    gross_loss = 0.0
    for r in items:
        p = float(r.get('profit_units', 0.0))
        cum += p
        if cum > peak:
            peak = cum
        dd = peak - cum
        if dd > max_dd:
            max_dd = dd
            
        if r.get('result') == 'LOSS':
            streak += 1
            if streak > max_loss_streak:
                max_loss_streak = streak
            gross_loss += abs(p)
        else:
            streak = 0
            gross_win += p
            
    pf = (gross_win / gross_loss) if gross_loss > 0 else (99.9 if gross_win > 0 else 0.0)
    return {
        'total': total, 'wins': wins, 'losses': losses, 'profit': profit,
        'hr': hr, 'ci_95': ci_95, 'roi': roi, 'avg_odds': avg_odds, 'median_odds': median_odds,
        'max_dd': max_dd, 'max_loss_streak': max_loss_streak, 'profit_factor': pf
    }

def run_simulation(settled_records, min_di10, min_di5, filter_odds=True, min_sot10m=0.0, block_halftime_leak=False):
    # Sortowanie chronologiczne wg timestamp zapobiega biasowi kolejności
    sorted_records = sorted(settled_records, key=lambda r: int(r.get('timestamp', 0)))
    seen = set()
    selected = []
    
    for r in sorted_records:
        scen = r.get('scenario')
        odds = float(r.get('entry_odds', 0.0))
        m = int(r.get('minute', 0))
        is_ht = ('HT' in scen or '1. POŁ' in scen)
        
        # Opcjonalna blokada wycieku w końcówce 1H (36'-45')
        if block_halftime_leak and is_ht and 36 <= m <= 45:
            continue
            
        if filter_odds:
            corridor = ODDS_CORRIDORS.get(scen, (1.40, 2.50))
            if not (corridor[0] <= odds <= corridor[1]):
                continue
            
        # Bezpośredni odczyt di_10 i di_5 (baza jest w 100% znormalizowana, zero podwójnego skalowania)
        di10 = int(r.get('di_10', 0))
        di5 = int(r.get('di_5', 0))
        sot10 = float(r.get('sot_10m', 0.0))
        
        if sot10 < min_sot10m:
            continue
            
        if di10 >= min_di10 and di5 >= min_di5:
            # Unikalny klucz meczu (match_id lub home_away_date)
            mkey = str(r.get('match_id') or f"{r.get('home')}_{r.get('away')}_{str(r.get('date', ''))[:10]}")
            if mkey in seen:
                continue
            seen.add(mkey)
            r_copy = dict(r)
            r_copy['eval_di10'] = di10
            r_copy['eval_di5'] = di5
            selected.append(r_copy)
            
    return selected

def print_stats_table(title, items, detailed=False):
    m = calculate_metrics(items)
    if not m:
        print(f"  {title:<42}: 0 typów")
        return
    if not detailed:
        print(f"  {title:<42}: {m['total']:>3} typów | {m['wins']:>3}W - {m['losses']:>3}L | HR: {m['hr']:>5.1f}% | AvgOdds: {m['avg_odds']:.2f} | Zysk: {m['profit']:>+6.2f}J | Yield: {m['roi']:>+6.1f}%")
    else:
        ci_str = f"[{m['ci_95'][0]}%, {m['ci_95'][1]}%]"
        print(f"  {title:<42}: {m['total']:>3} typów | {m['wins']:>3}W - {m['losses']:>3}L | HR: {m['hr']:>5.1f}% (95% CI: {ci_str}) | Kursy: śr {m['avg_odds']:.2f} (med {m['median_odds']:.2f}) | Zysk: {m['profit']:>+6.2f}J | Yield: {m['roi']:>+6.1f}% | Max DD: {m['max_dd']:>5.2f}J | Max Streak L: {m['max_loss_streak']} | PF: {m['profit_factor']:.2f}")

def analyze():
    if "--settle" in sys.argv:
        settle_from_flashscore()

    if not os.path.exists(LOG_FILE):
        print(f"❌ Brak pliku logów: {LOG_FILE}")
        return

    records = []
    with open(LOG_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    records.append(json.loads(line))
                except Exception:
                    pass

    total = len(records)
    if total == 0:
        print("ℹ️ Plik telegram_shadow_log.jsonl jest obecnie pusty.")
        return

    SPLIT_TIMESTAMP = 1788636000  # 2026-09-05 21:00:00 (zamrożenie parametrów)
    BUFFER_SECONDS = 300          # 5 minut bufora ochronnego na jitter minuty, feed lag i doliczony czas

    # 1. Budujemy mapę pierwszego kontaktu oraz najbardziej konserwatywnego (najwcześniejszego) kickoffu
    match_first_seen = {}
    match_earliest_kickoff = {}
    for r in records:
        mkey = str(r.get('match_id') or f"{r.get('home')}_{r.get('away')}_{str(r.get('date', ''))[:10]}")
        ts = int(r.get('timestamp', 0))
        m = int(r.get('minute', 0))
        if ts > 0:
            if mkey not in match_first_seen or ts < match_first_seen[mkey]:
                match_first_seen[mkey] = ts
            est_k = ts - (m if m <= 45 else m + 15) * 60
            if mkey not in match_earliest_kickoff or est_k < match_earliest_kickoff[mkey]:
                match_earliest_kickoff[mkey] = est_k

    # 2. Rygorystyczna definicja czystego Out-of-Sample (OOS):
    # Mecz kwalifikuje się do OOS wyłącznie wtedy, gdy:
    # a) Nigdy nie był widziany w logach przed 21:00:00 (brak snapshotów z sesji kalibracyjnej).
    # b) Jego najwcześniejszy estymowany start (z 5-min buforem anty-jitter) nastąpił >= 21:05:00.
    #    Eliminuje to wszelkie błędy doliczonego czasu, opóźnienia zegara feedu i meczów z pogranicza 20:55-21:05.
    def is_clean_oos(r):
        mkey = str(r.get('match_id') or f"{r.get('home')}_{r.get('away')}_{str(r.get('date', ''))[:10]}")
        if match_first_seen.get(mkey, 0) < SPLIT_TIMESTAMP:
            return False
        if match_earliest_kickoff.get(mkey, 0) < (SPLIT_TIMESTAMP + BUFFER_SECONDS):
            return False
        return True

    hist_records = [r for r in records if not is_clean_oos(r)]
    oos_records = [r for r in records if is_clean_oos(r)]

    settled = [r for r in hist_records if r.get("result") in ("WIN", "LOSS")]
    accepted = [r for r in hist_records if r.get("status") == "ACCEPTED"]
    rejected = [r for r in hist_records if r.get("status") == "REJECTED"]
    shadow_5star = [r for r in hist_records if r.get("status") == "SHADOW_5STAR" or r.get("rejection_reason") == "SHADOW_ONLY_5_STAR"]

    print("=" * 85)
    print(f"📊 RAPORT EMPIRYCZNY SHADOW TRACKER (Próba Historyczna / Baseline do 21:00:00)")
    print(f"   Wpisów historycznych: {len(hist_records)} | Rozliczonych meczów: {len(settled)} | W toku: {len(hist_records) - len(settled)}")
    print("=" * 85)

    # 1. LICZNIKI OPERACYJNE (CANDIDATES & SIGNALS)
    golden_cnt = sum(1 for r in accepted if r.get("scenario") == "OVER_15_HT")
    silver_cnt = len(accepted) - golden_cnt
    print("\n📦 1. LICZNIKI OPERACYJNE SYGNALIZATORA (Sesja Kalibracyjna):")
    print(f"  • ALL CANDIDATES (Przeanalizowane zdarzenia) : {len(hist_records):>5}")
    print(f"  • 🥇 GOLDEN SIGNALS (Over 1.5 HT)             : {golden_cnt:>5}")
    print(f"  • 🥈 SILVER SIGNALS (Pozostałe 4⭐)           : {silver_cnt:>5}")
    print(f"  • ⛔ 5⭐ SHADOW TRACKED                       : {len(shadow_5star):>5}")
    print(f"  • ❌ REJECTED (Odrzucone przez lejek)         : {len(rejected):>5}")

    # 2. LEJEK FILTRÓW
    stages = defaultdict(int)
    for r in hist_records:
        stages[r.get("filter_stage", "UNKNOWN")] += 1

    print("\n🔻 2. LEJEK FILTRÓW (Gdzie odpadały sytuacje w próbie bazowej):")
    for st in sorted(stages.keys()):
        cnt = stages[st]
        pct = (cnt / len(hist_records)) * 100
        print(f"  • {st:<22} : {cnt:>4} ({pct:>5.1f}%)")

    # 3. SYMULACJA KONTRFAKTYCZNA POLITYK WEJŚCIA
    print("\n" + "-" * 85)
    print(f"🧪 3. KONTRFAKTYCZNA SYMULACJA POLITYK (Backtest na próbie {len(settled)} rozliczonych rekordów)")
    print("   Chronologiczne wejście (najwcześniejszy snapshot), 1 typ na mecz, znormalizowany DI.")
    print("-" * 85)

    policies = [
        ("Polityka 4⭐ Baza (DI10>=55, DI5>=60)", 55, 60, True, 0.0, False),
        ("Polityka 4⭐ + Filtr Końcówki 1H (blokada 36'-45')", 55, 60, True, 0.0, True),
        ("Polityka 5⭐ (DI10>=65, DI5>=70 - Shadow)", 65, 70, True, 0.0, False),
        ("🥇 GOLDEN: Over 1.5 HT (15'-35' + SoT10>=0.8)", 55, 60, True, 0.8, True),
    ]

    for label, d10, d5, fo, sot_min, block_leak in policies:
        if "GOLDEN" in label:
            ht_settled = [r for r in settled if r.get('scenario') == 'OVER_15_HT' and 15 <= int(r.get('minute', 0)) <= 35]
            res = run_simulation(ht_settled, d10, d5, filter_odds=fo, min_sot10m=sot_min, block_halftime_leak=block_leak)
            print_stats_table(label, res, detailed=True)
        else:
            res = run_simulation(settled, d10, d5, filter_odds=fo, min_sot10m=sot_min, block_halftime_leak=block_leak)
            print_stats_table(label, res, detailed=False)

    # 4. DEKOMPOZYCJA WYNIKÓW 4⭐ WG RYNKÓW
    sel_4star = run_simulation(settled, 55, 60, filter_odds=True)
    if sel_4star:
        print("\n" + "-" * 85)
        print(f"🔍 4. DEKOMPOZYCJA WYNIKÓW 4⭐ WG SCENARIUSZY (N={len(sel_4star)} typów):")
        by_scen = defaultdict(list)
        for r in sel_4star:
            by_scen[r.get('scenario', 'INNE')].append(r)
        for sc in sorted(by_scen.keys()):
            print_stats_table(sc, by_scen[sc], detailed=(sc == 'OVER_15_HT'))

    # 5. CZYSTE WYNIKI OUT-OF-SAMPLE (NOWA SESJA LIVE OD 21:00:00)
    oos_unique_matches = set(str(r.get('match_id') or f"{r.get('home')}_{r.get('away')}_{str(r.get('date', ''))[:10]}") for r in oos_records)
    oos_settled = [r for r in oos_records if r.get('result') in ('WIN', 'LOSS')]

    print("\n" + "-" * 85)
    print("🌱 5. CZYSTE WYNIKI OUT-OF-SAMPLE (Nowa Sesja Live Paper-Trading po 21:00:00)")
    print("   Definicja OOS: wyłącznie mecze rozpoczęte i zaobserwowane po 2026-09-05 21:00:00")
    print(f"   Nowe mecze OOS: {len(oos_unique_matches):>3} | Snapshoty telemetryczne: {len(oos_records):>4} | Rozliczone: {len(oos_settled):>3}")
    print("-" * 85)
    if oos_settled:
        oos_ht = [r for r in oos_settled if r.get('scenario') == 'OVER_15_HT' and 15 <= int(r.get('minute', 0)) <= 35]
        oos_golden = run_simulation(oos_ht, 55, 60, filter_odds=True, min_sot10m=0.8, block_halftime_leak=True)
        print_stats_table("Out-of-Sample GOLDEN", oos_golden, detailed=True)
        oos_4star = run_simulation(oos_settled, 55, 60, filter_odds=True)
        print_stats_table("Out-of-Sample 4⭐ (Wszystkie)", oos_4star, detailed=True)
    else:
        print("  • OOS akumuluje dane na żywo (brak meczów zanieczyszczających sprzed 21:00).")
        print("  • Rozliczenia pojawią się automatycznie po zakończeniu spotkań (python analyze_shadow.py --settle).")

    print("\n" + "=" * 85)

if __name__ == "__main__":
    analyze()


