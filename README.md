# Swing Trader v2

## Multi-Strategy AI Swing Trading System

**Stack:** Python 3.10+ | Twelve Data API (800 calls/day free) | DeepSeek V4 Flash via OpenRouter

```
python swing-trader analyze AAPL
python swing-trader analyze SPY MSFT NVDA
python swing-trader watch
python swing-trader portfolio
python swing-trader backtest AAPL --capital 10000
python swing-trader status
```

## Architecture

```
swing_trader/
├── __init__.py     # Package exports
├── config.py       # Config loader (imports repo-root config.py)
├── data.py         # Twelve Data API + local caching
├── indicators.py   # 9 technical indicators (RSI, MACD, BB, ATR, ADX, OBV, VWAP, SMA, EMA)
├── signals.py      # 4 strategies + ensemble voting + LLM narrative overlay
├── risk.py         # ATR sizing, Kelly, portfolio metrics, correlation checks
├── monitor.py      # Position watchdog (replaces multi_watchdog.py)
├── backtest.py     # Walk-forward backtesting engine
├── reporting.py    # Terminal/Discord report formatters
└── cli.py          # Unified CLI entry point
```

## Quick Start

1. **Set up API keys:**
   ```bash
   cp config.example.py config.py
   # Edit config.py with your Twelve Data key and OpenRouter key
   ```

2. **Run from repo root:**
   ```bash
   python -m swing_trader.cli analyze AAPL
   python -m swing_trader.cli status
   ```

3. **Or install and use anywhere:**
   ```bash
   pip install -e .
   swing-trader analyze AAPL
   ```

## Strategies

| # | Strategy | Condition | Weight |
|---|----------|-----------|--------|
| 1 | **Trend Follow** | EMA12>EMA26, price>SMA50, ADX>25 | 0.30 |
| 2 | **Mean Reversion** | RSI<30 oversold or RSI>70 overbought | 0.25 |
| 3 | **Breakout** | price>SMA20+2ATR with 1.5x volume | 0.25 |
| 4 | **Momentum** | MACD rising, RSI 40-60, OBV climbing | 0.20 |

## Position Monitoring

Configure positions in `watchdog_positions.json`:

```json
{
  "positions": {
    "SOFI": {
      "entry": 16.87, "stop": 15.80, "trail_stop": 17.00,
      "tp1": 18.50, "tp2": 19.50, "shares": 5, "capital": 100
    }
  },
  "scheduled_times": ["09:45", "12:30", "16:00"]
}
```

## Backtesting

Walk-forward validation: trains on 60 days, tests on next 20, slides by 20.

```
python swing-trader backtest SPY --capital 10000
```

Returns: total return, Sharpe, max drawdown, win rate, profit factor, trade log.

## API Credits

- **Twelve Data:** 800 calls/day free — 2 calls per ticker (price + time series)
- **400 tickers/day maximum** — well within swing trading needs

---

**Paper trading only.** This is an educational analysis system, not financial advice.