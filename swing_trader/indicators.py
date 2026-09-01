"""
indicators.py — Technical indicators for swing trading.

All functions accept a list of Candle dicts (oldest-first) or, where noted,
a plain list of close prices.  Every function uses only stdlib math; numpy
is an optional fast-path for large arrays and is never required.

Candle format:  {"open": float, "high": float, "low": float,
                  "close": float, "volume": int}
IndicatorResult format is documented in ARCHITECTURE.md.
"""

from __future__ import annotations

import math
from typing import Any

# ---------------------------------------------------------------------------
# Optional numpy acceleration  (pure-stdlib fallback everywhere)
# ---------------------------------------------------------------------------
try:
    import numpy as np

    _HAS_NUMPY = True
except ImportError:
    np = None  # type: ignore[assignment]
    _HAS_NUMPY = False

# ---------------------------------------------------------------------------
# Internal helpers  —  extract arrays from candle dicts
# ---------------------------------------------------------------------------

def _prices(candles: list[dict[str, Any]]) -> list[float]:
    """Extract close prices from a list of Candle dicts."""
    return [c["close"] for c in candles]


def _highs(candles: list[dict[str, Any]]) -> list[float]:
    """Extract high prices from a list of Candle dicts."""
    return [c["high"] for c in candles]


def _lows(candles: list[dict[str, Any]]) -> list[float]:
    """Extract low prices from a list of Candle dicts."""
    return [c["low"] for c in candles]


def _to_prices(
    candles_or_prices: list[dict[str, Any]] | list[float],
) -> list[float]:
    """Normalise argument to a plain list of close prices."""
    if not candles_or_prices:
        return []
    # Duck-type: if the first element looks like a dict it's candles
    first = candles_or_prices[0]
    if isinstance(first, dict):
        return _prices(candles_or_prices)  # type: ignore[arg-type]
    return candles_or_prices  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Moving averages
# ---------------------------------------------------------------------------

def calc_sma(values: list[float], period: int) -> list[float]:
    """Simple moving average.

    Returns a list the same length as *values*; the first ``period - 1``
    positions are ``None``.
    """
    if period < 1 or not values:
        return [None] * len(values)  # type: ignore[list-item]

    out: list[float | None] = [None] * (period - 1)

    if _HAS_NUMPY and len(values) >= period:
        arr = np.array(values, dtype=float)
        cum = np.cumsum(arr)
        sma_vals = (cum[period - 1:] - np.concatenate([[0], cum[:-period]])) / period
        out.extend(sma_vals.tolist())
    else:
        window_sum = sum(values[:period])
        out.append(window_sum / period)
        for i in range(period, len(values)):
            window_sum += values[i] - values[i - period]
            out.append(window_sum / period)

    return out  # type: ignore[return-value]


def calc_ema(values: list[float], period: int) -> list[float]:
    """Exponential moving average.

    The seed value is the SMA of the first *period* values.  The multiplier
    (smoothing factor) is ``2 / (period + 1)``.

    Returns a list the same length as *values*; the first ``period - 1``
    positions are ``None``.
    """
    if period < 1 or not values:
        return [None] * len(values)  # type: ignore[list-item]

    out: list[float | None] = [None] * (period - 1)
    n = len(values)

    if n < period:
        return out  # type: ignore[return-value]

    multiplier = 2.0 / (period + 1.0)

    # Seed = SMA of first `period` values
    seed = sum(values[:period]) / period
    out.append(seed)

    ema = seed
    for i in range(period, n):
        ema = (values[i] - ema) * multiplier + ema
        out.append(ema)

    return out  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Relative Strength Index  (Wilder's RSI)
# ---------------------------------------------------------------------------

def calc_rsi(
    candles_or_prices: list[dict[str, Any]] | list[float],
    period: int = 14,
) -> float:
    """Single RSI value for the full series using Wilder's method."""
    prices = _to_prices(candles_or_prices)
    n = len(prices)
    if n < period + 1:
        return 0.0

    # First average gain / loss over the initial period
    gains, losses = 0.0, 0.0
    for i in range(1, period + 1):
        change = prices[i] - prices[i - 1]
        if change > 0:
            gains += change
        else:
            losses -= change  # keep losses positive

    avg_gain = gains / period
    avg_loss = losses / period

    # Wilder smoothed RSI from there
    for i in range(period + 1, n):
        change = prices[i] - prices[i - 1]
        gain = change if change > 0 else 0.0
        loss = -change if change < 0 else 0.0
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period

    if avg_loss == 0.0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100.0 - 100.0 / (1.0 + rs), 4)


