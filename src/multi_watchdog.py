#!/usr/bin/env python3
"""
Multi-Ticker Paper Trade Watchdog
==================================
Runs every 15 min during market hours. Stays SILENT unless something matters.
Scheduled briefings at configurable times (default: 9:45, 12:30, 16:00).
Alert-only reports for: stop threatened, target hit, RSI extreme, big move.

Configure positions in watchdog_positions.json (same directory as this script).
"""
import json, os, sys
from urllib.request import Request, urlopen
from datetime import datetime

try:
    import config
except ImportError:
    print("❌ config.py not found. Copy config.example.py to config.py and add your API keys.")
    sys.exit(1)

# ─── Paths ────────────────────────────────────────────────────────────────

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(SCRIPT_DIR, "watchdog_positions.json")
STATE_FILE = os.path.join(SCRIPT_DIR, ".watchdog_state.json")
TIMESTAMP_FILE = os.path.join(SCRIPT_DIR, ".last_timestamp")

TD_KEY = config.TWELVE_DATA_KEY

# ─── Config & State ───────────────────────────────────────────────────────

DEFAULT_CONFIG = {
    "scheduled_times": ["9:45", "12:30", "16:00"],
    "positions": {}
}

def load_config():
    try:
        with open(CONFIG_FILE) as f:
            cfg = json.load(f)
        cfg.setdefault("scheduled_times", DEFAULT_CONFIG["scheduled_times"])
        cfg.setdefault("positions", {})
        return cfg
    except (FileNotFoundError, json.JSONDecodeError):
        print(f"⚠️  No config found at {CONFIG_FILE}", file=sys.stderr)
        return None

def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def save_state(state):
    try:
        os.makedirs(SCRIPT_DIR, exist_ok=True)
        with open(STATE_FILE, "w") as f:
            json.dump(state, f)
    except OSError:
        pass

# ─── Twelve Data API ──────────────────────────────────────────────────────

def td_get(endpoint, params):
    params["apikey"] = TD_KEY
    parts = [f"{k}={v}" for k, v in params.items()]
    url = "https://api.twelvedata.com" + endpoint + "?" + "&".join(parts)
    try:
        req = Request(url, headers={"User-Agent": "multi-watchdog/1.0"})
        with urlopen(req, timeout=15) as r:
            return json.loads(r.read().decode())
    except Exception as e:
        return {"error": str(e)}

# ─── Helpers ──────────────────────────────────────────────────────────────

def format_dollar(val):
    if val >= 0:
        return f"${val:.2f}"
    return f"-${abs(val):.2f}"

def calc_rsi(candles):
    if not candles:
        return 50
    closes = [float(v["close"]) for v in candles]
    closes = closes[::-1]
    if len(closes) < 15:
        return 50
    gains = losses = 0.0
    for i in range(-14, 0):
        d = closes[i] - closes[i - 1]
        if d > 0:
            gains += d
        else:
            losses -= d
    if losses == 0:
        return 100.0
    return 100 - 100 / (1 + gains / 14 / (losses / 14))

# ─── Per-Ticker Check ─────────────────────────────────────────────────────

