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
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from typing import Dict, List, Any, Optional

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TELEMETRY_FILE = os.path.join(BASE_DIR, "watchdog_telemetry.jsonl")

# Wewnętrzne stałe konserwatywnego throttling STS (decoupled od zewnętrznych limitów API)
STS_PER_REQUEST_TIMEOUT = 3.0        # Maksymalny czas oczekiwania na podstronę STS dla pojedynczej karty
STS_BASE_COOLDOWN_SECONDS = 15.0     # Domyślny interwał odpytywania STS przy stabilnej pracy
STS_MIN_GOAL_COOLDOWN_SECONDS = 4.0  # Minimalny cooldown po golu (ochrona przed event storm)
STS_MAX_BACKOFF_SECONDS = 120.0      # Maksymalny czas uśpienia schedulera przy powtarzających się błędach


class CardSTSScheduler:
    """
    Niezależny harmonogram zapytań STS dla pojedynczej aktywnej karty.
    Zabezpiecza przed lawiną requestów (retry storm / avalanche) przy awarii STS:
    - Każda karta posiada własny licznik błędów i niezależny timestamp kolejnej dozwolonej próby.
    - Progresywny bounded backoff:
        * 0 błędów (stan stabilny / sukces): 15.0s (STS_BASE_COOLDOWN_SECONDS)
        * 1. błąd: 30.0s (15 * 2^1)
        * 2. błąd: 60.0s (15 * 2^2)
        * 3.+ błąd: 120.0s (15 * 2^3, górny pułap STS_MAX_BACKOFF_SECONDS)
    - Event-driven: zmiana wyniku (GOL) wyzwala dokładnie JEDNO natychmiastowe zapytanie STS
      (o ile minął min. cooldown 4.0s od poprzedniej próby).
    - Wynik meczu last_score jest natychmiast zatwierdzany w momencie podjęcia próby (record_attempt),
      co gwarantuje, że pojedyncze zdarzenie gola nie wywoła wielokrotnych zapytań STS co 4s.
    """
    def __init__(self, card_key: str):
        self.card_key = card_key
        self.last_attempt_time: float = 0.0
        self.next_allowed_time: float = 0.0
        self.consecutive_errors: int = 0
        self.backoff_seconds: float = STS_BASE_COOLDOWN_SECONDS
        self.last_score: str = "0:0"

    def can_poll(self, now: float, current_score: str, stage_rank: int) -> bool:
        # Zablokuj zapytania w stanach spoczynku/końcowych: HT (20) i FT (40)
        if stage_rank in (20, 40):
            return False

        # Event-driven: gol wyzwala odpytanie natychmiast (z min. 4.0s cooldownu anty-flood)
        score_just_changed = (current_score != self.last_score)
        if score_just_changed:
            if now - self.last_attempt_time >= STS_MIN_GOAL_COOLDOWN_SECONDS:
                return True
            # Jeśli cooldown 4.0s jeszcze trwa, czekamy aż minie
            return False

        # Tryb okresowy / po błędzie: po upływie wyznaczonego cooldownu / backoffu
        return now >= self.next_allowed_time

    def record_attempt(self, now: float, current_score: str):
        """
        Natychmiast zatwierdza podjęcie próby zapytania dla danego wyniku meczu.
        Gwarantuje, że pojedyncze zdarzenie gola (np. 0:0 -> 1:0) wywoła maksymalnie JEDNO
        natychmiastowe zapytanie STS, niezależnie od tego czy odpowiedź będzie sukcesem,
        błędem czy timeoutem.
        """
        self.last_attempt_time = now
        self.last_score = current_score

    def record_success(self, now: float, current_score: str):
        self.last_attempt_time = now
        self.last_score = current_score
        self.consecutive_errors = 0
        self.backoff_seconds = STS_BASE_COOLDOWN_SECONDS
        self.next_allowed_time = now + self.backoff_seconds

    def record_failure(self, now: float, error_msg: str = ""):
        self.last_attempt_time = now
        self.consecutive_errors += 1
        # Bounded exponential backoff: 30s po 1. błędzie, 60s po 2. błędzie, max 120s
        self.backoff_seconds = min(STS_MAX_BACKOFF_SECONDS, STS_BASE_COOLDOWN_SECONDS * (2 ** min(self.consecutive_errors, 3)))
        self.next_allowed_time = now + self.backoff_seconds


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
        self._wake_event = threading.Event()
        self._sts_pool = ThreadPoolExecutor(max_workers=3, thread_name_prefix="WatchdogSTSWorker")
        self._card_sts_schedulers: Dict[str, CardSTSScheduler] = {}

        if hasattr(self.telegram, 'register_on_card_added'):
            self.telegram.register_on_card_added(self._on_card_added)

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
            "sts_errors": 0,
            "telegram_errors": 0,
        }

    def _on_card_added(self, card_key: str):
        """Natychmiastowe wybudzenie Watchdoga przy dodaniu karty (0 -> 1) bez czekania na timeout pętli."""
        self.wake_up()

    def wake_up(self):
        """Wybudza uśpioną pętlę Watchdoga natychmiastowo."""
        self._wake_event.set()


    def _get_or_create_scheduler(self, card_key: str, initial_score: str = "0:0") -> CardSTSScheduler:
        if card_key not in self._card_sts_schedulers:
            sched = CardSTSScheduler(card_key)
            sched.last_score = initial_score
            self._card_sts_schedulers[card_key] = sched
        return self._card_sts_schedulers[card_key]

    def _prune_schedulers(self, active_keys: set):
        dead_keys = [k for k in self._card_sts_schedulers if k not in active_keys]
        for k in dead_keys:
            del self._card_sts_schedulers[k]

    def _fetch_subpage_isolated(self, sts_url: str, timeout: float = STS_PER_REQUEST_TIMEOUT) -> List[Dict[str, Any]]:
        """Pobiera rynki STS z niezależnym, sztywnym timeoutem per-request."""
        future = self._sts_pool.submit(self.sts_engine.get_match_real_live_markets, sts_url)
        return future.result(timeout=timeout)

    def start(self):
        """Uruchamia wątek Watchdoga w tle."""
        if self._running:
            return
        self._running = True
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, name="ActiveCardsWatchdogThread", daemon=True)
        self._thread.start()

    def stop(self, timeout=2.0):
        """Zatrzymuje wątek Watchdoga i zwalnia pulę workerów."""
        self._running = False
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=timeout)
        self._sts_pool.shutdown(wait=False)


    def _run_loop(self):
        """Pętla główna Watchdoga z dynamicznym interwałem uśpienia i natychmiastowym wybudzaniem (wake_event)."""
        while not self._stop_event.is_set():
            active_cnt = len(getattr(self.telegram, 'active_match_cards', {}))
            if active_cnt == 0:
                # Gdy brak aktywnych kart w RAM: uśpienie z natychmiastowym wybudzeniem (0 -> 1) lub max 3.0s, ZERO requestów HTTP
                self._wake_event.clear()
                self._wake_event.wait(timeout=3.0)
                continue

            cycle_start = time.time()
            try:
                self.check_active_cards_once()
            except Exception as e:
                print(f"[ActiveCardsWatchdog] Błąd cyklu: {e}")

            elapsed = time.time() - cycle_start
            sleep_time = max(0.5, 3.0 - elapsed)
            self._wake_event.clear()
            self._wake_event.wait(timeout=sleep_time)


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
            self._prune_schedulers(set())
            return []

        self._prune_schedulers(set(cards_snapshot.keys()))
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

        # 2. Rezerwowy feed ogólny STS (odpytywany TYLKO wtedy, gdy aktywna karta nie została odnaleziona we Flashscore)
        unmatched_cards_exist = any(
            not any(self.telegram._matches_card(c.get('home_team', ''), c.get('away_team', ''), m.get('home_team', ''), m.get('away_team', ''), k) for m in live_matches)
            for k, c in cards_snapshot.items()
            if not c.get("settling") and not c.get("settled")
        )

        if unmatched_cards_exist and sts_matches is None:
            try:
                sts_matches = self.sts_engine.fetch_live_matches(include_esports=False)
            except Exception:
                self.counters["sts_errors"] += 1
                sts_matches = []
        else:
            sts_matches = sts_matches or []

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

            # Niezależny harmonogram STS per-karta (Scheduler + Circuit Breaker + Sztywny Timeout)
            current_score = best_match.get("score_str", card.get("last_seen_score", "0:0"))
            last_score = card.get("last_rendered_score", card.get("initial_score", "0:0"))
            sts_url = card.get("sts_url") or best_match.get("sts_url")

            scheduler = self._get_or_create_scheduler(card_key, initial_score=last_score)

            # Sprawdź czy scheduler karty zezwala na zapytanie (event-driven po golu lub interwał 15s/backoff)
            if sts_url and '/live/' in sts_url and scheduler.can_poll(now, current_score, inc_rank):
                # Natychmiastowe zatwierdzenie próby dla tego wyniku (gwarancja dokładnie 1 requestu na gol)
                scheduler.record_attempt(now, current_score)
                try:
                    real_markets = self._fetch_subpage_isolated(sts_url, timeout=STS_PER_REQUEST_TIMEOUT)
                    if real_markets:
                        best_match['live_markets'] = real_markets
                        card["last_odds_check_time"] = now
                        scheduler.record_success(now, current_score)
                        self.counters["odds_refreshes"] += 1
                    else:
                        scheduler.record_failure(now, "Empty markets returned")
                        self.counters["sts_errors"] += 1
                except FutureTimeoutError:
                    scheduler.record_failure(now, f"Timeout STS (>{STS_PER_REQUEST_TIMEOUT}s)")
                    self.counters["sts_errors"] += 1
                except Exception as ex:
                    scheduler.record_failure(now, str(ex))
                    self.counters["sts_errors"] += 1


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
