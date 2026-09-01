"""
Monitor / Watchdog Agent
========================
Reads watchdog_positions.json, checks each open position against current
market data, and returns structured results (alerts, summaries, state).

Never prints — returns dicts for the CLI/reporting layer to format.
Replaces the old src/multi_watchdog.py.
"""

import json
import os
from datetime import datetime

from swing_trader.config import (
    WATCHDOG_CONFIG_PATH,
    STATE_FILE,
    TIMESTAMP_FILE,
)
from swing_trader.data import get_price, get_time_series, get_rsi
from swing_trader.indicators import calc_all

# ─── Default config ────────────────────────────────────────────────────────────

DEFAULT_CONFIG = {
    "scheduled_times": ["09:45", "12:30", "16:00"],
    "positions": {},
}

SCHEDULED_LABELS = {
    "09:45": "🔔 Opening Brief",
    "12:30": "📌 Midday Check",
    "16:00": "🏁 Closing Report",
}

# ─── Config helpers ────────────────────────────────────────────────────────────


def load_config() -> dict:
    """Read the watchdog JSON config from WATCHDOG_CONFIG_PATH.

    Returns
        dict with keys ``positions`` and ``scheduled_times``.
        Returns an empty default dict on file-not-found or corrupt JSON.
    """
    try:
        with open(WATCHDOG_CONFIG_PATH) as f:
            cfg = json.load(f)
        cfg.setdefault("scheduled_times", DEFAULT_CONFIG["scheduled_times"])
        cfg.setdefault("positions", {})
        return cfg
    except (FileNotFoundError, json.JSONDecodeError):
        return dict(DEFAULT_CONFIG)


def load_state() -> dict:
    """Read persisted watchdog state from STATE_FILE.

    Returns
        deserialized dict, or {} on any error.
    """
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_state(state: dict) -> None:
    """Persist *state* as JSON to STATE_FILE."""
    try:
        os.makedirs(os.path.dirname(STATE_FILE) or ".", exist_ok=True)
        with open(STATE_FILE, "w") as f:
            json.dump(state, f)
    except OSError:
        pass


def write_timestamp() -> None:
    """Write the current datetime string to TIMESTAMP_FILE."""
    try:
        os.makedirs(os.path.dirname(TIMESTAMP_FILE) or ".", exist_ok=True)
        with open(TIMESTAMP_FILE, "w") as f:
            f.write(datetime.now().strftime("%A, %B %d, %Y  %I:%M:%S %p %Z"))
    except OSError:
        pass


# ─── Per-ticker check ─────────────────────────────────────────────────────────


