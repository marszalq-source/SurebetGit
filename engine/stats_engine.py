import os
import json
import time
import datetime
from collections import defaultdict

SIGNALS_HISTORY_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "telegram_signals_history.json")

class StatsEngine:
    def __init__(self, history_file=None):
        self.history_file = history_file or SIGNALS_HISTORY_FILE
        self._ensure_file_exists()

    def _ensure_file_exists(self):
        if not os.path.exists(self.history_file):
            with open(self.history_file, 'w', encoding='utf-8') as f:
                json.dump([], f, indent=2, ensure_ascii=False)

    def load_history(self) -> list:
        if os.path.exists(self.history_file):
            try:
                with open(self.history_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                print(f"[StatsEngine] Błąd odczytu historii: {e}")
        return []

    def save_history(self, history: list):
        try:
            with open(self.history_file, 'w', encoding='utf-8') as f:
                json.dump(history, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"[StatsEngine] Błąd zapisu historii: {e}")

    def reset_history(self) -> bool:
        try:
            with open(self.history_file, 'w', encoding='utf-8') as f:
                json.dump([], f, indent=2, ensure_ascii=False)
            return True
        except Exception as e:
            print(f"[StatsEngine] Błąd resetu historii: {e}")
            return False

    def _normalize_name(self, name: str) -> str:
        if not name:
            return ""
        import re
        n = str(name).lower()
        n = re.sub(r'\[[^\]]*\]|\([^)]*\)', ' ', n)
        n = re.sub(r'[^a-z0-9\s]', ' ', n)
        words = [w.strip() for w in n.split() if w.strip()]
        return " ".join(words)

    def record_signal(self, match_data: dict, signal_data: dict, unit_tag: str = "1J") -> dict:
        history = self.load_history()
        home = match_data.get('home_team', '')
        away = match_data.get('away_team', '')
        score = match_data.get('score_str', '0:0')
        minute = match_data.get('minute', 0)
        league = match_data.get('league', '')
        badge = signal_data.get('badge', 'OVER')
        odds = float(signal_data.get('odds', 1.80))
        
        # Unit to value
        units = 1
        if '3' in unit_tag:
            units = 3
        elif '2' in unit_tag:
            units = 2
            
        unit_pln = 2.0  # 1J = 2.00 zł bazowo
        stake_pln = units * unit_pln

        # Dedup: Sprawdź czy dla tego meczu istnieje już aktywny sygnał PENDING w ostatnich 4 godzinach
        h_norm = self._normalize_name(home)
        a_norm = self._normalize_name(away)
        now_ts = time.time()

        for item in reversed(history):
            if item.get('status') == 'PENDING' and (now_ts - item.get('timestamp', now_ts) < 14400):
                ih_norm = self._normalize_name(item.get('home_team', ''))
                ia_norm = self._normalize_name(item.get('away_team', ''))
                if (h_norm and ih_norm and (h_norm == ih_norm or h_norm in ih_norm or ih_norm in h_norm)) and \
                   (a_norm and ia_norm and (a_norm == ia_norm or a_norm in ia_norm or ia_norm in a_norm)):
                    # Zaktualizuj istniejący sygnał in-place zamiast tworzyć duplikat
                    item['minute'] = minute
                    item['score_final'] = score
                    item['odds'] = odds
                    item['unit_tag'] = unit_tag
                    item['units'] = units
                    item['stake_pln'] = stake_pln
                    self.save_history(history)
                    return item

        sig_id = f"sig_{int(time.time())}_{home[:3].lower()}{away[:3].lower()}"
        now_str = time.strftime('%Y-%m-%d %H:%M:%S')
        
        entry = {
            "id": sig_id,
            "created_at": now_str,
            "timestamp": time.time(),
            "date": time.strftime('%Y-%m-%d'),
            "home_team": home,
            "away_team": away,
            "match_title": f"{home} vs {away}",
            "league": league,
            "minute": minute,
            "score_initial": score,
            "score_final": score,
            "market": badge,
            "odds": odds,
            "unit_tag": unit_tag,
            "units": units,
            "stake_pln": stake_pln,
            "status": "PENDING",
            "profit_units": 0.0,
            "profit_pln": 0.0,
            "resolved_at": ""
        }
        
        history.append(entry)
        self.save_history(history)
        return entry

    def settle_signal(self, home: str, away: str, status: str, final_score: str) -> bool:
        history = self.load_history()
        h_norm = self._normalize_name(home)
        a_norm = self._normalize_name(away)
        
        updated = False
        now_str = time.strftime('%Y-%m-%d %H:%M:%S')
        now_ts = time.time()
        
        for item in reversed(history):
            ih_norm = self._normalize_name(item.get('home_team', ''))
            ia_norm = self._normalize_name(item.get('away_team', ''))
            
            is_match = False
            if (h_norm and ih_norm and (h_norm == ih_norm or h_norm in ih_norm or ih_norm in h_norm)) and \
               (a_norm and ia_norm and (a_norm == ia_norm or a_norm in ia_norm or ia_norm in a_norm)):
                is_match = True
            
            if is_match:
                cur_st = item.get('status', 'PENDING')
                # OCHRONA: Jeśli typ został już trafiony (WON) lub unieważniony (VOID), NIGDY nie nadpisuj na LOST!
                if cur_st == 'WON' and status == 'LOST':
                    continue
                if cur_st in ('WON', 'VOID') and status == cur_st:
                    item['score_final'] = final_score
                    updated = True
                    continue
                
                if cur_st == 'PENDING' or (now_ts - item.get('timestamp', now_ts) < 18000):
                    item['status'] = status
                    item['score_final'] = final_score
                    item['resolved_at'] = now_str
                    
                    units = item.get('units', 1)
                    odds = item.get('odds', 1.80)
                    stake_pln = item.get('stake_pln', units * 2.0)
                    
                    if status == 'WON':
                        p_units = round(units * (odds - 1.0), 2)
                        p_pln = round(stake_pln * (odds - 1.0), 2)
                    elif status == 'LOST':
                        p_units = round(-1.0 * units, 2)
                        p_pln = round(-1.0 * stake_pln, 2)
                    else:  # VOID
                        p_units = 0.0
                        p_pln = 0.0
                        
                    item['profit_units'] = p_units
                    item['profit_pln'] = p_pln
                    updated = True
                    # Rozlicz wszystkie pasujące pendingi dla tego meczu
                    
        if updated:
            self.save_history(history)
        return updated

    def get_stats(self, period: str = 'all') -> dict:
        """
        Oblicza statystyki dla wybranego okresu:
        '1d' / 'today' -> Dzisiaj
        '7d' / 'week' -> Ostatnie 7 dni
        '30d' / 'month' -> Ostatnie 30 dni
        '90d' / '3m' -> Ostatnie 90 dni (3 miesiące)
        '365d' / 'year' -> Ostatni rok
        'all' -> Wszystkie sygnały
        """
        history = self.load_history()
        now = time.time()
        
        # Filtrowanie czasowe
        cutoff_sec = None
        period_label = "Wszystkie sygnały (Cały czas)"
        
        if period in ('1d', 'today', 'dzis', 'dzisiaj'):
            # Od początku dzisiejszego dnia
            today_start = datetime.datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
            cutoff_sec = today_start
            period_label = f"Dzisiaj ({time.strftime('%d.%m.%Y')})"
        elif period in ('7d', 'week', 'tydzien'):
            cutoff_sec = now - (7 * 86400)
            period_label = "Ostatnie 7 dni"
        elif period in ('30d', 'month', 'miesiac'):
            cutoff_sec = now - (30 * 86400)
            period_label = "Ostatnie 30 dni (Miesiąc)"
        elif period in ('90d', '3m', 'kwartal'):
            cutoff_sec = now - (90 * 86400)
            period_label = "Ostatnie 90 dni (3 miesiące)"
        elif period in ('365d', 'year', 'rok'):
            cutoff_sec = now - (365 * 86400)
            period_label = "Ostatni rok (12 miesięcy)"

        filtered = []
        for item in history:
            ts = item.get('timestamp', 0)
            if cutoff_sec is None or ts >= cutoff_sec:
                filtered.append(item)

        total_count = len(filtered)
        won_count = sum(1 for i in filtered if i.get('status') == 'WON')
        lost_count = sum(1 for i in filtered if i.get('status') == 'LOST')
        void_count = sum(1 for i in filtered if i.get('status') == 'VOID')
        pending_count = sum(1 for i in filtered if i.get('status') == 'PENDING')
        
        resolved_count = won_count + lost_count
        win_rate = round((won_count / resolved_count) * 100, 1) if resolved_count > 0 else 0.0

        total_staked_units = sum(i.get('units', 1) for i in filtered if i.get('status') in ('WON', 'LOST', 'VOID'))
        total_staked_pln = sum(i.get('stake_pln', 2.0) for i in filtered if i.get('status') in ('WON', 'LOST', 'VOID'))
        
        profit_units = round(sum(i.get('profit_units', 0.0) for i in filtered), 2)
        profit_pln = round(sum(i.get('profit_pln', 0.0) for i in filtered), 2)
        
        yield_pct = round((profit_units / total_staked_units) * 100, 1) if total_staked_units > 0 else 0.0
        
        avg_odds = 0.0
        odds_list = [i.get('odds', 0.0) for i in filtered if i.get('odds', 0) > 0]
        if odds_list:
            avg_odds = round(sum(odds_list) / len(odds_list), 2)

        # Skuteczność wg rynków
        markets_map = defaultdict(lambda: {'won': 0, 'lost': 0, 'void': 0, 'total': 0})
        for i in filtered:
            m = i.get('market', 'INNY')
            st = i.get('status', 'PENDING')
            if st in ('WON', 'LOST', 'VOID'):
                markets_map[m]['total'] += 1
                if st == 'WON':
                    markets_map[m]['won'] += 1
                elif st == 'LOST':
                    markets_map[m]['lost'] += 1
                elif st == 'VOID':
                    markets_map[m]['void'] += 1

        markets_summary = []
        for m_name, m_st in sorted(markets_map.items(), key=lambda x: x[1]['total'], reverse=True):
            res_m = m_st['won'] + m_st['lost']
            wr_m = round((m_st['won'] / res_m) * 100, 1) if res_m > 0 else 0.0
            markets_summary.append({
                'market': m_name,
                'total': m_st['total'],
                'won': m_st['won'],
                'lost': m_st['lost'],
                'win_rate': wr_m
            })

        return {
            'period_label': period_label,
            'period_code': period,
            'total_signals': total_count,
            'resolved_signals': resolved_count,
            'won': won_count,
            'lost': lost_count,
            'void': void_count,
            'pending': pending_count,
            'win_rate': win_rate,
            'avg_odds': avg_odds,
            'profit_units': profit_units,
            'profit_pln': profit_pln,
            'total_staked_units': total_staked_units,
            'total_staked_pln': total_staked_pln,
            'yield_pct': yield_pct,
            'markets': markets_summary
        }

    def format_telegram_message(self, stats: dict) -> str:
        pl = stats['period_label']
        tot = stats['resolved_signals']
        w = stats['won']
        l = stats['lost']
        v = stats['void']
        wr = stats['win_rate']
        pu = stats['profit_units']
        pp = stats['profit_pln']
        yd = stats['yield_pct']
        ao = stats['avg_odds']
        
        pu_sign = f"+{pu:.2f}" if pu >= 0 else f"{pu:.2f}"
        pp_sign = f"+{pp:.2f}" if pp >= 0 else f"{pp:.2f}"
        yd_sign = f"+{yd:.1f}" if yd >= 0 else f"{yd:.1f}"
        profit_emoji = "🟢 📈" if pu >= 0 else "🔴 📉"

        msg = (
            f"📊 <b>RAPORT STATYSTYCZNY SYGNAŁÓW</b> 📊\n"
            f"📅 <b>Okres:</b> <b>{pl}</b>\n\n"
            f"🎯 <b>Wszystkie rozstrzygnięte:</b> <b>{tot}</b>\n"
            f"✅ <b>Trafione:</b> <b>{w}</b> ({wr}%)\n"
            f"❌ <b>Nietrafione:</b> <b>{l}</b>\n"
        )
        if v > 0:
            msg += f"🟡 <b>Zwroty:</b> <b>{v}</b>\n"
        if stats.get('pending', 0) > 0:
            msg += f"⏳ <b>W trakcie gry:</b> <b>{stats['pending']}</b>\n"
            
        msg += (
            f"💰 <b>Bilans jednostek:</b> <b>{pu_sign} J</b> {profit_emoji}\n"
            f"🚀 <b>Yield:</b> <b>{yd_sign}%</b>\n"
        )
        if ao > 0:
            msg += f"📈 <b>Średni kurs:</b> <b>{ao:.2f}</b>\n"

        if stats.get('markets'):
            msg += "\n🏆 <b>Skuteczność wg rynków:</b>\n"
            for m in stats['markets'][:5]:
                star = " 💎" if m['win_rate'] >= 90 else ""
                msg += f"• <code>{m['market']}</code>: <b>{m['win_rate']}%</b> ({m['won']}/{m['total']}){star}\n"

        return msg.strip()

    def get_inline_keyboard(self, current_period: str = '30d') -> dict:
        buttons = [
            [
                {"text": ("▶ " if current_period in ('1d', 'today') else "") + "📊 Dzisiaj", "callback_data": "stats_1d"},
                {"text": ("▶ " if current_period in ('7d', 'week') else "") + "📅 7 Dni", "callback_data": "stats_7d"}
            ],
            [
                {"text": ("▶ " if current_period in ('30d', 'month') else "") + "📆 30 Dni", "callback_data": "stats_30d"},
                {"text": ("▶ " if current_period in ('90d', '3m') else "") + "📈 3 Miesiące", "callback_data": "stats_90d"}
            ],
            [
                {"text": ("▶ " if current_period in ('365d', 'year') else "") + "📅 1 Rok", "callback_data": "stats_365d"},
                {"text": ("▶ " if current_period == 'all' else "") + "🏆 Cały Czas", "callback_data": "stats_all"}
            ]
        ]
        return {"inline_keyboard": buttons}
