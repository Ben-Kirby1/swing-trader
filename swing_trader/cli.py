"""Swing Trader v2.0.0 CLI — Multi-strategy AI trading system"""

import argparse
import json
import os
import sys

from swing_trader.data import get_price, get_quote, get_time_series
from swing_trader.indicators import calc_all
from swing_trader.signals import generate_signal, llm_narrative
from swing_trader.risk import portfolio_metrics, correlation_warning
from swing_trader.monitor import check_all
from swing_trader.backtest import run_backtest, compare_strategies
from swing_trader.reporting import (
    format_terminal,
    format_discord,
    format_portfolio,
    format_backtest,
    format_monitor,
    GREEN,
    RED,
    YELLOW,
    BOLD,
    RESET,
    SEP,
)
from swing_trader.config import PROG_VERSION, OPENROUTER_KEY, WATCHDOG_CONFIG_PATH


def _cmd_analyze(args):
    """analyze TICKER [TICKER...]: fetch, indicators, signals, LLM narrative, print."""
    credits = 0

    for ticker in args.tickers:
        ticker = ticker.upper().strip()

        # Fetch
        price = get_price(ticker)
        if price is None:
            print(f"{RED}❌ {ticker}: No price data{RESET}")
            continue
        credits += 1

        candles = get_time_series(ticker, days=100)
        if not candles:
            print(f"{RED}❌ {ticker}: No time series data{RESET}")
            continue
        credits += 1

        # Indicators
        ind = calc_all(candles)
        if ind is None:
            print(f"{RED}❌ {ticker}: Could not compute indicators{RESET}")
            continue

        # Signal
        signal = generate_signal(ticker, candles, ind, price)
        if signal is None:
            print(f"{YELLOW}⚠️  {ticker}: No signal generated{RESET}")
            signal = {
                "ticker": ticker,
                "direction": "WAIT",
                "confidence": 0.0,
                "entry": 0.0,
                "stop": 0.0,
                "tp1": 0.0,
                "tp2": 0.0,
                "rr": 0.0,
                "reason": "Insufficient data for signal generation",
                "strategies": [],
                "price": price,
            }

        # LLM narrative (optional)
        narrative = None
        if OPENROUTER_KEY:
            narrative = llm_narrative(ticker, price, ind, signal)

        # Print terminal report
        print(format_terminal(signal, ind=ind))

        # Append LLM narrative if available
        if narrative:
            print(f"\n  {BOLD}LLM Narrative:{RESET}")
            print(f"  {narrative}")
            print(SEP)

    print(f"\n{GREEN}Credits used this session: {credits}{RESET}")


def _cmd_watch(args):
    """watch: run position watchdog check."""
    result = check_all()
    if result is None:
        print(f"{YELLOW}⚠️  No watchdog config found or all data unavailable{RESET}")
        return
    print(format_monitor(result))


def _cmd_portfolio(args):
    """portfolio [--file path]: compute and show portfolio metrics."""
    path = args.file
    if path is None:
        path = WATCHDOG_CONFIG_PATH

    if not path or not os.path.exists(path):
        print(f"{RED}❌ Positions file not found: {path}{RESET}")
        return

    try:
        with open(path) as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        print(f"{RED}❌ Error reading positions file: {e}{RESET}")
        return

    positions = data.get("positions", {})
    if not positions:
        print(f"{YELLOW}⚠️  No positions found in {path}{RESET}")
        return

    # Build list of Position dicts for portfolio_metrics
    pos_list = []
    total_cash = 0.0
    for ticker, p in positions.items():
        shares = p.get("shares", 0)
        entry = p.get("entry", 0.0)
        stop = p.get("stop", 0.0)
        trail_stop = p.get("trail_stop", stop)
        tp1 = p.get("tp1", 0.0)
        tp2 = p.get("tp2", 0.0)
        capital = p.get("capital", 0.0)
        open_date = p.get("open_date", "")

        total_cash += capital
        pos_list.append({
            "ticker": ticker,
            "entry": entry,
            "stop": stop,
            "trail_stop": trail_stop,
            "shares": shares,
            "capital": capital,
            "tp1": tp1,
            "tp2": tp2,
            "open_date": open_date,
        })

    # Compute actual position value from current prices
    pos_value = 0.0
    for pos in pos_list:
        price = get_price(pos["ticker"])
        if price is not None:
            pos_value += price * pos["shares"]

    cash_remaining = total_cash - sum(p["entry"] * p["shares"] for p in pos_list)
    if cash_remaining < 0:
        cash_remaining = 0.0

    metrics = portfolio_metrics(pos_list, cash_remaining)
    # Patch in computed positions_value and total_capital
    metrics["positions_value"] = round(pos_value, 2)
    metrics["total_capital"] = round(cash_remaining + pos_value, 2)

    print(format_portfolio(metrics))

    # Correlation warning
    tickers = [p["ticker"] for p in pos_list]
    warnings = correlation_warning(tickers)
    if warnings:
        print(f"\n{YELLOW}⚠️  Correlation Warnings:{RESET}")
        for w in warnings:
            print(f"  {YELLOW}• {w}{RESET}")