def check_ticker(
    ticker: str,
    pos: dict,
    state: dict,
) -> tuple[dict | None, list[str] | None, dict | None]:
    """Check a single open position against live market data.

    Parameters
        ticker:  Symbol (e.g. ``"AAPL"``).
        pos:     Position dict with keys *entry*, *stop*, *shares*, *capital*,
                 and optionally *trail_stop*, *tp1*, *tp2*.
        state:   Full watchdog state dict (or per-ticker subset read from it).

    Returns
        ``(summary_dict, alerts_or_None, new_ticker_state_or_None)``.
        All three are ``None`` when the API call fails (skip gracefully).
    """
    # ── Extract position parameters ───────────────────────────────────────
    entry = pos["entry"]
    stop = pos["stop"]
    trail_stop = pos.get("trail_stop", stop)
    tp1 = pos.get("tp1")
    tp2 = pos.get("tp2")
    shares = pos.get("shares", 1)
    capital = pos.get("capital", 0)

    # ── Unpack persisted per-ticker state ─────────────────────────────────
    ticker_state = state.get(ticker, {})
    current_stop = ticker_state.get("stop", trail_stop)
    stop_tightened = current_stop != stop

    tp1_hit = ticker_state.get("tp1_hit", False)
    tp2_hit = ticker_state.get("tp2_hit", False)
    tp1_drop_alerted = ticker_state.get("tp1_drop_alerted", False)
    tp2_drop_alerted = ticker_state.get("tp2_drop_alerted", False)

    # ── Fetch live data via data.py ───────────────────────────────────────
    price = get_price(ticker)
    if price is None:
        return None, None, None

    candles = get_time_series(ticker, days=2)
    if not candles or len(candles) < 1:
        return None, None, None

    # We request 100 days but only need 2 for this check — still use 2 for
    # day-open / prev-close.  The first candle is the most recent daily bar.
    today = candles[0]
    yesterday = candles[1] if len(candles) > 1 else today

    day_open = float(today["open"])
    day_high = float(today["high"])
    day_low = float(today["low"])
    prev_close = float(yesterday["close"])
    volume = int(today.get("volume", 0))

    rsi = get_rsi(ticker)
    if rsi is None:
        rsi = 50.0  # neutral fallback

    # ── P&L calculations ──────────────────────────────────────────────────
    unrealized = round((price - entry) * shares, 2)
    unrealized_pct = round((price / entry - 1) * 100, 2)
    day_change = round(price - prev_close, 2)
    day_change_pct = round((price / prev_close - 1) * 100, 2)

    # ── Alert evaluation ──────────────────────────────────────────────────
    alerts: list[str] = []
    new_stop = current_stop

    # Stop threatened / hit
    if price <= current_stop + 0.50:
        if price <= current_stop:
            alerts.append(f"🚨 **{ticker} STOP LOSS HIT** at ${price:.2f}!")
        else:
            dist = price - current_stop
            lock = "🔒" if stop_tightened else ""
            alerts.append(
                f"⚠️ **{ticker} stop threatened** — ${price:.2f} "
                f"within ${dist:.2f} of ${current_stop:.2f} {lock}"
            )

    # Take-profit targets
    if tp2 is not None and price >= tp2 and not tp2_hit:
        alerts.append(
            f"🎯 **{ticker} TP2 HIT!** at ${price:.2f} — take profit"
        )
    elif tp1 is not None and price >= tp1 and not tp1_hit:
        alerts.append(
            f"🎯 **{ticker} TP1 HIT!** at ${price:.2f} — consider exit or trail"
        )

    # Dropped back below TP (only alert once per ticker)
    if tp1 is not None and tp1_hit and price < tp1 and not tp1_drop_alerted:
        alerts.append(
            f"⚠️ **{ticker} dropped back below TP1** "
            f"(${tp1:.2f}) — now at ${price:.2f}"
        )
    if tp2 is not None and tp2_hit and price < tp2 and not tp2_drop_alerted:
        alerts.append(
            f"⚠️ **{ticker} dropped back below TP2** "
            f"(${tp2:.2f}) — now at ${price:.2f}"
        )

    # RSI extremes
    if rsi > 75:
        alerts.append(
            f"🔥 **{ticker} Overbought** — RSI at {rsi:.1f}, "
            "consider trailing stop"
        )
    elif rsi < 25:
        alerts.append(
            f"🥶 **{ticker} Oversold** — RSI at {rsi:.1f}, "
            "potential bounce"
        )

    # Large daily move
    if day_change_pct < -4:
        alerts.append(
            f"📉 **{ticker} dropped** {day_change_pct:+.1f}% "
            f"today — ${price:.2f}"
        )
    elif day_change_pct > 8:
        alerts.append(
            f"🚀 **{ticker} surged** {day_change_pct:+.1f}% "
            f"today — ${price:.2f}"
        )

    # ── Build new per-ticker state ────────────────────────────────────────
    new_ticker_state = {
        "stop": new_stop,
        "tp1_hit": tp1_hit or (tp1 is not None and price >= tp1),
        "tp2_hit": tp2_hit or (tp2 is not None and price >= tp2),
        "tp1_drop_alerted": tp1_drop_alerted
        or (tp1_hit and price < tp1),
        "tp2_drop_alerted": tp2_drop_alerted
        or (tp2_hit and price < tp2),
    }

    # ── Stop label ────────────────────────────────────────────────────────
    stop_label = f"**${current_stop:.2f}**"
    if stop_tightened:
        stop_label += " 🔒"

    # ── Summary dict ──────────────────────────────────────────────────────
    summary = {
        "ticker": ticker,
        "price": price,
        "day_change": day_change,
        "day_change_pct": day_change_pct,
        "entry": entry,
        "unrealized": unrealized,
        "unrealized_pct": unrealized_pct,
        "rsi": rsi,
        "current_stop": current_stop,
        "stop_label": stop_label,
        "tp1": tp1,
        "tp2": tp2,
        "shares": shares,
        "capital": capital,
        "day_low": day_low,
        "day_high": day_high,
        "volume": volume,
        "stop_tightened": stop_tightened,
    }

    return summary, alerts if alerts else None, new_ticker_state


# ─── Main check loop ────────────────────────────────────────────────────────


def check_all() -> dict:
    """Run the full watchdog scan across all configured positions.

    Loads config and persisted state, writes a timestamp, then loops
    through every position calling :func:`check_ticker`.  New per-ticker
    state is merged and saved.

    Returns
        A dict with:
        - **results** (list[dict]) — one summary per position.
        - **alerts** (dict[str, list[str]]) — ticker→alerts map.
        - **is_scheduled** (bool) — whether the current time matches a
          scheduled briefing slot.
        - **scheduled_label** (str | None) — label for the scheduled slot,
          or ``None`` outside scheduled times.
        - **time_str** (str) — human-readable current time.
    """
    now = datetime.now()
    time_key = now.strftime("%H:%M")
    time_str = now.strftime("%I:%M %p ET").lstrip("0")

    write_timestamp()

    cfg = load_config()
    positions = cfg.get("positions", {})
    scheduled_times = cfg.get("scheduled_times", DEFAULT_CONFIG["scheduled_times"])

    is_scheduled = time_key in scheduled_times
    scheduled_label = SCHEDULED_LABELS.get(time_key)
    if is_scheduled and scheduled_label is None:
        scheduled_label = f"⏰ Scheduled Check — {time_str}"

    if not positions:
        return {
            "results": [],
            "alerts": {},
            "is_scheduled": is_scheduled,
            "scheduled_label": scheduled_label,
            "time_str": time_str,
        }

    state = load_state()
    new_state: dict = {}
    results: list[dict] = []
    all_alerts: dict[str, list[str]] = {}

    for ticker in sorted(positions.keys()):
        pos = positions[ticker]
        result, alerts, ticker_state = check_ticker(ticker, pos, state)
        if result is None:
            continue
        results.append(result)
        if ticker_state is not None:
            new_state[ticker] = ticker_state
        if alerts:
            all_alerts[ticker] = alerts

    # Merge and persist new state
    if new_state:
        merged = state.copy()
        merged.update(new_state)
        save_state(merged)

    return {
        "results": results,
        "alerts": all_alerts,
        "is_scheduled": is_scheduled,
        "scheduled_label": scheduled_label,
        "time_str": time_str,
    }