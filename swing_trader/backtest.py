"""swing_trader/backtest.py — Agent 7: Backtesting.

Walk-forward backtesting, strategy comparison, and performance metrics.
Stdlib only (math, datetime). No numpy, no OpenRouter.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta
from typing import Any, Callable

# ---------------------------------------------------------------------------
# Lazy imports — modules may not exist at import time (agent parallel build).
# We defer resolution until the functions are actually called.
# ---------------------------------------------------------------------------

_SIGNALS_MODULE: Any | None = None
_INDICATORS_MODULE: Any | None = None


def _lazy_import_signals() -> Any:
    global _SIGNALS_MODULE
    if _SIGNALS_MODULE is None:
        from swing_trader import signals as _SIGNALS_MODULE  # type: ignore[import-untyped]
    return _SIGNALS_MODULE


def _lazy_import_indicators() -> Any:
    global _INDICATORS_MODULE
    if _INDICATORS_MODULE is None:
        from swing_trader import indicators as _INDICATORS_MODULE  # type: ignore[import-untyped]
    return _INDICATORS_MODULE


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_date(candle: dict, fallback_idx: int = 0) -> str:
    """Extract a date string from a candle dict."""
    date_val = candle.get("date")
    if date_val is not None:
        return str(date_val)
    # Synthesise a placeholder date
    base = datetime(2020, 1, 1) + timedelta(days=fallback_idx)
    return base.strftime("%Y-%m-%d")


def _resolve_date(candle: dict, fallback: str | None = None) -> str | None:
    """Return candle date or None."""
    d = candle.get("date")
    if d is not None:
        return str(d)
    return fallback


# ---------------------------------------------------------------------------
# 1. Walk-forward sliding window
# ---------------------------------------------------------------------------

def _walk_forward_sliding_window(
    candles: list[dict],
    train_size: int = 60,
    test_size: int = 20,
    step: int = 20,
) -> list[tuple[list[dict], list[dict]]]:
    """Split *candles* into sliding (train, test) windows.

    Parameters
    ----------
    candles : list[Candle]
        Full ordered time series (oldest first).
    train_size : int
        Number of candles in each training window (default 60).
    test_size : int
        Number of candles in each test window (default 20).
    step : int
        Slide step (default 20 — non-overlapping test windows).

    Returns
    -------
    list of (train_candles, test_candles) tuples.
    """
    windows: list[tuple[list[dict], list[dict]]] = []
    n = len(candles)
    required = train_size + test_size
    if n < required:
        return windows  # not enough data for even one window

    for start in range(0, n - required + 1, step):
        train = candles[start : start + train_size]
        test = candles[start + train_size : start + required]
        if len(test) == test_size:
            windows.append((train, test))
    return windows


# ---------------------------------------------------------------------------
# 2. Backtest runner
# ---------------------------------------------------------------------------

def _default_strategy_func(
    ticker: str,
    candles: list[dict],
    indicators: dict,
    price: float,
) -> dict:
    """Default strategy: delegate to ``signals.generate_signal``."""
    signals = _lazy_import_signals()
    # generate_signal signature: (ticker, candles, indicator_result) -> Signal
    signal = signals.generate_signal(ticker, candles, indicators, price)
    return signal


def run_backtest(
    ticker: str,
    candles: list[dict],
    initial_capital: float = 10000.0,
    risk_per_trade: float = 0.02,
    strategy_func: Callable | None = None,
) -> dict:
    """Run a walk-forward backtest.

    Walk-forward structure: train on 60 days, test on next 20, slide by 20.
    For each day in a test window, indicators are computed on the *training*
    window, the strategy function generates a signal, and if a BUY/SELL
    signal fires a trade is simulated.

    Trade rules
    -----------
    - Enter at the close of the signal day.
    - Max hold 10 bars (calendar days in the test window).
    - Stop-loss and take-profit levels come from the Signal dict.
    - Exit at close on the day a stop or TP level is touched, or at close
      of the max-hold bar, whichever comes first.

    Parameters
    ----------
    ticker : str
        Ticker symbol (forwarded to strategy_func).
    candles : list[Candle]
        Ordered daily candles (oldest first). Each candle is a dict with keys
        ``open``, ``high``, ``low``, ``close``, ``volume``, and optionally
        ``date`` (YYYY-MM-DD string).
    initial_capital : float
        Starting portfolio value (default 10 000).
    risk_per_trade : float
        Fraction of current capital risked per trade (default 0.02 = 2%).
    strategy_func : Callable or None
        Signature ``(ticker, candles, indicators, price) -> Signal``.
        Defaults to a wrapper around ``signals.generate_signal``.

    Returns
    -------
    dict with keys:
        ticker, start_date, end_date, initial_capital, final_capital,
        total_return_pct, max_drawdown_pct, sharpe_ratio, win_rate,
        avg_win, avg_loss, profit_factor, num_trades,
        equity_curve: list[{"date": str, "equity": float}]
    """
    if strategy_func is None:
        strategy_func = _default_strategy_func

    calc_all = _lazy_import_indicators().calc_all

    # ── metadata ──────────────────────────────────────────────────────
    start_date = _get_date(candles[0], 0) if candles else "N/A"
    end_date = _get_date(candles[-1], len(candles) - 1) if candles else "N/A"

    # ── walk-forward windows ──────────────────────────────────────────
    windows = _walk_forward_sliding_window(candles)

    capital = initial_capital
    trades: list[dict] = []
    equity_curve: list[dict] = []

    for train_set, test_set in windows:
        # ── for each day in the test window ──────────────────────────
        for i, test_candle in enumerate(test_set):
            test_close = test_candle["close"]
            test_date = _get_date(test_candle)

            # compute indicators on the training window
            indicators = calc_all(train_set)

            # generate signal
            signal = strategy_func(ticker, train_set, indicators, test_close)

            direction = signal.get("direction", "HOLD")
            if direction not in ("BUY", "SELL"):
                # Record equity (no trade today)
                equity_curve.append({"date": test_date, "equity": round(capital, 2)})
                continue

            # ── position sizing ──────────────────────────────────────
            entry_price = test_close
            stop_price = signal.get("stop", 0.0)
            tp_price = signal.get("tp1", 0.0)

            # Risk-based position sizing
            risk_amount = capital * risk_per_trade
            if direction == "BUY":
                stop_distance = entry_price - stop_price
            else:
                stop_distance = stop_price - entry_price

            if stop_distance <= 0:
                # Invalid stop — skip this trade
                equity_curve.append({"date": test_date, "equity": round(capital, 2)})
                continue

            position_value = risk_amount  # use risk as the position value
            shares = max(1, int(position_value / entry_price))

            # ── find exit ────────────────────────────────────────────
            remaining_bars = test_set[i + 1 : i + 1 + 10]
            exit_price: float | None = None
            exit_date: str | None = None
            exit_reason: str = "max_hold"

            for bar in remaining_bars:
                if direction == "BUY":
                    stop_hit = bar["low"] <= stop_price
                    tp_hit = bar["high"] >= tp_price
                else:  # SELL
                    stop_hit = bar["high"] >= stop_price
                    tp_hit = bar["low"] <= tp_price

                if stop_hit:
                    exit_price = bar["close"]
                    exit_date = _get_date(bar)
                    exit_reason = "stop"
                    break
                if tp_hit:
                    exit_price = bar["close"]
                    exit_date = _get_date(bar)
                    exit_reason = "tp"
                    break

            if exit_price is None and remaining_bars:
                # Max hold — exit at close of last bar in hold window
                last_bar = remaining_bars[-1]
                exit_price = last_bar["close"]
                exit_date = _get_date(last_bar)

            # ── P&L ──────────────────────────────────────────────────
            if exit_price is None:
                # No exit found (ran out of test data) — force exit at
                # the last test candle's close.
                exit_price = test_set[-1]["close"]
                exit_date = _get_date(test_set[-1])
                exit_reason = "end_of_window"

            if direction == "BUY":
                pnl = (exit_price - entry_price) / entry_price
            else:
                pnl = (entry_price - exit_price) / entry_price

            pnl_dollars = pnl * (shares * entry_price)
            capital += pnl_dollars

            trade_record = {
                "entry_date": test_date,
                "exit_date": exit_date,
                "direction": direction,
                "entry": round(entry_price, 4),
                "exit": round(exit_price, 4),
                "shares": shares,
                "pnl_pct": round(pnl * 100, 4),
                "pnl_dollars": round(pnl_dollars, 2),
                "exit_reason": exit_reason,
                "stop": round(stop_price, 4),
                "tp": round(tp_price, 4),
            }
            trades.append(trade_record)

            # Record equity after the trade completes
            equity_curve.append({
                "date": exit_date or test_date,
                "equity": round(capital, 2),
            })

    # ── compute summary metrics ───────────────────────────────────────
    if not trades:
        return {
            "ticker": ticker,
            "start_date": start_date,
            "end_date": end_date,
            "initial_capital": initial_capital,
            "final_capital": initial_capital,
            "total_return_pct": 0.0,
            "max_drawdown_pct": 0.0,
            "sharpe_ratio": 0.0,
            "win_rate": 0.0,
            "avg_win": 0.0,
            "avg_loss": 0.0,
            "profit_factor": 0.0,
            "num_trades": 0,
            "equity_curve": equity_curve,
        }

    wins = [t for t in trades if t["pnl_dollars"] > 0]
    losses = [t for t in trades if t["pnl_dollars"] <= 0]
    num_wins = len(wins)
    num_losses = len(losses)
    num_trades = len(trades)

    win_rate = num_wins / num_trades if num_trades else 0.0
    avg_win = sum(t["pnl_pct"] for t in wins) / num_wins if num_wins else 0.0
    avg_loss = sum(t["pnl_pct"] for t in losses) / num_losses if num_losses else 0.0

    gross_profit = sum(t["pnl_dollars"] for t in wins)
    gross_loss = abs(sum(t["pnl_dollars"] for t in losses))
    profit_factor = gross_profit / gross_loss if gross_loss else float("inf")

    total_return_pct = ((capital - initial_capital) / initial_capital) * 100.0

    sharpe = _calc_sharpe(equity_curve)
    max_dd = _calc_max_drawdown(equity_curve)

    return {
        "ticker": ticker,
        "start_date": start_date,
        "end_date": end_date,
        "initial_capital": initial_capital,
        "final_capital": round(capital, 2),
        "total_return_pct": round(total_return_pct, 4),
        "max_drawdown_pct": round(max_dd, 4),
        "sharpe_ratio": round(sharpe, 4),
        "win_rate": round(win_rate, 4),
        "avg_win": round(avg_win, 4),
        "avg_loss": round(avg_loss, 4),
        "profit_factor": round(profit_factor, 4) if profit_factor != float("inf") else float("inf"),
        "num_trades": num_trades,
        "equity_curve": equity_curve,
    }


# ---------------------------------------------------------------------------
# 3. Strategy comparison
# ---------------------------------------------------------------------------

def compare_strategies(ticker: str, candles: list[dict]) -> dict:
    """Run a simple (non-walk-forward) backtest for each individual strategy.

    For each candle in *candles*, indicators are computed on all prior
    candles, and ``signals.generate_signal`` is called. The resulting
    Signal contains a *strategies* list indicating which sub-strategies
    triggered. Per-strategy stats are aggregated from those signals.

    Returns
    -------
    dict of ``strategy_name -> {num_signals, avg_return_per_trade, win_rate}``
    """
    signals_mod = _lazy_import_signals()
    calc_all = _lazy_import_indicators().calc_all

    # Strategy name -> stats
    results: dict[str, dict] = {}

    for i in range(1, len(candles)):
        prior = candles[:i]
        curr = candles[i]

        price = curr["close"]
        indicators = calc_all(prior)
        signal = signals_mod.generate_signal(ticker, prior, indicators, price)
        direction = signal.get("direction", "HOLD")
        if direction == "HOLD":
            continue
        entry = signal.get("entry", price)
        stop = signal.get("stop", 0.0)
        tp = signal.get("tp1", 0.0)

        # Estimate per-strategy return (simplified: 1:1 risk:reward at TP)
        strategies = signal.get("strategies", [])
        if not strategies:
            # If individual strategy names are missing, attribute to "ensemble"
            strategies = ["ensemble"]

        # Simulate one bar forward (simple) — close of next candle or stop/TP
        next_idx = i + 1
        if next_idx < len(candles):
            exit_bar = candles[next_idx]
            if direction == "BUY":
                if exit_bar["low"] <= stop:
                    exit_price = stop
                elif exit_bar["high"] >= tp:
                    exit_price = tp
                else:
                    exit_price = exit_bar["close"]
                pnl_pct = (exit_price - price) / price
            else:  # SELL
                if exit_bar["high"] >= stop:
                    exit_price = stop
                elif exit_bar["low"] <= tp:
                    exit_price = tp
                else:
                    exit_price = exit_bar["close"]
                pnl_pct = (price - exit_price) / price
            is_win = pnl_pct > 0
        else:
            pnl_pct = 0.0
            is_win = False

        for strat in strategies:
            if strat not in results:
                results[strat] = {"num_signals": 0, "total_return": 0.0, "wins": 0}
            results[strat]["num_signals"] += 1
            results[strat]["total_return"] += pnl_pct
            if is_win:
                results[strat]["wins"] += 1

    # ── compute summary per strategy ──────────────────────────────
    output: dict[str, dict] = {}
    for name, data in results.items():
        n = data["num_signals"]
        output[name] = {
            "num_signals": n,
            "avg_return_per_trade": round(data["total_return"] * 100 / n, 4) if n else 0.0,
            "win_rate": round(data["wins"] / n, 4) if n else 0.0,
        }

    return output


# ---------------------------------------------------------------------------
# 4. Sharpe ratio (daily returns, annualised)
# ---------------------------------------------------------------------------

def _calc_sharpe(equity_curve: list[dict]) -> float:
    """Annualised Sharpe ratio from an equity curve.

    Parameters
    ----------
    equity_curve : list of {"date": str, "equity": float}
        Ordered equity values (oldest first).

    Returns
    -------
    float — annualised Sharpe (0.0 if fewer than 2 points or zero std dev).
    """
    if len(equity_curve) < 2:
        return 0.0

    eqs = [e["equity"] for e in equity_curve]

    # Daily returns: r_i = (eq_i - eq_{i-1}) / eq_{i-1}
    # Handle zero equity edge case
    daily_returns: list[float] = []
    for i in range(1, len(eqs)):
        prev = eqs[i - 1]
        if prev == 0:
            continue
        daily_returns.append((eqs[i] - prev) / prev)

    if len(daily_returns) < 2:
        return 0.0

    mean_ret = sum(daily_returns) / len(daily_returns)

    # population std for Sharpe is debated; use sample std (ddof=1)
    variance = sum((r - mean_ret) ** 2 for r in daily_returns) / (len(daily_returns) - 1)
    std_ret = math.sqrt(variance)

    if std_ret == 0:
        return 0.0

    return (mean_ret / std_ret) * math.sqrt(252)


# ---------------------------------------------------------------------------
# 5. Max drawdown
# ---------------------------------------------------------------------------

def _calc_max_drawdown(equity_curve: list[dict]) -> float:
    """Maximum peak-to-trough drawdown as a percentage.

    Drawdown at point *i* = (running_max - equity_i) / running_max * 100.
    Returns the largest such value.

    Parameters
    ----------
    equity_curve : list of {"date": str, "equity": float}

    Returns
    -------
    float — max drawdown % (0.0 if fewer than 2 points).
    """
    if len(equity_curve) < 2:
        return 0.0

    running_max = 0.0
    max_dd = 0.0

    for entry in equity_curve:
        eq = entry["equity"]
        if eq > running_max:
            running_max = eq
        if running_max > 0:
            dd = ((running_max - eq) / running_max) * 100.0
            if dd > max_dd:
                max_dd = dd

    return max_dd