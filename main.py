import time
import os
import csv
import requests
import pandas as pd
import numpy as np
from datetime import datetime, timezone

TELEGRAM_TOKEN = "8870751882:AAHele5_0uHugJBSWuBBjDnM19bq4R3jdhg"
TELEGRAM_CHAT_ID = "7992615064"

SYMBOLS = {
    "GC=F": "الذهب (Gold)",
    "BTC-USD": "بيتكوين (Bitcoin)",
}

INTERVAL = "15m"
RANGE = "5d"
CHECK_EVERY_SECONDS = 60 * 15
RISK_REWARD_TIERS = [1, 2, 3]
SWING_LOOKBACK = 20
COOLDOWN_BARS = 3
LOG_FILE = "multi_signals_log.csv"

YAHOO_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"


def fetch_data(symbol):
    params = {"range": RANGE, "interval": INTERVAL}
    headers = {"User-Agent": "Mozilla/5.0"}
    r = requests.get(YAHOO_URL.format(symbol=symbol), params=params, headers=headers, timeout=15)
    r.raise_for_status()
    data = r.json()
    result = data["chart"]["result"][0]
    timestamps = result["timestamp"]
    quote = result["indicators"]["quote"][0]
    df = pd.DataFrame({
        "Open": quote["open"], "High": quote["high"],
        "Low": quote["low"], "Close": quote["close"],
    }, index=pd.to_datetime(timestamps, unit="s"))
    df = df.dropna()
    if df.empty:
        raise RuntimeError(f"No data returned for {symbol}.")
    return df


def add_indicators(df):
    df = df.copy()
    df["EMA20"] = df["Close"].ewm(span=20, adjust=False).mean()
    df["EMA50"] = df["Close"].ewm(span=50, adjust=False).mean()
    delta = df["Close"].diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.rolling(14).mean()
    avg_loss = loss.rolling(14).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    df["RSI"] = 100 - (100 / (1 + rs))
    df["SwingHigh"] = df["High"].rolling(SWING_LOOKBACK).max()
    df["SwingLow"] = df["Low"].rolling(SWING_LOOKBACK).min()
    return df


def build_signal(df, symbol_name):
    """
    Entry = current market price (not a past support/resistance level).
    We only use EMA trend + RSI momentum to decide direction.
    """
    last = df.iloc[-1]
    price = float(last["Close"])  # current price -> used directly as entry
    ema20, ema50 = float(last["EMA20"]), float(last["EMA50"])
    rsi = float(last["RSI"])
    swing_high, swing_low = float(last["SwingHigh"]), float(last["SwingLow"])

    if any(pd.isna(v) for v in [ema20, ema50, rsi, swing_high, swing_low]):
        return None

    direction = None
    # Bullish momentum: uptrend + RSI healthy (not overbought)
    if ema20 > ema50 and 45 < rsi < 70:
        direction = "BUY"
        stop_loss = swing_low - (swing_high - swing_low) * 0.1
        risk = price - stop_loss
        if risk <= 0:
            return None
        targets = [price + risk * r for r in RISK_REWARD_TIERS]

    # Bearish momentum: downtrend + RSI healthy (not oversold)
    elif ema20 < ema50 and 30 < rsi < 55:
        direction = "SELL"
        stop_loss = swing_high + (swing_high - swing_low) * 0.1
        risk = stop_loss - price
        if risk <= 0:
            return None
        targets = [price - risk * r for r in RISK_REWARD_TIERS]

    if direction is None:
        return None

    return {
        "symbol_name": symbol_name, "direction": direction, "price": price,
        "stop_loss": stop_loss, "targets": targets, "rsi": rsi,
        "ema20": ema20, "ema50": ema50, "time": last.name,
    }


def format_message(sig):
    arrow = "شراء (BUY)" if sig["direction"] == "BUY" else "بيع (SELL)"
    tp_lines = "\n".join(
        f"  TP{i+1} (1:{r}): {tp:.2f}"
        for i, (r, tp) in enumerate(zip(RISK_REWARD_TIERS, sig["targets"]))
    )
    msg = (
        f"اشارة: {sig['symbol_name']}\n{arrow}\n\n"
        f"سعر الدخول (السعر الحالي): {sig['price']:.2f}\n"
        f"وقف الخسارة: {sig['stop_loss']:.2f}\n{tp_lines}\n\n"
        f"RSI: {sig['rsi']:.1f} | EMA20: {sig['ema20']:.2f} | EMA50: {sig['ema50']:.2f}\n"
        f"{sig['time']}\n\nاشارة فنية آلية وليست نصيحة استثمارية. نفذها بسعر السوق فورا."
    )
    return msg


