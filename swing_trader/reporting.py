"""Swing Trader — CLI Reporting & Formatting (Agent 8)"""

# ─── ANSI helpers ───────────────────────────────────────────────────────────

GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
BOLD = "\033[1m"
RESET = "\033[0m"

SEP = "─" * 72
THIN_SEP = "─" * 40


def _clr(val, good=True, bad=False):
    """Return green/red ANSI-wrapped str, or plain if boolean."""
    if good is True:
        return f"{GREEN}{val}{RESET}"
    if bad is True:
        return f"{RED}{val}{RESET}"
    return str(val)


def _pct(val):
    """Format as signed percentage string."""
    if val >= 0:
        return f"+{val:.2f}%"
    return f"{val:.2f}%"


def _dollar(val):
    """Format as dollar string with sign."""
    if val >= 0:
        return f"${val:.2f}"
    return f"-${abs(val):.2f}"


# ─── formatters ────────────────────────────────────────────────────────────

def format_terminal(signal: dict, ind: dict = None) -> str:
    """Colorized terminal output for a single signal."""
    ticker = signal.get("ticker", "???").upper()
    price = signal.get("price", 0.0)
    direction = signal.get("direction", "WAIT")
    confidence = signal.get("confidence", 0.0)
    entry = signal.get("entry", 0.0)
    stop = signal.get("stop", 0.0)
    tp1 = signal.get("tp1", 0.0)
    tp2 = signal.get("tp2", 0.0)
    rr = signal.get("rr", 0.0)
    reason = signal.get("reason", "")
    strategies = signal.get("strategies", [])

    # Direction color
    if direction == "BUY":
        dir_color = f"{GREEN}{BOLD}BUY{RESET}"
        dir_emoji = "🟢"
    elif direction == "SELL":
        dir_color = f"{RED}{BOLD}SELL{RESET}"
        dir_emoji = "🔴"
    elif direction == "HOLD":
        dir_color = f"{YELLOW}{BOLD}HOLD{RESET}"
        dir_emoji = "⏸️"
    else:
        dir_color = f"{YELLOW}{BOLD}WAIT{RESET}"
        dir_emoji = "⏳"

    out = []
    out.append(SEP)
    out.append(f"  {BOLD}{ticker}{RESET}  |  {dir_emoji} {dir_color}  |  ${price:<8.2f}")
    out.append(SEP)

    # Confidence bar
    c_pct = confidence * 100
    bar_len = 20
    filled = int(c_pct / 100 * bar_len)
    bar = "█" * filled + "░" * (bar_len - filled)
    if c_pct >= 70:
        bar_colored = f"{GREEN}{bar}{RESET}"
    elif c_pct >= 40:
        bar_colored = f"{YELLOW}{bar}{RESET}"
    else:
        bar_colored = f"{RED}{bar}{RESET}"
    out.append(f"  Confidence:  {bar_colored}  {c_pct:.0f}%")

    # Levels
    out.append(f"  Entry: ${entry:<8.2f}  Stop: ${stop:<8.2f}  "
               f"TP1: ${tp1:<8.2f}  TP2: ${tp2:<8.2f}")
    out.append(f"  R/R: 1:{rr:<.2f}  |  Strategies: {', '.join(strategies) if strategies else '—'}")
    out.append(f"  Reason: {reason}")

    # Indicator summary
    if ind:
        out.append(THIN_SEP)
        sma20 = ind.get("sma20", "—")
        sma50 = ind.get("sma50", "—")
        rsi = ind.get("rsi", 50)
        macd = ind.get("macd", 0.0)
        bb_u = ind.get("bb_u", "—")
        bb_l = ind.get("bb_l", "—")
        atr = ind.get("atr14", "—")
        adx = ind.get("adx", "—")

        # Color RSI
        rsi_str = f"{rsi:<.1f}"
        if rsi > 70:
            rsi_str = f"{RED}{rsi_str}{RESET}"
        elif rsi < 30:
            rsi_str = f"{GREEN}{rsi_str}{RESET}"
        else:
            rsi_str = f"{rsi_str}"

        out.append(f"  SMA20: ${sma20:<8.2f}  SMA50: ${sma50:<8.2f}  "
                   f"RSI: {rsi_str}")
        out.append(f"  MACD: {macd:<+.4f}  BB: ${bb_l}–${bb_u}  "
                   f"ATR: ${atr}  ADX: {adx}")

    out.append(SEP)
    return "\n".join(out)


