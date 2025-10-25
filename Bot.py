# bot.py
# Simulation trading bot (RSI + MA) - safe demo only (no real trades).
# Usage: python bot.py
# Exposes a small web server with /status and /trades endpoints.

import os
import time
import threading
from datetime import datetime
import random

from flask import Flask, jsonify
import pandas as pd
from ta.momentum import RSIIndicator

# ---- CONFIGURATION (ENV or defaults) ----
START_BALANCE = float(os.getenv("START_BALANCE", "1000.0"))
SYMBOL = os.getenv("SYMBOL", "EURUSD")
INTERVAL_SECONDS = int(os.getenv("INTERVAL_SECONDS", "60"))  # simulation tick
RSI_WINDOW = int(os.getenv("RSI_WINDOW", "14"))
MA_SHORT = int(os.getenv("MA_SHORT", "5"))
MA_LONG = int(os.getenv("MA_LONG", "20"))
TRADE_AMOUNT = float(os.getenv("TRADE_AMOUNT", "10.0"))

# ---- INTERNAL STATE ----
balance = START_BALANCE
open_position = None     # dict with keys: entry_price, direction, amount, entry_time
trade_history = []       # list of closed trades
price_series = []        # simulated closing prices
tick = 0
running = True

# ---- Flask app ----
app = Flask(__name__)

@app.route("/")
def home():
    return "Simulation bot running ✅"

@app.route("/status")
def status():
    return jsonify({
        "balance": round(balance, 6),
        "open_position": open_position,
        "trades_count": len(trade_history),
        "latest_price": price_series[-1] if price_series else None,
        "tick": tick
    })

@app.route("/trades")
def trades():
    return jsonify(trade_history)

# ---- Helpers ----
def generate_next_price():
    """Simple random-walk price generator around 1.1000 for demo."""
    base = 1.1000
    if not price_series:
        price = base + random.uniform(-0.005, 0.005)
    else:
        last = price_series[-1]
        # small mean-reverting random walk
        shock = random.uniform(-0.0008, 0.0008)
        price = last * (1 + shock)
    # round to 5 decimals like FX
    return round(price, 5)

def compute_indicators(prices):
    df = pd.DataFrame({"close": prices})
    if len(df) < max(RSI_WINDOW, MA_LONG) + 1:
        return None
    df['rsi'] = RSIIndicator(df['close'], window=RSI_WINDOW).rsi()
    df['ma_short'] = df['close'].rolling(window=MA_SHORT).mean()
    df['ma_long'] = df['close'].rolling(window=MA_LONG).mean()
    return df

def try_take_signal(df):
    global open_position, balance
    # use last row
    last = df.iloc[-1]
    prev = df.iloc[-2] if len(df) >= 2 else None
    if pd.isna(last['rsi']) or pd.isna(last['ma_short']) or pd.isna(last['ma_long']):
        return

    # simple rules:
    # BUY (call) if RSI < 30 AND short MA crosses above long MA
    # SELL (put) if RSI > 70 AND short MA crosses below long MA
    buy_signal = (last['rsi'] < 30) and prev is not None and (prev['ma_short'] <= prev['ma_long']) and (last['ma_short'] > last['ma_long'])
    sell_signal = (last['rsi'] > 70) and prev is not None and (prev['ma_short'] >= prev['ma_long']) and (last['ma_short'] < last['ma_long'])

    current_price = last['close']

    if open_position is None:
        if buy_signal:
            open_position = {
                "entry_time": datetime.utcnow().isoformat(),
                "entry_price": current_price,
                "direction": "call",
                "amount": TRADE_AMOUNT
            }
            # reserve amount from balance (simulation)
            balance -= TRADE_AMOUNT
            log("OPEN BUY at {:.5f}".format(current_price))
        elif sell_signal:
            open_position = {
                "entry_time": datetime.utcnow().isoformat(),
                "entry_price": current_price,
                "direction": "put",
                "amount": TRADE_AMOUNT
            }
            balance -= TRADE_AMOUNT
            log("OPEN SELL at {:.5f}".format(current_price))
    else:
        # simple exit rule: close after profit threshold or stop loss or MA cross reverse
        entry = open_position
        entry_price = entry["entry_price"]
        direction = entry["direction"]
        # simulate P/L: for FX call: profit if price goes up; put: profit if price goes down
        pnl = 0.0
        if direction == "call":
            pnl = (current_price - entry_price) * 10000  # pip-ish scaling for demo
        else:
            pnl = (entry_price - current_price) * 10000

        # define thresholds (in pips)
        take_profit = 5.0   # ~5 pips
        stop_loss = -10.0   # -10 pips

        # also reverse signal closes
        ma_reverse = False
        if prev is not None:
            if direction == "call" and (prev['ma_short'] >= prev['ma_long'] and last['ma_short'] < last['ma_long']):
                ma_reverse = True
            if direction == "put" and (prev['ma_short'] <= prev['ma_long'] and last['ma_short'] > last['ma_long']):
                ma_reverse = True

        if pnl >= take_profit or pnl <= stop_loss or ma_reverse:
            # close
            profit = pnl  # this is pip-derived; convert to currency for demo:
            profit_amount = (profit / 10.0) * (entry["amount"] / 100.0)  # arbitrary conversion for demo
            balance += entry["amount"] + profit_amount
            closed = {
                "entry_time": entry["entry_time"],
                "close_time": datetime.utcnow().isoformat(),
                "entry_price": entry_price,
                "close_price": current_price,
                "direction": direction,
                "amount": entry["amount"],
                "profit_amount": round(profit_amount, 6),
                "pnl_pips": round(pnl, 3)
            }
            trade_history.append(closed)
            log("CLOSE {} at {:.5f} | profit {:.6f}".format(direction.upper(), current_price, profit_amount))
            open_position = None

def log(msg):
    print(f"[{datetime.utcnow().isoformat()}] {msg}")

def simulation_loop():
    global tick, running
    while running:
        tick += 1
        price = generate_next_price()
        price_series.append(price)
        # keep series length reasonable
        if len(price_series) > 500:
            price_series.pop(0)

        df = compute_indicators(price_series)
        if df is not None:
            try_take_signal(df)

        # print small status every 10 ticks
        if tick % 10 == 0:
            log(f"Tick {tick} | Price {price:.5f} | Balance {balance:.6f} | Open {bool(open_position)} | Trades {len(trade_history)}")

        time.sleep(INTERVAL_SECONDS)

# ---- Start background thread ----
def start_background():
    t = threading.Thread(target=simulation_loop, daemon=True)
    t.start()

if __name__ == "__main__":
    # start simulation thread first
    start_background()
    # start Flask (Render expects server running)
    port = int(os.getenv("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)
