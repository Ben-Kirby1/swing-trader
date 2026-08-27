# Swing Trading Agent

Automated swing trading analysis system using technical indicators and LLM-driven decision support. Designed for 3-day to 3-week holding periods.

## Overview

Fetches real-time market data via the Twelve Data API, computes technical indicators (SMA, RSI, MACD, Bollinger Bands), and feeds the results into DeepSeek V4 Flash via OpenRouter for structured trade recommendation generation.

Built as a personal tool for paper trading — not financial advice.

## Components

| Script | Purpose |
|---|---|
| `src/swing_trader.py` | Analyze 1-10 tickers: quote, indicators, LLM reasoning |
| `src/multi_watchdog.py` | Real-time position monitor with alerting (15-min intervals) |
| `src/watchdog_positions.json` | Position config (entry, stop, targets) |

### Swing Trader (`src/swing_trader.py`)

```
python src/swing_trader.py AAPL
python src/swing_trader.py SPY MSFT TSLA SOFI
```

For each ticker it:
1. Fetches current price and previous close
2. Pulls 100 days of daily candles
3. Computes SMA20/50, RSI(14), MACD, Bollinger Bands, 52-week range
4. Sends structured data to DeepSeek V4 Flash for recommendation
5. Outputs technical context + LLM judgment

### Multi-Ticker Watchdog (`src/multi_watchdog.py`)

Runs continuously during market hours via cron (every 15 min). Silent mode by default — only produces output when something matters:

- **Stop loss threatened or hit**
- **Take-profit targets reached** (one-shot alerts)
- **RSI extremes** (>75 overbought, <25 oversold)
- **Large daily moves** (>4% drop, >8% surge)
- **Scheduled briefings** at opening (9:45), midday (12:30), and closing (16:00)

Config-driven position management — add or remove tickers without touching code.

## Setup

```
# 1. Clone and enter directory
git clone <repo-url>
cd swing-trader

# 2. Install dependencies
pip install -r requirements.txt

# 3. Add API keys
cp config.example.py config.py
# Edit config.py with your Twelve Data and OpenRouter keys

# 4. Run an analysis
python src/swing_trader.py AAPL
```

## API Keys

- **Twelve Data** — [twelvedata.com](https://twelvedata.com) — 800 free API calls/day
- **OpenRouter** — [openrouter.ai](https://openrouter.ai) — pay-per-token LLM access

Neither key is committed to the repo. Copy `config.example.py` to `config.py` and fill in your keys.

## Technical Indicators

Calculated from raw price data (no external TA libraries):

- **SMA20 / SMA50** — Simple moving averages for trend direction
- **RSI(14)** — Relative Strength Index for momentum/overbought-oversold
- **MACD** — 12/26 EMA crossover signal
- **Bollinger Bands (2σ, 20)** — Volatility envelope and squeeze detection
- **52-week high/low** — Long-term context

## Stack

- Python 3 (stdlib + numpy)
- Twelve Data REST API
- OpenRouter (DeepSeek V4 Flash)
- Cron-based scheduling (Hermes Agent)

---

*Personal paper trading project. Not financial advice.*