# Swing Trader v2 — Multi-Agent System Architecture

## Overview

Eight coordinated agents each own a module. Shared interfaces are defined here.
Every agent writes a self-contained Python file under `swing_trader/`, using only stdlib + numpy.
Agents are **stateless** — all state lives in config.py, .json state files, or function parameters.

## Shared Contracts

### Module → Module Dependencies

```
cli.py ─→ config.py, data.py, signals.py, risk.py, monitor.py, backtest.py, reporting.py
monitor.py ─→ data.py, config.py
signals.py ─→ indicators.py, data.py, config.py (optional: OpenRouter)
backtest.py ─→ data.py, indicators.py, risk.py
reporting.py ─→ (no internal deps, receives dicts)
```

### Data Structures (duck-typed dicts, no classes)

**Candle:** `{"open": float, "high": float, "low": float, "close": float, "volume": int}`  
**IndicatorResult:** `{"sma20": float, "sma50": float, "sma200": float, "rsi": float, "macd": float, "macd_signal": float, "macd_hist": float, "bb_u": float, "bb_l": float, "bb_m": float, "atr14": float, "adx": float, "obv": int, "vwap": float, "ema12": float, "ema26": float}`  
**Signal:** `{"ticker": str, "direction": "BUY"/"SELL"/"HOLD"/"WAIT", "confidence": float 0-1, "entry": float, "stop": float, "tp1": float, "tp2": float, "rr": float, "reason": str, "strategies": [str], "price": float}`  
**Position:** `{"ticker": str, "entry": float, "stop": float, "trail_stop": float, "shares": int, "capital": float, "tp1": float, "tp2": float, "open_date": str}`  
**PortfolioMetrics:** `{"total_capital": float, "cash": float, "positions_value": float, "total_pl": float, "unrealized_pl": float, "risk_pct": float, "concentration_pct": float}`

### File Paths (all resolved by config.py)

| Key | Default |
|-----|---------|
| `PROJECT_DIR` | `repo root` |
| `STATE_DIR` | `repo root` |
| `WATCHDOG_CONFIG` | `watchdog_positions.json` |
| `DATA_CACHE` | `.cache/` |

### API Keys (from config.py)
```python
TWELVE_DATA_KEY = "..."   # str
OPENROUTER_KEY = "..."    # str or ""
MODEL = "deepseek/deepseek-v4-flash"
```

### Twelve Data API Usage (from data.py)
- `get_price(ticker: str) -> float`
- `get_time_series(ticker: str, days: int = 100) -> list[Candle]`
- `get_rsi(ticker: str) -> float`  (uses Twelve Data RSI endpoint)
- Respects 8 calls/min rate limit with 7.5s spacing

---

## Agent Assignments

### Agent 1: Config + Init
**File: `swing_trader/__init__.py`, `swing_trader/config.py`**
- `__init__.py` — package exports
- `config.py` — load `config.py` from repo root (try/except), expose all constants
- Cache dir creation, state file helpers

### Agent 2: Data Layer
**File: `swing_trader/data.py`**
- `get_price(ticker)` → float
- `get_time_series(ticker, days=100)` → list[Candle]
- `get_rsi(ticker)` → float (Twelve Data RSI endpoint)
- `get_quote(ticker)` → dict (price, change, volume, prev_close)
- Rate limiter: 7.5s between calls (class-level or module funccaching)
- Local cache: save `.cache/{ticker}_ts.json` with expiry (max 60 min stale)
- Error handling: return empty/None on failure, never raise

### Agent 3: Indicators
**File: `swing_trader/indicators.py`**
- `calc_all(candles: list[Candle]) -> IndicatorResult`
- Individual: `calc_sma(c, period)`, `calc_ema(c, period)`, `calc_rsi(c)`, `calc_macd(c)`, `calc_bb(c, period=20, std=2)`, `calc_atr(candles, period=14)`, `calc_adx(candles, period=14)`, `calc_obv(candles)`, `calc_vwap(candles)`
- All vectorized with stdlib math (numpy optional fallback)
- Returns dict, not a class

### Agent 4: Signals Engine
**File: `swing_trader/signals.py`**
Four discrete strategies + ensemble:

1. **Trend Follow** — EMA12 > EMA26, price > SMA50, ADX > 25
2. **Mean Reversion** — RSI < 30 or price < BB_lower, RSI rising from oversold
3. **Breakout** — price > SMA20 + 2ATR, volume > 1.5x avg
4. **Momentum** — MACD crossover bullish, RSI 40-60 trending up, OBV rising

- `generate_signal(ticker, candles, indicator_result) -> Signal`
- `ensemble(signals: list[Signal]) -> Signal` — weighted vote, min 2 strategies agree
- `llm_narrative(ticker, price, indicators, signal) -> str` — calls OpenRouter for narrative overlay (only if OR_KEY is set)
- Strategy-to-weight: Trend=0.3, Reversion=0.25, Breakout=0.25, Momentum=0.2

### Agent 5: Risk Management
**File: `swing_trader/risk.py`**
- `atr_position_size(capital, risk_pct, entry, atr, multiplier=2) -> dict{shares, stop_distance, dollar_risk}`
- `kelly_fraction(win_rate, avg_win, avg_loss) -> float`
- `portfolio_metrics(positions: list[Position], cash) -> PortfolioMetrics`
- `max_concentration(positions, max_pct=0.5) -> bool`
- `correlation_warning(tickers) -> list[str]` (static list of known correlated groups)
- `adjust_stop(current_stop, price, rsi, trail_pct=0.05) -> float` (tighten if RSI > 75)

### Agent 6: Monitor / Watchdog
**File: `swing_trader/monitor.py`**
- Reads `watchdog_positions.json`
- `check_all() -> dict{reports, alerts}` — loops positions, calls data.py + indicators.py
- Alert types: stop threatened/hit, TP hit, RSI extreme, large daily move
- Scheduled check times: 09:45, 12:30, 16:00 (configurable)
- State persistence: `.watchdog_state.json` (stops, TP-hit flags)
- Returns structured dicts, no print() — print is CLI's job
- Timestamp file: `.last_timestamp`

### Agent 7: Backtesting
**File: `swing_trader/backtest.py`**
- `run_backtest(ticker, start_date, end_date, initial_capital, strategy_func) -> dict{metrics}`
  - Walk-forward: train on 60 days, test on next 20, slide by 20
  - Trades generated by calling strategy_func(candles)
- Metrics: total_return, max_drawdown, sharpe_ratio, win_rate, avg_win, avg_loss, profit_factor, num_trades
- `compare_strategies(ticker, candles) -> dict` — runs all 4 strategies individually
- No OpenRouter during backtest (heuristic only for consistency)

### Agent 8: CLI + Reporting
**File: `swing_trader/cli.py`, `swing_trader/reporting.py`**
- `reporting.py`: `format_terminal(signal)`, `format_discord(signal)`, `format_portfolio(metrics)`, `format_backtest(metrics)`
- `cli.py`: `main()` with argparse:
  - `analyze TICKER [TICKER...]` — fetch, indicators, signals, LLM narrative, print
  - `watch` — run monitor check
  - `portfolio [--file positions.json]` — print portfolio metrics
  - `backtest TICKER [--start YYYY-MM-DD] [--end YYYY-MM-DD]` — run backtest
  - `status` — check API keys, cache size, rate limit info