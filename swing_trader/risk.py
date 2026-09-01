"""Agent 5: Risk Management — position sizing, portfolio metrics, stop logic."""

import math


def atr_position_size(
    capital: float,
    risk_pct: float,
    entry: float,
    atr: float,
    multiplier: float = 2.0,
) -> dict:
    """Calculate position size using ATR-based stop distance.

    Parameters
    ----------
    capital : float
        Total account/trading capital.
    risk_pct : float
        Fraction of capital to risk on this trade (e.g. 0.01 = 1%).
    entry : float
        Planned entry price.
    atr : float
        Current ATR (average true range) value.
    multiplier : float, optional
        Multiplier applied to ATR for stop distance (default 2.0).

    Returns
    -------
    dict
        shares         : int   — number of shares/units
        stop_distance  : float — distance from entry to stop (atr * multiplier)
        dollar_risk    : float — total dollar amount at risk
        position_value : float — shares * entry
    """
    dollar_risk = capital * risk_pct
    stop_distance = atr * multiplier
    if stop_distance <= 0:
        # Degenerate case: ATR is zero or negative — no stop distance to size against
        return {
            "shares": 0,
            "stop_distance": 0.0,
            "dollar_risk": dollar_risk,
            "position_value": 0.0,
        }
    shares = max(1, int(dollar_risk / stop_distance))
    return {
        "shares": shares,
        "stop_distance": stop_distance,
        "dollar_risk": dollar_risk,
        "position_value": shares * entry,
    }


def kelly_fraction(win_rate: float, avg_win: float, avg_loss: float) -> float:
    """Compute the quarter-Kelly fraction for a trading strategy.

    Standard Kelly formula (f = win_rate - (1-win_rate) / (avg_win/abs(avg_loss)))
    is clamped to [0, 0.25] for safety (quarter-Kelly).

    Parameters
    ----------
    win_rate : float
        Proportion of winning trades (0 to 1).
    avg_win : float
        Average dollar (or point) gain on winning trades. Must be > 0.
    avg_loss : float
        Average dollar (or point) loss on losing trades. Must be > 0 (absolute
        value is used internally; if 0, returns 0).

    Returns
    -------
    float
        Clamped fraction of capital to risk (0.0 – 0.25).
    """
    if avg_loss == 0.0 or win_rate <= 0.0:
        return 0.0
    abs_avg_loss = abs(avg_loss)
    if abs_avg_loss == 0.0:
        return 0.0
    # Kelly formula
    b = avg_win / abs_avg_loss  # win/loss ratio
    if b == 0.0:
        return 0.0
    f = win_rate - (1.0 - win_rate) / b
    # Clamp to quarter-Kelly
    return max(0.0, min(f, 0.25))


def portfolio_metrics(positions: list[dict], cash: float) -> dict:
    """Compute aggregate portfolio metrics from a list of Position dicts.

    Each position dict may have:
        ticker (str), entry (float), shares (int), capital (float),
        stop (float), and one of current_price (float) or price (float).

    Parameters
    ----------
    positions : list[dict]
        List of open positions.
    cash : float
        Unallocated cash balance.

    Returns
    -------
    dict
        total_capital    : float — cash + cost basis of all positions
        cash             : float — as provided
        positions_value  : float — market value of all positions
        total_pl         : float — same as unrealized_pl (no history)
        unrealized_pl    : float — sum of (price - entry) * shares
        risk_pct         : float — aggregate dollar-at-risk / total_capital
        concentration_pct: float — largest single position / total_capital
    """
    total_capital = cash
    positions_value = 0.0
    unrealized_pl = 0.0
    total_dollar_risk = 0.0
    max_position_value = 0.0

    for pos in positions:
        entry = pos.get("entry", 0.0)
        shares = pos.get("shares", 0)
        cap = pos.get("capital", 0.0)
        price = pos.get("current_price") or pos.get("price")
        stop = pos.get("stop")

        # Cost basis contribution to total capital
        cost_basis = shares * entry
        total_capital += min(cap, cost_basis) if cap else cost_basis

        # Market value
        pos_value = shares * (price if price is not None else entry)
        positions_value += pos_value
        max_position_value = max(max_position_value, pos_value)

        # Unrealised P&L (skip if no current price)
        if price is not None:
            unrealized_pl += shares * (price - entry)

        # Dollar at risk (skip if no stop price)
        if stop is not None and entry > 0:
            stop_distance = entry - stop  # positive distance below entry
            if stop_distance > 0:
                total_dollar_risk += shares * stop_distance

    risk_pct = (total_dollar_risk / total_capital) if total_capital > 0 else 0.0
    concentration_pct = (
        max_position_value / total_capital if total_capital > 0 else 0.0
    )

    return {
        "total_capital": total_capital,
        "cash": cash,
        "positions_value": positions_value,
        "total_pl": unrealized_pl,
        "unrealized_pl": unrealized_pl,
        "risk_pct": risk_pct,
        "concentration_pct": concentration_pct,
    }