def check_ticker(ticker, pos, state, scheduled_label):
    entry = pos["entry"]
    stop = pos["stop"]
    trail_stop = pos.get("trail_stop", stop)
    tp1 = pos.get("tp1")
    tp2 = pos.get("tp2")
    shares = pos.get("shares", 1)
    capital = pos.get("capital", 0)

    ticker_state = state.get(ticker, {})
    current_stop = ticker_state.get("stop", stop)
    stop_tightened = current_stop != stop

    tp1_hit = ticker_state.get("tp1_hit", False)
    tp2_hit = ticker_state.get("tp2_hit", False)
    tp1_drop_alerted = ticker_state.get("tp1_drop_alerted", False)
    tp2_drop_alerted = ticker_state.get("tp2_drop_alerted", False)

    q = td_get("/price", {"symbol": ticker})
    if "error" in q or "price" not in q:
        return None, None, None
    price = float(q["price"])

    ts = td_get("/time_series", {"symbol": ticker, "interval": "1day", "outputsize": "2"})
    if "error" in ts or "values" not in ts or len(ts["values"]) < 1:
        return None, None, None

    today = ts["values"][0]
    yesterday = ts["values"][1] if len(ts["values"]) > 1 else today
    day_open = float(today["open"])
    day_high = float(today["high"])
    day_low = float(today["low"])
    prev_close = float(yesterday["close"])
    volume = int(today.get("volume", 0))

    rsi_data = td_get("/rsi", {"symbol": ticker, "interval": "1day", "time_period": "14", "outputsize": "1"})
    rsi = 50
    if "error" not in rsi_data:
        vals = rsi_data.get("values", [])
        if vals:
            rsi = float(vals[0].get("rsi", 50))

    unrealized = round((price - entry) * shares, 2)
    unrealized_pct = round((price / entry - 1) * 100, 2)
    day_change = round(price - prev_close, 2)
    day_change_pct = round((price / prev_close - 1) * 100, 2)

    # ─── ALERTS ───
    alerts = []
    new_stop = current_stop

    if price <= current_stop + 0.50:
        if price <= current_stop:
            alerts.append(f"🚨 **{ticker} STOP LOSS HIT** at ${price:.2f}!")
        else:
            dist = price - current_stop
            lock = "🔒" if stop_tightened else ""
            alerts.append(f"⚠️ **{ticker} stop threatened** — ${price:.2f} within ${dist:.2f} of ${current_stop:.2f} {lock}")

    if tp2 and price >= tp2 and not tp2_hit:
        alerts.append(f"🎯 **{ticker} TP2 HIT!** at ${price:.2f} — take profit")
    elif tp1 and price >= tp1 and not tp1_hit:
        alerts.append(f"🎯 **{ticker} TP1 HIT!** at ${price:.2f} — consider exit or trail")

    if tp1 and tp1_hit and price < tp1 and not tp1_drop_alerted:
        alerts.append(f"⚠️ **{ticker} dropped back below TP1** (${tp1:.2f}) — now at ${price:.2f}")
    if tp2 and tp2_hit and price < tp2 and not tp2_drop_alerted:
        alerts.append(f"⚠️ **{ticker} dropped back below TP2** (${tp2:.2f}) — now at ${price:.2f}")

    if rsi > 75:
        alerts.append(f"🔥 **{ticker} Overbought** — RSI at {rsi:.1f}, consider trailing stop")
    elif rsi < 25:
        alerts.append(f"🥶 **{ticker} Oversold** — RSI at {rsi:.1f}, potential bounce")

    if day_change_pct < -4:
        alerts.append(f"📉 **{ticker} dropped** {day_change_pct:+.1f}% today — ${price:.2f}")
    elif day_change_pct > 8:
        alerts.append(f"🚀 **{ticker} surged** {day_change_pct:+.1f}% today — ${price:.2f}")

    new_ticker_state = {
        "stop": new_stop,
        "tp1_hit": tp1_hit or (tp1 is not None and price >= tp1),
        "tp2_hit": tp2_hit or (tp2 is not None and price >= tp2),
        "tp1_drop_alerted": tp1_drop_alerted or (tp1_hit and price < tp1),
        "tp2_drop_alerted": tp2_drop_alerted or (tp2_hit and price < tp2),
    }

    stop_label = f"**${current_stop:.2f}**"
    if stop_tightened:
        stop_label += " 🔒"

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
        "stop_tightened": stop_tightened
    }

    return summary, alerts if alerts else None, new_ticker_state

# ─── Report Builders ──────────────────────────────────────────────────────

def build_scheduled_report(results, time_str, label):
    lines = [f"{label} — {time_str}", ""]
    header = "Ticker | Price   | Entry   | P&L       | RSI  | Stop     | Status"
    sep = "───────┼─────────┼─────────┼───────────┼──────┼──────────┼───────"
    lines.append(header)
    lines.append(sep)

    total_pl = 0.0
    total_capital = 0.0

    for r in results:
        pl = r["unrealized"]
        pct = r["unrealized_pct"]
        total_pl += pl
        total_capital += r.get("capital", 0)

        if r.get("stop_tightened"):
            status = "🟡 trail"
        elif r["rsi"] > 75:
            status = "🔴 hot"
        elif r["price"] >= (r.get("tp1") or 9999):
            status = "🎯 tp1+"
        else:
            status = "🟢 hold"

        pl_str = format_dollar(pl)
        lines.append(
            f"{r['ticker']:5s} | ${r['price']:<5.2f} | ${r['entry']:<5.2f} | "
            f"{pl_str:>8s} | {r['rsi']:<4.1f} | {r['stop_label']:>8s} | {status}"
        )

    if total_capital > 0:
        cash = round(total_capital + total_pl - sum(p["shares"] * p["entry"] for p in results), 2)
        if cash > 0:
            lines.append(f"CASH   | —       | —       | {format_dollar(cash):>8s} | —    | —        | 💰")

    if total_capital > 0:
        portfolio_value = round(total_capital + total_pl, 2)
        lines.append("")
        lines.append(f"Portfolio: ${total_capital:.2f} → **${portfolio_value:.2f}** ({total_pl:+.2f})")

    return "\n".join(lines)