# ---------------------------------------------------------------------------
# MACD  (Moving Average Convergence / Divergence)
# ---------------------------------------------------------------------------

def calc_macd(
    candles_or_prices: list[dict[str, Any]] | list[float],
) -> dict[str, float]:
    """MACD indicator.

    Returns ``{macd, signal, histogram}`` — the **last** values of each
    series.  *macd* = EMA12 − EMA26, *signal* = EMA9 of *macd*,
    *histogram* = macd − signal.
    """
    prices = _to_prices(candles_or_prices)
    n = len(prices)
    if n < 33:  # need at least 33 data points for EMA26 + EMA9
        return {"macd": 0.0, "signal": 0.0, "histogram": 0.0}

    # Fast EMA12  and  Slow EMA26  (both None-padded)
    ema12 = _ema_values(prices, 12)
    ema26 = _ema_values(prices, 26)

    # MACD line = EMA12 − EMA26  (only where both are valid)
    macd_line: list[float] = []
    for a, b in zip(ema12, ema26):
        if a is not None and b is not None:
            macd_line.append(a - b)

    # Signal = EMA9 of the MACD line
    if len(macd_line) >= 9:
        signal_series = _ema_values(macd_line, 9)
        last_signal = signal_series[-1] if signal_series[-1] is not None else 0.0
    else:
        last_signal = 0.0

    last_macd = macd_line[-1] if macd_line else 0.0
    last_hist = last_macd - last_signal

    return {"macd": round(last_macd, 4),
            "signal": round(last_signal, 4),
            "histogram": round(last_hist, 4)}


def _ema_values(values: list[float], period: int) -> list[float | None]:
    """Private helper: returns same-length EMA array (None-padded)."""
    return calc_ema(values, period)


# ---------------------------------------------------------------------------
# Bollinger Bands
# ---------------------------------------------------------------------------

def calc_bb(
    candles_or_prices: list[dict[str, Any]] | list[float],
    period: int = 20,
    std_dev: float = 2.0,
) -> dict[str, float]:
    """Bollinger Bands.

    Returns ``{upper, middle, lower}`` — the **last** values.
    Middle is the SMA over *period*; upper/lower add/subtract
    ``std_dev * population-std`` of the same window.
    """
    prices = _to_prices(candles_or_prices)
    n = len(prices)
    if n < period:
        return {"upper": 0.0, "middle": 0.0, "lower": 0.0}

    window = prices[-period:]

    if _HAS_NUMPY:
        arr = np.array(window, dtype=float)
        middle = float(arr.mean())
        std = float(arr.std(ddof=0))
    else:
        total = sum(window)
        middle = total / period
        variance = sum((x - middle) ** 2 for x in window) / period
        std = math.sqrt(variance)

    upper = middle + std_dev * std
    lower = middle - std_dev * std

    return {"upper": round(upper, 4),
            "middle": round(middle, 4),
            "lower": round(max(lower, 0.0), 4)}


# ---------------------------------------------------------------------------
# Average True Range  (ATR)  — candles only
# ---------------------------------------------------------------------------

