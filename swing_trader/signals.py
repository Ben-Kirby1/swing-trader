"""
Agent 4: Signals Engine

Four discrete trading strategies + ensemble voting + optional LLM narrative.
Each strategy returns a Signal dict or None if conditions are not met.
"""

from __future__ import annotations

import json
import urllib.request
import urllib.error
from typing import Any

from . import config
from .indicators import (
    calc_sma,
    calc_ema,
    calc_rsi,
    calc_macd,
    calc_bb,
    calc_atr,
    calc_adx,
    calc_obv,
)

# ---------------------------------------------------------------------------
# Type aliases (duck-typed dicts per architecture spec)
# ---------------------------------------------------------------------------

Candle = dict[str, float | int]  # {open, high, low, close, volume}
IndicatorResult = dict[str, float | int]
Signal = dict[str, Any]  # see ARCHITECTURE.md for full schema

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

STRATEGY_WEIGHTS: dict[str, float] = {
    "trend_follow": 0.30,
    "mean_reversion": 0.25,
    "breakout": 0.25,
    "momentum": 0.20,
}


def _obv_trend(candles: list[Candle], lookback: int = 5) -> int:
    """Return +1 if OBV is rising over *lookback* candles, -1 if falling, 0 flat."""
    if len(candles) < lookback + 1:
        return 0
    recent = candles[-(lookback + 1):]
    vals: list[int] = []
    obv = 0
    for i in range(1, len(recent)):
        c_prev = recent[i - 1]
        c_cur = recent[i]
        volume = int(c_cur["volume"])
        if c_cur["close"] > c_prev["close"]:
            obv += volume
        elif c_cur["close"] < c_prev["close"]:
            obv -= volume
        vals.append(obv)
    # Check if strictly increasing
    rising = all(vals[i] < vals[i + 1] for i in range(len(vals) - 1))
    falling = all(vals[i] > vals[i + 1] for i in range(len(vals) - 1))
    if rising:
        return 1
    if falling:
        return -1
    return 0


def _volume_ratio(candles: list[Candle], period: int = 20) -> float:
    """Ratio of latest volume to average volume over *period* candles."""
    if len(candles) < period + 1:
        return 1.0
    recent = candles[-(period + 1):-1]
    avg_vol = sum(c["volume"] for c in recent) / period
    if avg_vol == 0:
        return 1.0
    return candles[-1]["volume"] / avg_vol


def _rsi_turning_up(candles: list[Candle], period: int = 14) -> bool:
    """True if RSI was below 25 and is now rising (computed from raw candles)."""
    if len(candles) < period + 2:
        return False
    # Compute last two RSI values
    rsi_now = calc_rsi(candles, period)
    rsi_prev = calc_rsi(candles[:-1], period)
    return rsi_prev < 25 and rsi_now > rsi_prev


def _macd_hist_rising(candles: list[Candle], periods: int = 3) -> bool:
    """True if MACD histogram has been rising for *periods* consecutive candles."""
    if len(candles) < periods + 2:
        return False
    vals: list[float] = []
    for i in range(len(candles) - periods, len(candles) + 1):
        sub = candles[:i] if i < len(candles) else candles
        macd = calc_macd(sub)
        vals.append(macd["macd"] - macd["signal"])
    if len(vals) < periods:
        return False
    return all(vals[i] < vals[i + 1] for i in range(len(vals) - periods, len(vals) - 1))


def _macd_hist_falling(candles: list[Candle], periods: int = 3) -> bool:
    """True if MACD histogram has been falling for *periods* consecutive candles."""
    if len(candles) < periods + 2:
        return False
    vals: list[float] = []
    for i in range(len(candles) - periods, len(candles) + 1):
        sub = candles[:i] if i < len(candles) else candles
        macd = calc_macd(sub)
        vals.append(macd["macd"] - macd["signal"])
    if len(vals) < periods:
        return False
    return all(vals[i] > vals[i + 1] for i in range(len(vals) - periods, len(vals) - 1))


def _make_signal(
    ticker: str,
    direction: str,
    confidence: float,
    entry: float,
    stop: float,
    tp1: float,
    tp2: float,
    reason: str,
    price: float,
    strategies: list[str],
) -> Signal:
    """Build a canonical Signal dict with R/R computed."""
    risk = abs(entry - stop)
    rr = round(abs(tp1 - entry) / risk, 2) if risk > 0 else 0.0
    return {
        "ticker": ticker,
        "direction": direction,
        "confidence": round(min(confidence, 1.0), 3),
        "entry": round(entry, 2),
        "stop": round(stop, 2),
        "tp1": round(tp1, 2),
        "tp2": round(tp2, 2),
        "rr": rr,
        "reason": reason,
        "strategies": strategies,
        "price": round(price, 2),
    }


