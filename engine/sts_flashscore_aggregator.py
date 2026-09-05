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
        self._scan_lock = threading.Lock()
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

    def scan_all(self, only_signals: bool = False, min_minute: int = 0, half_filter: str = "ALL", demo_mode: bool = False) -> Dict[str, Any]:
        """
        Zwraca natychmiastowo mecze z pamięci RAM (czas < 0.001s).
        Zawiera inteligentny mechanizm Live Real-Time Clock Sync (minuty płynnie kroczą w czasie rzeczywistym).
        NIGDY nie blokuje żądań HTTP!
        """
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
            "signals_count": signals_cnt
        }


    def _execute_full_scan(self, demo_mode: bool = False):
        with self._scan_lock:
            start_time = time.time()
            # 1. Pobierz wszystkie mecze z Flashscore Live oraz zakończone z dzisiaj i wczoraj
            fs_all_matches = self.fs_engine.get_live_soccer_matches(include_all_today=True)
            fs_matches = [m for m in fs_all_matches if m.get('is_live')] if not demo_mode else fs_all_matches
            
            # Pobierz pełną bazę zakończonych spotkań (do 3-4 dni wstecz dla pełnej ciągłości)
            fs_finished_all = []
            try:
                fs_finished_all = self.fs_engine.get_finished_results(days_back=3)
            except Exception as e:
                print(f"[Aggregator] Finished results fetch error: {e}")

            # 2. Pobierz mecze z STS (jeśli są)
            sts_matches = []
            try:
                sts_matches = self.sts_engine.fetch_live_matches(include_esports=False)
                if sts_matches:
                    self.matcher.pre_normalize_matches(sts_matches)
            except Exception as e:
                print(f"[Aggregator] STS fetch error: {e}")

            # 3. Automatycznie rozlicz kupony w Dzienniku Typera oraz karty na Telegramie
            all_today_matches = fs_all_matches + sts_matches + fs_finished_all
            try:
                self.tracker.auto_resolve_bets(all_today_matches)
            except Exception as ex:
                print(f"[Aggregator] Błąd auto-rozliczania kuponów: {ex}")

            try:
                all_live_now = [m for m in (fs_all_matches + sts_matches) if m.get('is_live')]
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

            # Zaktualizuj listę meczów na żywo z BetsAPI (szybki request HTTP)
            try:
                self.betsapi.update_live_matches_list()
            except Exception:
                pass

            # Zaktualizuj listę meczów na żywo z Goaloo (szybki request HTTP)
            try:
                self.goaloo.update_live_matches_list()
            except Exception:
                pass

            processed_matches = []
            signals_count = 0
            used_sts_urls = set()

            for fs_m in target_matches:
                # 1. PRIORYTET 1: BeeSports (100% realne statystyki live)
                stats = self.beesports.get_live_stats(
                    fs_m.get('home_team', ''),
                    fs_m.get('away_team', ''),
                    minute=fs_m.get('minute', 1)
                )

                # 2. PRIORYTET 2: BetsAPI (gdy brak na BeeSports)
                if not stats or not stats.get('has_stats'):
                    stats = self.betsapi.get_live_stats(
                        fs_m.get('home_team', ''),
                        fs_m.get('away_team', ''),
                        minute=fs_m.get('minute', 1)
                    )

                # 3. PRIORYTET 3: Goaloo (gdy brak na BeeSports i BetsAPI)
                if not stats or not stats.get('has_stats'):
                    stats = self.goaloo.get_live_stats(
                        fs_m.get('home_team', ''),
                        fs_m.get('away_team', ''),
                        minute=fs_m.get('minute', 1)
                    )

                # 4. PRIORYTET 4: Flashscore (gdy brak na BeeSports, BetsAPI i Goaloo)
                if not stats or not stats.get('has_stats'):
                    stats = stats_map.get(fs_m['flashscore_id'], {})

                if stats.get('xg_total', 0.0) == 0.0 and (stats.get('shots_total', 0) > 0 or stats.get('shots_on_target_total', 0) > 0 or stats.get('corners_total', 0) > 0):
                    sot_h = stats.get('shots_on_target_home', 0)
                    sot_a = stats.get('shots_on_target_away', 0)
                    soff_h = max(0, stats.get('shots_total_home', 0) - sot_h)
                    soff_a = max(0, stats.get('shots_total_away', 0) - sot_a)
                    dang_h = stats.get('dangerous_attacks_home', 0)
                    dang_a = stats.get('dangerous_attacks_away', 0)
                    corn_h = stats.get('corners_home', 0)
                    corn_a = stats.get('corners_away', 0)
                    big_h = stats.get('big_chances_home', 0)
                    big_a = stats.get('big_chances_away', 0)

                    xg_h = round(sot_h * 0.25 + soff_h * 0.05 + dang_h * 0.01 + corn_h * 0.035 + big_h * 0.35, 2)
                    xg_a = round(sot_a * 0.25 + soff_a * 0.05 + dang_a * 0.01 + corn_a * 0.035 + big_a * 0.35, 2)
                    stats['xg_home'] = xg_h
                    stats['xg_away'] = xg_a
                    stats['xg_total'] = round(xg_h + xg_a, 2)

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

                    # Zsynchronizuj najświeższy wynik
                    if sts_match.get('score_str') and sts_match.get('score_str') != '0:0':
                        fs_m['score_str'] = sts_match['score_str']
                        fs_m['home_score'] = sts_match.get('home_score', fs_m.get('home_score', 0))
                        fs_m['away_score'] = sts_match.get('away_score', fs_m.get('away_score', 0))
                else:
                    odds_dict = {}
                    sts_url = 'https://www.sts.pl/live/pilka-nozna'
                    matched_with_sts = False
                    fs_m['live_markets'] = []
                    sts_home = None
                    sts_away = None

                # Wyznacz wskaźniki i triggery bramkowe
                eval_res = self.triggers.evaluate_match(fs_m, stats, odds_dict)

                # Jeśli mecz generuje sygnał lub ma wysoki indeks, pobierz 100% realne kursy z podstrony STS
                if matched_with_sts and sts_url and '/live/' in sts_url and (eval_res.get('has_signals') or stats.get('danger_index', 0) >= 65):
                    real_sub_mkts = self.sts_engine.get_match_real_live_markets(sts_url)
                    if real_sub_mkts:
                        fs_m['live_markets'] = real_sub_mkts
                        odds_dict['live_markets'] = real_sub_mkts
                        eval_res = self.triggers.evaluate_match(fs_m, stats, odds_dict)

                if eval_res.get('has_signals'):
                    signals_count += len(eval_res.get('signals', []))

                # Analiza kontekstowa przedmeczowa (z fetch_h2h=False dla braku opóźnień sieciowych w pętli live)
                prematch_ctx = self.prematch_analyzer.analyze_fixture(
                    fs_m['flashscore_id'], fs_m['league'], fs_m['home_team'], fs_m['away_team'], fetch_h2h=False
                )

                is_worth, grade, reasons = self._evaluate_worth_watching(
                    eval_res.get('has_signals', False), eval_res.get('danger_index', 50), eval_res.get('apm', 0.8), prematch_ctx, 'FLASHSCORE'
                )

                d_idx = eval_res.get('danger_index', 50)
                d_rat = "EKSTREMALNY" if d_idx >= 75 else ("WYSOKI" if d_idx >= 55 else ("ŚREDNI" if d_idx >= 35 else "NISKI"))

                processed_matches.append({
                    'id': fs_m['flashscore_id'],
                    'league': fs_m['league'],
                    'home_team': fs_m['home_team'],
                    'away_team': fs_m['away_team'],
                    'sts_home_team': sts_home,
                    'sts_away_team': sts_away,
                    'flashscore_home_team': orig_fs_home,
                    'flashscore_away_team': orig_fs_away,
                    'home_score': fs_m['home_score'],
                    'away_score': fs_m['away_score'],
                    'score_str': fs_m['score_str'],
                    'minute': fs_m['minute'],
                    'half': fs_m['half'],
                    'stage_text': fs_m['stage_text'],
                    'stats': stats,
                    'danger_index': d_idx,
                    'danger_rating': d_rat,
                    'apm': eval_res.get('apm', 0.8),
                    'has_signals': eval_res.get('has_signals', False),
                    'signals': eval_res.get('signals', []),
                    'odds': odds_dict,
                    'live_markets': fs_m.get('live_markets', []),
                    'matched_with_sts': matched_with_sts,
                    'sts_url': sts_url,
                    'flashscore_url': fs_m.get('url', f"https://www.flashscore.pl/mecz/{fs_m['flashscore_id']}/"),
                    'prematch_context': prematch_ctx,
                    'is_worth_watching': is_worth,
                    'worth_grade': grade,
                    'worth_reasons': reasons
                })

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
                prematch_ctx = self.prematch_analyzer.analyze_fixture(
                    sts_id, sts_m['league'], sts_m['home_team'], sts_m['away_team'], fetch_h2h=False
                )

                # PRIORYTET 1: Sprawdź najpierw 100% realne statystyki z BeeSports
                stats = self.beesports.get_live_stats(
                    sts_m['home_team'],
                    sts_m['away_team'],
                    minute=sts_m['minute']
                )

                # PRIORYTET 2: Sprawdź BetsAPI
                if not stats or not stats.get('has_stats'):
                    stats = self.betsapi.get_live_stats(
                        sts_m['home_team'],
                        sts_m['away_team'],
                        minute=sts_m['minute']
                    )

                # PRIORYTET 3: Sprawdź Goaloo
                if not stats or not stats.get('has_stats'):
                    stats = self.goaloo.get_live_stats(
                        sts_m['home_team'],
                        sts_m['away_team'],
                        minute=sts_m['minute']
                    )

                # PRIORYTET 4 / FALLBACK: Wylicz dynamiczne statystyki na żywo z modelu radarowego STS
                if not stats or not stats.get('has_stats'):
                    stats = self._estimate_live_stats(
                        score_h=sts_m['home_score'],
                        score_a=sts_m['away_score'],
                        minute=sts_m['minute'],
                        o1=sts_m.get('odds_1', 2.20),
                        oX=sts_m.get('odds_X', 3.20),
                        o2=sts_m.get('odds_2', 3.10),
                        league=sts_m['league']
                    )

                fs_repr = {
                    'flashscore_id': sts_id,
                    'home_team': sts_m['home_team'],
                    'away_team': sts_m['away_team'],
                    'home_score': sts_m['home_score'],
                    'away_score': sts_m['away_score'],
                    'minute': sts_m['minute'],
                    'half': sts_m['half'],
                    'is_started': sts_m.get('is_started', True),
                    'live_markets': sts_m.get('live_markets', [])
                }

                eval_res = self.triggers.evaluate_match(fs_repr, stats, sts_m['goals_odds'])

                # Jeśli mecz generuje sygnał lub ma wysoki indeks, pobierz 100% realne kursy z podstrony STS
                if sts_m.get('url') and '/live/' in sts_m['url'] and (eval_res.get('has_signals') or stats.get('danger_index', 0) >= 65):
                    real_sub_mkts = self.sts_engine.get_match_real_live_markets(sts_m['url'])
                    if real_sub_mkts:
                        sts_m['live_markets'] = real_sub_mkts
                        fs_repr['live_markets'] = real_sub_mkts
                        eval_res = self.triggers.evaluate_match(fs_repr, stats, sts_m['goals_odds'])
                if eval_res.get('has_signals'):
                    signals_count += len(eval_res['signals'])

                is_worth, grade, reasons = self._evaluate_worth_watching(
                    eval_res.get('has_signals', False), eval_res.get('danger_index', 50), eval_res.get('apm', 0.8), prematch_ctx, 'STS_ONLY'
                )

                d_idx = eval_res.get('danger_index', 50)
                d_rat = "EKSTREMALNY" if d_idx >= 75 else ("WYSOKI" if d_idx >= 55 else ("ŚREDNI" if d_idx >= 35 else "NISKI"))
                live_mkts = sts_m.get('live_markets', [])

                processed_matches.append({
                    'id': sts_id,
                    'league': sts_m['league'],
                    'home_team': sts_m['home_team'],
                    'away_team': sts_m['away_team'],
                    'sts_home_team': sts_m['home_team'],
                    'sts_away_team': sts_m['away_team'],
                    'flashscore_home_team': sts_m['home_team'],
                    'flashscore_away_team': sts_m['away_team'],
                    'home_score': sts_m['home_score'],
                    'away_score': sts_m['away_score'],
                    'score_str': sts_m['score_str'],
                    'minute': sts_m['minute'],
                    'half': sts_m['half'],
                    'is_started': sts_m.get('is_started', True),
                    'stage_text': sts_m.get('stage_text', 'LIVE STS'),
                    'stats': stats,
                    'danger_index': d_idx,
                    'danger_rating': d_rat,
                    'apm': eval_res.get('apm', 0.8),
                    'has_signals': eval_res.get('has_signals', False),
                    'signals': eval_res.get('signals', []),
                    'odds': sts_m['goals_odds'],
                    'live_markets': live_mkts,
                    'matched_with_sts': True,
                    'sts_url': sts_m['url'],
                    'flashscore_url': 'https://www.flashscore.pl/',
                    'prematch_context': prematch_ctx,
                    'is_worth_watching': is_worth,
                    'worth_grade': grade,
                    'worth_reasons': reasons
                })

                # Telegram: Sprawdź czy padł gol i zaktualizuj wiadomość o trafieniu
                self.telegram.check_and_notify_goal_event(processed_matches[-1])

                # Telegram: Jeśli jest aktywny sygnał, wyślij lub zaktualizuj w locie kartę meczu
                if eval_res.get('has_signals') and eval_res.get('signals'):
                    primary_sig = eval_res['signals'][0]
                    self.telegram.notify_goal_signal(processed_matches[-1], primary_sig)

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
            self.cached_results = processed_matches
            self.last_scan_time = time.time()

            return {
                'timestamp': time.strftime('%H:%M:%S'),
                'total_live_matches': len(processed_matches),
                'displayed_matches_count': len(processed_matches),
                'signals_count': signals_count,
                'scan_duration_sec': scan_duration,
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
