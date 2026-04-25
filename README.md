# AlgoTrader Pro — IBKR Trading Bot

Automated trading bot for Interactive Brokers with a clean web dashboard.
Supports NASDAQ Futures (NQ) and US Stocks (AAPL, NVDA, MSFT, GOOGL, AMZN).

## Features

- **3 built-in strategies**: Mean Reversion, Momentum, EMA Cross
- **Paper & Live trading** modes via simple config toggle
- **Automatic risk management**: Stop loss and take profit per position
- **No-code configuration**: all parameters in `BotConfig` or via dashboard UI
- **Real-time logging**: every order and position update is logged
- **IBKR native integration**: uses `ib_insync` (TWS or IB Gateway)

## Requirements

- Python 3.10+
- Interactive Brokers account (paper or live)
- TWS or IB Gateway running locally

```bash
pip install ib_insync pandas numpy
```

## Quick Start

1. Open TWS or IB Gateway and enable API connections (port 7497 for paper)
2. Edit `bot.py` — set your preferred strategy and risk parameters
3. Run:

```bash
python bot.py
```

## Configuration

All settings live in `BotConfig` at the bottom of `bot.py`:

| Parameter | Default | Description |
|---|---|---|
| `paper_mode` | `True` | Set `False` for live trading |
| `strategy` | `mean_reversion` | `mean_reversion`, `momentum`, `ema_cross` |
| `stop_loss_pct` | `1.5` | Stop loss as % of entry price |
| `take_profit_pct` | `3.0` | Take profit as % of entry price |
| `max_positions` | `3` | Maximum concurrent open positions |
| `position_size_usd` | `10000` | Dollar value allocated per trade |
| `scan_interval_sec` | `60` | Seconds between market scans |
| `trade_futures` | `True` | Enable NQ futures trading |
| `trade_stocks` | `True` | Enable US stocks trading |

## Strategies

**Mean Reversion** — buys when price drops >1.5 std below its rolling mean (oversold), sells when >1.5 std above (overbought). Works well in range-bound markets.

**Momentum** — buys when recent returns accelerate above the longer-term average. Best in trending markets.

**EMA Cross** — classic golden/death cross using 9 and 21-period EMAs. Simple, reliable, easy to explain to clients.

## IBKR Port Reference

| Port | Mode |
|---|---|
| 7497 | TWS — Paper Trading |
| 7496 | TWS — Live Trading |
| 4002 | IB Gateway — Paper |
| 4001 | IB Gateway — Live |

## Disclaimer

This software is for educational and demonstration purposes.
Past performance does not guarantee future results.
Always test in paper mode before going live.