# ---------------------------------------------------------------------------
# Strategy 1: Trend Follow
# ---------------------------------------------------------------------------

def trend_follow(ticker: str, candles: list[Candle], ind: IndicatorResult) -> Signal | None:
    """Bullish trend following: EMA12 > EMA26, price > SMA50, ADX > 25."""
    price = candles[-1]["close"]
    ema12 = ind.get("ema12", 0)
    ema26 = ind.get("ema26", 0)
    sma50 = ind.get("sma50", 0)
    adx = ind.get("adx", 0)
    atr = ind.get("atr14", 0)

    if not (ema12 > ema26 and price > sma50 and adx > 25):
        return None

    confidence = 0.6
    reasons = []

    # OBV trend (last 5 candles)
    if _obv_trend(candles, 5) == 1:
        confidence += 0.1
        reasons.append("OBV rising")

    # Volume above average
    vol_ratio = _volume_ratio(candles)
    if vol_ratio > 1.0:
        confidence += 0.1
        reasons.append("volume > avg")

    # Strong trend
    if adx > 30:
        confidence += 0.1
        reasons.append("ADX>30 (strong trend)")

    entry = price
    stop = price - atr * 2
    tp1 = entry + atr * 3
    tp2 = entry + atr * 5

    reason = "Trend follow: EMA12({})>EMA26({}), price({})>SMA50({}), ADX({})>25".format(
        round(ema12, 2), round(ema26, 2), round(price, 2), round(sma50, 2), round(adx, 1)
    )
    if reasons:
        reason += " | " + ", ".join(reasons)

    return _make_signal(ticker, "BUY", confidence, entry, stop, tp1, tp2, reason, price, ["trend_follow"])


# ---------------------------------------------------------------------------
# Strategy 2: Mean Reversion
# ---------------------------------------------------------------------------

def mean_reversion(ticker: str, candles: list[Candle], ind: IndicatorResult) -> Signal | None:
    """Oversold bounce (BUY) or overbought pullback (SELL)."""
    price = candles[-1]["close"]
    rsi = ind.get("rsi", 50)
    bb_lower = ind.get("bb_l", 0)
    bb_upper = ind.get("bb_u", 0)
    bb_middle = ind.get("bb_m", 0)
    atr = ind.get("atr14", 0)

    direction: str | None = None

    # Oversold bounce — BUY
    if rsi < 30 and price < bb_lower:
        direction = "BUY"
    # Overbought pullback — SELL
    elif rsi > 70 and price > bb_upper:
        direction = "SELL"
    else:
        return None

    confidence = 0.5
    reasons = []

    # RSI turning up from below 25 (oversold bounce extra confirmation)
    if direction == "BUY" and _rsi_turning_up(candles):
        confidence += 0.15
        reasons.append("RSI turning up from <25")

    # Volume spike
    vol_ratio = _volume_ratio(candles)
    if vol_ratio > 1.5:
        confidence += 0.1
        reasons.append("volume spike")

    # Price at 52-week low support — approximated by checking if price is near
    # the lowest close in the available candle range
    if len(candles) >= 10:
        lows = [c["low"] for c in candles]
        min_low = min(lows)
        max_low = max(lows)
        if 0 < (price - min_low) / (max_low - min_low) < 0.15:
            confidence += 0.1
            reasons.append("price near support (52w low)")

    entry = price

    if direction == "BUY":
        stop = price - atr * 1.5
        tp1 = bb_middle
        tp2 = bb_upper
    else:
        stop = price + atr * 1.5
        tp1 = bb_middle
        tp2 = bb_lower

    reason = "Mean reversion {}: RSI({}), price({}) {}".format(
        direction, round(rsi, 1), round(price, 2),
        "<BB_lower({})".format(round(bb_lower, 2)) if direction == "BUY"
        else ">BB_upper({})".format(round(bb_upper, 2)),
    )
    if reasons:
        reason += " | " + ", ".join(reasons)

    return _make_signal(ticker, direction, confidence, entry, stop, tp1, tp2, reason, price, ["mean_reversion"])


# ---------------------------------------------------------------------------
# Strategy 3: Breakout
# ---------------------------------------------------------------------------

