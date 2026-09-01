"""Swing Trader v2 — Data Layer.

Stdlib-only Twelve Data API client with rate limiting and local caching.
"""

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from swing_trader.config import (
    CACHE_DIR,
    TWELVE_DATA_KEY as TD_KEY,
)

# ── Rate limiter ────────────────────────────────────────────────────────────────

_last_call_time: float = 0.0
RATE_LIMIT: float = 7.5

_BASE_URL = "https://api.twelvedata.com"


def _rate_limit() -> None:
    """Sleep if needed to honour the 7.5 s minimum spacing between calls."""
    global _last_call_time
    elapsed = time.time() - _last_call_time
    if elapsed < RATE_LIMIT:
        time.sleep(RATE_LIMIT - elapsed)
    _last_call_time = time.time()


# ── Low-level HTTP helper ───────────────────────────────────────────────────────


def _td_get(endpoint: str, params: dict) -> dict:
    """Build the URL, apply rate limiting, fetch and parse JSON.

    Returns the parsed JSON dict on success or ``{"error": str(e)}`` on failure.
    Catches network errors, HTTP errors, timeouts, and malformed JSON.
    """
    url = f"{_BASE_URL}/{endpoint.lstrip('/')}"
    params_copy = dict(params)
    params_copy.setdefault("apikey", TD_KEY)
    query = urllib.parse.urlencode(params_copy)
    full_url = f"{url}?{query}"

    _rate_limit()
    try:
        with urllib.request.urlopen(full_url, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        # Twelve Data may return a JSON error body
        if isinstance(data, dict) and data.get("status") == "error":
            return {"error": data.get("message", "Unknown Twelve Data error")}
        return data
    except (urllib.error.URLError, urllib.error.HTTPError, OSError,
            json.JSONDecodeError, ValueError) as e:
        return {"error": str(e)}


# ── Public API ──────────────────────────────────────────────────────────────────


def get_price(ticker: str) -> float | None:
    """Fetch the current price of *ticker* via the ``/price`` endpoint.

    Returns the price as a float, or ``None`` on any error.
    """
    data = _td_get("price", {"symbol": ticker.upper()})
    if "error" in data:
        return None
    raw = data.get("price")
    if raw is None:
        return None
    try:
        return float(raw)
    except (ValueError, TypeError):
        return None


def get_quote(ticker: str) -> dict:
    """Fetch a full quote for *ticker* via the ``/quote`` endpoint.

    Returns a dict with keys ``price``, ``change``, ``change_pct``, ``volume``,
    ``previous_close``, or an empty dict on error.
    """
    data = _td_get("quote", {"symbol": ticker.upper()})
    if "error" in data:
        return {}
    return {
        "price": _safe_float(data.get("price")),
        "change": _safe_float(data.get("change")),
        "change_pct": _safe_float(data.get("percent_change")),
        "volume": _safe_int(data.get("volume")),
        "previous_close": _safe_float(data.get("previous_close")),
    }


def get_time_series(ticker: str, days: int = 100) -> list | None:
    """Fetch historical daily candles for *ticker*.

    Results are cached to ``.cache/{ticker}_ts.json`` for up to 60 minutes.
    Returns a list of Candle dicts ``{"open", "high", "low", "close", "volume"}``
    in chronological order (oldest first), or ``None`` on error.
    """
    ticker = ticker.upper()

    # Try cache first
    cached = _cache_read(ticker)
    if cached is not None:
        return cached

    data = _td_get("time_series", {
        "symbol": ticker,
        "interval": "1day",
        "outputsize": str(days),
    })
    if "error" in data:
        return None

    raw_values = data.get("values")
    if not raw_values or not isinstance(raw_values, list):
        return None

    candles: list[dict] = []
    for item in raw_values:
        candles.append({
            "datetime": str(item.get("datetime", "")),
            "open": _safe_float(item.get("open")),
            "high": _safe_float(item.get("high")),
            "low": _safe_float(item.get("low")),
            "close": _safe_float(item.get("close")),
            "volume": _safe_int(item.get("volume")),
        })

    if not candles:
        return None

    # Twelve Data returns newest first; reverse for chronological order
    candles.reverse()

    _cache_write(ticker, candles)
    return candles


def get_rsi(ticker: str) -> float | None:
    """Fetch the 14-period daily RSI value for *ticker*.

    Returns the RSI as a float, or ``None`` on error.
    """
    data = _td_get("rsi", {
        "symbol": ticker.upper(),
        "interval": "1day",
        "time_period": "14",
        "outputsize": "1",
    })
    if "error" in data:
        return None
    values = data.get("values")
    if not values or not isinstance(values, list) or len(values) == 0:
        return None
    raw = values[0].get("rsi")
    if raw is None:
        return None
    try:
        return float(raw)
    except (ValueError, TypeError):
        return None


# ── Cache helpers ───────────────────────────────────────────────────────────────


def _cache_path(ticker: str) -> Path:
    """Resolve the cache file path for *ticker*."""
    return Path(CACHE_DIR) / f"{ticker.upper()}_ts.json"


def _cache_read(ticker: str) -> list | None:
    """Read cached candles if the file exists and is less than 60 minutes old."""
    path = _cache_path(ticker)
    if not path.is_file():
        return None
    try:
        age = time.time() - path.stat().st_mtime
    except OSError:
        return None
    if age > 3600:  # 60 minutes
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, list):
            return data
    except (OSError, json.JSONDecodeError):
        pass
    return None


def _cache_write(ticker: str, data: list) -> None:
    """Persist *data* to the cache file for *ticker* (best-effort)."""
    path = _cache_path(ticker)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
    except OSError:
        pass  # best-effort — don't fail the caller


# ── Internal helpers ────────────────────────────────────────────────────────────


def _safe_float(value: object) -> float:
    """Convert *value* to float, returning ``0.0`` on failure."""
    if value is None:
        return 0.0
    try:
        return float(value)
    except (ValueError, TypeError):
        return 0.0


def _safe_int(value: object) -> int:
    """Convert *value* to int, returning ``0`` on failure."""
    if value is None:
        return 0
    try:
        return int(value)
    except (ValueError, TypeError):
        return 0