def max_concentration(positions: list[dict], max_pct: float = 0.5) -> bool:
    """Check whether any single position exceeds the concentration limit.

    Parameters
    ----------
    positions : list[dict]
        List of position dicts (must contain 'shares', 'entry'; may have
        'current_price' or 'price').
    max_pct : float, optional
        Maximum allowed fraction of total capital for one position (default 0.5).

    Returns
    -------
    bool
        True if any position exceeds the limit (problem flag).
    """
    total_capital = 0.0
    position_values = []

    for pos in positions:
        entry = pos.get("entry", 0.0)
        shares = pos.get("shares", 0)
        cap = pos.get("capital", 0.0)
        price = pos.get("current_price") or pos.get("price")

        cost_basis = shares * entry
        total_capital += min(cap, cost_basis) if cap else cost_basis
        pos_value = shares * (price if price is not None else entry)
        position_values.append(pos_value)

    if total_capital <= 0:
        return False

    for pv in position_values:
        if pv / total_capital > max_pct:
            return True
    return False


# Known correlation groups for US equities / ETFs
_CORRELATION_GROUPS: dict[str, list[str]] = {
    "Tech": ["AAPL", "MSFT", "NVDA", "AMD", "QQQ", "XLK", "GOOGL", "META"],
    "Financial": ["JPM", "GS", "XLF", "BAC", "WFC"],
    "Energy": ["XLE", "CVX", "XOM"],
    "Consumer": ["AMZN", "XLY", "WMT", "COST"],
    "Healthcare": ["XLV", "UNH", "LLY", "JNJ"],
    "Crypto-adjacent": ["COIN", "MSTR", "HOOD"],
}


def correlation_warning(tickers: list[str]) -> dict:
    """Check a list of tickers for known correlation overlap.

    Parameters
    ----------
    tickers : list[str]
        Ticker symbols to check.

    Returns
    -------
    dict
        is_correlated : bool        — True if any correlated pair found
        groups        : list[str]   — names of groups with multiple tickers
        pairs         : list[tuple]  — individual correlated (a, b) pairs
    """
    ticker_set = {t.upper() for t in tickers}
    matched_groups: list[str] = []
    pairs: list[tuple[str, str]] = []

    for group_name, members in _CORRELATION_GROUPS.items():
        present = [t for t in members if t in ticker_set]
        if len(present) >= 2:
            matched_groups.append(group_name)
            # Generate all unique unordered pairs within the group
            for i in range(len(present)):
                for j in range(i + 1, len(present)):
                    pairs.append((present[i], present[j]))

    return {
        "is_correlated": len(pairs) > 0,
        "groups": matched_groups,
        "pairs": pairs,
    }


def adjust_stop(
    current_stop: float,
    price: float,
    rsi: float,
    trail_pct: float = 0.05,
) -> float:
    """Adjust a trailing stop based on RSI and price action.

    Rules (applied in order):
    1. If RSI > 75  → tighten: return max(current_stop, price * (1 - trail_pct))
       (never loosens the stop).
    2. If stop is stale (>30% below price) → return price * (1 - trail_pct).
    3. Otherwise → return current_stop unchanged.

    Parameters
    ----------
    current_stop : float
        Current stop-loss level.
    price : float
        Current market price.
    rsi : float
        Current RSI value (0–100).
    trail_pct : float, optional
        Trailing percentage below price (default 0.05 = 5%).

    Returns
    -------
    float
        The adjusted stop price.
    """
    trail_price = price * (1.0 - trail_pct)

    # Rule 1 — RSI overbought: tighten the stop
    if rsi > 75.0:
        return max(current_stop, trail_price)

    # Rule 2 — stale stop that is way too far below price
    if current_stop < price * 0.7:
        return trail_price

    # Rule 3 — no change
    return current_stop