def format_discord(signal: dict) -> str:
    """Clean Discord markdown format for a signal."""
    ticker = signal.get("ticker", "???").upper()
    direction = signal.get("direction", "WAIT")
    confidence = signal.get("confidence", 0.0)
    entry = signal.get("entry", 0.0)
    stop = signal.get("stop", 0.0)
    tp1 = signal.get("tp1", 0.0)
    tp2 = signal.get("tp2", 0.0)
    rr = signal.get("rr", 0.0)
    reason = signal.get("reason", "")
    price = signal.get("price", 0.0)
    strategies = signal.get("strategies", [])

    emoji_map = {"BUY": "🟢", "SELL": "🔴", "HOLD": "⏸️", "WAIT": "⏳"}
    emoji = emoji_map.get(direction, "⏳")

    c_pct = round(confidence * 100)

    lines = [f"**{ticker}** — {emoji} {direction}"]
    targets = []
    if tp1:
        targets.append(f"TP1 ${tp1:.2f}")
    if tp2:
        targets.append(f"TP2 ${tp2:.2f}")

    lines.append(f"Price: **${price:.2f}**")
    lines.append(f"Confidence: **{c_pct}%** | R/R: **1:{rr:.2f}**")
    lines.append(f"Entry: **${entry:.2f}** | Stop: **${stop:.2f}**" +
                 (f" | {' | '.join(targets)}" if targets else ""))
    if strategies:
        lines.append(f"Strategies: `{'`, `'.join(strategies)}`")
    lines.append(f"Reason: {reason}")

    return "\n".join(lines)


def format_portfolio(metrics: dict) -> str:
    """Portfolio summary with table, emoji indicators."""
    total = metrics.get("total_capital", 0.0)
    cash = metrics.get("cash", 0.0)
    pos_val = metrics.get("positions_value", 0.0)
    total_pl = metrics.get("total_pl", 0.0)
    unrealized = metrics.get("unrealized_pl", 0.0)
    risk_pct = metrics.get("risk_pct", 0.0)
    conc_pct = metrics.get("concentration_pct", 0.0)

    out = []
    out.append(SEP)
    out.append(f"  {BOLD}Portfolio Summary{RESET}")
    out.append(SEP)

    # Table header
    out.append(f"  {'Item':<20s} {'Value':>12s}  {'Indicator':>12s}")
    out.append(f"  {'─'*20} {'─'*12}  {'─'*12}")

    pl_emoji = "📈" if total_pl >= 0 else "📉"
    risk_emoji = "⚠️" if risk_pct > 15 else "✅"
    conc_emoji = "🔴" if conc_pct > 50 else "🟢"

    out.append(f"  {'Total Capital':<20s} {_dollar(total):>12s}  {pl_emoji:>12s}")
    out.append(f"  {'Cash':<20s} {_dollar(cash):>12s}  {'💰':>12s}")
    out.append(f"  {'Positions Value':<20s} {_dollar(pos_val):>12s}  {'📊':>12s}")
    out.append(f"  {'Total P&L':<20s} {_dollar(total_pl):>12s}  {pl_emoji:>12s}")
    out.append(f"  {'Unrealized P&L':<20s} {_dollar(unrealized):>12s}  {pl_emoji:>12s}")
    out.append(f"  {'Risk %':<20s} {f'{risk_pct:.1f}%':>12s}  {risk_emoji:>12s}")
    out.append(f"  {'Concentration %':<20s} {f'{conc_pct:.1f}%':>12s}  {conc_emoji:>12s}")

    out.append(SEP)
    return "\n".join(out)


