import time
import threading
from typing import Dict, List, Any
from .flashscore_engine import FlashscoreEngine
from .sts_live_engine import STSLiveEngine
from .beesports_engine import BeeSportsEngine
from .betsapi_engine import BetsAPIEngine
from .goaloo_engine import GoalooEngine
from .live_matcher import LiveMatcher
from .goal_triggers import GoalTriggersEngine
from .prematch_analyzer import PrematchAnalyzer
from .telegram_notifier import TelegramNotifier
from .bet_tracker import BetTracker

class STSFlashscoreAggregator:
    def __init__(self):
        self.fs_engine = FlashscoreEngine()
        self.sts_engine = STSLiveEngine()
        self.beesports = BeeSportsEngine()
        self.betsapi = BetsAPIEngine()
        self.goaloo = GoalooEngine()
        self.matcher = LiveMatcher()
        self.triggers = GoalTriggersEngine()
        self.prematch_analyzer = PrematchAnalyzer()
        self.telegram = TelegramNotifier()
        self.tracker = BetTracker()
        self.last_scan_time = 0
        self.cached_results = []
        self._is_enriched = False
        self._enrichment_status = "INITIALIZING"
        self._scan_lock = threading.Lock()
        self._cache_lock = threading.Lock()
        self.start_background_scanner()

    def start_background_scanner(self):
        """Uruchamia ciągły skaner w tle co 10 sekund (wyniki zawsze w pamięci RAM)."""
        if getattr(self, '_scanner_running', False):
            return
        self._scanner_running = True

        def _worker():
            import asyncio
            try:
                loop = asyncio.get_event_loop()
            except RuntimeError:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)

            try:
                self._execute_full_scan()
            except Exception as e:
                print(f"[Aggregator Initial Scan] {e}")

            while True:
                time.sleep(10)
                try:
                    self._execute_full_scan()
                except Exception as ex:
                    print(f"[Aggregator Background Loop] {ex}")


        t = threading.Thread(target=_worker, daemon=True, name="AggregatorBackgroundScanner")
        t.start()

        def _settlement_worker():
            """
            Niezależna, błyskawiczna pętla rozliczeń (SETTLEMENT_INTERVAL = 5s).
            Sprawdza aktywne pozycje w pamięci RAM i natychmiast rozlicza:
            1. Flashscore Live / HT / FT
            2. Telegram Cards (In-Place Edit)
            3. BetTracker (Dziennik Typera)
            4. ShadowLogger (telegram_shadow_log.jsonl)
            """
            SETTLEMENT_INTERVAL = 5
            time.sleep(2)
            t_last_cycle_end = time.time()

            while True:
                try:
                    active_cards_count = len(getattr(self.telegram, 'active_match_cards', {}))
                    has_pending_bets = False
                    try:
                        bets_data = self.tracker._load_data()
                        has_pending_bets = any(b.get("status") == "PENDING" for b in bets_data.get("bets", []))
                    except Exception:
                        pass

                    # Rozliczaj tylko jeśli są aktywne pozycje w RAM
                    if active_cards_count > 0 or has_pending_bets:
                        t_cycle_start = time.time()
                        cycle_wait_sec = round(t_cycle_start - t_last_cycle_end, 2) if t_last_cycle_end else float(SETTLEMENT_INTERVAL)

                        fs_live_today = []
                        t_fetch_start = time.time()
                        try:
                            fs_live_today = self.fs_engine.get_live_soccer_matches(include_all_today=True)
                        except Exception as ex:
                            print(f"[FastSettlement] Błąd pobierania live Flashscore: {ex}")

                        sts_live = []
                        try:
                            sts_live = self.sts_engine.fetch_live_matches(include_esports=False)
                        except Exception:
                            pass

                        fs_finished = []
                        try:
                            fs_finished = self.fs_engine.get_finished_results(days_back=1)
                        except Exception as ex:
                            print(f"[FastSettlement] Błąd pobierania zakończonych Flashscore: {ex}")

                        t_fetch_end = time.time()
                        fetch_dur = round(t_fetch_end - t_fetch_start, 3)

                        all_matches = fs_live_today + sts_live + fs_finished
                        if all_matches:
                            for m in all_matches:
                                m['_feed_fetched_at'] = t_fetch_end
                                m['_cycle_start_at'] = t_cycle_start
                                m['_fetch_duration_sec'] = fetch_dur
                                m['_poll_interval_sec'] = SETTLEMENT_INTERVAL
                                m['_cycle_wait_sec'] = cycle_wait_sec

                            all_live = [m for m in all_matches if m.get('is_live')]
                            all_fin = [m for m in all_matches if not m.get('is_live')]

                            settled_cards = self.telegram.auto_settle_active_cards(live_matches=all_live, finished_matches=all_fin)
                            resolved_bets = self.tracker.auto_resolve_bets(all_matches)

                            try:
                                from .shadow_logger import ShadowLogger
                                ShadowLogger().update_settled_matches_batch(fs_finished)
                            except Exception as ex:
                                print(f"[FastSettlement] Błąd ShadowLogger: {ex}")

                            if settled_cards > 0 or (resolved_bets and len(resolved_bets) > 0):
                                print(f"[FastSettlement] ⚡ Błyskawicznie rozliczono: {settled_cards} kart Telegram, {len(resolved_bets) if resolved_bets else 0} kuponów!")
                except Exception as loop_ex:
                    print(f"[FastSettlement Loop] Wyjątek: {loop_ex}")

                time.sleep(SETTLEMENT_INTERVAL)
                t_last_cycle_end = time.time()

        t_settle = threading.Thread(target=_settlement_worker, daemon=True, name="FastSettlementWorker")
        t_settle.start()

        # Dedykowany, ultra-szybki Watchdog aktywnych kart (Active Cards Sentinel)
        from .active_cards_watchdog import ActiveCardsWatchdog
        self.watchdog = ActiveCardsWatchdog(
            fs_engine=self.fs_engine,
            sts_engine=self.sts_engine,
            telegram_notifier=self.telegram
        )
        self.watchdog.start()

    def _update_cache_entry(self, match_id: str, enriched_data: Dict[str, Any]):
        """
        Atomowo aktualizuje pojedynczy mecz w self.cached_results w locie w pamięci RAM.
        Zapewnia monotoniczność minuty, wyniku, rynków i brak utraty danych.
        """
        if not match_id or not enriched_data:
            return
        with self._cache_lock:
            for idx, entry in enumerate(self.cached_results):
                cid = entry.get('id') or entry.get('flashscore_id')
                if cid and cid == match_id:
                    # Monotoniczność minuty: nie cofaj minuty
                    old_min = entry.get('minute', 0)
                    new_min = enriched_data.get('minute', 0)
                    if old_min > new_min and new_min > 0:
                        enriched_data['minute'] = old_min
                        enriched_data['stage_text'] = entry.get('stage_text', enriched_data.get('stage_text'))
                        enriched_data['half'] = entry.get('half', enriched_data.get('half'))

                    # Monotoniczność wyniku: nie cofaj wyniku jeśli stary był niepusty
                    old_score = entry.get('score_str', '0:0')
                    new_score = enriched_data.get('score_str', '0:0')
                    if old_score != '0:0' and new_score == '0:0':
                        enriched_data['score_str'] = old_score
                        enriched_data['home_score'] = entry.get('home_score', enriched_data.get('home_score'))
                        enriched_data['away_score'] = entry.get('away_score', enriched_data.get('away_score'))

                    # Monotoniczność rynków STS
                    if entry.get('live_markets') and not enriched_data.get('live_markets'):
                        enriched_data['live_markets'] = entry['live_markets']

                    self.cached_results[idx] = enriched_data
                    return
            self.cached_results.append(enriched_data)

    def scan_all(self, only_signals: bool = False, min_minute: int = 0, half_filter: str = "ALL", demo_mode: bool = False) -> Dict[str, Any]:
        """
        Zwraca natychmiastowo mecze z pamięci RAM (czas < 0.001s).
        Zawiera inteligentny mechanizm Live Real-Time Clock Sync (minuty płynnie kroczą w czasie rzeczywistym).
        NIGDY nie blokuje żądań HTTP!
        """
        with self._cache_lock:
            matches = [dict(m) for m in self.cached_results] if self.cached_results else []
        elapsed_sec = max(0, time.time() - self.last_scan_time) if self.last_scan_time > 0 else 0

        elapsed_mins = int(elapsed_sec // 60)

        filtered = []
        signals_cnt = 0

        for m in matches:
            # Płynna synchronizacja minuty w czasie rzeczywistym
            if elapsed_mins > 0 and m.get('half') in ('1H', '2H') and m.get('minute', 0) > 0:
                base_min = m['minute']
                max_cap = 45 if m['half'] == '1H' else 90
                cur_min = min(max_cap + 5, base_min + elapsed_mins)
                m['minute'] = cur_min
                m['stage_text'] = f"{cur_min}'"

            if m.get('minute', 0) < min_minute:
                continue
            if half_filter == "1H" and m.get('half') != "1H":
                continue
            if half_filter == "2H" and m.get('half') != "2H":
                continue
            if only_signals and not m.get('has_signals'):
                continue
            if m.get('has_signals'):
                signals_cnt += len(m.get('signals', []))
            filtered.append(m)

        return {
            "timestamp": time.strftime('%H:%M:%S'),
            "matches": filtered,
            "total_live_matches": len(matches),
            "signals_count": signals_cnt,
            "is_enriched": getattr(self, '_is_enriched', True),
            "enrichment_status": getattr(self, '_enrichment_status', 'COMPLETE'),
            "is_stale": elapsed_sec > 120
        }


    def _execute_full_scan(self, demo_mode: bool = False):
        with self._scan_lock:
            start_time = time.time()
            # 1. Pobierz wszystkie mecze z Flashscore Live oraz zakończone z dzisiaj i wczoraj
            fs_all_matches = self.fs_engine.get_live_soccer_matches(include_all_today=True)
            fs_matches = [m for m in fs_all_matches if m.get('is_live')] if not demo_mode else fs_all_matches
            
            # Rozliczanie odseparowane: obsługiwane przez niezależny wątek FastSettlementWorker (co 5s).
            # Nie blokujemy pętli skanera live pobieraniem historii 3 dni wstecz!
            fs_finished_all = []

            # 2. Pobierz mecze z STS (jeśli są)
            sts_matches = []
            try:
                sts_matches = self.sts_engine.fetch_live_matches(include_esports=False)
                if sts_matches:
                    self.matcher.pre_normalize_matches(sts_matches)
            except Exception as e:
                print(f"[Aggregator] STS fetch error: {e}")

            # INSTANT FRESH SNAPSHOT (Warstwa prezentacyjna / cache dla UI):
            # Zasilenie / odświeżenie RAM natychmiastowym bazowym snapshotem LIVE bez czekania na enrichment
            if fs_matches or sts_matches:
                existing_cache_map = {}
                for item in self.cached_results:
                    cid = item.get('id') or item.get('flashscore_id')
                    if cid:
                        existing_cache_map[cid] = item

                fresh_baseline = []
                seen_pairs = set()

                for m in fs_matches:
                    m_id = m.get('flashscore_id')
                    pair_key = f"{m.get('home_team', '')}_{m.get('away_team', '')}".lower()
                    seen_pairs.add(pair_key)
                    existing_entry = existing_cache_map.get(m_id)

                    sts_m = self.matcher.match_flashscore_with_sts(m, sts_matches) if sts_matches else None
                    matched_with_sts = sts_m is not None
                    sts_url = sts_m.get('url', 'https://www.sts.pl/live/pilka-nozna') if sts_m else 'https://www.sts.pl/live/pilka-nozna'
                    live_mkts = sts_m.get('live_markets', []) if sts_m else (existing_entry.get('live_markets', []) if existing_entry else [])
                    odds_dict = sts_m.get('goals_odds', {}) if sts_m else (existing_entry.get('odds', {}) if existing_entry else {})

                    # Synchronizacja z STS
                    m_min = m.get('minute', 0)
                    m_half = m.get('half', '1H')
                    m_stage = m.get('stage_text', '')
                    m_score = m.get('score_str', '0:0')
                    m_h_score = m.get('home_score', 0)
                    m_a_score = m.get('away_score', 0)

                    if sts_m:
                        sts_min = sts_m.get('minute', 0)
                        if sts_min > 0 and (sts_min >= m_min or m_min == 0):
                            m_min = sts_min
                            m_half = sts_m.get('half', m_half)
                            m_stage = sts_m.get('stage_text', f"{sts_min}'")
                        if sts_m.get('half') in ('2H', 'FT') and m_half == 'HT':
                            m_half = sts_m.get('half')
                            m_stage = sts_m.get('stage_text', m_stage)
                        if sts_m.get('score_str') and sts_m.get('score_str') != '0:0':
                            m_score = sts_m['score_str']
                            m_h_score = sts_m.get('home_score', m_h_score)
                            m_a_score = sts_m.get('away_score', m_a_score)

                    fresh_baseline.append({
                        'id': m_id,
                        'league': (sts_m.get('league') if sts_m and not str(sts_m.get('league', '')).startswith('Piłka Nożna') else None) or m.get('league', 'Piłka Nożna'),
                        'home_team': (sts_m.get('home_team') if sts_m else None) or m.get('home_team', ''),
                        'away_team': (sts_m.get('away_team') if sts_m else None) or m.get('away_team', ''),
                        'sts_home_team': sts_m.get('home_team') if sts_m else (existing_entry.get('sts_home_team') if existing_entry else None),
                        'sts_away_team': sts_m.get('away_team') if sts_m else (existing_entry.get('sts_away_team') if existing_entry else None),
                        'flashscore_home_team': m.get('home_team', ''),
                        'flashscore_away_team': m.get('away_team', ''),
                        'home_score': m_h_score,
                        'away_score': m_a_score,
                        'score_str': m_score,
                        'minute': m_min,
                        'half': m_half,
                        'stage_text': m_stage,
                        'stats': existing_entry.get('stats', {}) if existing_entry else {},
                        'danger_index': existing_entry.get('danger_index', 50) if existing_entry else 50,
                        'danger_index_10': existing_entry.get('danger_index_10', 50) if existing_entry else 50,
                        'danger_index_5': existing_entry.get('danger_index_5', 50) if existing_entry else 50,
                        'danger_rating': existing_entry.get('danger_rating', 'ŚREDNI') if existing_entry else 'ŚREDNI',
                        'apm': existing_entry.get('apm', 0.8) if existing_entry else 0.8,
                        'has_signals': existing_entry.get('has_signals', False) if existing_entry else False,
                        'signals': existing_entry.get('signals', []) if existing_entry else [],
                        'odds': odds_dict,
                        'live_markets': live_mkts,
                        'matched_with_sts': matched_with_sts or (existing_entry.get('matched_with_sts', False) if existing_entry else False),
                        'sts_url': sts_url,
                        'flashscore_url': m.get('url', f"https://www.flashscore.pl/mecz/{m_id}/"),
                        'prematch_context': existing_entry.get('prematch_context', {}) if existing_entry else {},
                        'is_worth_watching': existing_entry.get('is_worth_watching', False) if existing_entry else False,
                        'worth_grade': existing_entry.get('worth_grade', 'STANDARD') if existing_entry else 'STANDARD',
                        'worth_reasons': existing_entry.get('worth_reasons', ['⚪ Wczytywanie statystyk...']) if existing_entry else ['⚪ Wczytywanie statystyk...'],
                        'enrichment_status': existing_entry.get('enrichment_status', 'PENDING') if existing_entry else 'PENDING'
                    })

                for sm in sts_matches:
                    sm_pair = f"{sm.get('home_team', '')}_{sm.get('away_team', '')}".lower()
                    if sm_pair not in seen_pairs:
                        seen_pairs.add(sm_pair)
                        sts_uid = f"sts_{abs(hash(sm['home_team'] + sm['away_team']))}"
                        existing_entry = existing_cache_map.get(sts_uid)
                        fresh_baseline.append({
                            'id': sts_uid,
                            'league': sm.get('league', 'Piłka Nożna – STS Live'),
                            'home_team': sm.get('home_team', ''),
                            'away_team': sm.get('away_team', ''),
                            'sts_home_team': sm.get('home_team', ''),
                            'sts_away_team': sm.get('away_team', ''),
                            'flashscore_home_team': sm.get('home_team', ''),
                            'flashscore_away_team': sm.get('away_team', ''),
                            'home_score': sm.get('home_score', 0),
                            'away_score': sm.get('away_score', 0),
                            'score_str': sm.get('score_str', '0:0'),
                            'minute': sm.get('minute', 0),
                            'half': sm.get('half', '1H'),
                            'stage_text': sm.get('stage_text', ''),
                            'stats': existing_entry.get('stats', {}) if existing_entry else {},
                            'danger_index': existing_entry.get('danger_index', 50) if existing_entry else 50,
                            'danger_index_10': existing_entry.get('danger_index_10', 50) if existing_entry else 50,
                            'danger_index_5': existing_entry.get('danger_index_5', 50) if existing_entry else 50,
                            'danger_rating': existing_entry.get('danger_rating', 'ŚREDNI') if existing_entry else 'ŚREDNI',
                            'apm': existing_entry.get('apm', 0.8) if existing_entry else 0.8,
                            'has_signals': existing_entry.get('has_signals', False) if existing_entry else False,
                            'signals': existing_entry.get('signals', []) if existing_entry else [],
                            'odds': sm.get('goals_odds', {}),
                            'live_markets': sm.get('live_markets', []),
                            'matched_with_sts': True,
                            'sts_url': sm.get('url', 'https://www.sts.pl/live/pilka-nozna'),
                            'flashscore_url': 'https://www.flashscore.pl/',
                            'prematch_context': existing_entry.get('prematch_context', {}) if existing_entry else {},
                            'is_worth_watching': existing_entry.get('is_worth_watching', False) if existing_entry else False,
                            'worth_grade': existing_entry.get('worth_grade', 'STANDARD') if existing_entry else 'STANDARD',
                            'worth_reasons': existing_entry.get('worth_reasons', ['⚪ Wczytywanie statystyk...']) if existing_entry else ['⚪ Wczytywanie statystyk...'],
                            'enrichment_status': existing_entry.get('enrichment_status', 'PENDING') if existing_entry else 'PENDING'
                        })

                with self._cache_lock:
                    self.cached_results = fresh_baseline
                self.last_scan_time = time.time()
                if not self._is_enriched:
                    self._enrichment_status = "PENDING"

            # 3. Zabezpieczenie auto-rozliczania kart (szybki fallback gdy są aktywne karty)
            all_today_matches = fs_all_matches + sts_matches
            if getattr(self.telegram, 'active_match_cards', None):
                try:
                    all_live_now = [m for m in all_today_matches if m.get('is_live')]
                    all_finished_now = [m for m in all_today_matches if not m.get('is_live')]
                    self.telegram.auto_settle_active_cards(live_matches=all_live_now, finished_matches=all_finished_now)
                except Exception as ex:
                    print(f"[Aggregator] Błąd auto-rozliczania Telegram: {ex}")

            # Ogranicz do max 40 najbardziej aktywnych meczów w jednym cyklu dla maksymalnej prędkości
            target_matches = fs_matches[:40]
            if target_matches:
                self.matcher.pre_normalize_matches(target_matches)

            # Pobieranie statystyk równolegle w wątkach (ThreadPoolExecutor)
            from concurrent.futures import ThreadPoolExecutor
            stats_map = {}
            with ThreadPoolExecutor(max_workers=16) as executor:
                future_to_id = {executor.submit(self.fs_engine.get_match_statistics, m['flashscore_id']): m['flashscore_id'] for m in target_matches}
                for future in future_to_id:
                    m_id = future_to_id[future]
                    try:
                        stats_map[m_id] = future.result(timeout=2.5)
                    except Exception:
                        stats_map[m_id] = {}

            # Zaktualizuj listę meczów na żywo z BeeSports przez worker
            try:
                if getattr(self.sts_engine, '_worker', None):
                    bs_matches = self.sts_engine._worker.get_beesports_matches()
                    if bs_matches:
                        self.beesports._matches_cache = bs_matches
                        self.beesports._matches_cache_time = time.time()
            except Exception:
                pass

            # Zaktualizuj listę meczów na żywo z BetsAPI (jeśli włączone)
            if getattr(self.betsapi, 'enabled', False):
                try:
                    self.betsapi.update_live_matches_list()
                except Exception:
                    pass

            # Zaktualizuj listę meczów na żywo z Goaloo (szybki request HTTP)
            try:
                self.goaloo.update_live_matches_list()
            except Exception:
                pass

            # Równoległe pobieranie statystyk Goaloo dla meczów na żywo (max 8 wątków, timeout 2.5s)
            goaloo_stats_map = {}
            live_goaloo_candidates = [
                m for m in fs_matches 
                if not self.triggers.is_analytic_blacklisted(m.get('league', ''), m.get('home_team', ''), m.get('away_team', ''))
            ]
            if live_goaloo_candidates:
                with ThreadPoolExecutor(max_workers=8) as executor:
                    future_to_pair = {
                        executor.submit(
                            self.goaloo.get_live_stats,
                            m.get('home_team', ''),
                            m.get('away_team', ''),
                            minute=m.get('minute', 1)
                        ): (m.get('home_team', ''), m.get('away_team', ''))
                        for m in live_goaloo_candidates
                    }
                    for future in future_to_pair:
                        pair = future_to_pair[future]
                        try:
                            g_res = future.result(timeout=2.5)
                            if g_res and g_res.get('has_stats'):
                                goaloo_stats_map[pair] = g_res
                        except Exception:
                            pass

            processed_matches = []
            signals_count = 0
            used_sts_urls = set()

            for fs_m in target_matches:
                # 0. WCZESNY FILTR CZARNEJ LISTY: Jeśli mecz w lidze amatorskiej/młodzieżowej, pomiń zewnętrzne odpytywania
                is_bl = self.triggers.is_analytic_blacklisted(fs_m.get('league', ''), fs_m.get('home_team', ''), fs_m.get('away_team', ''))

                if is_bl:
                    stats = stats_map.get(fs_m['flashscore_id'], {})
                else:
                    # 1. PRIORYTET 1: Goaloo (główne źródło statystyk - prefetch)
                    pair_k = (fs_m.get('home_team', ''), fs_m.get('away_team', ''))
                    stats = goaloo_stats_map.get(pair_k) or self.goaloo.get_live_stats(
                        fs_m.get('home_team', ''),
                        fs_m.get('away_team', ''),
                        minute=fs_m.get('minute', 1)
                    )

                    # 2. PRIORYTET 2: BeeSports (dodatkowe źródło gdy brak w Goaloo)
                    if not stats or not stats.get('has_stats'):
                        stats = self.beesports.get_live_stats(
                            fs_m.get('home_team', ''),
                            fs_m.get('away_team', ''),
                            minute=fs_m.get('minute', 1)
                        )

                    # 3. PRIORYTET 3: BetsAPI (opcjonalne, gdy włączone)
                    if getattr(self.betsapi, 'enabled', False) and (not stats or not stats.get('has_stats')):
                        stats = self.betsapi.get_live_stats(
                            fs_m.get('home_team', ''),
                            fs_m.get('away_team', ''),
                            minute=fs_m.get('minute', 1)
                        )

                    # 4. PRIORYTET 4: Flashscore (gdy brak na Goaloo i BeeSports)
                    if not stats or not stats.get('has_stats'):
                        stats = stats_map.get(fs_m['flashscore_id'], {})

                s_tot = stats.get('shots_total') or 0
                sot_tot = stats.get('shots_on_target_total') or 0
                corn_tot = stats.get('corners_total') or 0
                xg_tot = stats.get('xg_total') or 0.0
                if xg_tot == 0.0 and (s_tot > 0 or sot_tot > 0 or corn_tot > 0):
                    sot_h = float(stats.get('shots_on_target_home') or 0)
                    sot_a = float(stats.get('shots_on_target_away') or 0)
                    soff_h = max(0.0, float(stats.get('shots_total_home') or 0) - sot_h)
                    soff_a = max(0.0, float(stats.get('shots_total_away') or 0) - sot_a)
                    dang_h = float(stats.get('dangerous_attacks_home') or 0)
                    dang_a = float(stats.get('dangerous_attacks_away') or 0)
                    corn_h = float(stats.get('corners_home') or 0)
                    corn_a = float(stats.get('corners_away') or 0)
                    big_h = float(stats.get('big_chances_home') or 0)
                    big_a = float(stats.get('big_chances_away') or 0)

                    xg_h = round(sot_h * 0.25 + soff_h * 0.05 + dang_h * 0.01 + corn_h * 0.035 + big_h * 0.35, 2)
                    xg_a = round(sot_a * 0.25 + soff_a * 0.05 + dang_a * 0.01 + corn_a * 0.035 + big_a * 0.35, 2)
                    stats['xg_home'] = xg_h
                    stats['xg_away'] = xg_a
                    stats['xg_total'] = round(xg_h + xg_a, 2)
                    stats['xg_is_estimated'] = True
                else:
                    stats.setdefault('xg_is_estimated', False)

                # Dopasuj do STS
                sts_match = self.matcher.match_flashscore_with_sts(fs_m, sts_matches) if sts_matches else None
                orig_fs_home = fs_m.get('home_team', '')
                orig_fs_away = fs_m.get('away_team', '')
                orig_fs_league = fs_m.get('league', '')

                if sts_match:
                    used_sts_urls.add(sts_match.get('url'))
                    odds_dict = sts_match.get('goals_odds', {})
                    sts_url = sts_match.get('url', 'https://www.sts.pl/live/pilka-nozna')
                    matched_with_sts = True
                    fs_m['live_markets'] = sts_match.get('live_markets', [])

                    # GWARANCJA: Nazwa i liga z STS zawsze na pierwszym miejscu
                    sts_home = sts_match.get('home_team') or orig_fs_home
                    sts_away = sts_match.get('away_team') or orig_fs_away
                    sts_league = sts_match.get('league', '')
                    if sts_league and not sts_league.startswith('Piłka Nożna'):
                        fs_m['league'] = sts_league
                    else:
                        fs_m['league'] = orig_fs_league or sts_league or "Piłka Nożna"

                    fs_m['home_team'] = sts_home
                    fs_m['away_team'] = sts_away

                    # ZAWSZE bierz najświeższy czas z STS (lub ten o większej minucie)
                    sts_min = sts_match.get('minute', 0)
                    if sts_min > 0 and (sts_min >= fs_m.get('minute', 0) or fs_m.get('minute', 0) == 0):
                        fs_m['minute'] = sts_min
                        fs_m['half'] = sts_match.get('half', fs_m.get('half', '1H'))
                        fs_m['stage_text'] = sts_match.get('stage_text', f"{sts_min}'")
                    if sts_match.get('half') in ('2H', 'FT') and fs_m.get('half') == 'HT':
                        fs_m['half'] = sts_match.get('half')
                        fs_m['stage_text'] = sts_match.get('stage_text', fs_m.get('stage_text'))

                    # Zsynchronizuj najświeższy wynik
                    if sts_match.get('score_str') and sts_match.get('score_str') != '0:0':
                        fs_m['score_str'] = sts_match['score_str']
                        fs_m['home_score'] = sts_match.get('home_score', fs_m.get('home_score', 0))
                        fs_m['away_score'] = sts_match.get('away_score', fs_m.get('away_score', 0))

                    # 5. PRIORYTET 5 / FALLBACK: Model radarowy STS (z bezwzględnym zachowaniem is_estimated = True)
                    if (not stats or not stats.get('has_stats')):
                        stats = self._estimate_live_stats(
                            score_h=fs_m.get('home_score', 0),
                            score_a=fs_m.get('away_score', 0),
                            minute=fs_m.get('minute', 0),
                            o1=sts_match.get('odds_1', 2.20),
                            oX=sts_match.get('odds_X', 3.20),
                            o2=sts_match.get('odds_2', 3.10),
                            league=sts_match.get('league', fs_m.get('league', ''))
                        )
                else:
                    odds_dict = {}
                    sts_url = 'https://www.sts.pl/live/pilka-nozna'
                    matched_with_sts = False
                    fs_m['live_markets'] = []
                    sts_home = None
                    sts_away = None

                if not stats:
                    stats = {}

                # Wyznacz wskaźniki i triggery bramkowe
                eval_res = self.triggers.evaluate_match(fs_m, stats, odds_dict)

                # Kwalifikacja kandydata do pobrania dokładnych kursów STS_REAL przez Playwright:
                # Nie otwieramy podstron dla meczów mających już rynki z feedu bez sygnału!
                should_fetch_real = (
                    matched_with_sts
                    and sts_url
                    and '/live/' in sts_url
                    and not is_bl
                    and (
                        eval_res.get('has_signals')
                        or (self.triggers.is_candidate_eligible(fs_m, stats) and not fs_m.get('live_markets'))
                    )
                )
                if should_fetch_real:
                    real_sub_mkts = self.sts_engine.get_match_real_live_markets(sts_url)
                    if real_sub_mkts:
                        fs_m['live_markets'] = real_sub_mkts
                        odds_dict['live_markets'] = real_sub_mkts
                        eval_res = self.triggers.evaluate_match(fs_m, stats, odds_dict)

                if eval_res.get('has_signals'):
                    signals_count += len(eval_res.get('signals', []))

                # Analiza kontekstowa przedmeczowa (z fetch_h2h=False dla braku opóźnień sieciowych w pętli live)
                prematch_ctx = self.prematch_analyzer.analyze_fixture(
                    fs_m.get('flashscore_id', ''), fs_m.get('league', ''), fs_m.get('home_team', ''), fs_m.get('away_team', ''), fetch_h2h=False
                )

                is_worth, grade, reasons = self._evaluate_worth_watching(
                    eval_res.get('has_signals', False), eval_res.get('danger_index', 50), eval_res.get('apm', 0.8), prematch_ctx, 'FLASHSCORE'
                )

                d_idx = eval_res.get('danger_index', 50)
                d_rat = "EKSTREMALNY" if d_idx >= 75 else ("WYSOKI" if d_idx >= 55 else ("ŚREDNI" if d_idx >= 35 else "NISKI"))

                enrich_status = 'COMPLETE' if (stats and stats.get('has_stats')) else ('ESTIMATED' if (stats and stats.get('is_estimated')) else 'BASIC')

                target_record = {
                    'id': fs_m.get('flashscore_id', ''),
                    'league': fs_m.get('league', ''),
                    'home_team': fs_m.get('home_team', ''),
                    'away_team': fs_m.get('away_team', ''),
                    'sts_home_team': sts_home,
                    'sts_away_team': sts_away,
                    'flashscore_home_team': orig_fs_home,
                    'flashscore_away_team': orig_fs_away,
                    'home_score': fs_m.get('home_score', 0),
                    'away_score': fs_m.get('away_score', 0),
                    'score_str': fs_m.get('score_str', '0:0'),
                    'minute': fs_m.get('minute', 0),
                    'half': fs_m.get('half', '1H'),
                    'stage_text': fs_m.get('stage_text', ''),
                    'stats': stats,
                    'danger_index': d_idx,
                    'danger_index_10': eval_res.get('danger_index_10', d_idx),
                    'danger_index_5': eval_res.get('danger_index_5', d_idx),
                    'danger_rating': d_rat,
                    'apm': eval_res.get('apm', 0.8),
                    'has_signals': eval_res.get('has_signals', False),
                    'signals': eval_res.get('signals', []),
                    'odds': odds_dict,
                    'live_markets': fs_m.get('live_markets', []),
                    'matched_with_sts': matched_with_sts,
                    'sts_url': sts_url,
                    'flashscore_url': fs_m.get('url', f"https://www.flashscore.pl/mecz/{fs_m.get('flashscore_id', '')}/"),
                    'prematch_context': prematch_ctx,
                    'is_worth_watching': is_worth,
                    'worth_grade': grade,
                    'worth_reasons': reasons,
                    'enrichment_status': enrich_status
                }
                processed_matches.append(target_record)
                self._update_cache_entry(target_record['id'], target_record)

                # Telegram: Sprawdź czy padł gol i zaktualizuj wiadomość o trafieniu
                self.telegram.check_and_notify_goal_event(processed_matches[-1])

                if eval_res.get('has_signals'):
                    for sig in eval_res.get('signals', []):
                        self.telegram.notify_goal_signal(processed_matches[-1], sig)

            # Dołącz mecze obecne na żywo w STS, które nie zostały jeszcze sparsowane przez Flashscore
            for sts_m in sts_matches:
                if sts_m.get('url') and sts_m['url'] in used_sts_urls:
                    continue
                if any(self.matcher.match_flashscore_with_sts(p_m, [sts_m]) for p_m in processed_matches):
                    continue

                sts_id = f"sts_{abs(hash(sts_m['home_team'] + sts_m['away_team']))}"

                # 0. WCZESNY FILTR CZARNEJ LISTY: Jeśli mecz w lidze amatorskiej/młodzieżowej, pomiń zewnętrzne odpytywania
                is_bl_sts = self.triggers.is_analytic_blacklisted(
                    sts_m.get('league', ''), sts_m.get('home_team', ''), sts_m.get('away_team', '')
                )

                prematch_ctx = self.prematch_analyzer.analyze_fixture(
                    sts_id, sts_m.get('league', ''), sts_m.get('home_team', ''), sts_m.get('away_team', ''), fetch_h2h=False
                )

                if is_bl_sts:
                    stats = self._estimate_live_stats(
                        score_h=sts_m.get('home_score', 0),
                        score_a=sts_m.get('away_score', 0),
                        minute=sts_m.get('minute', 0),
                        o1=sts_m.get('odds_1', 2.20),
                        oX=sts_m.get('odds_X', 3.20),
                        o2=sts_m.get('odds_2', 3.10),
                        league=sts_m.get('league', '')
                    )
                else:
                    # PRIORYTET 1: Goaloo (główne źródło)
                    stats = self.goaloo.get_live_stats(
                        sts_m['home_team'],
                        sts_m['away_team'],
                        minute=sts_m['minute']
                    )

                    # PRIORYTET 2: BeeSports (dodatkowe źródło gdy brak w Goaloo)
                    if not stats or not stats.get('has_stats'):
                        stats = self.beesports.get_live_stats(
                            sts_m['home_team'],
                            sts_m['away_team'],
                            minute=sts_m['minute']
                        )

                    # PRIORYTET 3: BetsAPI (opcjonalne, gdy włączone)
                    if getattr(self.betsapi, 'enabled', False) and (not stats or not stats.get('has_stats')):
                        stats = self.betsapi.get_live_stats(
                            sts_m['home_team'],
                            sts_m['away_team'],
                            minute=sts_m['minute']
                        )

                    # PRIORYTET 4 / FALLBACK: Wylicz dynamiczne statystyki na żywo z modelu radarowego STS
                    if not stats or not stats.get('has_stats'):
                        stats = self._estimate_live_stats(
                            score_h=sts_m.get('home_score', 0),
                            score_a=sts_m.get('away_score', 0),
                            minute=sts_m.get('minute', 0),
                            o1=sts_m.get('odds_1', 2.20),
                            oX=sts_m.get('odds_X', 3.20),
                            o2=sts_m.get('odds_2', 3.10),
                            league=sts_m.get('league', '')
                        )

                fs_repr = {
                    'flashscore_id': sts_id,
                    'home_team': sts_m.get('home_team', ''),
                    'away_team': sts_m.get('away_team', ''),
                    'home_score': sts_m.get('home_score', 0),
                    'away_score': sts_m.get('away_score', 0),
                    'minute': sts_m.get('minute', 0),
                    'half': sts_m.get('half', '1H'),
                    'league': sts_m.get('league', ''),
                    'is_started': sts_m.get('is_started', True),
                    'live_markets': sts_m.get('live_markets', [])
                }

                eval_res = self.triggers.evaluate_match(fs_repr, stats, sts_m.get('goals_odds', {}))

                # Kwalifikacja kandydata do pobrania dokładnych kursów STS_REAL przez Playwright:
                # Nie otwieramy podstron dla meczów mających już rynki z feedu bez sygnału!
                should_fetch_real_sts = (
                    sts_m.get('url')
                    and '/live/' in sts_m.get('url', '')
                    and not is_bl_sts
                    and (
                        eval_res.get('has_signals')
                        or (self.triggers.is_candidate_eligible(fs_repr, stats) and not sts_m.get('live_markets'))
                    )
                )
                if should_fetch_real_sts:
                    real_sub_mkts = self.sts_engine.get_match_real_live_markets(sts_m.get('url', ''))
                    if real_sub_mkts:
                        sts_m['live_markets'] = real_sub_mkts
                        fs_repr['live_markets'] = real_sub_mkts
                        eval_res = self.triggers.evaluate_match(fs_repr, stats, sts_m.get('goals_odds', {}))
                if eval_res.get('has_signals'):
                    signals_count += len(eval_res['signals'])

                is_worth, grade, reasons = self._evaluate_worth_watching(
                    eval_res.get('has_signals', False), eval_res.get('danger_index', 50), eval_res.get('apm', 0.8), prematch_ctx, 'STS_ONLY'
                )

                d_idx = eval_res.get('danger_index', 50)
                d_rat = "EKSTREMALNY" if d_idx >= 75 else ("WYSOKI" if d_idx >= 55 else ("ŚREDNI" if d_idx >= 35 else "NISKI"))
                live_mkts = sts_m.get('live_markets', [])

                enrich_status = 'COMPLETE' if (stats and stats.get('has_stats')) else ('ESTIMATED' if (stats and stats.get('is_estimated')) else 'BASIC')

                sts_record = {
                    'id': sts_id,
                    'league': sts_m.get('league', ''),
                    'home_team': sts_m.get('home_team', ''),
                    'away_team': sts_m.get('away_team', ''),
                    'sts_home_team': sts_m.get('home_team', ''),
                    'sts_away_team': sts_m.get('away_team', ''),
                    'flashscore_home_team': sts_m.get('home_team', ''),
                    'flashscore_away_team': sts_m.get('away_team', ''),
                    'home_score': sts_m.get('home_score', 0),
                    'away_score': sts_m.get('away_score', 0),
                    'score_str': sts_m.get('score_str', '0:0'),
                    'minute': sts_m.get('minute', 0),
                    'half': sts_m.get('half', '1H'),
                    'is_started': sts_m.get('is_started', True),
                    'stage_text': sts_m.get('stage_text', 'LIVE STS'),
                    'stats': stats,
                    'danger_index': d_idx,
                    'danger_index_10': eval_res.get('danger_index_10', d_idx),
                    'danger_index_5': eval_res.get('danger_index_5', d_idx),
                    'danger_rating': d_rat,
                    'apm': eval_res.get('apm', 0.8),
                    'has_signals': eval_res.get('has_signals', False),
                    'signals': eval_res.get('signals', []),
                    'odds': sts_m.get('goals_odds', {}),
                    'live_markets': live_mkts,
                    'matched_with_sts': True,
                    'sts_url': sts_m.get('url', ''),
                    'flashscore_url': 'https://www.flashscore.pl/',
                    'prematch_context': prematch_ctx,
                    'is_worth_watching': is_worth,
                    'worth_grade': grade,
                    'worth_reasons': reasons,
                    'enrichment_status': enrich_status
                }
                processed_matches.append(sts_record)
                self._update_cache_entry(sts_record['id'], sts_record)

                # Telegram: Sprawdź czy padł gol i zaktualizuj wiadomość o trafieniu
                self.telegram.check_and_notify_goal_event(processed_matches[-1])

                # Telegram: Jeśli jest aktywny sygnał, wyślij lub zaktualizuj w locie kartę meczu
                if eval_res.get('has_signals') and eval_res.get('signals'):
                    primary_sig = eval_res['signals'][0]
                    self.telegram.notify_goal_signal(processed_matches[-1], primary_sig)

            # Jeśli było więcej meczów na Flashscore niż w pierwszej partii target_matches,
            # dołącz pozostałe jako w pełni zsynchronizowane z STS, aby panel /api/scan widział pełną listę live
            for fs_rem in fs_matches[40:]:
                if any(p['id'] == fs_rem.get('flashscore_id') for p in processed_matches):
                    continue
                sts_match = self.matcher.match_flashscore_with_sts(fs_rem, sts_matches) if sts_matches else None
                odds_dict = sts_match.get('goals_odds', {}) if sts_match else {}
                sts_url = sts_match.get('url', 'https://www.sts.pl/live/pilka-nozna') if sts_match else 'https://www.sts.pl/live/pilka-nozna'
                matched_with_sts = sts_match is not None
                if sts_match:
                    used_sts_urls.add(sts_match.get('url'))

                orig_fs_home = fs_rem.get('home_team', '')
                orig_fs_away = fs_rem.get('away_team', '')
                orig_fs_league = fs_rem.get('league', '')

                if sts_match:
                    sts_home = sts_match.get('home_team') or orig_fs_home
                    sts_away = sts_match.get('away_team') or orig_fs_away
                    sts_league = sts_match.get('league', '')
                    if sts_league and not sts_league.startswith('Piłka Nożna'):
                        fs_rem['league'] = sts_league
                    else:
                        fs_rem['league'] = orig_fs_league or sts_league or "Piłka Nożna"
                    fs_rem['home_team'] = sts_home
                    fs_rem['away_team'] = sts_away

                    sts_min = sts_match.get('minute', 0)
                    if sts_min > 0 and (sts_min >= fs_rem.get('minute', 0) or fs_rem.get('minute', 0) == 0):
                        fs_rem['minute'] = sts_min
                        fs_rem['half'] = sts_match.get('half', fs_rem.get('half', '1H'))
                        fs_rem['stage_text'] = sts_match.get('stage_text', f"{sts_min}'")
                    if sts_match.get('half') in ('2H', 'FT') and fs_rem.get('half') == 'HT':
                        fs_rem['half'] = sts_match.get('half')
                        fs_rem['stage_text'] = sts_match.get('stage_text', fs_rem.get('stage_text'))

                    if sts_match.get('score_str') and sts_match.get('score_str') != '0:0':
                        fs_rem['score_str'] = sts_match['score_str']
                        fs_rem['home_score'] = sts_match.get('home_score', fs_rem.get('home_score', 0))
                        fs_rem['away_score'] = sts_match.get('away_score', fs_rem.get('away_score', 0))

                    fs_rem['live_markets'] = sts_match.get('live_markets', [])
                else:
                    fs_rem['live_markets'] = []

                # Pobranie statystyk dla meczu poza TOP 40:
                # 1. Goaloo (główne źródło - z prefetchu)
                pair_k = (fs_rem.get('home_team', ''), fs_rem.get('away_team', ''))
                stats = goaloo_stats_map.get(pair_k) or self.goaloo.get_live_stats(fs_rem.get('home_team', ''), fs_rem.get('away_team', ''), minute=fs_rem.get('minute', 1))
                
                # 2. BeeSports (dodatkowe źródło gdy brak w Goaloo)
                if not stats or not stats.get('has_stats'):
                    stats = self.beesports.get_live_stats(fs_rem.get('home_team', ''), fs_rem.get('away_team', ''), minute=fs_rem.get('minute', 1))

                # 3. BetsAPI (opcjonalne, gdy włączone)
                if getattr(self.betsapi, 'enabled', False) and (not stats or not stats.get('has_stats')):
                    stats = self.betsapi.get_live_stats(fs_rem.get('home_team', ''), fs_rem.get('away_team', ''), minute=fs_rem.get('minute', 1))

                # 2. Fallback: Jeśli brak statystyk zewnętrznych, ale mecz ma STS -> model radarowy STS
                if (not stats or not stats.get('has_stats')) and sts_match:
                    stats = self._estimate_live_stats(
                        score_h=fs_rem.get('home_score', 0),
                        score_a=fs_rem.get('away_score', 0),
                        minute=fs_rem.get('minute', 0),
                        o1=sts_match.get('odds_1', 2.20),
                        oX=sts_match.get('odds_X', 3.20),
                        o2=sts_match.get('odds_2', 3.10),
                        league=sts_match.get('league', fs_rem.get('league', ''))
                    )
                elif not stats:
                    stats = {}

                eval_res = self.triggers.evaluate_match(fs_rem, stats, odds_dict)
                d_idx = eval_res.get('danger_index', stats.get('danger_index', 50) if stats else 50)
                d_rat = "EKSTREMALNY" if d_idx >= 75 else ("WYSOKI" if d_idx >= 55 else ("ŚREDNI" if d_idx >= 35 else "NISKI"))
                prematch_ctx = self.prematch_analyzer.analyze_fixture(
                    fs_rem.get('flashscore_id'), fs_rem.get('league', ''), fs_rem.get('home_team', ''), fs_rem.get('away_team', ''), fetch_h2h=False
                )
                is_worth, grade, reasons = self._evaluate_worth_watching(
                    eval_res.get('has_signals', False), d_idx, eval_res.get('apm', 0.8), prematch_ctx, 'FLASHSCORE'
                )

                enrich_status = 'COMPLETE' if (stats and stats.get('has_stats')) else ('ESTIMATED' if (stats and stats.get('is_estimated')) else 'BASIC')

                rem_record = {
                    'id': fs_rem.get('flashscore_id'),
                    'league': fs_rem.get('league', ''),
                    'home_team': fs_rem.get('home_team', ''),
                    'away_team': fs_rem.get('away_team', ''),
                    'sts_home_team': sts_match.get('home_team') if sts_match else None,
                    'sts_away_team': sts_match.get('away_team') if sts_match else None,
                    'flashscore_home_team': orig_fs_home,
                    'flashscore_away_team': orig_fs_away,
                    'home_score': fs_rem.get('home_score', 0),
                    'away_score': fs_rem.get('away_score', 0),
                    'score_str': fs_rem.get('score_str', '0:0'),
                    'minute': fs_rem.get('minute', 0),
                    'half': fs_rem.get('half', '1H'),
                    'stage_text': fs_rem.get('stage_text', ''),
                    'stats': stats,
                    'danger_index': d_idx,
                    'danger_index_10': eval_res.get('danger_index_10', d_idx),
                    'danger_index_5': eval_res.get('danger_index_5', d_idx),
                    'danger_rating': d_rat,
                    'apm': eval_res.get('apm', stats.get('apm', 0.8) if stats else 0.8),
                    'has_signals': eval_res.get('has_signals', False),
                    'signals': eval_res.get('signals', []),
                    'odds': odds_dict,
                    'live_markets': sts_match.get('live_markets', []) if sts_match else [],
                    'matched_with_sts': matched_with_sts,
                    'sts_url': sts_url,
                    'flashscore_url': fs_rem.get('url', f"https://www.flashscore.pl/mecz/{fs_rem.get('flashscore_id')}/"),
                    'prematch_context': prematch_ctx,
                    'is_worth_watching': is_worth,
                    'worth_grade': grade,
                    'worth_reasons': reasons,
                    'enrichment_status': enrich_status
                }
                processed_matches.append(rem_record)
                self._update_cache_entry(rem_record['id'], rem_record)

            # Sortuj mecze: najpierw te z aktywnymi sygnałami, potem wg Danger Index
            processed_matches.sort(
                key=lambda x: (1 if x['has_signals'] else 0, x['danger_index'], x['apm']),
                reverse=True
            )

            # Końcowy pass auto-rozliczenia na przetworzonych meczach
            try:
                self.telegram.auto_settle_active_cards(live_matches=processed_matches, finished_matches=fs_finished_all)
            except Exception as ex:
                print(f"[Aggregator] Błąd post-scan auto_settle: {ex}")

            # Czyszczenie pamięci RAM z meczów nieobecnych w feedzie live (ochrona 24/7)
            try:
                active_keys = {
                    str(m.get('flashscore_id') or m.get('id') or f"{m.get('home_team')}_{m.get('away_team')}").strip().lower()
                    for m in processed_matches
                }
                self.triggers.cleanup_unseen_matches(active_keys)
            except Exception as ex:
                pass

            scan_duration = round(time.time() - start_time, 2)
            with self._cache_lock:
                self.cached_results = processed_matches
            self.last_scan_time = time.time()
            self._is_enriched = True
            self._enrichment_status = "COMPLETE"

            return {
                'timestamp': time.strftime('%H:%M:%S'),
                'total_live_matches': len(processed_matches),
                'displayed_matches_count': len(processed_matches),
                'signals_count': signals_count,
                'scan_duration_sec': scan_duration,
                'is_enriched': True,
                'enrichment_status': 'COMPLETE',
                'matches': processed_matches
            }




    def _evaluate_worth_watching(self, has_signals: bool, danger_idx: int, apm: float, p_ctx: Dict[str, Any], source: str) -> tuple:
        reasons = []
        grade = "STANDARD"
        is_worth = False

        if has_signals:
            reasons.append("🚨 Aktywny sygnał wejścia (Over)")
            grade = "TOP"
            is_worth = True

        if danger_idx >= 70 or apm >= 1.0:
            reasons.append(f"🔥 Bardzo wysoki napór na bramkę ({danger_idx}%, {apm} APM)")
            grade = "TOP"
            is_worth = True
        elif danger_idx >= 55 or apm >= 0.8:
            reasons.append(f"⚡ Dobra dynamika spotkania ({danger_idx}%)")
            if grade != "TOP": grade = "GOOD"
            is_worth = True

        p_rating = p_ctx.get('prematch_goal_rating', 50)
        ht_pct = p_ctx.get('ht_over05_pct', 70)

        if p_rating >= 85 or ht_pct >= 85:
            reasons.append(f"⭐ Liga/drużyny ultra-bramkowe ({ht_pct}% Over 0.5 HT)")
            grade = "TOP"
            is_worth = True
        elif p_rating >= 75:
            reasons.append(f"🟢 Wysoki potencjał bramkowy ({p_rating}%)")
            if grade != "TOP": grade = "GOOD"
            is_worth = True

        if p_ctx.get('congestion', {}).get('has_european_soon'):
            reasons.append("🇪🇺 Rotacja / Mecz pucharowy wkrótce")
            is_worth = True

        if not is_worth:
            reasons.append("⚪ Standardowy mecz / brak wyraźnej przewagi statystycznej")

        return is_worth, grade, reasons

    def _estimate_live_stats(self, score_h: int, score_a: int, minute: int, o1: float, oX: float, o2: float, league: str) -> Dict[str, Any]:
        """
        Wylicza realistyczne statystyki meczowe na żywo (xG, Strzały, Rożne, Posiadanie, Napór)
        na podstawie kursów STS Live, wyniku i upływu czasu, gdy brak danych z Flashscore.
        """
        if minute <= 0:
            return {
                'xg_home': 0.0, 'xg_away': 0.0, 'xg_total': 0.0,
                'shots_total_home': 0, 'shots_total_away': 0,
                'shots_on_target_home': 0, 'shots_on_target_away': 0,
                'corners_home': 0, 'corners_away': 0, 'corners_total': 0,
                'dangerous_attacks_home': 0, 'dangerous_attacks_away': 0, 'dangerous_attacks_total': 0,
                'possession_home': 50, 'possession_away': 50,
                'danger_index': 0,
                'danger_rating': 'OCZEKUJE',
                'apm': 0.0,
                'score': f"{score_h}:{score_a}"
            }

        minute = max(1, min(90, minute))

        # 1. Prawdopodobieństwo wygranej z kursów (implied probability)
        prob_1 = (1.0 / max(1.01, o1))
        prob_2 = (1.0 / max(1.01, o2))
        total_p = prob_1 + prob_2

        possession_h = round((prob_1 / total_p) * 100) if total_p > 0 else 50
        possession_h = max(25, min(75, possession_h))
        possession_a = 100 - possession_h

        # 2. Szacowane xG
        time_factor = minute / 90.0
        xg_h = round(score_h * 0.75 + (possession_h / 100.0) * time_factor * 1.2 + 0.1, 2)
        xg_a = round(score_a * 0.75 + (possession_a / 100.0) * time_factor * 1.6 + 0.1, 2)
        xg_tot = round(xg_h + xg_a, 2)

        # 3. Strzały i celne
        shots_h = max(score_h, int(time_factor * 8 * (possession_h / 50.0)))
        shots_a = max(score_a, int(time_factor * 11 * (possession_a / 50.0)))

        sot_h = max(score_h, int(shots_h * 0.35))
        sot_a = max(score_a, int(shots_a * 0.45))

        # 4. Rzuty rożne
        corners_h = max(0, int(time_factor * 5 * (possession_h / 50.0)))
        corners_a = max(0, int(time_factor * 7 * (possession_a / 50.0)))
        corners_tot = corners_h + corners_a

        # 5. Niebezpieczne ataki i Indeks Groźności
        danger_h = int(time_factor * 35 * (possession_h / 50.0))
        danger_a = int(time_factor * 45 * (possession_a / 50.0))
        danger_tot = danger_h + danger_a

        apm = round((danger_tot) / max(1, minute), 2)
        danger_idx = min(95, int(45 + (score_h + score_a) * 8 + (apm * 15)))

        return {
            'xg_home': xg_h, 'xg_away': xg_a, 'xg_total': xg_tot,
            'shots_total_home': shots_h, 'shots_total_away': shots_a,
            'shots_on_target_home': sot_h, 'shots_on_target_away': sot_a,
            'corners_home': corners_h, 'corners_away': corners_a, 'corners_total': corners_tot,
            'dangerous_attacks_home': danger_h, 'dangerous_attacks_away': danger_a, 'dangerous_attacks_total': danger_tot,
            'possession_home': possession_h, 'possession_away': possession_a,
            'danger_index': danger_idx,
            'apm': apm,
            'is_estimated': True
        }
