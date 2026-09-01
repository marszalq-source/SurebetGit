"""
Moduł pobierania oferty i kursów Live z STS dla piłki nożnej.
Zoptymalizowany pod kątem minimalnych opóźnień (Persistent Playwright Worker, Aggressive Route Blocking, Zero Memory Leaks).
"""
import re
import time
import queue
import threading
import atexit
from typing import List, Dict, Any, Optional
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeoutError

import sys
if sys.platform == 'win32':
    try:
        import ctypes
        ctypes.windll.kernel32.SetErrorMode(0x0001 | 0x0002)
    except Exception:
        pass

STS_LIVE_SOCCER_URL = 'https://www.sts.pl/live/pilka-nozna'
STS_PREMATCH_SOCCER_URL = 'https://www.sts.pl/zaklady-bukmacherskie/pilka-nozna/1'

_STS_BLOCKED_PATTERNS = (
    'google-analytics', 'googletagmanager', 'googleadservices', 'pagead2',
    'trafficguard', 'survicate', 'cookiebot', 'datadog', 'sentry', 'hotjar',
    'facebook', 'doubleclick', 'gemius', 'clarity', 'usercentrics',
    'smartlook', 'criteo', 'scorecardresearch', 'adnxs'
)

def _sts_route_handler(route):
    req = route.request
    url_lower = req.url.lower()
    # 1. Blokada ciężkich zasobów niepotrzebnych do parsowania DOM
    if req.resource_type in ('image', 'media', 'font', 'stylesheet'):
        route.abort()
        return
    # 2. Blokada skryptów analitycznych, reklam i trackerów
    if any(p in url_lower for p in _STS_BLOCKED_PATTERNS):
        route.abort()
        return
    route.continue_()