def breakout(ticker: str, candles: list[Candle], ind: IndicatorResult) -> Signal | None:
    """Upside breakout or downside breakdown based on SMA20 + 2*ATR."""
    price = candles[-1]["close"]
    sma20 = ind.get("sma20", 0)
    sma50 = ind.get("sma50", 0)
    atr = ind.get("atr14", 0)
    adx = ind.get("adx", 0)

    if sma20 == 0 or atr == 0:
        return None

    vol_ratio = _volume_ratio(candles)
    obv_trend = _obv_trend(candles, 5)

    direction: str | None = None
    breakout_level: float = 0

    # Upside breakout
    if price > sma20 + 2 * atr and vol_ratio > 1.5:
        direction = "BUY"
        breakout_level = sma20 + 2 * atr
    # Downside breakdown
    elif price < sma20 - 2 * atr and vol_ratio > 1.5:
        direction = "SELL"
        breakout_level = sma20 - 2 * atr
    else:
        return None

    confidence = 0.55
    reasons = []

    # Price above SMA50 (bullish filter for upside)
    if direction == "BUY" and price > sma50:
        confidence += 0.15
        reasons.append("price > SMA50")
    # For SELL, price below SMA50 adds confidence
    if direction == "SELL" and price < sma50:
        confidence += 0.15
        reasons.append("price < SMA50")

    # ADX confirms trend
    if adx > 25:
        confidence += 0.1
        reasons.append("ADX>25 confirming trend")

    # OBV rising (bullish volume confirmation)
    if direction == "BUY" and obv_trend == 1:
        confidence += 0.1
        reasons.append("OBV rising")
    if direction == "SELL" and obv_trend == -1:
        confidence += 0.1
        reasons.append("OBV falling")

    entry = price

    if direction == "BUY":
        stop = breakout_level - atr
        tp1 = entry + atr * 3
        tp2 = entry + atr * 6
    else:
        stop = breakout_level + atr
        tp1 = entry - atr * 3
        tp2 = entry - atr * 6

    reason = "Breakout {}: price({}) {} SMA20{}2ATR({}), volume {:.1f}x avg".format(
        direction,
        round(price, 2),
        ">" if direction == "BUY" else "<",
        "+" if direction == "BUY" else "-",
        round(breakout_level, 2),
        vol_ratio,
    )
    if reasons:
        reason += " | " + ", ".join(reasons)

    return _make_signal(ticker, direction, confidence, entry, stop, tp1, tp2, reason, price, ["breakout"])


# ---------------------------------------------------------------------------
# Strategy 4: Momentum
# ---------------------------------------------------------------------------

def momentum(ticker: str, candles: list[Candle], ind: IndicatorResult) -> Signal | None:
    """Momentum: MACD histogram direction + RSI in range + OBV trend."""
    price = candles[-1]["close"]
    rsi = ind.get("rsi", 50)
    atr = ind.get("atr14", 0)
    adx = ind.get("adx", 0)
    bb_upper = ind.get("bb_u", 0)
    bb_lower = ind.get("bb_l", 0)

    if len(candles) < 10:
        return None

    macd = calc_macd(candles)
    macd_hist = macd["macd"] - macd["signal"]
    obv_trend = _obv_trend(candles, 5)

    direction: str | None = None

    # Bullish momentum
    if macd_hist > 0 and _macd_hist_rising(candles, 3) and 40 <= rsi <= 60 and obv_trend == 1:
        direction = "BUY"
    # Bearish momentum
    elif macd_hist < 0 and _macd_hist_falling(candles, 3) and 40 <= rsi <= 60 and obv_trend == -1:
        direction = "SELL"
    else:
        return None

    confidence = 0.5
    reasons = []

    # ADX > 20 (trend present)
    if adx > 20:
        confidence += 0.15
        reasons.append("ADX>20")

    # Volume rising
    vol_ratio = _volume_ratio(candles)
    if vol_ratio > 1.0:
        confidence += 0.1
        reasons.append("volume rising")

    # BB width expanding (volatility increasing)
    bb_width = bb_upper - bb_lower if bb_upper and bb_lower else 0
    if bb_width > 0:
        # Compute historical average BB width to see if it's expanding
        past_bbs = [calc_bb(candles[:i], 20, 2) for i in range(len(candles) - 5, len(candles) + 1) if i >= 21]
        if past_bbs:
            recent_width = bb_width
            avg_width = sum(b["upper"] - b["lower"] for b in past_bbs) / len(past_bbs)
            if recent_width > avg_width * 1.1:
                confidence += 0.1
                reasons.append("BB width expanding (volatility↑)")

    entry = price

    if direction == "BUY":
        stop = price - atr * 2
        tp1 = entry + atr * 3
        tp2 = entry + atr * 5
    else:
        stop = price + atr * 2
        tp1 = entry - atr * 3
        tp2 = entry - atr * 5

    reason = "Momentum {}: MACD hist({}), RSI({}), OBV trend {}".format(
        direction,
        round(macd_hist, 4),
        round(rsi, 1),
        "up" if direction == "BUY" else "down",
    )
    if reasons:
        reason += " | " + ", ".join(reasons)

    return _make_signal(ticker, direction, confidence, entry, stop, tp1, tp2, reason, price, ["momentum"])


