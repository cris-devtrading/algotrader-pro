"""
AlgoTrader Pro — IBKR Trading Bot
==================================
Professional automated trading bot for Interactive Brokers.
Supports: NASDAQ Futures (NQ), US Stocks (AAPL, NVDA, MSFT, GOOGL, AMZN)
Strategies: Mean Reversion | Momentum | EMA Cross
Modes: Paper Trading | Live Trading

Requirements:
    pip install ib_insync pandas numpy
    TWS or IB Gateway must be running before starting the bot.
"""

import time
import logging
import numpy as np
import pandas as pd
from datetime import datetime
from dataclasses import dataclass, field
from typing import Optional
from ib_insync import IB, Stock, Future, MarketOrder, LimitOrder, util

# ─── Logging Setup ────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("AlgoTraderPro")


# ─── Configuration ────────────────────────────────────────────────────────────
@dataclass
class BotConfig:
    # IBKR connection
    host: str = "127.0.0.1"
    port: int = 7497          # 7497 = TWS paper | 7496 = TWS live | 4002 = Gateway paper
    client_id: int = 1

    # Trading mode
    paper_mode: bool = True   # Set False for live trading

    # Strategy selection: "mean_reversion" | "momentum" | "ema_cross"
    strategy: str = "mean_reversion"

    # Markets to trade
    trade_futures: bool = True   # NQ NASDAQ futures
    trade_stocks: bool = True    # US equities

    # Risk management
    stop_loss_pct: float = 1.5   # % below entry price
    take_profit_pct: float = 3.0 # % above entry price
    max_positions: int = 3       # Maximum concurrent open positions
    position_size_usd: float = 10_000  # Dollar value per position

    # Strategy parameters
    lookback_bars: int = 20      # Bars for indicator calculation
    scan_interval_sec: int = 60  # How often to scan for signals (seconds)

    # Symbols
    stock_symbols: list = field(default_factory=lambda: [
        "AAPL", "NVDA", "MSFT", "GOOGL", "AMZN"
    ])


# ─── Position Tracker ─────────────────────────────────────────────────────────
@dataclass
class Position:
    symbol: str
    side: str          # "LONG" or "SHORT"
    quantity: int
    entry_price: float
    stop_loss: float
    take_profit: float
    entry_time: datetime = field(default_factory=datetime.now)
    pnl: float = 0.0

    def update_pnl(self, current_price: float):
        if self.side == "LONG":
            self.pnl = (current_price - self.entry_price) * self.quantity
        else:
            self.pnl = (self.entry_price - current_price) * self.quantity

    def should_exit(self, current_price: float) -> Optional[str]:
        """Returns exit reason or None."""
        if self.side == "LONG":
            if current_price <= self.stop_loss:
                return "stop_loss"
            if current_price >= self.take_profit:
                return "take_profit"
        else:
            if current_price >= self.stop_loss:
                return "stop_loss"
            if current_price <= self.take_profit:
                return "take_profit"
        return None


# ─── Strategies ───────────────────────────────────────────────────────────────
class Strategies:
    """
    All strategies return a signal: "BUY", "SELL", or None.
    Input: pandas Series of closing prices (most recent last).
    """

    @staticmethod
    def mean_reversion(prices: pd.Series, lookback: int = 20) -> Optional[str]:
        """
        Buy when price is > 1.5 std below mean (oversold).
        Sell when price is > 1.5 std above mean (overbought).
        """
        if len(prices) < lookback:
            return None
        mean = prices.rolling(lookback).mean().iloc[-1]
        std = prices.rolling(lookback).std().iloc[-1]
        current = prices.iloc[-1]

        if current < mean - 1.5 * std:
            return "BUY"
        if current > mean + 1.5 * std:
            return "SELL"
        return None

    @staticmethod
    def momentum(prices: pd.Series, lookback: int = 20) -> Optional[str]:
        """
        Buy when momentum is positive and accelerating.
        Sell when momentum is negative and decelerating.
        """
        if len(prices) < lookback:
            return None
        returns = prices.pct_change().dropna()
        recent = returns.iloc[-5:].mean()
        longer = returns.iloc[-lookback:].mean()

        if recent > longer * 1.5 and recent > 0:
            return "BUY"
        if recent < longer * 1.5 and recent < 0:
            return "SELL"
        return None

    @staticmethod
    def ema_cross(prices: pd.Series, fast: int = 9, slow: int = 21) -> Optional[str]:
        """
        Buy on fast EMA crossing above slow EMA (golden cross).
        Sell on fast EMA crossing below slow EMA (death cross).
        """
        if len(prices) < slow + 1:
            return None
        ema_fast = prices.ewm(span=fast).mean()
        ema_slow = prices.ewm(span=slow).mean()

        cross_up = ema_fast.iloc[-2] < ema_slow.iloc[-2] and ema_fast.iloc[-1] > ema_slow.iloc[-1]
        cross_down = ema_fast.iloc[-2] > ema_slow.iloc[-2] and ema_fast.iloc[-1] < ema_slow.iloc[-1]

        if cross_up:
            return "BUY"
        if cross_down:
            return "SELL"
        return None


