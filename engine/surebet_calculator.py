"""
Engine obliczeń Surebet dla SurebetGit
Obsługuje automatyczny podatek per bukmacher:
- BETCLIC: 0% podatku (Gra bez podatku)
- STS & LVBET: 12% podatku w Polsce
"""

def get_bookmaker_tax_multiplier(bookmaker_name: str) -> float:
    """Zwraca mnożnik po podatku: 1.0 dla Betclic (0% tax), 0.88 dla STS i LVBET (12% tax)."""
    bname = str(bookmaker_name).upper().strip()
    if bname == 'BETCLIC':
        return 1.0  # Gra bez podatku
    return 0.88     # 12% podatku w PL (STS, LVBET)


def calculate_surebet(odds_dict, apply_tax=True, total_stake=10.0):
    """
    odds_dict przykładowo:
    {
        '1': {'odds': 1.82, 'bookmaker': 'LVBET'},
        'X': {'odds': 4.35, 'bookmaker': 'BETCLIC'},
        '2': {'odds': 6.30, 'bookmaker': 'BETCLIC'}
    }
    """
    outcomes = list(odds_dict.keys())
    if len(outcomes) < 2:
        return None

    effective_odds = {}
    tax_multipliers = {}
    inv_sum = 0.0

    for outcome, data in odds_dict.items():
        raw_odds = float(data['odds'])
        bookie = data.get('bookmaker', 'STS')
        
        # Wyliczenie podatku w zależności od bukmachera
        tax_mult = get_bookmaker_tax_multiplier(bookie)
        tax_multipliers[outcome] = tax_mult

        eff_odds = raw_odds * tax_mult
        if eff_odds <= 0:
            return None
        effective_odds[outcome] = eff_odds
        inv_sum += 1.0 / eff_odds

    if inv_sum <= 0:
        return None

    profit_margin = ((1.0 / inv_sum) - 1.0) * 100.0
    profit_margin_rounded = round(profit_margin, 2)

    stakes = {}
    for outcome, eff_odds in effective_odds.items():
        stake = (total_stake / (inv_sum * eff_odds))
        stakes[outcome] = round(stake, 2)
    
    sample_outcome = outcomes[0]
    raw_odds_sample = float(odds_dict[sample_outcome]['odds'])
    sample_tax_mult = tax_multipliers[sample_outcome]
    
    guaranteed_return = round(stakes[sample_outcome] * raw_odds_sample * sample_tax_mult, 2)
    net_profit = round(guaranteed_return - total_stake, 2)

    return {
        'is_surebet': profit_margin > 0,
        'profit_percent': profit_margin_rounded,
        'inv_sum': inv_sum,
        'apply_tax': True,
        'total_stake': total_stake,
        'guaranteed_return': guaranteed_return,
        'net_profit': net_profit,
        'stakes': stakes,
        'effective_odds': effective_odds
    }


def find_best_surebet_combination(outcomes_by_bookmaker, is_3way_sport=True, apply_tax=True, total_stake=10.0):
    """
    Wybiera najbardziej zyskowną KOMPLETNĄ kombinację z automatycznym podatkiem per bukmacher.
    """
    best_overall_res = None
    best_profit = -999.0

    if is_3way_sport:
        market_combinations = [
            ['1', 'X', '2'],
            ['1X', '2'],
            ['X2', '1'],
            ['12', 'X']
        ]
    else:
        market_combinations = [
            ['1', '2'],
            ['Over 2.5', 'Under 2.5']
        ]

    for comb_keys in market_combinations:
        sub_odds_dict = {}
        valid = True
        for key in comb_keys:
            if key not in outcomes_by_bookmaker or not outcomes_by_bookmaker[key]:
                valid = False
                break
            best_entry = max(outcomes_by_bookmaker[key], key=lambda x: float(x['odds']))
            sub_odds_dict[key] = best_entry

        if not valid:
            continue

        res = calculate_surebet(sub_odds_dict, apply_tax=apply_tax, total_stake=total_stake)
        if res:
            res['best_combination'] = sub_odds_dict
            res['market_type'] = " / ".join(comb_keys)
            if res['profit_percent'] > best_profit:
                best_profit = res['profit_percent']
                best_overall_res = res

    return best_overall_res