# ---------------------------------------------------------------------------
# Ensemble
# ---------------------------------------------------------------------------

def ensemble(signals: list[Signal]) -> Signal:
    """Weighted ensemble of triggered signals.

    Rules:
      - Need ≥2 strategies agreeing on direction OR one with confidence > 0.7
      - Entry/Stop/TP: confidence-weighted average
      - R/R calculated from averaged values
    """
    if not signals:
        return {
            "ticker": "",
            "direction": "HOLD",
            "confidence": 0.0,
            "entry": 0.0,
            "stop": 0.0,
            "tp1": 0.0,
            "tp2": 0.0,
            "rr": 0.0,
            "reason": "No strategy triggered",
            "strategies": [],
            "price": 0.0,
        }

    # Aggregate confidence-weighted values per direction
    buy_weighted: dict[str, float] = {"confidence": 0, "entry": 0, "stop": 0, "tp1": 0, "tp2": 0, "total_w": 0}
    sell_weighted: dict[str, float] = {"confidence": 0, "entry": 0, "stop": 0, "tp1": 0, "tp2": 0, "total_w": 0}

    buy_count = 0
    sell_count = 0
    all_reasons: list[str] = []
    all_strategies: list[str] = []

    for s in signals:
        strat_name = s["strategies"][0] if s["strategies"] else "unknown"
        weight = STRATEGY_WEIGHTS.get(strat_name, 0.20)

        # The effective weight for averaging is weight * confidence (so higher
        # confidence strategies carry more influence)
        eff = weight * s["confidence"]

        all_reasons.append(s["reason"])
        if strat_name not in all_strategies:
            all_strategies.append(strat_name)

        if s["direction"] == "BUY":
            buy_weighted["confidence"] += weight * s["confidence"]
            buy_weighted["entry"] += eff * s["entry"]
            buy_weighted["stop"] += eff * s["stop"]
            buy_weighted["tp1"] += eff * s["tp1"]
            buy_weighted["tp2"] += eff * s["tp2"]
            buy_weighted["total_w"] += eff
            buy_count += 1
        elif s["direction"] == "SELL":
            sell_weighted["confidence"] += weight * s["confidence"]
            sell_weighted["entry"] += eff * s["entry"]
            sell_weighted["stop"] += eff * s["stop"]
            sell_weighted["tp1"] += eff * s["tp1"]
            sell_weighted["tp2"] += eff * s["tp2"]
            sell_weighted["total_w"] += eff
            sell_count += 1

    ticker = signals[0]["ticker"]
    price = signals[0]["price"]

    # Determine winning direction
    buy_confidence = buy_weighted["confidence"]
    sell_confidence = sell_weighted["confidence"]

    # Check agreement rule
    if buy_confidence > sell_confidence:
        if buy_count < 2 and buy_confidence <= 0.7:
            return _make_signal(
                ticker, "WAIT", 0.0, price, price, price, price,
                "Insufficient agreement: {} BUY strategies, confidence {:.2f}".format(buy_count, buy_confidence),
                price, all_strategies,
            )
        w = buy_weighted
        direction = "BUY"
        final_conf = buy_confidence
    else:
        if sell_count < 2 and sell_confidence <= 0.7:
            return _make_signal(
                ticker, "WAIT", 0.0, price, price, price, price,
                "Insufficient agreement: {} SELL strategies, confidence {:.2f}".format(sell_count, sell_confidence),
                price, all_strategies,
            )
        w = sell_weighted
        direction = "SELL"
        final_conf = sell_confidence

    tw = w["total_w"]
    if tw > 0:
        entry = w["entry"] / tw
        stop = w["stop"] / tw
        tp1 = w["tp1"] / tw
        tp2 = w["tp2"] / tw
    else:
        # Fallback: unweighted average
        matching = [s for s in signals if s["direction"] == direction]
        if matching:
            entry = sum(s["entry"] for s in matching) / len(matching)
            stop = sum(s["stop"] for s in matching) / len(matching)
            tp1 = sum(s["tp1"] for s in matching) / len(matching)
            tp2 = sum(s["tp2"] for s in matching) / len(matching)
        else:
            entry = stop = tp1 = tp2 = price

    reason = " | ".join(all_reasons)

    return _make_signal(
        ticker, direction, final_conf, entry, stop, tp1, tp2, reason, price, all_strategies,
    )