# ─── Main Bot ─────────────────────────────────────────────────────────────────
class AlgoTraderPro:
    def __init__(self, config: BotConfig):
        self.cfg = config
        self.ib = IB()
        self.positions: dict[str, Position] = {}
        self.strategy_fn = {
            "mean_reversion": Strategies.mean_reversion,
            "momentum": Strategies.momentum,
            "ema_cross": Strategies.ema_cross,
        }[config.strategy]
        self.running = False
        self.trade_log: list[dict] = []

    # ── Connection ────────────────────────────────────────────────────────────
    def connect(self):
        mode = "PAPER" if self.cfg.paper_mode else "LIVE"
        log.info(f"Connecting to IBKR TWS [{mode}] at {self.cfg.host}:{self.cfg.port} ...")
        self.ib.connect(self.cfg.host, self.cfg.port, clientId=self.cfg.client_id)
        log.info(f"Connected. Account: {self.ib.managedAccounts()}")

    def disconnect(self):
        self.ib.disconnect()
        log.info("Disconnected from IBKR.")

    # ── Market Data ───────────────────────────────────────────────────────────
    def get_contract(self, symbol: str):
        if symbol == "NQ":
            contract = Future("NQ", "202503", "CME")
        else:
            contract = Stock(symbol, "SMART", "USD")
        self.ib.qualifyContracts(contract)
        return contract

    def get_prices(self, symbol: str, bars: int = 50) -> Optional[pd.Series]:
        """Fetch recent 1-min closing prices."""
        try:
            contract = self.get_contract(symbol)
            history = self.ib.reqHistoricalData(
                contract,
                endDateTime="",
                durationStr=f"{bars + 5} S" if symbol == "NQ" else "1 D",
                barSizeSetting="1 min",
                whatToShow="TRADES",
                useRTH=True,
            )
            if not history:
                return None
            df = util.df(history)
            return df["close"].tail(bars)
        except Exception as e:
            log.warning(f"Failed to get prices for {symbol}: {e}")
            return None

    def get_current_price(self, symbol: str) -> Optional[float]:
        try:
            contract = self.get_contract(symbol)
            ticker = self.ib.reqMktData(contract, "", False, False)
            self.ib.sleep(1)
            price = ticker.last or ticker.close
            self.ib.cancelMktData(contract)
            return float(price) if price else None
        except Exception as e:
            log.warning(f"Price fetch error for {symbol}: {e}")
            return None

    # ── Order Execution ───────────────────────────────────────────────────────
    def place_order(self, symbol: str, action: str, quantity: int) -> bool:
        """Place a market order. Returns True if successful."""
        try:
            contract = self.get_contract(symbol)
            order = MarketOrder(action, quantity)
            trade = self.ib.placeOrder(contract, order)
            self.ib.sleep(1)
            log.info(f"Order placed: {action} {quantity} {symbol} — status: {trade.orderStatus.status}")
            return True
        except Exception as e:
            log.error(f"Order failed for {symbol}: {e}")
            return False

    # ── Position Management ───────────────────────────────────────────────────
    def open_position(self, symbol: str, signal: str, current_price: float):
        if symbol in self.positions:
            return
        if len(self.positions) >= self.cfg.max_positions:
            log.info(f"Max positions ({self.cfg.max_positions}) reached. Skipping {symbol}.")
            return

        quantity = max(1, int(self.cfg.position_size_usd / current_price))
        action = "BUY" if signal == "BUY" else "SELL"
        side = "LONG" if signal == "BUY" else "SHORT"

        sl_mult = 1 - self.cfg.stop_loss_pct / 100 if side == "LONG" else 1 + self.cfg.stop_loss_pct / 100
        tp_mult = 1 + self.cfg.take_profit_pct / 100 if side == "LONG" else 1 - self.cfg.take_profit_pct / 100

        stop_loss = round(current_price * sl_mult, 2)
        take_profit = round(current_price * tp_mult, 2)

        if self.place_order(symbol, action, quantity):
            pos = Position(
                symbol=symbol, side=side, quantity=quantity,
                entry_price=current_price, stop_loss=stop_loss, take_profit=take_profit,
            )
            self.positions[symbol] = pos
            log.info(
                f"OPENED {side} {quantity} {symbol} @ ${current_price:.2f} "
                f"| SL: ${stop_loss:.2f} | TP: ${take_profit:.2f}"
            )
            self._log_trade("OPEN", symbol, side, quantity, current_price)

    def close_position(self, symbol: str, reason: str, current_price: float):
        pos = self.positions.get(symbol)
        if not pos:
            return
        action = "SELL" if pos.side == "LONG" else "BUY"
        if self.place_order(symbol, action, pos.quantity):
            pos.update_pnl(current_price)
            log.info(
                f"CLOSED {pos.side} {symbol} @ ${current_price:.2f} "
                f"| Reason: {reason} | P&L: ${pos.pnl:+.2f}"
            )
            self._log_trade("CLOSE", symbol, pos.side, pos.quantity, current_price, pos.pnl, reason)
            del self.positions[symbol]

    # ── Scanning Loop ─────────────────────────────────────────────────────────
    def scan(self):
        symbols = []
        if self.cfg.trade_futures:
            symbols.append("NQ")
        if self.cfg.trade_stocks:
            symbols.extend(self.cfg.stock_symbols)

        for symbol in symbols:
            try:
                current_price = self.get_current_price(symbol)
                if not current_price:
                    continue

                # Check exits on open positions
                if symbol in self.positions:
                    pos = self.positions[symbol]
                    exit_reason = pos.should_exit(current_price)
                    if exit_reason:
                        self.close_position(symbol, exit_reason, current_price)
                    else:
                        pos.update_pnl(current_price)
                    continue

                # Look for entry signals
                prices = self.get_prices(symbol, bars=self.cfg.lookback_bars + 10)
                if prices is None:
                    continue

                signal = self.strategy_fn(prices, self.cfg.lookback_bars)
                if signal:
                    log.info(f"Signal: {signal} on {symbol} @ ${current_price:.2f}")
                    self.open_position(symbol, signal, current_price)

            except Exception as e:
                log.error(f"Error scanning {symbol}: {e}")

    # ── Main Loop ─────────────────────────────────────────────────────────────
    def run(self):
        log.info(f"Strategy: {self.cfg.strategy.upper()} | Mode: {'PAPER' if self.cfg.paper_mode else 'LIVE'}")
        log.info(f"Stop Loss: {self.cfg.stop_loss_pct}% | Take Profit: {self.cfg.take_profit_pct}%")
        log.info("Bot running. Press Ctrl+C to stop.\n")
        self.running = True

        try:
            while self.running:
                self.scan()
                self._print_status()
                time.sleep(self.cfg.scan_interval_sec)
        except KeyboardInterrupt:
            log.info("Stopping bot...")
        finally:
            # Close all open positions on exit
            for symbol in list(self.positions.keys()):
                price = self.get_current_price(symbol)
                if price:
                    self.close_position(symbol, "bot_shutdown", price)
            self.disconnect()

    # ── Helpers ───────────────────────────────────────────────────────────────
    def _print_status(self):
        if not self.positions:
            log.info("No open positions.")
            return
        total_pnl = sum(p.pnl for p in self.positions.values())
        log.info(f"── Positions ({len(self.positions)}) | Total P&L: ${total_pnl:+.2f} ──")
        for p in self.positions.values():
            log.info(f"  {p.side:5s} {p.symbol:6s} x{p.quantity} | P&L: ${p.pnl:+.2f}")

    def _log_trade(self, action, symbol, side, qty, price, pnl=None, reason=None):
        self.trade_log.append({
            "time": datetime.now().isoformat(),
            "action": action,
            "symbol": symbol,
            "side": side,
            "quantity": qty,
            "price": price,
            "pnl": pnl,
            "reason": reason,
        })


# ─── Entry Point ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    config = BotConfig(
        paper_mode=True,           # Change to False for live trading
        strategy="mean_reversion", # "mean_reversion" | "momentum" | "ema_cross"
        trade_futures=True,
        trade_stocks=True,
        stop_loss_pct=1.5,
        take_profit_pct=3.0,
        max_positions=3,
        position_size_usd=10_000,
        scan_interval_sec=60,
    )

    bot = AlgoTraderPro(config)
    bot.connect()
    bot.run()