def calc_atr(
    candles: list[dict[str, Any]],
    period: int = 14,
) -> float:
    """Average True Range using Wilder's EMA smoothing.

    True Range = max(high − low, abs(high − prev_close), abs(low − prev_close)).
    ATR is the EMA (Wilder style) of TR over *period*.
    """
    n = len(candles)
    if n < period + 1:
        return 0.0

    # True Range values
    tr_values: list[float] = []
    for i in range(1, n):
        h, lv, prev_c = candles[i]["high"], candles[i]["low"], candles[i - 1]["close"]
        tr = max(h - lv, abs(h - prev_c), abs(lv - prev_c))
        tr_values.append(tr)

    # Wilder's EMA of TR
    atr = sum(tr_values[:period]) / period
    for i in range(period, len(tr_values)):
        atr = (atr * (period - 1) + tr_values[i]) / period

    return round(atr, 4)


# ---------------------------------------------------------------------------
# Average Directional Index  (ADX)  — candles only
# ---------------------------------------------------------------------------

def calc_adx(
    candles: list[dict[str, Any]],
    period: int = 14,
) -> float:
    """Average Directional Index (Wilder).

    Standard ADX pipeline:
      1. +DM, −DM, TR for each bar
      2. Wilder-smoothed +DM, −DM, TR over *period*
      3. +DI = 100 × +DM_smooth / TR_smooth
      4. −DI = 100 × −DM_smooth / TR_smooth
      5. DX = 100 × |+DI − −DI| / (+DI + −DI)
      6. ADX = SMA of DX over *period*
    """
    n = len(candles)
    if n < 2 * period:
        return 0.0

    plus_dm: list[float] = [0.0]
    minus_dm: list[float] = [0.0]
    tr_list: list[float] = []

    for i in range(1, n):
        h, lv, prev_h, prev_l, prev_c = (
            candles[i]["high"],
            candles[i]["low"],
            candles[i - 1]["high"],
            candles[i - 1]["low"],
            candles[i - 1]["close"],
        )

        up_move = h - prev_h
        down_move = prev_l - lv

        if up_move > down_move and up_move > 0:
            plus_dm.append(up_move)
        else:
            plus_dm.append(0.0)

        if down_move > up_move and down_move > 0:
            minus_dm.append(down_move)
        else:
            minus_dm.append(0.0)

        tr = max(h - lv, abs(h - prev_c), abs(lv - prev_c))
        tr_list.append(tr)

    # --- Wilder smooth +DM, −DM, TR ---
    def _wilder_smooth(raw: list[float], p: int) -> list[float]:
        out = [0.0] * (p - 1) + [sum(raw[:p])]
        val = out[-1]
        for v in raw[p:]:
            val = (val * (p - 1) + v) / p
            out.append(val)
        return out  # same length as raw (padded front)

    # tr_list has n-1 values; +dm and -dm have n values (first is 0).
    # Align: drop first element of +dm/-dm so all start from same bar.
    pdm_smooth = _wilder_smooth(plus_dm[1:], period)
    mdm_smooth = _wilder_smooth(minus_dm[1:], period)
    tr_smooth = _wilder_smooth(tr_list, period)

    # --- Directional Indicators ---
    dx_list: list[float] = []
    for i in range(len(pdm_smooth)):
        if tr_smooth[i] == 0:
            dx_list.append(0.0)
            continue
        pdi = 100.0 * pdm_smooth[i] / tr_smooth[i]
        ndi = 100.0 * mdm_smooth[i] / tr_smooth[i]
        if pdi + ndi == 0:
            dx_list.append(0.0)
        else:
            dx_list.append(100.0 * abs(pdi - ndi) / (pdi + ndi))

    # ADX = SMA of DX over *period*
    if len(dx_list) < period:
        return 0.0
    adx = sum(dx_list[-period:]) / period
    return round(adx, 4)


# ---------------------------------------------------------------------------
# On-Balance Volume  (OBV)  — candles only
# ---------------------------------------------------------------------------