def _cmd_backtest(args):
    """backtest TICKER [--start] [--end] [--capital]: walk-forward backtest."""
    ticker = args.ticker.upper().strip()
    print(f"{BOLD}Running walk-forward backtest for {ticker}...{RESET}")

    candles = get_time_series(ticker, days=500)
    if not candles or len(candles) < 100:
        print(f"{RED}❌ {ticker}: Need 100+ candles for backtest, got {len(candles) if candles else 0}{RESET}")
        return

    # Filter candles by date range if specified
    bt_candles = candles
    if args.start:
        bt_candles = [c for c in bt_candles if c.get("datetime", "") >= args.start]
    if args.end:
        bt_candles = [c for c in bt_candles if c.get("datetime", "") <= args.end]
    if len(bt_candles) < 100:
        print(f"{RED}❌ {ticker}: Need 100+ candles for backtest after date filter, got {len(bt_candles)}{RESET}")
        return

    # Run main backtest
    metrics = run_backtest(
        ticker,
        bt_candles,
        initial_capital=args.capital,
    )

    if metrics is None:
        print(f"{RED}❌ Backtest returned no results{RESET}")
        return

    print(format_backtest(metrics))

    # Compare individual strategies
    print(f"\n{BOLD}Strategy Comparison:{RESET}")
    comp = compare_strategies(ticker, candles)
    if comp:
        for strat_name, strat_metrics in sorted(comp.items()):
            ret = strat_metrics.get("total_return", 0.0)
            ret_str = f"{GREEN}+{ret:.2f}%{RESET}" if ret >= 0 else f"{RED}{ret:.2f}%{RESET}"
            wr = strat_metrics.get("win_rate", 0.0)
            wr_str = f"{GREEN}{wr:.1f}%{RESET}" if wr >= 50 else f"{RED}{wr:.1f}%{RESET}"
            trades = strat_metrics.get("num_trades", 0)
            print(f"  {strat_name+':':<15s} Return: {ret_str}  |  Win Rate: {wr_str}  |  Trades: {trades}")
    print(SEP)


