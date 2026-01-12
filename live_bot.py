import os
import time
import datetime
import pandas as pd
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from fyers_apiv3 import fyersModel

# ── CONFIGURATION ────────────────────────────────────────────────────────────
client_id = os.environ.get("FYERS_CLIENT_ID", "").strip()
access_token = os.environ.get("FYERS_ACCESS_TOKEN", "").strip()

SYMBOLS = ["NSE:NIFTY50-INDEX", "NSE:NIFTYBANK-INDEX"]
TIMEFRAME = "5"                     # Good choice for options context
INITIAL_CAPITAL = 50000
LOT_SIZE_NIFTY = 25                 # You're using 2026 context → ok
LOT_SIZE_BANKNIFTY = 15

# ── RISK MANAGEMENT ──────────────────────────────────────────────────────────
def calculate_position_size(current_capital, symbol):
    if current_capital < INITIAL_CAPITAL:
        lots = 1
    else:
        additional = int((current_capital - INITIAL_CAPITAL) // 100000)
        lots = 1 + additional

    if "NIFTY50" in symbol:
        qty = lots * LOT_SIZE_NIFTY
    else:
        qty = lots * LOT_SIZE_BANKNIFTY

    return lots, qty

# ── FYERS CONNECTION ─────────────────────────────────────────────────────────
def connect_to_fyers():
    print("Connecting to Fyers API...")
    try:
        fyers = fyersModel.FyersModel(client_id=client_id, is_async=False,
                                     token=access_token, log_path="")
        profile = fyers.get_profile()
        if profile.get("code") == 200:
            print(f"Connected as {profile['data']['name']}")
            return fyers
        else:
            print("Connection failed:", profile)
            return None
    except Exception as e:
        print("Connection error:", e)
        return None

# ── MAIN STRATEGY ────────────────────────────────────────────────────────────
def check_strategy(fyers, symbol):
    try:
        today = datetime.date.today()
        start = today - datetime.timedelta(days=6)

        data = {
            "symbol": symbol,
            "resolution": TIMEFRAME,
            "date_format": "1",               # ← FIXED: Must be "1" with YYYY-MM-DD
            "range_from": start.strftime('%Y-%m-%d'),
            "range_to": today.strftime('%Y-%m-%d'),
            "cont_flag": "1"
        }

        hist = fyers.history(data=data)
        if hist.get('s') != 'ok':
            print(f"History fetch failed for {symbol}: {hist}")
            return

        df = pd.DataFrame(hist['candles'], columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='s')

        current_price = float(df['close'].iloc[-1])

        # Trend
        df['ema9']  = df['close'].ewm(span=9, adjust=False).mean()
        df['ema21'] = df['close'].ewm(span=21, adjust=False).mean()

        trend_up   = df['ema9'].iloc[-1] > df['ema21'].iloc[-1]
        trend_down = df['ema9'].iloc[-1] < df['ema21'].iloc[-1]

        # Volatility (ATR %)
        tr = pd.concat([df['high'] - df['low'],
                        abs(df['high'] - df['close'].shift()),
                        abs(df['low'] - df['close'].shift())], axis=1).max(axis=1)
        atr = tr.rolling(14).mean().iloc[-1]
        vol_pct = (atr / current_price) * 100

        # Momentum proxy
        momentum_5  = (current_price - df['close'].iloc[-5]) / df['close'].iloc[-5] * 100
        momentum_10 = (current_price - df['close'].iloc[-10]) / df['close'].iloc[-10] * 100

        # ── Option Chain ───────────────────────────────────────────────────────
        chain = fyers.option_chain({"symbol": symbol.replace("-INDEX", "")})
        if chain.get('code') != 200:
            print("Option chain failed")
            return

        options = chain['data']['options']
        atm_strike = round(current_price / 50) * 50

        ce = None
        pe = None
        for opt in options:
            if opt['strike_price'] == atm_strike:
                if opt['option_type'] == 'CE':
                    ce = opt
                elif opt['option_type'] == 'PE':
                    pe = opt

        if not ce or not pe:
            return

        # ── Signal Conditions ──────────────────────────────────────────────────
        signal = None
        direction = None
        premium = None

        if (trend_up and momentum_5 > 0.4 and momentum_10 > 0.25 and
            vol_pct > 0.9 and 60 <= ce.get('ltp', 0) <= 250):
            signal = True
            direction = "CE"
            premium = ce.get('ltp', 0)

        elif (trend_down and momentum_5 < -0.4 and momentum_10 < -0.25 and
              vol_pct > 0.9 and 60 <= pe.get('ltp', 0) <= 250):
            signal = True
            direction = "PE"
            premium = pe.get('ltp', 0)

        if signal:
            lots, qty = calculate_position_size(INITIAL_CAPITAL, symbol)
            sl_points = 45 if direction == "CE" else 50
            sl_price = current_price - sl_points if direction == "CE" else current_price + sl_points

            print("\n" + "═" * 80)
            print("       ATM DIRECTIONAL OPTION BUY SIGNAL")
            print("═" * 80)
            print(f"Direction     : BUY {direction}")
            print(f"Underlying    : {current_price:.2f}")
            print(f"ATM Strike    : {atm_strike}")
            print(f"Premium       : ₹{premium:.1f}")
            print(f"SL Distance   : {sl_points} points")
            print(f"Position      : {lots} Lots ({qty} Qty)")
            print(f"Volatility    : {vol_pct:.1f}%")
            print(f"Mom 5/10      : {momentum_5:+.2f}% / {momentum_10:+.2f}%")
            print("═" * 80 + "\n")

    except Exception as e:
        print(f"Strategy error {symbol}: {e}")

# ── MAIN LOOP ────────────────────────────────────────────────────────────────
def run_trading_logic():
    print("=== ATM MOMENTUM OPTION BUYER BOT STARTED ===")
    fyers = connect_to_fyers()
    if not fyers:
        return

    while True:
        print(f"\nScan at {datetime.datetime.now().strftime('%H:%M:%S')}")
        for symbol in SYMBOLS:
            check_strategy(fyers, symbol)
        time.sleep(300)  # 5 min

# ── HEALTH SERVER ────────────────────────────────────────────────────────────
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot running")

def start_web_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), HealthCheckHandler)
    print(f"Health server on port {port}")
    server.serve_forever()

if __name__ == "__main__":
    t = threading.Thread(target=run_trading_logic, daemon=True)
    t.start()
    start_web_server()