# ---------------------------------------------------------------------------
# Signal Generator (entry point)
# ---------------------------------------------------------------------------

def generate_signal(ticker: str, candles: list[Candle], ind: IndicatorResult, price: float) -> Signal:
    """Run all 4 strategies and ensemble the results.

    Returns a HOLD signal if no strategy fires.
    """
    strategies = [
        trend_follow,
        mean_reversion,
        breakout,
        momentum,
    ]

    triggered: list[Signal] = []
    for fn in strategies:
        sig = fn(ticker, candles, ind)
        if sig is not None:
            triggered.append(sig)

    if not triggered:
        return {
            "ticker": ticker,
            "direction": "HOLD",
            "confidence": 0.0,
            "entry": price,
            "stop": price,
            "tp1": price,
            "tp2": price,
            "rr": 0.0,
            "reason": "No strategy triggered",
            "strategies": [],
            "price": price,
        }

    return ensemble(triggered)


# ---------------------------------------------------------------------------
# LLM Narrative (OpenRouter)
# ---------------------------------------------------------------------------

def llm_narrative(ticker: str, price: float, ind: IndicatorResult, signal: Signal) -> str:
    """Request a 2-3 sentence narrative overlay from an LLM via OpenRouter.

    Returns empty string if OPENROUTER_KEY is not set or on any error.
    """
    or_key = config.OPENROUTER_KEY
    model = config.MODEL

    if not or_key or or_key == "sk-or-...2a4b" or or_key.startswith("sk-or-"):
        # Key is placeholder or empty — skip
        return ""

    # Build a compact indicator summary
    lines = [
        "## Indicator Summary",
        f"- Price: ${price:.2f}",
        f"- SMA20: ${ind.get('sma20', 0):.2f}",
        f"- SMA50: ${ind.get('sma50', 0):.2f}",
        f"- SMA200: ${ind.get('sma200', 0):.2f}",
        f"- EMA12: ${ind.get('ema12', 0):.2f}",
        f"- EMA26: ${ind.get('ema26', 0):.2f}",
        f"- RSI(14): {ind.get('rsi', 0):.1f}",
        f"- MACD: {ind.get('macd', 0):.4f}",
        f"- MACD Signal: {ind.get('macd_signal', 0):.4f}",
        f"- MACD Hist: {ind.get('macd_hist', 0):.4f}",
        f"- BB Upper: ${ind.get('bb_u', 0):.2f}",
        f"- BB Middle: ${ind.get('bb_m', 0):.2f}",
        f"- BB Lower: ${ind.get('bb_l', 0):.2f}",
        f"- ATR(14): ${ind.get('atr14', 0):.2f}",
        f"- ADX(14): {ind.get('adx', 0):.1f}",
        f"- OBV: {ind.get('obv', 0)}",
        f"- VWAP: ${ind.get('vwap', 0):.2f}",
        "",
        "## Generated Signal",
        f"- Direction: {signal['direction']}",
        f"- Confidence: {signal['confidence']:.2f}",
        f"- Entry: ${signal['entry']:.2f}",
        f"- Stop: ${signal['stop']:.2f}",
        f"- TP1: ${signal['tp1']:.2f}",
        f"- TP2: ${signal['tp2']:.2f}",
        f"- R/R: {signal['rr']:.2f}",
        f"- Reason: {signal['reason']}",
        f"- Strategies: {', '.join(signal['strategies'])}",
    ]
    prompt_text = "\n".join(lines)

    payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are a swing trading analyst. Provide a 2-3 sentence narrative "
                    "overlay for this technical setup. Be specific: name levels, catalysts, risks."
                ),
            },
            {
                "role": "user",
                "content": f"Ticker: {ticker}\n\n{prompt_text}",
            },
        ],
        "temperature": 0.3,
        "max_tokens": 200,
    }

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions",
        data=data,
        headers={
            "Authorization": f"Bearer {or_key}",
            "Content-Type": "application/json",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            narrative: str = result["choices"][0]["message"]["content"].strip()
            return narrative
    except (urllib.error.URLError, json.JSONDecodeError, KeyError, IndexError, OSError):
        return ""