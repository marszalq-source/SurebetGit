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
import re
import threading
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from typing import Dict, List, Any, Optional, Tuple

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
        self._last_logged_sync: Dict[str, Any] = {}
        self._last_logged_snapshot: Dict[str, Any] = {}

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
        for k in list(self._last_logged_sync.keys()):
            if k not in active_keys:
                del self._last_logged_sync[k]
        for k in list(self._last_logged_snapshot.keys()):
            if k not in active_keys:
                del self._last_logged_snapshot[k]

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

    @staticmethod
    def _parse_score_goals(score_str: str) -> int:
        try:
            parts = str(score_str or '').split(':')
            if len(parts) == 2:
                return int(parts[0]) + int(parts[1])
        except Exception:
            pass
        return -1

    @staticmethod
    def _is_extra_time_match(match_dict: Optional[Dict[str, Any]]) -> bool:
        """
        Bezpieczne wykrywanie dogrywki (Extra Time).
        Uwaga: NIE używa ogólnego podciągu 'et' (false-positive np. na 'reset', 'set').
        AC=6 → Extra Time, AC=7 → Karne (Penalties).
        Oba traktowane jako po-regulaminowe dla ochrony rynku FT.
        """
        if not match_dict:
            return False
        half = str(match_dict.get('half') or '').upper()
        stage_text = str(match_dict.get('stage_text') or '').lower()
        status_code = str(match_dict.get('status_code') or '').strip()
        minute = int(match_dict.get('minute') or 0)
        if half in ('ET', 'DOGRYWKA', 'EXTRA_TIME', 'PENALTIES', 'PEN'):
            return True
        # Używamy doprecyzowanych fraz zamiast ogólnego 'et' (niebezpieczny false-positive)
        if any(w in stage_text for w in ['dogr', 'extra time', 'po dogr', 'dogrywka', 'karne', 'penalties']):
            return True
        # Dokładne dopasowanie słowa 'et' jako całego słowa (np. "ET 105'" ale nie "reset")
        if re.search(r'\bet\b', stage_text):
            return True
        # Oficjalne kody Flashscore: AC=6 (Extra Time), AC=7 (Penalties)
        if status_code in ('6', '7'):
            return True
        if minute > 90 and half not in ('1H', '2H', 'FT'):
            return True
        return False

    def _reconcile_match_sources(
        self,
        card_key: str,
        card: Dict[str, Any],
        fs_match: Optional[Dict[str, Any]],
        sts_match: Optional[Dict[str, Any]]
    ) -> Tuple[Dict[str, Any], str, str]:
        """
        Wielowymiarowa, bezpieczna synchronizacja Flashscore i STS:
        Uwzględnia: wynik, minutę, fazę meczu (stage/rank), kody AC/AB,
        okres rynku (FT vs Extra Time) oraz monotoniczność.
        NIGDY nie stosuje ślepej reguły 'więcej goli = lepsze źródło'.
        """
        if fs_match and not sts_match:
            return fs_match, "FLASHSCORE", "FLASHSCORE_ONLY"
        if sts_match and not fs_match:
            # Jedyne źródło to STS — jeśli jest w ET/Pen i rynek FT, blokuj
            target_period_solo = str(card.get('target_period') or 'FT').upper()
            if target_period_solo == 'FT' and self._is_extra_time_match(sts_match):
                return sts_match, "STS", "STS_ONLY_ET_BLOCKED_FOR_FT_MARKET"
            return sts_match, "STS", "STS_ONLY"

        target_period = str(card.get('target_period') or 'FT').upper()
        card_highest_goals = card.get('highest_goals', card.get('initial_goals', 0))

        fs_score = fs_match.get("score_str", "0:0")
        sts_score = sts_match.get("score_str", "0:0")
        fs_min = int(fs_match.get("minute") or 0)
        sts_min = int(sts_match.get("minute") or 0)

        fs_stage, fs_rank = self.telegram._get_match_stage_rank(
            half=fs_match.get('half'),
            stage_text=fs_match.get('stage_text'),
            is_live=fs_match.get('is_live', True),
            status_code=str(fs_match.get('status_code', '')),
            minute=fs_min
        )
        sts_stage, sts_rank = self.telegram._get_match_stage_rank(
            half=sts_match.get('half'),
            stage_text=sts_match.get('stage_text'),
            is_live=sts_match.get('is_live', True),
            status_code=str(sts_match.get('status_code', '')),
            minute=sts_min
        )

        fs_goals = self._parse_score_goals(fs_score)
        sts_goals = self._parse_score_goals(sts_score)

        fs_is_et = self._is_extra_time_match(fs_match)
        sts_is_et = self._is_extra_time_match(sts_match)

        # Reguła 1: FT vs Extra Time (Dogrywka)
        # Dla rynku FT (90 min) wynik z dogrywki NIE MOŻE zmienić wyniku regulaminowego.
        if target_period == 'FT':
            if sts_is_et and not fs_is_et and fs_rank >= 30:
                best_match = dict(fs_match)
                if sts_match.get("sts_url"):
                    best_match["sts_url"] = sts_match["sts_url"]
                return best_match, "FLASHSCORE", "FT_MARKET_PROTECTION_REGULATION_PREFERRED"
            if fs_is_et and not sts_is_et and sts_rank >= 30:
                best_match = dict(sts_match)
                return best_match, "STS", "FT_MARKET_PROTECTION_REGULATION_PREFERRED"
            # Oba źródła są w dogrywce — bezpieczny fallback do FS (lower score = bardziej regulaminowe)
            if sts_is_et and fs_is_et:
                return fs_match, "FLASHSCORE", "BOTH_SOURCES_ET_BLOCKED_FOR_FT_MARKET"

        # Reguła 2: Ochrona monotoniczności wyniku (brak rollbacku)
        if fs_goals >= card_highest_goals and sts_goals < card_highest_goals:
            best_match = dict(fs_match)
            if sts_match.get("sts_url"):
                best_match["sts_url"] = sts_match["sts_url"]
            return best_match, "FLASHSCORE", "MONOTONICITY_GUARD_STS_ROLLBACK_REJECTED"

        # Reguła 3: Zgodna aktualizacja nowszego gola na żywo z STS
        # Aby zaakceptować wyższy wynik STS, faza i czas muszą być spójne (brak anachronizmów typu 45' vs 60')
        if sts_goals > fs_goals:
            sts_congruent = (
                sts_rank >= fs_rank 
                and sts_min >= (fs_min - 3)
                and not sts_is_et
            )
            if sts_congruent:
                selected_source = "STS"
                reason = "STS_AHEAD_GOALS_CONGRUENT"
                best_match = dict(sts_match)
                if fs_match.get("flashscore_id"):
                    best_match["flashscore_id"] = fs_match["flashscore_id"]
            else:
                selected_source = "FLASHSCORE"
                reason = "INCONGRUENT_STAGE_STS_REJECTED"
                best_match = dict(fs_match)
                if sts_match.get("sts_url"):
                    best_match["sts_url"] = sts_match["sts_url"]

        elif fs_goals > sts_goals:
            selected_source = "FLASHSCORE"
            reason = "FLASHSCORE_AHEAD_GOALS"
            best_match = dict(fs_match)
            if sts_match.get("sts_url"):
                best_match["sts_url"] = sts_match["sts_url"]
            if sts_match.get("live_markets"):
                best_match["live_markets"] = sts_match["live_markets"]
        else:
            # Reguła 4: EQUAL_GOALS — preferuj nowszą fazę/minutę zamiast ślepego consensusu FS.
            # Kluczowy przypadek: FS 0:1 45' HT vs STS 0:1 48' 2H → STS ma wyższy rank (30 > 20)
            # co oznacza, że mecz już ruszył w 2H — nie wolno zwrócić przestarzałego snapshotu HT.
            if sts_rank > fs_rank and not sts_is_et:
                selected_source = "STS"
                reason = "EQUAL_GOALS_STS_FRESHER_STAGE"
                best_match = dict(sts_match)
                if fs_match.get("flashscore_id"):
                    best_match["flashscore_id"] = fs_match["flashscore_id"]
                if not best_match.get("sts_url"):
                    best_match["sts_url"] = fs_match.get("sts_url") or best_match.get("sts_url")
            elif sts_rank == fs_rank and sts_min > fs_min + 2 and not sts_is_et:
                # Ten sam rank (np. obie 2H), ale STS ma minutę późniejszą o >2 minuty
                selected_source = "STS"
                reason = "EQUAL_GOALS_STS_FRESHER_MINUTE"
                best_match = dict(sts_match)
                if fs_match.get("flashscore_id"):
                    best_match["flashscore_id"] = fs_match["flashscore_id"]
                if not best_match.get("live_markets") and fs_match.get("live_markets"):
                    best_match["live_markets"] = fs_match["live_markets"]
            else:
                selected_source = "FLASHSCORE"
                reason = "EQUAL_GOALS_CONSENSUS"
                best_match = dict(fs_match)
                if sts_match.get("sts_url"):
                    best_match["sts_url"] = sts_match["sts_url"]
                if sts_match.get("live_markets"):
                    best_match["live_markets"] = sts_match["live_markets"]

        if fs_score != sts_score:
            self._log_source_sync_telemetry(
                card_key=card_key,
                fs_match=fs_match,
                sts_match=sts_match,
                selected_match=best_match,
                selected_source=selected_source,
                reason=reason
            )

        return best_match, selected_source, reason

    def _log_source_sync_telemetry(
        self,
        card_key: str,
        fs_match: Optional[Dict[str, Any]],
        sts_match: Optional[Dict[str, Any]],
        selected_match: Dict[str, Any],
        selected_source: str,
        reason: str
    ):
        """Loguje zdarzenie synchronizacji/rozbieżności wyników między Flashscore i STS."""
        now = time.time()
        fs_score = fs_match.get("score_str", "") if fs_match else ""
        sts_score = sts_match.get("score_str", "") if sts_match else ""
        selected_score = selected_match.get("score_str", "")

        state_sig = (fs_score, sts_score, selected_score, selected_source, reason)
        if self._last_logged_sync.get(card_key) == state_sig:
            return
        self._last_logged_sync[card_key] = state_sig

        rec = {
            "timestamp": round(now, 3),
            "datetime": time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(now)),
            "type": "SOURCE_SYNC",
            "match_id": card_key,
            "flashscore_score": fs_score,
            "sts_score": sts_score,
            "selected_score": selected_score,
            "flashscore_minute": fs_match.get("minute") if fs_match else None,
            "sts_minute": sts_match.get("minute") if sts_match else None,
            "flashscore_stage": fs_match.get("half") or fs_match.get("stage_text") if fs_match else None,
            "sts_stage": sts_match.get("half") or sts_match.get("stage_text") if sts_match else None,
            "selected_source": selected_source,
            "reason_for_selection": reason
        }
        self._log_telemetry(rec)

    def _log_snapshot_telemetry(
        self,
        card_key: str,
        accepted: bool,
        inc_rank: int,
        highest_stage_rank: int,
        score: str,
        minute: int,
        ac_code: str,
        ab_code: str,
        stage: str,
        reject_reason: Optional[str] = None
    ):
        """Loguje akceptację lub odrzucenie snapshotu przez Watchdog z deduplikacją."""
        now = time.time()
        state_sig = (accepted, inc_rank, highest_stage_rank, score, minute, stage, reject_reason)
        if self._last_logged_snapshot.get(card_key) == state_sig:
            return
        self._last_logged_snapshot[card_key] = state_sig

        rec = {
            "timestamp": round(now, 3),
            "datetime": time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(now)),
            "type": "WATCHDOG_SNAPSHOT",
            "match_id": card_key,
            "accepted": accepted,
            "inc_rank": inc_rank,
            "highest_stage_rank": highest_stage_rank,
            "score": score,
            "minute": minute,
            "AC": ac_code,
            "AB": ab_code,
            "stage": stage,
        }
        if not accepted:
            rec["reject_reason"] = reject_reason or "STALE_STAGE_RANK (inc_rank < highest_stage_rank)"

        self._log_telemetry(rec)

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

        # 2. Feed ogólny STS: odpytywany dla wszystkich aktywnych kart, aby zapobiec Source Masking (opóźnienie Flashscore nie blokuje wykrycia gola)
        active_pending_cards = [
            c for k, c in cards_snapshot.items()
            if not c.get("settling") and not c.get("settled")
        ]
        if active_pending_cards and sts_matches is None:
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

            # Znajdź dopasowanie w obu źródłach niezależnie
            fs_matching = [m for m in live_matches if self.telegram._matches_card(card_home, card_away, m.get('home_team', ''), m.get('away_team', ''), card_key)]
            sts_matching = [m for m in sts_matches if self.telegram._matches_card(card_home, card_away, m.get('home_team', ''), m.get('away_team', ''), card_key)]

            fs_match = max(fs_matching, key=lambda m: (
                self.telegram._get_match_stage_rank(m.get('half'), m.get('stage_text'), m.get('is_live', True), str(m.get('status_code', '')), int(m.get('minute') or 0))[1],
                int(m.get('minute') or 0)
            )) if fs_matching else None

            sts_match = max(sts_matching, key=lambda m: (
                self.telegram._get_match_stage_rank(m.get('half'), m.get('stage_text'), m.get('is_live', True), str(m.get('status_code', '')), int(m.get('minute') or 0))[1],
                int(m.get('minute') or 0)
            )) if sts_matching else None

            if not fs_match and not sts_match:
                continue

            # Bezpieczny, wielowymiarowy wybór wyniku (eliminacja Source Masking)
            best_match, selected_source, sync_reason = self._reconcile_match_sources(
                card_key=card_key,
                card=card,
                fs_match=fs_match,
                sts_match=sts_match
            )

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
            current_score = best_match.get("score_str", card.get("last_seen_score", "0:0"))
            minute_val = int(best_match.get('minute') or 0)
            ac_code = str(best_match.get('stage_text') or best_match.get('status_code') or '')
            ab_code = str(best_match.get('status_code') or '')

            if inc_rank < highest_rank:
                # Odrzuć zredukowany / opóźniony snapshot (Stale Snapshot Rejection)
                self.counters["stale_snapshots_rejected"] += 1
                self._log_snapshot_telemetry(
                    card_key=card_key,
                    accepted=False,
                    inc_rank=inc_rank,
                    highest_stage_rank=highest_rank,
                    score=current_score,
                    minute=minute_val,
                    ac_code=ac_code,
                    ab_code=ab_code,
                    stage=inc_stage,
                    reject_reason="STALE_STAGE_RANK (inc_rank < highest_stage_rank)"
                )
                continue

            # Zaakceptowany snapshot
            self._log_snapshot_telemetry(
                card_key=card_key,
                accepted=True,
                inc_rank=inc_rank,
                highest_stage_rank=highest_rank,
                score=current_score,
                minute=minute_val,
                ac_code=ac_code,
                ab_code=ab_code,
                stage=inc_stage,
                reject_reason=None
            )

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