def _cmd_status(args):
    """status: check environment, config, cache."""
    print(f"{BOLD}Swing Trader v{PROG_VERSION} — System Status{RESET}")
    print(SEP)

    # API keys
    td_key = os.environ.get("TWELVE_DATA_KEY", "")
    if td_key:
        masked = td_key[:4] + "…" + td_key[-4:] if len(td_key) > 8 else "***"
        print(f"  Twelve Data Key:  {GREEN}✓{RESET}  {masked}")
    else:
        try:
            from swing_trader.config import TWELVE_DATA_KEY
            if TWELVE_DATA_KEY and TWELVE_DATA_KEY != "...":
                masked = TWELVE_DATA_KEY[:4] + "…" + TWELVE_DATA_KEY[-4:] if len(TWELVE_DATA_KEY) > 8 else "***"
                print(f"  Twelve Data Key:  {GREEN}✓{RESET}  {masked}")
            else:
                print(f"  Twelve Data Key:  {RED}✗{RESET}  not configured")
        except ImportError:
            print(f"  Twelve Data Key:  {RED}✗{RESET}  not found in config")

    or_key = OPENROUTER_KEY
    if or_key and or_key != "sk-or-...2a4b":
        print(f"  OpenRouter Key:   {GREEN}✓{RESET}  LLM narratives enabled")
    else:
        print(f"  OpenRouter Key:   {YELLOW}⚠{RESET}  not configured — heuristic only")

    # Cache directory
    cache_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".cache")
    if os.path.isdir(cache_dir):
        total_size = 0
        file_count = 0
        for root, dirs, files in os.walk(cache_dir):
            for f in files:
                fp = os.path.join(root, f)
                try:
                    total_size += os.path.getsize(fp)
                    file_count += 1
                except OSError:
                    pass
        if total_size < 1024:
            size_str = f"{total_size} B"
        elif total_size < 1024 * 1024:
            size_str = f"{total_size / 1024:.1f} KB"
        else:
            size_str = f"{total_size / 1024 / 1024:.1f} MB"
        print(f"  Cache Directory:  {GREEN}✓{RESET}  {cache_dir}")
        print(f"  Cache Size:       {size_str}  ({file_count} files)")
    else:
        print(f"  Cache Directory:  {YELLOW}⚠{RESET}  {cache_dir} — does not exist")

    # Watchdog config
    wd_path = WATCHDOG_CONFIG_PATH
    if wd_path and os.path.exists(wd_path):
        print(f"  Watchdog Config:  {GREEN}✓{RESET}  {wd_path}")
    else:
        print(f"  Watchdog Config:  {YELLOW}⚠{RESET}  {wd_path or 'not set'} — not found")

    # Module availability
    checks = []
    for mod_name, mod_alias in [
        ("data", "data"),
        ("indicators", "indicators"),
        ("signals", "signals"),
        ("risk", "risk"),
        ("monitor", "monitor"),
        ("backtest", "backtest"),
        ("reporting", "reporting"),
    ]:
        try:
            __import__(f"swing_trader.{mod_name}")
            checks.append(f"    {mod_name:<15s}  {GREEN}✓{RESET}")
        except ImportError:
            checks.append(f"    {mod_name:<15s}  {RED}✗{RESET}")

    print(f"  Modules:")
    print("\n".join(checks))
    print(SEP)

    # Twelve Data rate info
    print(f"  Daily API Budget:  800 calls/day free tier")
    print(f"  Rate Limit:        8 calls/min")
    print(SEP)


def main():
    parser = argparse.ArgumentParser(
        prog="swing-trader",
        description=f"Swing Trader v{PROG_VERSION} - Multi-strategy AI trading system",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # analyze
    a = sub.add_parser("analyze", help="Analyze ticker(s) with all strategies + LLM narrative")
    a.add_argument("tickers", nargs="+", help="Ticker symbols")

    # watch
    sub.add_parser("watch", help="Run position watchdog")

    # portfolio
    p = sub.add_parser("portfolio", help="Show portfolio metrics")
    p.add_argument("--file", default=None, help="Path to positions JSON")

    # backtest
    b = sub.add_parser("backtest", help="Run walk-forward backtest")
    b.add_argument("ticker", help="Ticker symbol")
    b.add_argument("--start", type=str, default=None, help="Start date YYYY-MM-DD")
    b.add_argument("--end", type=str, default=None, help="End date YYYY-MM-DD")
    b.add_argument("--capital", type=float, default=10000.0, help="Initial capital")

    # status
    sub.add_parser("status", help="Check system status")

    args = parser.parse_args()

    # Dispatch
    if args.command == "analyze":
        _cmd_analyze(args)
    elif args.command == "watch":
        _cmd_watch(args)
    elif args.command == "portfolio":
        _cmd_portfolio(args)
    elif args.command == "backtest":
        _cmd_backtest(args)
    elif args.command == "status":
        _cmd_status(args)


if __name__ == "__main__":
    main()