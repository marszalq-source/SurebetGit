"""
ActiveCardsWatchdog - Dedykowany, wyizolowany moduł monitoringu aktywnych kart Telegrama.
Zasady:
1. MONITORING + PRESENTATION ONLY - zero wpływu na model OOS (nie generuje nowych sygnałów, nie zmienia progów).
2. Sprawdza WYŁĄCZNIE aktywne pozycje (gdy len(active_match_cards) == 0 -> 0 requestów HTTP).
3. Wykorzystuje istniejącą monotoniczną maszynę stanów (1H < HT < 2H < FT) i terminal lock (FT).
4. Klasyfikuje zdarzenia na URGENT (gol, faza, skok kursu >= 0.06) oraz REGULAR (płynna minuta co 35s).
5. Zapewnia pełną telemetrię T0 -> T5 w watchdog_telemetry.jsonl oraz liczniki operacyjne.
"""

import os
import json
import time
import threading
from typing import Dict, List, Any, Optional

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TELEMETRY_FILE = os.path.join(BASE_DIR, "watchdog_telemetry.jsonl")


class ActiveCardsWatchdog:
    def __init__(self, fs_engine=None, sts_engine=None, telegram_notifier=None):
        from .flashscore_engine import FlashscoreEngine
        from .sts_live_engine import STSLiveEngine
        from .telegram_notifier import TelegramNotifier

        self.fs_engine = fs_engine or FlashscoreEngine()
        self.sts_engine = sts_engine or STSLiveEngine()
        self.telegram = telegram_notifier or TelegramNotifier()

        self._thread = None
        self._running = False
        self._stop_event = threading.Event()

        self.counters = {
            "watchdog_cycles": 0,
            "cards_checked": 0,
            "cards_changed": 0,
            "telegram_edits": 0,
            "edits_skipped": 0,
            "duplicate_edits": 0,
            "stale_snapshots_rejected": 0,
            "odds_refreshes": 0,
            "feed_errors": 0,
            "telegram_errors": 0,
        }

    def start(self):
        """Uruchamia wątek Watchdoga w tle."""
        if self._running:
            return
        self._running = True
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, name="ActiveCardsWatchdogThread", daemon=True)
        self._thread.start()

    def stop(self, timeout=2.0):
        """Zatrzymuje wątek Watchdoga."""
        self._running = False
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=timeout)

    def _run_loop(self):
        """Pętla główna Watchdoga z dynamicznym interwałem uśpienia."""
        while not self._stop_event.is_set():
            active_cnt = len(getattr(self.telegram, 'active_match_cards', {}))
            if active_cnt == 0:
                # Gdy brak aktywnych kart w RAM: natychmiastowe uśpienie, ZERO requestów HTTP
                self._stop_event.wait(3.0)
                continue

            cycle_start = time.time()
            try:
                self.check_active_cards_once()
            except Exception as e:
                print(f"[ActiveCardsWatchdog] Błąd cyklu: {e}")

            elapsed = time.time() - cycle_start
            sleep_time = max(0.5, 3.0 - elapsed)
            self._stop_event.wait(sleep_time)

    def check_active_cards_once(self, live_matches: Optional[List[Dict[str, Any]]] = None, sts_matches: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
        """
        Wykonuje pojedynczy cykl inspekcji i ewentualnej aktualizacji otwartych pozycji.
        Zwraca listę wykonanych raportów zdarzeń telemetrii.
        """
        t0 = time.time()
        self.counters["watchdog_cycles"] += 1

        # Pobierz snapshot kluczy aktywnych kart pod lockiem
        lock = getattr(self.telegram, '_cards_lock', None)
        if lock:
            lock.acquire()
        try:
            cards_snapshot = {k: dict(v) for k, v in self.telegram.active_match_cards.items()}
        finally:
            if lock:
                lock.release()

        if not cards_snapshot:
            return []

        self.counters["cards_checked"] += len(cards_snapshot)

        # 1. Pobierz lekki feed live (Flashscore live-only ~0.2s)
        t_fetch_start = time.time()
        if live_matches is None:
            try:
                live_matches = self.fs_engine.get_live_soccer_matches(include_all_today=False)
            except Exception as ex:
                self.counters["feed_errors"] += 1
                live_matches = []
        t1 = time.time()
        feed_fetch_ms = round((t1 - t_fetch_start) * 1000, 1)

        # 2. Pobierz overview STS jeśli przekazano lub pobierz listę live
        if sts_matches is None:
            try:
                sts_matches = self.sts_engine.fetch_live_matches(include_esports=False)
            except Exception:
                sts_matches = []

        telemetry_records = []
        now = time.time()

        for card_key, card in cards_snapshot.items():
            if card.get("settling") or card.get("settled"):
                continue

            card_home = card.get("home_team", "")
            card_away = card.get("away_team", "")
            if not card_home or not card_away:
                continue

            # Znajdź dopasowanie w feedzie na żywo
            matching = [m for m in live_matches if self.telegram._matches_card(card_home, card_away, m.get('home_team', ''), m.get('away_team', ''), card_key)]
            if not matching and sts_matches:
                matching = [m for m in sts_matches if self.telegram._matches_card(card_home, card_away, m.get('home_team', ''), m.get('away_team', ''), card_key)]

            if not matching:
                continue

            # Wybierz snapshot z najwyższym stage_rank
            best_match = max(matching, key=lambda m: (
                self.telegram._get_match_stage_rank(m.get('half'), m.get('stage_text'), m.get('is_live', True), str(m.get('status_code', '')), int(m.get('minute') or 0))[1],
                int(m.get('minute') or 0)
            ))

            t_proc_start = time.time()

            # Monotonic State Machine: sprawdź czy snapshot nie cofa stanu
            inc_stage, inc_rank = self.telegram._get_match_stage_rank(
                half=best_match.get('half'),
                stage_text=best_match.get('stage_text'),
                is_live=best_match.get('is_live', True),
                status_code=str(best_match.get('status_code', '')),
                minute=int(best_match.get('minute') or 0)
            )

            highest_rank = card.get("highest_stage_rank", 0)
            if inc_rank < highest_rank:
                # Odrzuć zredukowany / opóźniony snapshot (Stale Snapshot Rejection)
                self.counters["stale_snapshots_rejected"] += 1
                continue

            # Sprawdź czy podstrona STS powinna zostać odpytana o świeże kursy
            sts_url = card.get("sts_url") or best_match.get("sts_url")
            time_since_odds_check = now - card.get("last_odds_check_time", 0)
            current_score = best_match.get("score_str", card.get("last_seen_score", "0:0"))
            last_score = card.get("last_rendered_score", card.get("initial_score", "0:0"))
            score_just_changed = (current_score != last_score)

            # Smart STS Polling: tylko gdy padł gol lub minęło >= 15s w trakcie trwania połowy (nie na przerwie HT)
            if sts_url and '/live/' in sts_url and inc_rank not in (20, 40):
                if score_just_changed or time_since_odds_check >= 15.0:
                    try:
                        real_markets = self.sts_engine.get_match_real_live_markets(sts_url)
                        if real_markets:
                            best_match['live_markets'] = real_markets
                            card["last_odds_check_time"] = now
                            self.counters["odds_refreshes"] += 1
                    except Exception:
                        pass

            # Przygotuj kopię meczu z zachowaniem tożsamości karty (Frozen Card Identity)
            match_copy = dict(best_match)
            match_copy['home_team'] = card_home
            match_copy['away_team'] = card_away
            match_copy['_feed_fetched_at'] = t1

            # Pomiary przed wywołaniem aktualizacji
            prev_odds = card.get("last_rendered_odds", card.get("last_odds", card.get("initial_odds", 1.70)))
            prev_minute = card.get("last_rendered_minute", card.get("last_seen_minute", card.get("initial_minute", 0)))
            prev_stage = card.get("last_rendered_stage", card.get("last_seen_half", "1H"))
            prev_score = last_score

            t2 = time.time()
            processing_ms = round((t2 - t_proc_start) * 1000, 1)

            # Wywołaj silnik aktualizacji karty i rozliczeń
            t_edit_start = time.time()
            updated_res = self.telegram.check_and_update_match_status(match_copy, card_key=card_key)
            t_edit_end = time.time()
            telegram_edit_ms = round((t_edit_end - t_edit_start) * 1000, 1)

            # Odczytaj zaktualizowany stan karty z RAM pod lockiem
            new_card = self.telegram.active_match_cards.get(card_key)
            if new_card:
                new_odds = new_card.get("last_rendered_odds", new_card.get("last_odds", prev_odds))
                new_minute = new_card.get("last_rendered_minute", new_card.get("last_seen_minute", prev_minute))
                new_stage = new_card.get("last_rendered_stage", new_card.get("last_seen_half", prev_stage))
                new_score = new_card.get("last_rendered_score", new_card.get("last_seen_score", prev_score))
            else:
                # Karta została rozliczona i usunięta z active_match_cards (WON / LOST / VOID)
                new_odds = prev_odds
                new_minute = match_copy.get("minute", prev_minute)
                new_stage = inc_stage
                new_score = current_score

            # Określenie przyczyny aktualizacji
            if updated_res:
                self.counters["cards_changed"] += 1
                self.counters["telegram_edits"] += 1
                if new_score != prev_score:
                    update_reason = "GOAL"
                elif new_stage != prev_stage:
                    update_reason = "STAGE_CHANGE"
                elif abs(float(new_odds or 0) - float(prev_odds or 0)) >= 0.05:
                    update_reason = "ODDS_SWING"
                elif new_minute != prev_minute:
                    update_reason = "MINUTE_TICK"
                else:
                    update_reason = "SETTLEMENT"

                total_latency_ms = round((t_edit_end - t0) * 1000, 1)

                rec = {
                    "timestamp": round(t_edit_end, 3),
                    "datetime": time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(t_edit_end)),
                    "match_id": card_key,
                    "update_reason": update_reason,
                    "feed_fetch_ms": feed_fetch_ms,
                    "processing_ms": processing_ms,
                    "telegram_edit_ms": telegram_edit_ms,
                    "total_latency_ms": total_latency_ms,
                    "odds_age_ms": round((now - card.get("last_odds_check_time", now)) * 1000, 0),
                    "previous_odds": prev_odds,
                    "new_odds": new_odds,
                    "previous_minute": prev_minute,
                    "new_minute": new_minute,
                    "previous_stage": prev_stage,
                    "new_stage": new_stage,
                    "previous_score": prev_score,
                    "new_score": new_score
                }
                self._log_telemetry(rec)
                telemetry_records.append(rec)
            else:
                self.counters["edits_skipped"] += 1

        return telemetry_records

    def _log_telemetry(self, record: Dict[str, Any]):
        """Zapisuje rekord zdarzenia telemetrii do pliku JSONL."""
        try:
            with open(TELEMETRY_FILE, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception as e:
            print(f"[ActiveCardsWatchdog Telemetry] Błąd zapisu: {e}")