def build_alert_report(ticker, summary, alerts, time_str):
    lines = [f"⚠️ Alert — {ticker} — {time_str}", ""]
    lines.append(
        f"📊 **{summary['ticker']}** — ${summary['price']:.2f} "
        f"({summary['day_change']:+.2f}, {summary['day_change_pct']:+.1f}%)"
    )
    parts = [f"🎯 Entry: **${summary['entry']:.2f}**"]
    parts.append(f"Stop: {summary['stop_label']}")
    targets = []
    if summary.get("tp1"):
        targets.append(f"${summary['tp1']:.2f}")
    if summary.get("tp2"):
        targets.append(f"${summary['tp2']:.2f}")
    if targets:
        parts.append(f"Targets: {' / '.join(targets)}")
    lines.append(" | ".join(parts))
    lines.append(
        f"💰 P&L: {format_dollar(summary['unrealized'])} "
        f"({summary['unrealized_pct']:+.2f}%)"
    )
    lines.append(
        f"📈 RSI: {summary['rsi']:.1f} | "
        f"Range: ${summary['day_low']:.2f} – ${summary['day_high']:.2f} | "
        f"Vol: {summary['volume']:,}"
    )
    if alerts:
        lines.append("")
        lines.append("───")
        lines.extend(alerts)
    return "\n".join(lines)

# ─── Main ─────────────────────────────────────────────────────────────────

def main():
    now = datetime.now()
    time_key = now.strftime("%H:%M")
    time_str = now.strftime("%I:%M %p ET").lstrip("0")

    try:
        os.makedirs(SCRIPT_DIR, exist_ok=True)
        with open(TIMESTAMP_FILE, "w") as f:
            f.write(now.strftime("%A, %B %d, %Y  %I:%M:%S %p %Z"))
    except OSError:
        pass

    config = load_config()
    if config is None:
        return

    positions = config.get("positions", {})
    scheduled_times = config.get("scheduled_times", DEFAULT_CONFIG["scheduled_times"])
    scheduled_label_templates = {
        "09:45": "🔔 Opening Brief",
        "12:30": "📌 Midday Check",
        "16:00": "🏁 Closing Report",
    }
    is_scheduled = time_key in scheduled_times
    scheduled_label = scheduled_label_templates.get(time_key)
    if is_scheduled and not scheduled_label:
        scheduled_label = f"⏰ Scheduled Check — {time_str}"

    if not positions:
        if is_scheduled:
            print(f"{scheduled_label} — {time_str}\n\nNo positions configured.")
        return

    state = load_state()
    new_state = {}
    results = []
    all_alerts = {}

    for ticker, pos in sorted(positions.items()):
        result, alerts, ticker_state = check_ticker(ticker, pos, state, is_scheduled)
        if result is None:
            continue
        results.append(result)
        if ticker_state is not None:
            new_state[ticker] = ticker_state
        if alerts:
            all_alerts[ticker] = alerts

    if new_state:
        merged = state.copy()
        merged.update(new_state)
        save_state(merged)

    if not results:
        if is_scheduled:
            print(f"{scheduled_label} — {time_str}\n\n⚠️ Data unavailable — API errors")
        return

    any_alerts = bool(all_alerts)

    if is_scheduled:
        output = build_scheduled_report(results, time_str, scheduled_label)
        alert_lines = []
        for ticker in sorted(all_alerts.keys()):
            alert_lines.extend(all_alerts[ticker])
        if alert_lines:
            output += "\n\n───\n" + "\n".join(alert_lines)
    elif any_alerts:
        trigger_ticker = sorted(all_alerts.keys())[0]
        trigger_result = next((r for r in results if r["ticker"] == trigger_ticker), None)
        if trigger_result:
            output = build_alert_report(trigger_ticker, trigger_result, all_alerts[trigger_ticker], time_str)
        else:
            return
    else:
        return

    print(output)

if __name__ == "__main__":
    main()