def send_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message}
    try:
        r = requests.post(url, data=payload, timeout=10)
        r.raise_for_status()
    except Exception as e:
        print(f"[ERROR] Failed to send Telegram message: {e}")


def init_log():
    if not os.path.exists(LOG_FILE):
        with open(LOG_FILE, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["time", "symbol", "direction", "entry", "sl", "tp1", "tp2", "tp3", "status", "closed_at"])


def log_signal(sig, symbol):
    with open(LOG_FILE, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            sig["time"], symbol, sig["direction"], sig["price"], sig["stop_loss"],
            sig["targets"][0], sig["targets"][1], sig["targets"][2], "OPEN", ""
        ])


def check_open_signals(symbol, current_price):
    if not os.path.exists(LOG_FILE):
        return
    with open(LOG_FILE, "r", newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        header = next(reader)
        rows = list(reader)

    updated = []
    for row in rows:
        time_, sym, direction, entry, sl, tp1, tp2, tp3, status, closed_at = row
        if status == "OPEN" and sym == symbol:
            entry, sl, tp1, tp2, tp3 = map(float, [entry, sl, tp1, tp2, tp3])
            new_status = None
            if direction == "BUY":
                if current_price <= sl:
                    new_status = "LOSS (SL)"
                elif current_price >= tp3:
                    new_status = "WIN (TP3)"
                elif current_price >= tp2:
                    new_status = "WIN (TP2)"
                elif current_price >= tp1:
                    new_status = "WIN (TP1)"
            else:
                if current_price >= sl:
                    new_status = "LOSS (SL)"
                elif current_price <= tp3:
                    new_status = "WIN (TP3)"
                elif current_price <= tp2:
                    new_status = "WIN (TP2)"
                elif current_price <= tp1:
                    new_status = "WIN (TP1)"
            if new_status:
                row[8] = new_status
                row[9] = str(datetime.now(timezone.utc))
                send_telegram(f"تحديث نتيجة اشارة {sym} {direction} من {time_}:\n{new_status}\nالسعر الحالي: {current_price:.2f}")
        updated.append(row)

    with open(LOG_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(header)
        writer.writerows(updated)


def send_stats():
    if not os.path.exists(LOG_FILE):
        return
    with open(LOG_FILE, "r", newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        header = next(reader)
        rows = list(reader)
    closed = [r for r in rows if r[8] != "OPEN"]
    wins = [r for r in closed if r[8].startswith("WIN")]
    total = len(closed)
    if total == 0:
        return
    win_rate = (len(wins) / total) * 100
    msg = f"احصائيات عامة حتى الآن:\nاجمالي الصفقات المغلقة: {total}\nرابحة: {len(wins)}\nنسبة النجاح: {win_rate:.1f}%"
    send_telegram(msg)


def main_loop():
    print(f"Starting signal bot | symbols={list(SYMBOLS.keys())}")
    init_log()
    last_direction = {sym: None for sym in SYMBOLS}
    bars_since_last = {sym: COOLDOWN_BARS for sym in SYMBOLS}
    loop_count = 0

    while True:
        for symbol, name in SYMBOLS.items():
            try:
                df = fetch_data(symbol)
                df = add_indicators(df)
                current_price = float(df.iloc[-1]["Close"])

                check_open_signals(symbol, current_price)

                sig = build_signal(df, name)
                bars_since_last[symbol] += 1

                if sig and (sig["direction"] != last_direction[symbol] or bars_since_last[symbol] >= COOLDOWN_BARS):
                    msg = format_message(sig)
                    send_telegram(msg)
                    log_signal(sig, symbol)
                    last_direction[symbol] = sig["direction"]
                    bars_since_last[symbol] = 0
                    print(f"[{name}] Signal sent: {sig['direction']} @ {sig['price']:.2f}")
                else:
                    print(f"[{name}] No new setup. Price={current_price:.2f}")

            except Exception as e:
                print(f"[ERROR - {name}] {e}")

        loop_count += 1
        if loop_count % 20 == 0:
            send_stats()

        time.sleep(CHECK_EVERY_SECONDS)


if __name__ == "__main__":
    main_loop()
