#!/usr/bin/env python3
"""
Swing Trading Agent — DeepSeek V4 Flash + Twelve Data (800 calls/day free)
===========================================================================
Usage:  python src/swing_trader.py AAPL
        python src/swing_trader.py SPY MSFT TSLA  (up to 10)

Or from the repo root:
        python -m src.swing_trader AAPL
"""
import json, sys, time, math
from datetime import datetime
from urllib.request import Request, urlopen

try:
    import config
except ImportError:
    print("❌ config.py not found. Copy config.example.py to config.py and add your API keys.")
    sys.exit(1)

TD_KEY = config.TWELVE_DATA_KEY
OR_KEY = config.OPENROUTER_KEY
MODEL = config.MODEL

BASE = "https://api.twelvedata.com"
last_call = 0

def td_get(endpoint, params):
    global last_call
    e = time.time() - last_call
    if e < 0.2:
        time.sleep(0.2 - e)
    params["apikey"] = TD_KEY
    url = BASE + endpoint + "?" + "&".join(f"{k}={v}" for k, v in params.items())
    last_call = time.time()
    try:
        req = Request(url, headers={"User-Agent": "swing-trader/1.0"})
        with urlopen(req, timeout=15) as r:
            return json.loads(r.read().decode())
    except Exception as e:
        return {"error": str(e)}

def query_deepseek(prompt):
    if not OR_KEY:
        return "[ERROR: No OpenRouter key]"
    headers = {"Authorization": f"Bearer {OR_KEY}", "Content-Type": "application/json"}
    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": (
                "You are a professional swing trader (3 days to 3 weeks). "
                "Analyze decisively: BUY/SELL/HOLD/WAIT with "
                "confidence %, entry, stop, TP1/TP2, R/R ratio, "
                "technical reasoning, catalysts, and risks."
            )},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.3,
        "max_tokens": 1500,
    }
    try:
        req = Request("https://openrouter.ai/api/v1/chat/completions",
          data=json.dumps(payload).encode(), headers=headers, method="POST")
        with urlopen(req, timeout=60) as r:
            return json.loads(r.read().decode())["choices"][0]["message"]["content"]
    except Exception as e:
        return f"[OpenRouter error: {e}]"

def calc_indicators(values):
    c = [float(v["close"]) for v in values][::-1]
    h = [float(v["high"]) for v in values][::-1]
    l = [float(v["low"]) for v in values][::-1]
    vol = [int(v["volume"]) for v in values][::-1]
    n = len(c)
    if n < 50:
        return None, f"Need 50+ days, got {n}"

    sma20 = sum(c[-20:]) / 20
    sma50 = sum(c[-50:]) / 50
    cur = c[-1]

    gains = losses = 0.0
    for i in range(-14, 0):
        d = c[i] - c[i-1]
        if d > 0: gains += d
        else: losses -= d
    rsi = 100.0 if losses == 0 else 100 - 100 / (1 + gains/14 / (losses/14))

    ema12 = sum(c[-12:]) / 12
    ema26 = sum(c[-26:]) / 26
    for i in range(-12, 0):
        ema12 = c[i] * 2/13 + ema12 * 11/13
    for i in range(-26, 0):
        ema26 = c[i] * 2/27 + ema26 * 25/27

    r20 = c[-20:]
    m = sum(r20) / 20
    std = math.sqrt(sum((x-m)**2 for x in r20) / 20)

    return {
        "price": round(cur, 2), "sma20": round(sma20, 2), "sma50": round(sma50, 2),
        "rsi": round(rsi, 1), "macd": round(ema12 - ema26, 4),
        "bb_u": round(m + 2*std, 2), "bb_l": round(m - 2*std, 2),
        "avg_vol": int(sum(vol[-20:]) / 20),
        "above_sma20": cur > sma20, "above_sma50": cur > sma50,
        "high_52w": max(h[-252:]), "low_52w": min(l[-252:]),
    }, None

def analyze(ticker):
    print(f"\n{'─'*60}")
    print(f"  {ticker.upper()}  |  {datetime.now():%b %d, %Y %H:%M}  |  {MODEL}")
    print(f"{'─'*60}")

    q = td_get("/price", {"symbol": ticker})
    if "error" in q or "price" not in q:
        return 0
    cur = float(q["price"])
    prev = float(q.get("previous_close", 0))
    chg = round(cur - prev, 2)
    chg_pct = round(chg / prev * 100, 2) if prev else 0
    print(f"  ${cur} ({chg:+.2f}, {chg_pct:+.2f}%)")

    ts = td_get("/time_series", {"symbol": ticker, "interval": "1day", "outputsize": "100"})
    values = ts.get("values", [])
    if not values:
        print(f"  ❌ No time series data"); return 0
    ind, err = calc_indicators(values)
    if err:
        print(f"  ❌ {err}"); return 0

    rl = "oversold" if ind['rsi'] < 30 else "overbought" if ind['rsi'] > 70 else "neutral"
    sq = " ⚡ squeeze" if (ind['bb_u'] - ind['bb_l']) / ind['sma20'] < 0.05 else ""
    print(f"  SMA20:${ind['sma20']}  SMA50:${ind['sma50']}  RSI:{ind['rsi']}({rl}){sq}")
    print(f"  MACD:{ind['macd']}  BB:${ind['bb_l']}–${ind['bb_u']}  AvgVol:{ind['avg_vol']:,}")
    print(f"  52w: ${ind['low_52w']:.2f} – ${ind['high_52w']:.2f}")

    exch = ts.get("meta", {}).get("exchange", "US")
    lines = [
        f"## {ticker} ({exch})",
        f"Price: ${cur}  Change: {chg:+.2f} ({chg_pct:+.2f}%)",
        f"SMA20: ${ind['sma20']}  SMA50: ${ind['sma50']}",
        f"RSI: {ind['rsi']} ({rl})  MACD: {ind['macd']}",
        f"Bollinger: ${ind['bb_l']} – ${ind['bb_u']}  AvgVol: {ind['avg_vol']:,}",
        f"52w: ${ind['low_52w']:.2f} – ${ind['high_52w']:.2f}",
        "", "Signal? Confidence? Entry? Stop? TP1/TP2? R/R? Reasoning? Risks?"
    ]

    print(f"  DeepSeek analyzing...")
    print(f"\n{query_deepseek(chr(10).join(lines))}\n")
    return 2

if __name__ == "__main__":
    ts = [t.upper().strip() for t in sys.argv[1:10]] or ["AAPL"]
    u = sum(analyze(t) or 0 for t in ts)
    print(f"  Credits: {u}/800 used today")