def format_backtest(metrics: dict) -> str:
    """Color-coded backtest results table."""
    total_return = metrics.get("total_return", 0.0)
    sharpe = metrics.get("sharpe_ratio", 0.0)
    max_dd = metrics.get("max_drawdown", 0.0)
    win_rate = metrics.get("win_rate", 0.0)
    profit_factor = metrics.get("profit_factor", 0.0)
    num_trades = metrics.get("num_trades", 0)
    avg_win = metrics.get("avg_win", 0.0)
    avg_loss = metrics.get("avg_loss", 0.0)

    out = []
    out.append(SEP)
    out.append(f"  {BOLD}Backtest Results{RESET}")
    out.append(SEP)

    ret_str = _clr(f"+{total_return:.2f}%", good=total_return >= 0, bad=total_return < 0)
    sharpe_str = _clr(f"{sharpe:.2f}", good=sharpe >= 1.0, bad=sharpe < 1.0)
    dd_str = _clr(f"{max_dd:.2f}%", good=max_dd > -20, bad=max_dd <= -20)
    wr_str = _clr(f"{win_rate:.1f}%", good=win_rate >= 50, bad=win_rate < 50)
    pf_str = _clr(f"{profit_factor:.2f}", good=profit_factor >= 1.5, bad=profit_factor < 1.5)

    out.append(f"  {'Return':<20s} {ret_str:>12s}")
    out.append(f"  {'Sharpe Ratio':<20s} {sharpe_str:>12s}")
    out.append(f"  {'Max Drawdown':<20s} {dd_str:>12s}")
    out.append(f"  {'Win Rate':<20s} {wr_str:>12s}")
    out.append(f"  {'Profit Factor':<20s} {pf_str:>12s}")
    out.append(f"  {'Total Trades':<20s} {num_trades:>12d}")

    if avg_win:
        out.append(f"  {'Avg Win':<20s} {_clr(_dollar(avg_win), good=True):>12s}")
    if avg_loss:
        out.append(f"  {'Avg Loss':<20s} {_clr(_dollar(avg_loss), bad=True):>12s}")

    out.append(SEP)
    return "\n".join(out)


def format_monitor(result: dict) -> str:
    """Scheduled report or alert report formatted as a table.

    Accepts the output of check_all() which returns:
      { "reports": list[dict], "alerts": dict[ticker -> list[str]] }
    """
    reports = result.get("reports", [])
    alerts = result.get("alerts", {})

    if not reports:
        return f"{YELLOW}⚠️ No position data available{RESET}"

    out = []
    out.append(SEP)
    out.append(f"  {BOLD}Position Monitor{RESET}")
    out.append(SEP)
    out.append(
        f"  {'Ticker':<7s} {'Price':>8s} {'Entry':>8s}  "
        f"{'P&L':>10s} {'RSI':>6s} {'Stop':>8s} {'Status':>10s}"
    )
    out.append(f"  {'─'*7} {'─'*8} {'─'*8}  {'─'*10} {'─'*6} {'─'*8} {'─'*10}")

    total_pl = 0.0

    for r in reports:
        ticker = r.get("ticker", "???").upper()
        price = r.get("price", 0.0)
        entry = r.get("entry", 0.0)
        unrealized = r.get("unrealized", 0.0)
        rsi = r.get("rsi", 50)
        current_stop = r.get("current_stop", 0.0)
        stop_tightened = r.get("stop_tightened", False)
        tp1 = r.get("tp1")
        capital = r.get("capital", 0)

        total_pl += unrealized

        # Determine status
        if stop_tightened:
            status = "🟡 trail"
        elif rsi > 75:
            status = "🔴 hot"
        elif price >= (tp1 or 99999):
            status = "🎯 tp1+"
        else:
            status = "🟢 hold"

        pl_str = _dollar(unrealized)
        stop_label = f"${current_stop:.2f}"

        out.append(
            f"  {ticker:<7s} ${price:<6.2f} ${entry:<6.2f}  "
            f"{pl_str:>10s} {rsi:<5.1f} {stop_label:>8s} {status:>10s}"
        )

    # Cash row if any reports have capital info
    if reports:
        total_capital = sum(r.get("capital", 0) for r in reports)
        total_value = total_capital + total_pl
        cash = total_capital - sum(r.get("shares", 0) * r.get("entry", 0) for r in reports)
        if cash > 0:
            out.append(
                f"  {'CASH':<7s} {'—':>8s} {'—':>8s}  "
                f"{_dollar(cash):>10s} {'—':>6s} {'—':>8s} {'💰':>10s}"
            )

        out.append("")
        pl_color = f"{GREEN}{_dollar(total_pl)}{RESET}" if total_pl >= 0 else f"{RED}{_dollar(total_pl)}{RESET}"
        out.append(f"  Portfolio: ${total_capital:.2f} → {pl_color}")

    # Alerts section
    if alerts:
        out.append("")
        out.append(THIN_SEP)
        out.append(f"  {BOLD}Alerts{RESET}")
        out.append(THIN_SEP)
        for ticker in sorted(alerts.keys()):
            for alert in alerts[ticker]:
                out.append(f"  {alert}")

    out.append(SEP)
    return "\n".join(out)