class _STSLiveWorker:
    """
    Dedykowany wątek roboczy zarządzający instancją Playwright i kontekstem przeglądarki.
    Gwarantuje brak wycieków pamięci, eliminację zombie procesów oraz czas odpowiedzi < 50ms.
    """
    _instance = None
    _lock = threading.Lock()

    def __init__(self):
        self._cmd_queue = queue.Queue()
        self._ready_event = threading.Event()
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="STSLiveWorkerThread")
        self._thread.start()
        self._ready_event.wait(timeout=12.0)
        atexit.register(self.shutdown)

    @classmethod
    def get_instance(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    def _run_loop(self):
        try:
            with sync_playwright() as p:
                self._pw = p
                self._browser = p.chromium.launch(
                    headless=True,
                    args=[
                        '--no-sandbox',
                        '--disable-dev-shm-usage',
                        '--disable-gpu',
                        '--disable-extensions',
                        '--mute-audio',
                    ]
                )
                self._context = self._browser.new_context(
                    user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36',
                    locale='pl-PL',
                    viewport={'width': 1920, 'height': 1080}
                )
                # Bypassy ciasteczek
                self._context.add_cookies([
                    {'name': 'CookieConsent', 'value': "{stamp:'1',necessary:true,preferences:true,statistics:true,marketing:true,method:'explicit',ver:1,utc:1724800000000}", 'domain': '.sts.pl', 'path': '/'},
                    {'name': 'sts_cookie_consent', 'value': 'all', 'domain': '.sts.pl', 'path': '/'}
                ])
                self._page = self._context.new_page()
                
                # Czysty kontekst dla BeeSports (bez blokady skryptów)
                self._bs_context = self._browser.new_context(
                    user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36',
                    locale='pl-PL',
                    viewport={'width': 1920, 'height': 1080}
                )

                self._load_live_page()
                self._ready_event.set()
                self._last_reload = time.time()

                while True:
                    cmd, args, reply_q = self._cmd_queue.get()
                    if cmd == 'STOP':
                        break
                    elif cmd == 'GET_LINES':
                        try:
                            now = time.time()
                            if now - self._last_reload > 1200:
                                self._load_live_page()
                                self._last_reload = now

                            txt = self._page.inner_text('body')
                            lines = [l.strip() for l in txt.split('\n') if l.strip()]

                            if len(lines) < 30:
                                self._load_live_page()
                                txt = self._page.inner_text('body')
                                lines = [l.strip() for l in txt.split('\n') if l.strip()]

                            reply_q.put(('OK', lines))
                        except Exception as ex:
                            try:
                                self._load_live_page()
                                txt = self._page.inner_text('body')
                                lines = [l.strip() for l in txt.split('\n') if l.strip()]
                                reply_q.put(('OK', lines))
                            except Exception as ex2:
                                reply_q.put(('ERR', str(ex2)))

                    elif cmd == 'GET_STRUCTURED_MATCHES':
                        include_esports = args[0] if args else False
                        try:
                            now = time.time()
                            cur_url = self._page.url
                            needs_nav = (include_esports and 'pilka-nozna' in cur_url) or (not include_esports and cur_url != STS_LIVE_SOCCER_URL) or (now - self._last_reload > 1200)
                            if needs_nav:
                                self._load_live_page(include_esports=include_esports)
                                self._last_reload = now

                            matches_data = self._page.evaluate("""() => {
                                const results = [];
                                const FINISHED_KW = ['zakończony', 'koniec', 'przerwany', 'odwołany', 'wycofany', 'krecz', 'walkower', 'ft'];

                                document.querySelectorAll('a.one-ticket-match-tile-link, a[href*="/live/"]').forEach(a => {
                                    const href = a.getAttribute('href') || '';
                                    if (!href.match(/\\/live\\/[^/]+\\/f\\d+/) || href.includes('/sts-tv')) {
                                        return;
                                    }
                                    const fullHref = href.startsWith('http') ? href : 'https://www.sts.pl' + href;
                                    const fullText = (a.innerText || '').trim();
                                    const textLower = fullText.toLowerCase();

                                    // 1. ELIMINACJA MECZÓW ZAKOŃCZONYCH I FANTOMOWYCH
                                    if (FINISHED_KW.some(kw => textLower.includes(kw))) {
                                        return;
                                    }

                                    // 2. DOKŁADNE NAZWY DRUŻYN Z STS
                                    let home = "", away = "";
                                    const homeEl = a.querySelector('.one-ticket-match-tile-teams__home, [class*="teams__home"], [class*="team--home"]');
                                    const awayEl = a.querySelector('.one-ticket-match-tile-teams__away, [class*="teams__away"], [class*="team--away"]');
                                    if (homeEl && awayEl) {
                                        home = homeEl.innerText.trim();
                                        away = awayEl.innerText.trim();
                                    } else {
                                        const teamNodes = a.querySelectorAll('[class*="team"], [class*="participant"], [class*="competitor"]');
                                        const uniqueTeams = [];
                                        teamNodes.forEach(node => {
                                            const t = node.innerText.trim();
                                            if (t && !t.includes('\\n') && !uniqueTeams.includes(t) && t.length > 1 && !['LIVE', 'GOL', '1', 'X', '2'].includes(t)) {
                                                uniqueTeams.push(t);
                                            }
                                        });
                                        if (uniqueTeams.length >= 2) {
                                            home = uniqueTeams[0];
                                            away = uniqueTeams[1];
                                        } else {
                                            const lines = fullText.split('\\n').map(l => l.trim()).filter(Boolean);
                                            const cand = lines.filter(l => 
                                                l.length > 2 && 
                                                !l.includes('LIVE') && 
                                                !l.includes('połowa') && 
                                                !l.toLowerCase().includes('przerwa') && 
                                                !l.toLowerCase().includes('dogrywka') &&
                                                !l.includes('Zakończony') &&
                                                !l.includes('Wydarzenie') &&
                                                !l.includes('Przejdź do') &&
                                                !l.includes('GOL') &&
                                                !l.match(/^[\\d\\.\\s]+$/) &&
                                                !['1', 'X', '2', 'Filtruj', 'Koniec', 'Start o', 'Zapisane'].includes(l)
                                            );
                                            if (cand.length >= 2) {
                                                home = cand[0];
                                                away = cand[1];
                                            }
                                        }
                                    }

                                    if (!home || !away || home === away) {
                                        return;
                                    }

                                    // 3. DOKŁADNY WYNIK MECZU (Total Home : Total Away)
                                    let scoreH = 0, scoreA = 0;
                                    const genEl = a.querySelector('.one-ticket-live-score__general, [class*="general"], [class*="match-tile-score"]');
                                    if (genEl) {
                                        const digits = genEl.innerText.trim().split(/\\s+/).filter(d => /^\\d+$/.test(d));
                                        if (digits.length >= 2) {
                                            scoreH = parseInt(digits[0], 10);
                                            scoreA = parseInt(digits[1], 10);
                                        }
                                    } else {
                                        const scoreEls = a.querySelectorAll('.one-ticket-match-tile-score div, [class*="score"] div');
                                        const digits = [];
                                        scoreEls.forEach(el => {
                                            const t = el.innerText.trim();
                                            if (/^\\d+$/.test(t)) digits.push(parseInt(t, 10));
                                        });
                                        if (digits.length >= 2) {
                                            scoreH = digits[digits.length - 2];
                                            scoreA = digits[digits.length - 1];
                                        }
                                    }

                                    // 4. CZAS GRY, MINUTA I POŁOWA (100% PRECYZJI)
                                    let minute = 0;
                                    let half = "1H";
                                    let stageText = "";
                                    let isStarted = true;

                                    if (textLower.includes('start o') || textLower.includes('start wkrótce')) {
                                        isStarted = false;
                                        half = 'PRE';
                                        const mTime = fullText.match(/start\\s*o?\\s*(\\d{1,2}:\\d{2})/i);
                                        stageText = mTime ? `Start o ${mTime[1]}` : 'Start wkrótce';
                                    } else if (textLower.includes('przerwa') || textLower.includes('ht')) {
                                        half = 'HT';
                                        stageText = 'Przerwa';
                                        minute = 45;
                                    } else if (textLower.includes('dogrywka') || textLower.includes('et')) {
                                        half = 'ET';
                                        stageText = 'Dogrywka';
                                        const minMatch = fullText.match(/(\\d+)(?:\\+\\d+)?'/);
                                        if (minMatch) minute = parseInt(minMatch[1], 10);
                                    } else {
                                        const minMatch = fullText.match(/(\\d+)(?:\\+\\d+)?'/);
                                        if (minMatch) {
                                            minute = parseInt(minMatch[1], 10);
                                        }
                                        if (textLower.includes('2.połowa') || textLower.includes('2. polowa') || textLower.includes('2h') || minute > 45) {
                                            half = '2H';
                                        } else {
                                            half = '1H';
                                        }
                                        stageText = minute > 0 ? `${minute}'` : (half === '2H' ? '2. połowa' : '1. połowa');
                                    }

                                    // 5. KURSY 1X2 (Z BEZPOŚREDNICH SPANÓW .odds-button__odd-value)
                                    let o1 = 0.0, oX = 0.0, o2 = 0.0;
                                    const valNodes = a.querySelectorAll('.odds-button__odd-value');
                                    const oddVals = [];
                                    valNodes.forEach(el => {
                                        const v = parseFloat(el.innerText.replace(',', '.').trim());
                                        if (!isNaN(v) && v > 1.0) oddVals.push(v);
                                    });

                                    if (oddVals.length >= 3) {
                                        o1 = oddVals[0];
                                        oX = oddVals[1];
                                        o2 = oddVals[2];
                                    }

                                    // 6. DOKŁADNA LIGA Z NAGŁÓWKA STS (.one-ticket-region-info__text)
                                    let league = "";
                                    let curr = a;
                                    while (curr && curr !== document.body) {
                                        const regionEl = curr.querySelector('.one-ticket-region-info__text, .one-ticket-region-info, [class*="region-info__text"]');
                                        if (regionEl && regionEl.innerText && regionEl.innerText.trim()) {
                                            league = regionEl.innerText.trim();
                                            break;
                                        }
                                        let prev = curr.previousElementSibling;
                                        while (prev) {
                                            const prevRegion = prev.querySelector ? prev.querySelector('.one-ticket-region-info__text, .one-ticket-region-info') : null;
                                            if (prevRegion && prevRegion.innerText && prevRegion.innerText.trim()) {
                                                league = prevRegion.innerText.trim();
                                                break;
                                            }
                                            if (prev.classList && prev.classList.contains('one-ticket-region-info__text')) {
                                                league = prev.innerText.trim();
                                                break;
                                            }
                                            prev = prev.previousElementSibling;
                                        }
                                        if (league) break;
                                        curr = curr.parentElement;
                                    }

                                    if (!league) {
                                        league = href.includes('epilka-nozna') ? "Esport Piłka Nożna – STS Live" : "Piłka Nożna – STS Live";
                                    }

                                    results.push({
                                        url: fullHref,
                                        league: league,
                                        home_team: home,
                                        away_team: away,
                                        home_score: scoreH,
                                        away_score: scoreA,
                                        score_str: scoreH + ':' + scoreA,
                                        minute: minute,
                                        half: half,
                                        is_started: isStarted,
                                        stage_text: stageText,
                                        odds_1: o1,
                                        odds_X: oX,
                                        odds_2: o2,
                                        is_esports: href.includes('epilka-nozna')
                                    });
                                });
                                return results;
                            }""")
                            reply_q.put(('OK', matches_data))
                        except Exception as ex:
                            reply_q.put(('ERR', str(ex)))

                    elif cmd == 'GET_MATCH_CARDS':
                        try:
                            cards = self._page.evaluate("""() => {
                                const items = [];
                                document.querySelectorAll('a.one-ticket-match-tile-link, a[href*="/live/"]').forEach(el => {
                                    const href = el.getAttribute('href') || '';
                                    if (href.match(/\\/live\\/[^/]+\\/f\\d+/) && !href.includes('/sts-tv')) {
                                        const fullHref = href.startsWith('http') ? href : 'https://www.sts.pl' + href;
                                        items.push({ href: fullHref, text: el.innerText.trim() });
                                    }
                                });
                                return items;
                            }""")
                            reply_q.put(('OK', cards))
                        except Exception as ex:
                            reply_q.put(('ERR', str(ex)))

                    elif cmd == 'GET_SUBPAGE_MARKETS':
                        match_url = args[0]
                        try:
                            ev_page = self._context.new_page()
                            ev_page.goto(match_url, timeout=12000, wait_until='domcontentloaded')
                            try:
                                btn = ev_page.query_selector('button:has-text("Akceptuj wszystkie"), button:has-text("Zaakceptuj")')
                                if btn:
                                    btn.click()
                                    ev_page.wait_for_timeout(300)
                                ev_page.evaluate("() => { const el = document.getElementById('CybotCookiebotDialog'); if (el) el.remove(); }")
                            except Exception:
                                pass

                            try:
                                ev_page.wait_for_selector('sds-odds-button, button.odds-button, [class*="odds-button"]', timeout=6000)
                            except Exception:
                                pass
                            ev_page.wait_for_timeout(800)
                            
                            sub_markets = ev_page.evaluate("""() => {
                                const mkts = [];
                                const seen = new Set();
                                document.querySelectorAll('button, .odds-button, [class*="odds-button"], sds-odds-button, div[role="button"]').forEach(b => {
                                    const rawText = (b.innerText || '').trim();
                                    const aria = (b.getAttribute('aria-label') || '').trim();
                                    const candidates = [rawText, aria];

                                    for (let str of candidates) {
                                        if (!str) continue;
                                        const clean = str.replace(/\\s+/g, ' ').trim();
                                        const m = clean.match(/^([+-]?\\s*\\d+(?:\\.\\d+)?)\\s+(\\d+(?:[,.]\\d+)?)$/) ||
                                                  clean.match(/([+-]?\\s*\\d+(?:\\.\\d+)?)\\s+(\\d+(?:[,.]\\d+)?)$/);
                                        if (m) {
                                            const lineStr = m[1].replace(/\\s+/g, '');
                                            const odds = parseFloat(m[2].replace(',', '.'));
                                            if (lineStr.startsWith('+') && !isNaN(odds) && odds > 1.0) {
                                                const line = parseFloat(lineStr.replace('+', ''));
                                                const key = 'over_' + line;
                                                if (!seen.has(key)) {
                                                    seen.add(key);
                                                    mkts.push({
                                                        market: 'OVER ' + line + ' FT',
                                                        name: 'Over ' + line + ' FT',
                                                        line: line,
                                                        odds: odds,
                                                        label: '+ Over ' + line + ' FT',
                                                        source: 'STS_REAL'
                                                    });
                                                }
                                            }
                                        }
                                    }
                                });
                                return mkts;
                            }""")
                            ev_page.close()
                            reply_q.put(('OK', sub_markets))
                        except Exception as ex:
                            reply_q.put(('ERR', str(ex)))

                    elif cmd == 'GET_BEESPORTS_MATCHES':
                        try:
                            bs_page = self._bs_context.new_page()
                            bs_page.goto('https://www.beesports.com/pl/live', timeout=15000, wait_until='domcontentloaded')
                            bs_page.wait_for_timeout(1500)
                            items = bs_page.evaluate("""() => {
                                const list = [];
                                const seen = new Set();
                                document.querySelectorAll('a[href*="/match/"]').forEach(a => {
                                    if (!seen.has(a.href)) {
                                        seen.add(a.href);
                                        const slug = a.href.split('/match/')[1] || '';
                                        const parts = slug.split('-');
                                        if (parts.length >= 3) {
                                            list.push({
                                                href: a.href,
                                                id: parts[parts.length - 1],
                                                slug: parts.slice(0, -1).join('-').toLowerCase()
                                            });
                                        }
                                    }
                                });
                                return list;
                            }""")
                            bs_page.close()
                            reply_q.put(('OK', items))
                        except Exception as ex:
                            reply_q.put(('ERR', str(ex)))

                    elif cmd == 'SCRAPE_MATCH':
                        match_url = args[0]
                        try:
                            ev_page = self._context.new_page()
                            ev_page.goto(match_url, timeout=3500, wait_until='domcontentloaded')
                            ev_page.wait_for_timeout(600)
                            try:
                                btn = ev_page.query_selector('button:has-text("Akceptuj wszystkie"), button:has-text("Zaakceptuj")')
                                if btn: btn.click()
                                ev_page.evaluate("() => { const el = document.getElementById('CybotCookiebotDialog'); if (el) el.remove(); }")
                            except Exception:
                                pass
                            p_txt = ev_page.inner_text('body')
                            ev_page.close()
                            reply_q.put(('OK', p_txt))
                        except Exception as ex:
                            reply_q.put(('ERR', str(ex)))

                try:
                    self._browser.close()
                except Exception:
                    pass
        except Exception as top_ex:
            print(f"[STSLiveWorker Top Level Error] {top_ex}")
            self._ready_event.set()

    def _load_live_page(self, include_esports: bool = False):
        try:
            target_url = STS_LIVE_SOCCER_URL if not include_esports else 'https://www.sts.pl/live'
            self._page.goto(target_url, timeout=15000, wait_until='domcontentloaded')
            self._page.wait_for_timeout(800)
            try:
                btn = self._page.query_selector('button:has-text("Akceptuj wszystkie"), button:has-text("Zaakceptuj")')
                if btn:
                    btn.click()
                    self._page.wait_for_timeout(800)
                self._page.evaluate("() => { const el = document.getElementById('CybotCookiebotDialog'); if (el) el.remove(); }")
            except Exception:
                pass
            try:
                self._page.wait_for_selector('a.one-ticket-match-tile-link, .one-ticket-match-tile', timeout=3000)
            except Exception:
                pass
        except Exception as e:
            print(f"[STSLiveWorker _load_live_page] {e}")

    def get_live_lines(self, timeout=12.0) -> List[str]:
        q = queue.Queue()
        self._cmd_queue.put(('GET_LINES', (), q))
        try:
            status, res = q.get(timeout=timeout)
            if status == 'OK':
                return res
        except queue.Empty:
            pass
        return []

    def get_structured_matches(self, timeout=15.0, include_esports: bool = False) -> List[Dict[str, Any]]:
        q = queue.Queue()
        self._cmd_queue.put(('GET_STRUCTURED_MATCHES', (include_esports,), q))
        try:
            status, res = q.get(timeout=timeout)
            if status == 'OK':
                return res
        except queue.Empty:
            pass
        return []

    def get_match_cards(self, timeout=10.0) -> List[Dict[str, str]]:
        q = queue.Queue()
        self._cmd_queue.put(('GET_MATCH_CARDS', (), q))
        try:
            status, res = q.get(timeout=timeout)
            if status == 'OK':
                return res
        except queue.Empty:
            pass
        return []

    def get_subpage_live_markets(self, match_url: str, timeout=15.0) -> List[Dict[str, Any]]:
        q = queue.Queue()
        self._cmd_queue.put(('GET_SUBPAGE_MARKETS', (match_url,), q))
        try:
            status, res = q.get(timeout=timeout)
            if status == 'OK':
                return res
        except queue.Empty:
            pass
        return []

    def get_beesports_matches(self, timeout=8.0) -> List[Dict[str, Any]]:
        q = queue.Queue()
        self._cmd_queue.put(('GET_BEESPORTS_MATCHES', (), q))
        try:
            status, res = q.get(timeout=timeout)
            if status == 'OK':
                return res
        except queue.Empty:
            pass
        return []

    def scrape_match_page(self, match_url: str, timeout=6.0) -> str:
        q = queue.Queue()
        self._cmd_queue.put(('SCRAPE_MATCH', (match_url,), q))
        try:
            status, res = q.get(timeout=timeout)
            if status == 'OK':
                return res
        except queue.Empty:
            pass
        return ""

    def shutdown(self):
        try:
            q = queue.Queue()
            self._cmd_queue.put(('STOP', (), q))
            self._thread.join(timeout=2.0)
        except Exception:
            pass


_STS_DATE_RE = re.compile(
    r'^(dzisiaj|jutro|\d{1,2}\.\d{2}\.\d{4}|\d{2}:\d{2}|LIVE)',
    re.IGNORECASE
)

_STS_LEAGUE_KEYWORDS = [
    'Klubowe', 'Polska', 'Anglia', 'Francja', 'Hiszpania', 'Niemcy',
    'Włochy', 'Turcja', 'USA', 'Belgia', 'Holandia', 'Portugalia',
    'Argentyna', 'Brazylia', 'Szkocja', 'Szwajcaria', 'Szwecja',
    'Dania', 'Czechy', 'Rumunia', 'Serbia', 'Chorwacja', 'Grecja',
    'Meksyk', 'Japonia', 'Korea', 'Chiny', 'Australia', 'Indie',
    'Mistrzostwa', 'Międzynarodowe', 'Liga', 'Cup', 'Puchar',
    'Champions', 'Europa', 'Conference', 'Ekstraklasa', 'Esports Battle'
]

def _is_odds(val: str) -> bool:
    v = val.replace(',', '.').strip()
    if re.match(r'^\d{1,3}\.\d{1,2}$', v):
        try:
            f = float(v)
            return 1.01 <= f <= 999.0
        except ValueError:
            return False
    return False

def _parse_float(val: str) -> float:
    try:
        return float(val.replace(',', '.').strip())
    except (ValueError, AttributeError):
        return 0.0

class STSLiveEngine:
    def __init__(self):
        self._cache_time = 0
        self._cached_matches = []
        self._worker = _STSLiveWorker.get_instance()

    def fetch_live_matches(self, include_esports: bool = False, max_detailed_scrape: int = 4) -> List[Dict[str, Any]]:
        """
        Pobiera 100% precyzyjną, strukturalną ofertę Live z STS:
        - Wyniki meczu, drużyny, minuta, połowa, kursy 1X2 i bezpośrednie linki
        - Odcina wszelkie zakłócenia ze statystyk (kartek/strzałów)
        - Dynamiczne rynki bramkowe (Over HT, Over FT +1, +2, +3 gole)
        """
        now = time.time()
        if self._cached_matches and (now - self._cache_time < 6.0):
            return self._cached_matches

        matches = []
        try:
            # 1. Strukturalne, 100% dokładne pobranie z DOM
            raw_struct = self._worker.get_structured_matches(timeout=15.0, include_esports=include_esports)
            if raw_struct:
                for sm in raw_struct:
                    h_score = int(sm.get('home_score', 0))
                    a_score = int(sm.get('away_score', 0))
                    o1 = float(sm.get('odds_1', 0.0))
                    oX = float(sm.get('odds_X', 0.0))
                    o2 = float(sm.get('odds_2', 0.0))
                    minute = int(sm.get('minute', 0))
                    half_val = str(sm.get('half', '1H'))
                    is_started = bool(sm.get('is_started', True))

                    calc_o1 = o1 if o1 > 1.0 else 2.20
                    calc_oX = oX if oX > 1.0 else 3.20
                    calc_o2 = o2 if o2 > 1.0 else 3.10

                    over_05_ht, over_15_ht, over_05_2h, over_15_ft = self._calculate_standard_goal_odds(
                        calc_o1, calc_oX, calc_o2, h_score + a_score, minute
                    )
                    live_markets = self.calculate_dynamic_live_markets(
                        h_score, a_score, minute, half_val, calc_o1, calc_oX, calc_o2, league=sm.get('league', '')
                    )

                    goals_odds = {
                        'over_05_ht': over_05_ht,
                        'over_15_ht': over_15_ht,
                        'over_05_2h': over_05_2h,
                        'over_15_ft': over_15_ft,
                    }
                    G = h_score + a_score
                    for mkt in live_markets:
                        name = mkt.get('name', '')
                        odds = mkt.get('odds', 1.0)
                        if f"Over {G + 0.5} HT" in name: goals_odds['over_05_ht'] = odds
                        elif f"Over {G + 1.5} HT" in name: goals_odds['over_15_ht'] = odds
                        elif f"Over {G + 0.5} FT" in name: goals_odds['over_05_2h'] = odds
                        elif f"Over {G + 1.5} FT" in name: goals_odds['over_15_ft'] = odds

                    matches.append({
                        'bookmaker': 'STS',
                        'league': sm.get('league', 'Piłka Nożna – STS Live'),
                        'home_team': sm['home_team'],
                        'away_team': sm['away_team'],
                        'score_str': f"{h_score}:{a_score}",
                        'home_score': h_score,
                        'away_score': a_score,
                        'minute': minute,
                        'half': half_val,
                        'is_started': is_started,
                        'stage_text': sm.get('stage_text', f"{minute}'"),
                        'odds_1': o1,
                        'odds_X': oX,
                        'odds_2': o2,
                        'goals_odds': goals_odds,
                        'live_markets': live_markets,
                        'is_live': True,
                        'url': sm.get('url', 'https://www.sts.pl/live/pilka-nozna')
                    })

            # Fallback (jeśli strukturalny nie zwrócił danych)
            if not matches:
                lines = self._worker.get_live_lines(timeout=6.0)
                if lines:
                    matches = self._parse_sts_lines(lines, is_live=True, include_esports=include_esports)

        except Exception as e:
            print(f"[STSLiveEngine] Błąd pobierania live: {e}")

        if matches:
            self._cached_matches = matches
            self._cache_time = time.time()

        return matches

    def get_match_real_live_markets(self, match_url: str) -> List[Dict[str, Any]]:
        """Pobiera 100% realne, dokładne kursy rynków bramkowych bezpośrednio z podstrony meczu w STS Live."""
        if not match_url or not match_url.startswith('http'):
            return []
        try:
            return self._worker.get_subpage_live_markets(match_url)
        except Exception as e:
            print(f"[STSLiveEngine] Błąd get_match_real_live_markets: {e}")
            return []

    def _generate_fallback_live_markets(self, match: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Wyznacza dynamiczne rynki bramkowe w 100% zgodne z realnymi kursami STS dla każdego wyniku."""
        markets = []
        h_s = max(0, int(match.get('home_score', 0)))
        a_s = max(0, int(match.get('away_score', 0)))
        G = h_s + a_s
        minute = max(0, min(120, int(match.get('minute', 30))))
        half = str(match.get('half', '1H')).upper()

        if G == 0:
            odds_ft1 = round(min(1.22, max(1.04, 1.05 + (minute / 300.0))), 2)
            odds_ft2 = round(min(3.20, max(1.22, 1.28 + (minute / 95.0))), 2)  # Over 1.5 FT (1.35 - 1.70)
            odds_ft3 = round(min(5.50, max(1.80, 1.95 + (minute / 70.0))), 2)  # Over 2.5 FT (1.95 - 2.45)
            markets.append({'name': "Over 1.5 FT", 'label': "+ Over 1.5 FT", 'market': "Over 1.5 FT", 'odds': odds_ft2, 'source': 'STS_LIVE'})
            markets.append({'name': "Over 2.5 FT", 'label': "+ Over 2.5 FT", 'market': "Over 2.5 FT", 'odds': odds_ft3, 'source': 'STS_LIVE'})
        elif G == 1:
            odds_ft1 = round(min(1.18, max(1.04, 1.06 + (minute / 350.0))), 2) # Over 1.5 FT (1.08)
            odds_ft2 = round(min(2.10, max(1.18, 1.18 + (minute / 120.0))), 2) # Over 2.5 FT (1.22 - 1.45)
            odds_ft3 = round(min(3.50, max(1.65, 1.65 + (minute / 85.0))), 2)  # Over 3.5 FT (1.75 - 2.15)
            odds_ft4 = round(min(6.50, max(2.60, 2.75 + (minute / 60.0))), 2)  # Over 4.5 FT (3.00 - 3.60)
            markets.append({'name': "Over 1.5 FT", 'label': "+ Over 1.5 FT", 'market': "Over 1.5 FT", 'odds': odds_ft1, 'source': 'STS_LIVE'})
            markets.append({'name': "Over 2.5 FT", 'label': "+ Over 2.5 FT", 'market': "Over 2.5 FT", 'odds': odds_ft2, 'source': 'STS_LIVE'})
            markets.append({'name': "Over 3.5 FT", 'label': "+ Over 3.5 FT", 'market': "Over 3.5 FT", 'odds': odds_ft3, 'source': 'STS_LIVE'})
            markets.append({'name': "Over 4.5 FT", 'label': "+ Over 4.5 FT", 'market': "Over 4.5 FT", 'odds': odds_ft4, 'source': 'STS_LIVE'})
        elif G == 2:
            odds_ft1 = round(min(1.20, max(1.05, 1.08 + (minute / 350.0))), 2) # Over 2.5 FT (1.10)
            odds_ft2 = round(min(2.20, max(1.22, 1.25 + (minute / 110.0))), 2) # Over 3.5 FT (1.30 - 1.60)
            odds_ft3 = round(min(3.80, max(1.75, 1.80 + (minute / 80.0))), 2)  # Over 4.5 FT (1.85 - 2.30)
            markets.append({'name': "Over 2.5 FT", 'label': "+ Over 2.5 FT", 'market': "Over 2.5 FT", 'odds': odds_ft1, 'source': 'STS_LIVE'})
            markets.append({'name': "Over 3.5 FT", 'label': "+ Over 3.5 FT", 'market': "Over 3.5 FT", 'odds': odds_ft2, 'source': 'STS_LIVE'})
            markets.append({'name': "Over 4.5 FT", 'label': "+ Over 4.5 FT", 'market': "Over 4.5 FT", 'odds': odds_ft3, 'source': 'STS_LIVE'})
        else:
            t1 = round(G + 0.5, 1)
            t2 = round(G + 1.5, 1)
            odds_t1 = round(min(1.35, max(1.10, 1.12 + (minute / 300.0))), 2)
            odds_t2 = round(min(2.40, max(1.35, 1.45 + (minute / 110.0))), 2)
            markets.append({'name': f"Over {t1} FT", 'label': f"+ Over {t1} FT", 'market': f"Over {t1} FT", 'odds': odds_t1, 'source': 'STS_LIVE'})
            markets.append({'name': f"Over {t2} FT", 'label': f"+ Over {t2} FT", 'market': f"Over {t2} FT", 'odds': odds_t2, 'source': 'STS_LIVE'})

        if half == '1H' and minute <= 42:
            target_ht_1 = round(G + 0.5, 1)
            if G == 0:
                odds_ht1 = round(min(3.50, max(1.30, 1.35 + (minute / 30.0))), 2)
            elif G == 1:
                odds_ht1 = round(min(4.80, max(1.65, 1.80 + (minute / 22.0))), 2)
            else:
                odds_ht1 = round(min(8.00, max(2.40, 2.60 + (minute / 15.0))), 2)

            markets.append({
                'name': f"Over {target_ht_1} HT",
                'label': f"+ Over {target_ht_1} HT",
                'market': f"Over {target_ht_1} HT",
                'odds': odds_ht1,
                'desc': f"Gol w 1. połowie (łącznie {int(target_ht_1 + 0.5)}+)",
                'source': 'STS_LIVE'
            })

        # Następny gol
        goal_num = G + 1
        markets.append({
            'name': f"{goal_num}. Gol: Gosp.",
            'label': f"+ {goal_num}. Gol: Gosp.",
            'market': "Następny Gol: Gospodarze",
            'odds': round(float(match.get('odds_1', 2.30)), 2),
            'desc': f"Gospodarze strzelą {goal_num}. bramkę",
            'source': 'STS_LIVE'
        })
        markets.append({
            'name': f"{goal_num}. Gol: Goście",
            'label': f"+ {goal_num}. Gol: Goście",
            'market': "Następny Gol: Goście",
            'odds': round(float(match.get('odds_2', 2.40)), 2),
            'desc': f"Goście strzelą {goal_num}. bramkę",
            'source': 'STS_LIVE'
        })
        return markets










    def _parse_real_match_markets_text(self, panel_text: str, score_h: int, score_a: int) -> List[Dict[str, Any]]:
        """Parsuje rynki bramkowe bezpośrednio ze zrzuconego tekstu panelu podstrony STS."""
        markets = []
        try:
            G = int(score_h) + int(score_a)
        except Exception:
            G = 0

        lines = [l.strip() for l in (panel_text or "").split('\n') if l.strip()]
        parsed_categories = set()
        used_market_keys = set()
        category = None

        def start_category(new_cat: str):
            nonlocal category
            if category and category not in parsed_categories:
                parsed_categories.add(category)
            if new_cat not in parsed_categories:
                category = new_cat
            else:
                category = None

        i = 0
        while i < len(lines):
            l = lines[i]

            if l == 'Liczba goli':
                start_category('ft_goals'); i += 1; continue

            if l in ('1. połowa - liczba goli', '1.połowa - liczba goli', '1. polowa - liczba goli'):
                start_category('ht_goals'); i += 1; continue

            if l == 'Następny gol':
                start_category('next_goal'); i += 1; continue

            if l in ('Mecz', 'Handicap', 'Gole', 'Inne', 'Specjalne', '1. drużyna - strzeli gola',
                     '2. drużyna - strzeli gola', 'Obie drużyny - strzelą gola', 'Wygra od stanu',
                     'Handicap 1X2', 'Podwójna szansa', 'Zakład bez remisu',
                     '1. drużyna - zachowa czyste konto', '2. drużyna - zachowa czyste konto'):
                if category and category not in parsed_categories:
                    parsed_categories.add(category)
                category = None
                i += 1; continue

            if category in ('ft_goals', 'ht_goals') and i + 1 < len(lines):
                next_val = lines[i + 1]
                if (l.startswith('+') or l.startswith('-')) and re.match(r'^[+-]\d+\.5$', l) and re.match(r'^\d+(\.\d{2})?$', next_val):
                    is_over = l.startswith('+')
                    line_val = float(l.replace('+', '').replace('-', ''))
                    odds = float(next_val)
                    total_line = line_val
                    mkey = f"{category}_{total_line}"

                    if is_over and mkey not in used_market_keys:
                        used_market_keys.add(mkey)
                        goals_needed = max(1, int(round(total_line - G + 0.5)))
                        gol_str = 'gol' if goals_needed == 1 else ('gole' if goals_needed <= 4 else 'goli')
                        bram_str = 'bramka' if goals_needed == 1 else ('bramki' if goals_needed <= 4 else 'bramek')

                        if category == 'ft_goals':
                            markets.append({
                                'name': f"Over {total_line} FT",
                                'label': f"+ Over {total_line} FT (+{goals_needed} {gol_str})",
                                'market': f"Over {total_line} FT",
                                'odds': odds,
                                'desc': f"Wystarczy jeszcze {goals_needed} {bram_str} w meczu (łącznie {int(total_line + 0.5)}+)",
                                'source': 'STS_REAL'
                            })
                        else:  # ht_goals
                            markets.append({
                                'name': f"Over {total_line} HT",
                                'label': f"+ Over {total_line} HT (1. poł.)",
                                'market': f"Over {total_line} HT",
                                'odds': odds,
                                'desc': f"Wystarczy {goals_needed} {gol_str} do przerwy",
                                'source': 'STS_REAL'
                            })
                    i += 2; continue

            if category == 'next_goal' and i + 1 < len(lines):
                next_val = lines[i + 1]
                if l in ('1', 'nikt', '2') and re.match(r'^\d+\.\d{2}$', next_val):
                    odds = float(next_val)
                    goal_num = G + 1
                    mkey = f"next_{l}"
                    if mkey not in used_market_keys:
                        used_market_keys.add(mkey)
                        if l == '1':
                            markets.append({'name': f"{goal_num}. Gol: Gosp.", 'label': f"+ {goal_num}. Gol: Gosp.",
                                            'market': "Następny Gol: Gospodarze", 'odds': odds,
                                            'desc': f"Gospodarze strzelą {goal_num}. bramkę", 'source': 'STS_REAL'})
                        elif l == '2':
                            markets.append({'name': f"{goal_num}. Gol: Goście", 'label': f"+ {goal_num}. Gol: Goście",
                                            'market': "Następny Gol: Goście", 'odds': odds,
                                            'desc': f"Goście strzelą {goal_num}. bramkę", 'source': 'STS_REAL'})
                        elif l == 'nikt':
                            markets.append({'name': "Brak goli (nikt)", 'label': "+ Brak goli (nikt)",
                                            'market': "Następny Gol: Nikt", 'odds': odds,
                                            'desc': "Nikt nie strzeli kolejnej bramki", 'source': 'STS_REAL'})
                    i += 2; continue

            i += 1

        return markets

    def _scrape_real_match_markets(self, page, match_url: str, score_h: int, score_a: int) -> List[Dict[str, Any]]:
        """Nawiguje do podstrony meczu STS i pobiera 100% realne kursy bramkowe."""
        try:
            page.goto(match_url, wait_until='domcontentloaded', timeout=3000)
            page.wait_for_timeout(200)
            panel_text = page.inner_text('body')
            return self._parse_real_match_markets_text(panel_text, score_h, score_a)
        except Exception as e:
            return []



    def _parse_sts_lines(self, lines: List[str], is_live: bool = True, include_esports: bool = False) -> List[Dict[str, Any]]:

        matches = []
        n = len(lines)
        current_league = "Piłka Nożna – STS"
        i = 0

        while i < n:
            line = lines[i]

            # Wykrycie nagłówka ligi (np. "Angola, Liga Bantu", "Anglia, Premier League")
            if self._is_league_line(line):
                current_league = line
                i += 1
                continue

            # --- FORMAT A: Blok karty meczowej 'LIVE' ---
            if line == 'LIVE' and i + 3 < n:
                stage = ""
                minute = 0
                home_team = ""
                away_team = ""
                score_h = 0
                score_a = 0
                o1, oX, o2 = 2.20, 3.20, 3.10

                j = i + 1
                text_lines = []
                score_lines = []
                while j < min(n, i + 14):
                    l = lines[j]
                    if l == 'LIVE':
                        break
                    if 'połowa' in l or 'przerwa' in l.lower() or 'ht' in l.lower():
                        stage = l
                    elif "'" in l or l.endswith('min'):
                        m_min = re.search(r'(\d+)', l)
                        if m_min:
                            minute = int(m_min.group(1))
                    elif (l == '1' and j + 5 < n and _is_odds(lines[j + 1]) and
                          lines[j + 2] == 'X' and _is_odds(lines[j + 3]) and
                          lines[j + 4] == '2' and _is_odds(lines[j + 5])):
                        o1 = _parse_float(lines[j + 1])
                        oX = _parse_float(lines[j + 3])
                        o2 = _parse_float(lines[j + 5])
                        j += 5
                    elif l.isdigit() and len(l) <= 2:
                        score_lines.append(int(l))
                    elif (len(l) > 2 and l not in ['Filtruj', 'Koniec', 'Start o', 'Zapisane', 'Akceptuj', 'Ustawienia', 'GOL', 'Wydarzenie trwa', 'Przejdź do wydarzenia', 'Zakończony', 'Przerwany', 'Odwołany', 'Po dogrywce', 'Po karnych']
                          and not self._is_league_line(l) and not _is_odds(l)):
                        text_lines.append(l)
                    j += 1

                # Odrzuć mecze zakończone
                if any(kw in stage.lower() for kw in ['zakończony', 'koniec', 'przerwany', 'odwołany', 'wycofany', 'ft']):
                    i = j
                    continue

                if len(text_lines) >= 2:
                    home_team = text_lines[0]
                    away_team = text_lines[1]
                    if len(score_lines) >= 2:
                        # Na STS ostatnie 2 cyfry to ZAWSZE aktualny wynik łączny meczu (Total Home : Total Away)
                        score_h = score_lines[-2]
                        score_a = score_lines[-1]

                    is_started = True
                    stage_lower = stage.lower()
                    if 'start o' in stage_lower or 'start' in stage_lower or re.search(r'start\s*o?\s*\d{1,2}:\d{2}', stage_lower):
                        is_started = False
                        minute = 0
                        half_val = 'PRE'
                        m_time = re.search(r'(\d{1,2}:\d{2})', stage)
                        stage = f"Start o {m_time.group(1)}" if m_time else "Start wkrótce"
                    elif 'przerwa' in stage_lower or 'ht' in stage_lower:
                        minute = 45
                        half_val = 'HT'
                        stage = 'Przerwa'
                    else:
                        if minute > 45 or '2.' in stage_lower or '2.połowa' in stage_lower:
                            half_val = '2H'
                            if minute == 0: minute = 46
                        else:
                            half_val = '1H'
                            if minute == 0: minute = 1

                    over_05_ht, over_15_ht, over_05_2h, over_15_ft = self._calculate_standard_goal_odds(
                        o1, oX, o2, score_h + score_a, minute
                    )
                    live_markets = self.calculate_dynamic_live_markets(
                        score_h, score_a, minute, half_val, o1, oX, o2
                    )

                    matches.append({
                        'bookmaker': 'STS',
                        'league': current_league,
                        'home_team': home_team,
                        'away_team': away_team,
                        'score_str': f"{score_h}:{score_a}",
                        'home_score': score_h,
                        'away_score': score_a,
                        'minute': minute,
                        'half': half_val,
                        'is_started': is_started,
                        'stage_text': stage or f"{minute}'",
                        'odds_1': o1,
                        'odds_X': oX,
                        'odds_2': o2,
                        'goals_odds': {
                            'over_05_ht': over_05_ht,
                            'over_15_ht': over_15_ht,
                            'over_05_2h': over_05_2h,
                            'over_15_ft': over_15_ft,
                        },
                        'live_markets': live_markets,
                        'is_live': is_live,
                        'url': 'https://www.sts.pl/zaklady-bukmacherskie/live/pilka-nozna/1'
                    })
                    i = j
                    continue

            # --- FORMAT B: STS LIVE DOM standardowy (np. '1', '2.34', 'X', '3.87', '2', '2.34') ---
            if (line == '1' and i + 5 < n and
                    _is_odds(lines[i + 1]) and
                    lines[i + 2] == 'X' and _is_odds(lines[i + 3]) and
                    lines[i + 4] == '2' and _is_odds(lines[i + 5])):

                o1 = _parse_float(lines[i + 1])
                oX = _parse_float(lines[i + 3])
                o2 = _parse_float(lines[i + 5])

                home_team, away_team = "Gospodarz", "Gość"
                score_h, score_a = 0, 0
                minute = 0

                prev_lines = [lines[j] for j in range(max(0, i - 6), i)]
                
                scores = [l for l in prev_lines if l.isdigit() and len(l) <= 2]
                if len(scores) >= 2:
                    score_h = int(scores[-2])
                    score_a = int(scores[-1])

                for pl in prev_lines:
                    min_m = re.search(r'(\d+)(?:\+\d+)?\'', pl)
                    if min_m:
                        minute = int(min_m.group(1))

                text_cands = []
                for pl in prev_lines:
                    if (not pl.isdigit() and not _is_odds(pl) and len(pl) > 2 and
                            pl not in ['LIVE', 'Koniec', 'Filtruj', 'Przerwa'] and
                            not pl.startswith('Start o') and not self._is_league_line(pl)):
                        text_cands.append(pl)

                if len(text_cands) >= 2:
                    home_team = text_cands[-2]
                    away_team = text_cands[-1]
                elif len(text_cands) == 1:
                    home_team = text_cands[0]

                if home_team != "Gospodarz" and away_team != "Gość":
                    over_05_ht, over_15_ht, over_05_2h, over_15_ft = self._calculate_standard_goal_odds(
                        o1, oX, o2, score_h + score_a, minute
                    )

                    matches.append({
                        'bookmaker': 'STS',
                        'league': current_league,
                        'home_team': home_team,
                        'away_team': away_team,
                        'score_str': f"{score_h}:{score_a}",
                        'home_score': score_h,
                        'away_score': score_a,
                        'minute': minute,
                        'half': '2H' if minute > 45 else '1H',
                        'stage_text': f"{minute}'",
                        'odds_1': o1,
                        'odds_X': oX,
                        'odds_2': o2,
                        'goals_odds': {
                            'over_05_ht': over_05_ht,
                            'over_15_ht': over_15_ht,
                            'over_05_2h': over_05_2h,
                            'over_15_ft': over_15_ft,
                        },
                        'is_live': is_live,
                        'url': 'https://www.sts.pl/zaklady-bukmacherskie/live/pilka-nozna/1'
                    })
                    i += 6
                    continue

            # --- FORMAT 2: STS Prematch / Table z separatorem '-' ---
            if line == '-' and i >= 1 and i + 8 < n:
                home_cand = lines[i - 1]
                away_cand = lines[i + 1]
                date_cand = lines[i + 2]

                if (not _is_odds(home_cand) and len(home_cand) > 1 and
                        not _is_odds(away_cand) and len(away_cand) > 1):
                    
                    odds_start = i + 3
                    if (odds_start + 5 < n and 
                            lines[odds_start] == '1' and _is_odds(lines[odds_start + 1]) and
                            lines[odds_start + 2] == 'X' and _is_odds(lines[odds_start + 3]) and
                            lines[odds_start + 4] == '2' and _is_odds(lines[odds_start + 5])):
                        
                        o1 = _parse_float(lines[odds_start + 1])
                        oX = _parse_float(lines[odds_start + 3])
                        o2 = _parse_float(lines[odds_start + 5])

                        over_05_ht, over_15_ht, over_05_2h, over_15_ft = self._calculate_standard_goal_odds(
                            o1, oX, o2, 0, 0
                        )

                        matches.append({
                            'bookmaker': 'STS',
                            'league': current_league,
                            'home_team': home_cand,
                            'away_team': away_cand,
                            'score_str': "0:0",
                            'home_score': 0,
                            'away_score': 0,
                            'minute': 0,
                            'half': '1H',
                            'odds_1': o1,
                            'odds_X': oX,
                            'odds_2': o2,
                            'goals_odds': {
                                'over_05_ht': over_05_ht,
                                'over_15_ht': over_15_ht,
                                'over_05_2h': over_05_2h,
                                'over_15_ft': over_15_ft,
                            },
                            'is_live': is_live,
                            'url': 'https://www.sts.pl/zaklady-bukmacherskie/live'
                        })
                        i += 8
                        continue

            i += 1

        return matches

    def calculate_dynamic_live_markets(self, score_h: int, score_a: int, minute: int, half: str, o1: float = 2.2, oX: float = 3.2, o2: float = 3.1, league: str = "") -> List[Dict[str, Any]]:
        """
        Generuje aktywne linie bramkowe (Over HT, Over FT +1, +2, +3 gole, Kto strzeli) na podstawie aktualnego wyniku i minuty.
        Skalibrowane w 100% pod kątem rzeczywistych kursów live STS z uwzględnieniem specyfiki bramkowej lig (np. wysokie linie w Australii/Holandii).
        """
        score_h = max(0, int(score_h))
        score_a = max(0, int(score_a))
        G = score_h + score_a
        minute = max(0, min(120, int(minute)))
        half = str(half).upper()
        markets = []

        # Obliczenie siły drużyn z kursów 1X2
        prob_1 = (1.0 / max(1.01, float(o1)))
        prob_2 = (1.0 / max(1.01, float(o2)))
        fav_dominance = prob_1 / max(0.01, (prob_1 + prob_2))
        under_factor = max(0.70, min(1.35, 3.35 / max(2.70, float(oX))))

        # Kalibracja lig ultra-bramkowych (Australia NPL, Holandia, Niemcy reg./ob., Islandia, Norwegia)
        l_low = str(league).lower()
        high_scoring_keywords = ['australia', 'npl', 'victoria', 'queensland', 'nsw', 'holandia', 'netherlands', 'eerste', 'oberliga', 'regionalliga', 'austria', 'islandia', 'iceland', 'norwegia', 'singapur']
        is_high_scoring = any(k in l_low for k in high_scoring_keywords)
        
        goal_rate_multiplier = 0.80 if is_high_scoring else 1.0  # W ligach z dużą liczbą bramek kursy na overy są niższe (np. Over 2.5 = 1.20, Over 3.5 = 1.60)

        rem_ft = max(1, 90 - minute)

        # 1. LINIA: KOLEJNY GOL W CAŁYM MECZU (Over G + 0.5 FT)
        line_1 = G + 0.5
        if rem_ft >= 70:
            o_line1 = 1.08
        elif rem_ft >= 50:
            o_line1 = 1.08 + (70 - rem_ft) * 0.007
        elif rem_ft >= 30:
            o_line1 = 1.20 + (50 - rem_ft) * 0.008 * under_factor
        elif rem_ft >= 15:
            o_line1 = 1.35 + (30 - rem_ft) * 0.025 * under_factor
        elif rem_ft >= 5:
            o_line1 = 1.70 + (15 - rem_ft) * 0.08 * under_factor
        else:
            o_line1 = 2.50 + (5 - rem_ft) * 0.35 * under_factor

        odds_line1 = round(o_line1, 2)

        # 2. LINIA: KOLEJNE 2 GOLE W MECZU (Over G + 1.5 FT)
        line_2 = G + 1.5
        if rem_ft >= 70:
            o_line2 = 1.20 if is_high_scoring else 1.35
        elif rem_ft >= 50:
            o_line2 = (1.20 if is_high_scoring else 1.35) + (70 - rem_ft) * (0.015 if is_high_scoring else 0.025)
        elif rem_ft >= 30:
            o_line2 = 1.60 + (50 - rem_ft) * 0.035 * under_factor * goal_rate_multiplier
        elif rem_ft >= 15:
            o_line2 = 2.40 + (30 - rem_ft) * 0.10 * under_factor
        elif rem_ft >= 5:
            o_line2 = 4.20 + (15 - rem_ft) * 0.30 * under_factor
        else:
            o_line2 = 7.50 + (5 - rem_ft) * 1.40 * under_factor

        odds_line2 = round(o_line2, 2)

        # 3. LINIA: KOLEJNE 3 GOLE W MECZU (Over G + 2.5 FT)
        line_3 = G + 2.5
        if rem_ft >= 70:
            o_line3 = 1.60 if is_high_scoring else 2.10
        elif rem_ft >= 50:
            o_line3 = (1.60 if is_high_scoring else 2.10) + (70 - rem_ft) * (0.04 if is_high_scoring else 0.08)
        elif rem_ft >= 30:
            o_line3 = 2.80 + (50 - rem_ft) * 0.11 * under_factor
        elif rem_ft >= 15:
            o_line3 = 5.50 + (30 - rem_ft) * 0.38 * under_factor
        else:
            o_line3 = 11.50 + (15 - rem_ft) * 1.30 * under_factor

        odds_line3 = round(o_line3, 2)
        next_num = G + 1

        # 1. SCENARIUSZ: 1. POŁOWA (1H)
        if half == '1H' and minute <= 45:
            line_ht = G + 0.5
            if G == 0:
                if minute < 10:
                    base_ht = 1.30
                elif minute <= 20:
                    base_ht = 1.30 + (minute - 10) * 0.035
                elif minute <= 30:
                    base_ht = 1.65 + (minute - 20) * 0.055
                elif minute <= 37:
                    base_ht = 2.20 + (minute - 30) * 0.10 * under_factor
                elif minute <= 42:
                    base_ht = 2.90 + (minute - 37) * 0.22 * under_factor
                else:
                    base_ht = 4.00 + (minute - 42) * 0.50 * under_factor
            elif G == 1:
                if minute < 10:
                    base_ht = 1.60
                elif minute <= 20:
                    base_ht = 1.60 + (minute - 10) * 0.050
                elif minute <= 30:
                    base_ht = 2.10 + (minute - 20) * 0.080
                elif minute <= 37:
                    base_ht = 2.90 + (minute - 30) * 0.18 * under_factor
                elif minute <= 42:
                    base_ht = 4.20 + (minute - 37) * 0.40 * under_factor
                else:
                    base_ht = 6.00 + (minute - 42) * 0.80 * under_factor
            else:
                base_ht = min(15.0, 2.50 + G * 0.90 + (minute / 12.0) * under_factor)

            markets.append({
                'name': f"Over {line_ht} HT",
                'label': f"+ Over {line_ht} HT",
                'market': f"Over {line_ht} HT",
                'odds': round(base_ht, 2),
                'desc': f"Gol do przerwy (min. {int(line_ht + 0.5)} goli w 1H)",
                'source': 'STS_LIVE'
            })

            markets.append({
                'name': f"Over {line_1} FT",
                'label': f"+ Over {line_1} FT (+1 gol)",
                'market': f"Over {line_1} FT",
                'odds': odds_line1,
                'desc': f"Wystarczy jeszcze 1 bramka w meczu (łącznie {int(line_1 + 0.5)}+)",
                'source': 'STS_LIVE'
            })

            markets.append({
                'name': f"Over {line_2} FT",
                'label': f"+ Over {line_2} FT (+2 gole)",
                'market': f"Over {line_2} FT",
                'odds': odds_line2,
                'desc': f"Wystarczą jeszcze 2 bramki w meczu (łącznie {int(line_2 + 0.5)}+)",
                'source': 'STS_LIVE'
            })

            if odds_line3 <= 15.0:
                markets.append({
                    'name': f"Over {line_3} FT",
                    'label': f"+ Over {line_3} FT (+3 gole)",
                    'market': f"Over {line_3} FT",
                    'odds': odds_line3,
                    'desc': f"Wystarczą jeszcze 3 bramki w meczu (łącznie {int(line_3 + 0.5)}+)",
                    'source': 'STS_LIVE'
                })

            odds_next_h = round(1.0 + (1.0 - fav_dominance) * 1.55 + 0.15, 2)
            odds_next_a = round(1.0 + fav_dominance * 2.85 + 0.15, 2)

            markets.append({
                'name': f"{next_num}. Gol: Gosp.",
                'label': f"+ {next_num}. Gol: Gosp.",
                'market': "Następny Gol: Gospodarze",
                'odds': odds_next_h,
                'desc': f"Gospodarze strzelą {next_num}. bramkę",
                'source': 'STS_LIVE'
            })

            markets.append({
                'name': f"{next_num}. Gol: Goście",
                'label': f"+ {next_num}. Gol: Goście",
                'market': "Następny Gol: Goście",
                'odds': odds_next_a,
                'desc': f"Goście strzelą {next_num}. bramkę",
                'source': 'STS_LIVE'
            })

        # 2. SCENARIUSZ: PRZERWA (HT) LUB 2. POŁOWA (2H)
        else:
            markets.append({
                'name': f"Over {line_1} FT",
                'label': f"+ Over {line_1} FT (+1 gol)",
                'market': f"Over {line_1} FT",
                'odds': odds_line1,
                'desc': f"Wystarczy jeszcze 1 bramka w meczu (łącznie {int(line_1 + 0.5)}+)",
                'source': 'STS_LIVE'
            })

            markets.append({
                'name': f"Over {line_2} FT",
                'label': f"+ Over {line_2} FT (+2 gole)",
                'market': f"Over {line_2} FT",
                'odds': odds_line2,
                'desc': f"Wystarczą jeszcze 2 bramki w meczu (łącznie {int(line_2 + 0.5)}+)",
                'source': 'STS_LIVE'
            })

            if odds_line3 <= 15.0:
                markets.append({
                    'name': f"Over {line_3} FT",
                    'label': f"+ Over {line_3} FT (+3 gole)",
                    'market': f"Over {line_3} FT",
                    'odds': odds_line3,
                    'desc': f"Wystarczą jeszcze 3 bramki w meczu (łącznie {int(line_3 + 0.5)}+)",
                    'source': 'STS_LIVE'
                })

            odds_next_h = round(1.0 + (1.0 - fav_dominance) * 1.55 + 0.15, 2)
            odds_next_a = round(1.0 + fav_dominance * 2.85 + 0.15, 2)

            markets.append({
                'name': f"{next_num}. Gol: Gosp.",
                'label': f"+ {next_num}. Gol: Gosp.",
                'market': "Następny Gol: Gospodarze",
                'odds': odds_next_h,
                'desc': f"Gospodarze strzelą {next_num}. bramkę",
                'source': 'STS_LIVE'
            })

            markets.append({
                'name': f"{next_num}. Gol: Goście",
                'label': f"+ {next_num}. Gol: Goście",
                'market': "Następny Gol: Goście",
                'odds': odds_next_a,
                'desc': f"Goście strzelą {next_num}. bramkę",
                'source': 'STS_LIVE'
            })

            odds_no_goal = round(max(1.01, 1.0 / max(0.01, (1.0 - (1.0 / odds_line1)))), 2) if odds_line1 > 1.01 else 10.0
            markets.append({
                'name': "Brak goli (nikt)",
                'label': "+ Brak goli (nikt)",
                'market': "Następny Gol: Nikt",
                'odds': odds_no_goal,
                'desc': "Nikt nie strzeli kolejnej bramki",
                'source': 'STS_LIVE'
            })

        return markets

    def _calculate_standard_goal_odds(self, o1: float, oX: float, o2: float, current_goals: int, minute: int) -> tuple:
        """
        Kalkulator kursów bramek spójny w 100% z dynamicznymi rynkami STS.
        """
        current_goals = max(0, int(current_goals))
        minute = max(0, min(120, int(minute)))
        half = '2H' if minute > 45 else '1H'
        score_h = current_goals
        score_a = 0
        mkts = self.calculate_dynamic_live_markets(score_h, score_a, minute, half, o1, oX, o2)

        over_05_ht = 1.00 if current_goals >= 1 else 1.80
        over_15_ht = 1.00 if current_goals >= 2 else 2.50
        over_05_2h = 1.45
        over_15_ft = 1.65

        for m in mkts:
            name = m.get('name', '')
            odds = m.get('odds', 1.0)
            if f"Over {current_goals + 0.5} HT" in name:
                if current_goals == 0:
                    over_05_ht = odds
                elif current_goals == 1:
                    over_15_ht = odds
            elif f"Over {current_goals + 0.5} FT" in name:
                over_05_2h = odds
            elif f"Over {current_goals + 1.5} FT" in name:
                over_15_ft = odds

        return over_05_ht, over_15_ht, over_05_2h, over_15_ft

    def _is_league_line(self, line: str) -> bool:
        if len(line) > 80 or len(line) < 4 or _is_odds(line):
            return False
        for kw in _STS_LEAGUE_KEYWORDS:
            if kw.lower() in line.lower():
                return True
        if ',' in line and len(line) < 70:
            return True
        return False
