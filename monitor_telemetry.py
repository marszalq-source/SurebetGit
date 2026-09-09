#!/usr/bin/env python3
"""
Narzędzie do monitoringu telemetrii produkcyjnej w czasie rzeczywistym.
Weryfikuje:
1. Logi WAITING_FOR_ODDS: Śledzenie meczów w stanie oczekiwania na korytarz kursowy.
2. Rozkład sygnałów Scenariusza 3 (POST_GOAL_FT) i 5 (OVER_05_2H) przy wynikach 1:0 i 0:1:
   Gwarancja 100% selekcji Over 1.5 FT i zerowej obecności Over 2.5 FT.

Uruchomienie:
    python monitor_telemetry.py
    python monitor_telemetry.py --tail 50   (analiza ostatnich N wpisów)
"""
import os
import sys
import json
from collections import defaultdict, Counter

if sys.stdout and hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SHADOW_LOG_FILE = os.path.join(BASE_DIR, "telegram_shadow_log.jsonl")
SIGNALS_FILE = os.path.join(BASE_DIR, "telegram_signals_history.json")


def load_shadow_logs(limit=None):
    if not os.path.exists(SHADOW_LOG_FILE):
        return []
    records = []
    with open(SHADOW_LOG_FILE, 'r', encoding='utf-8', errors='ignore') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except Exception:
                continue
    if limit and len(records) > limit:
        return records[-limit:]
    return records


def load_telegram_signals():
    if not os.path.exists(SIGNALS_FILE):
        return []
    try:
        with open(SIGNALS_FILE, 'r', encoding='utf-8', errors='ignore') as f:
            return json.load(f)
    except Exception:
        return []


def audit_waiting_for_odds(records):
    print("=" * 75)
    print("📊 PUNKT 1: TELEMETRIA 'WAITING_FOR_ODDS' (OCZEKIWANIE NA KURS)")
    print("=" * 75)

    waiting_entries = [
        r for r in records
        if r.get('status') == 'WAITING_FOR_ODDS' or 'WAITING_FOR_ODDS' in str(r.get('rejection_reason', ''))
    ]

    if not waiting_entries:
        print("  ℹ️ Brak zarejestrowanych zdarzeń WAITING_FOR_ODDS w bieżącej próbie logów.")
        print("     (Pojawią się automatycznie, gdy w meczu padnie gol, a kurs na Over 1.5 FT spadnie poniżej progu).")
        print()
        return

    print(f"  • Łącznie wpisów w stanie oczekiwania: {len(waiting_entries)}")

    # Grupowanie wg meczu
    by_match = defaultdict(list)
    for r in waiting_entries:
        mkey = f"{r.get('home')} vs {r.get('away')} ({r.get('date', '')[:10]})"
        by_match[mkey].append(r)

    print(f"  • Unikalnych meczów oczekujących na korytarz: {len(by_match)}")
    print("\n  Ostatnie 5 meczów w stanie oczekiwania na kurs:")
    for mkey, m_list in list(by_match.items())[-5:]:
        last_r = m_list[-1]
        print(f"    - {mkey} | Min: {last_r.get('minute')}' | Wynik: {last_r.get('score')} | Rynek: {last_r.get('market')} | Kurs: {last_r.get('entry_odds'):.2f} | Info: {last_r.get('rejection_reason')}")

    print()


def audit_scenarios_and_lines(records, signals):
    print("=" * 75)
    print("🎯 PUNKT 2: ROZKŁAD LINII DLA SCENARIUSZY 3 I 5 PRZY STANIE 1:0 / 0:1")
    print("=" * 75)

    # 1. Sprawdzenie w historii wysłanych sygnałów Telegrama
    one_goal_signals = [
        s for s in signals
        if s.get('score_initial') in ('1:0', '0:1')
    ]

    print(f"  A. Wysłane sygnały Telegrama przy wyniku 1:0 / 0:1 (Łącznie: {len(one_goal_signals)}):")
    if one_goal_signals:
        markets_count = Counter(s.get('market') for s in one_goal_signals)
        for mkt, count in markets_count.most_common():
            pct = (count / len(one_goal_signals)) * 100
            print(f"     • {mkt:<15}: {count:>3} sygnałów ({pct:>5.1f}%)")

        # Sprawdzenie ostatnich 5 sygnałów
        print("\n     Ostatnie 5 sygnałów przy stanie 1:0 / 0:1:")
        for s in one_goal_signals[-5:]:
            print(f"       [{s.get('date')} {s.get('minute')}'] {s.get('match_title')} | Start: {s.get('score_initial')} -> Koniec: {s.get('score_final')} | {s.get('market')} @ {s.get('odds')} | Status: {s.get('status')}")

    # 2. Sprawdzenie w logach Shadow (odrzucenia wyższych linii i próby przeskoku)
    blocked_jumps = [
        r for r in records
        if 'HIGHER_LINE_JUMP_BLOCKED' in str(r.get('rejection_reason', ''))
    ]

    print(f"\n  B. Blokady przeskoku na wyższe linie (FILTR D / HIGHER_LINE_JUMP_BLOCKED):")
    print(f"     • Zablokowane próby wymuszenia Over 2.5+ przy 1:0/0:1: {len(blocked_jumps)}")
    if blocked_jumps:
        print("     Ostatnie udaremnione przeskoki:")
        for r in blocked_jumps[-5:]:
            print(f"       - {r.get('home')} vs {r.get('away')} ({r.get('minute')}') | Wynik: {r.get('score')} | Niedozwolony rynek: {r.get('market')} | Powód: {r.get('rejection_reason')}")

    print("=" * 75)


def main():
    limit = 500
    if len(sys.argv) > 2 and sys.argv[1] == '--tail':
        try:
            limit = int(sys.argv[2])
        except ValueError:
            pass

    records = load_shadow_logs(limit=limit)
    signals = load_telegram_signals()

    audit_waiting_for_odds(records)
    audit_scenarios_and_lines(records, signals)


if __name__ == '__main__':
    main()