def calc_obv(candles: list[dict[str, Any]]) -> int:
    """On-Balance Volume.

    OBV starts at 0.  If ``close > prev_close`` → OBV += volume,
    if ``close < prev_close`` → OBV −= volume, else unchanged.
    """
    if not candles:
        return 0

    obv = 0
    for i in range(1, len(candles)):
        close = candles[i]["close"]
        prev_close = candles[i - 1]["close"]
        vol = candles[i]["volume"]
        if close > prev_close:
            obv += vol
        elif close < prev_close:
            obv -= vol
    return obv


# ---------------------------------------------------------------------------
# Volume-Weighted Average Price  (VWAP)  — candles only
# ---------------------------------------------------------------------------

def calc_vwap(candles: list[dict[str, Any]]) -> float:
    """Volume-Weighted Average Price.

    VWAP = Σ(typical_price × volume) / Σ(volume)
    Typical price = (high + low + close) / 3.

    For daily candles this is the full-series VWAP; for intraday candles
    it is the rolling VWAP over the period provided.
    """
    if not candles:
        return 0.0

    total_pv = 0.0
    total_vol = 0
    for c in candles:
        tp = (c["high"] + c["low"] + c["close"]) / 3.0
        vol = c["volume"]
        total_pv += tp * vol
        total_vol += vol

    if total_vol == 0:
        return 0.0
    return round(total_pv / total_vol, 4)


# ---------------------------------------------------------------------------
# calc_all  —  main entry point
# ---------------------------------------------------------------------------

def calc_all(candles: list[dict[str, Any]]) -> dict[str, float | int]:
    """Compute every indicator and return a complete ``IndicatorResult`` dict.

    Handles fewer than 50 candles gracefully — returns 0.0 for values it
    cannot compute and populates only what the data supports.
    """
    n = len(candles)

    # --- Always available ---
    obv = calc_obv(candles)
    vwap = calc_vwap(candles)

    # --- MAs & EMA ---
    prices = _prices(candles)

    sma20 = _last_sma(prices, 20)
    sma50 = _last_sma(prices, 50)
    sma200 = _last_sma(prices, 200)
    ema12 = _last_ema(prices, 12)
    ema26 = _last_ema(prices, 26)

    # --- RSI ---
    rsi = calc_rsi(prices, 14) if n >= 14 else 0.0

    # --- MACD ---
    if n >= 33:
        macd_result = calc_macd(prices)
    else:
        macd_result = {"macd": 0.0, "signal": 0.0, "histogram": 0.0}

    # --- Bollinger Bands ---
    bb = calc_bb(prices, 20, 2.0) if n >= 20 else {"upper": 0.0, "middle": 0.0, "lower": 0.0}

    # --- ATR ---
    atr14 = calc_atr(candles, 14) if n >= 14 else 0.0

    # --- ADX ---
    adx = calc_adx(candles, 14) if n >= 28 else 0.0

    return {
        "sma20": sma20,
        "sma50": sma50,
        "sma200": sma200,
        "rsi": rsi,
        "macd": macd_result["macd"],
        "macd_signal": macd_result["signal"],
        "macd_hist": macd_result["histogram"],
        "bb_u": bb["upper"],
        "bb_l": bb["lower"],
        "bb_m": bb["middle"],
        "atr14": atr14,
        "adx": adx,
        "obv": obv,
        "vwap": vwap,
        "ema12": ema12,
        "ema26": ema26,
    }


# ---------------------------------------------------------------------------
# Helpers for calc_all  —  grab last value from SMA/EMA series
# ---------------------------------------------------------------------------

def _last_sma(values: list[float], period: int) -> float:
    """Compute SMA over *period* and return the last value (or 0.0)."""
    if len(values) < period:
        return 0.0
    series = calc_sma(values, period)
    last = series[-1]
    return round(last, 4) if last is not None else 0.0


def _last_ema(values: list[float], period: int) -> float:
    """Compute EMA over *period* and return the last value (or 0.0)."""
    if len(values) < period:
        return 0.0
    series = calc_ema(values, period)
    last = series[-1]
    return round(last, 4) if last is not None else 0.0