import os
import time
import datetime
import pandas as pd
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from fyers_apiv3 import fyersModel

# ── CONFIG ───────────────────────────────────────────────────────────────────
client_id = os.environ.get("FYERS_CLIENT_ID", "").strip()
access_token = os.environ.get("FYERS_ACCESS_TOKEN", "").strip()

SYMBOLS = ["NSE:NIFTY50-INDEX", "NSE:NIFTYBANK-INDEX"]
TIMEFRAME = "5"
INITIAL_CAPITAL = 50000
LOT_SIZE_NIFTY = 25                 # your simulation choice
LOT_SIZE_BANKNIFTY = 15

# ── POSITION SIZING ──────────────────────────────────────────────────────────
def calculate_position_size(current_capital, symbol):
    if current_capital < INITIAL_CAPITAL:
        lots = 1
    else:
        additional = int((current_capital - INITIAL_CAPITAL) // 100000)
        lots = 1 + additional

    qty = lots * (LOT_SIZE_NIFTY if "NIFTY50" in symbol else LOT_SIZE_BANKNIFTY)
    return lots, qty

# ── CONNECT ──────────────────────────────────────────────────────────────────
def connect_to_fyers():
    print("Connecting to Fyers...")
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

# ── STRATEGY ─────────────────────────────────────────────────────────────────
def check_strategy(fyers, symbol):
    try:
        today = datetime.date.today()
        start = today - datetime.timedelta(days=6)

        hist_data = {
            "symbol": symbol,
            "resolution": TIMEFRAME,
            "date_format": "1",
            "range_from": start.strftime('%Y-%m-%d'),
            "range_to": today.strftime('%Y-%m-%d'),
            "cont_flag": "1"
        }

        hist = fyers.history(hist_data)
        if hist.get('s') != 'ok':
            print(f"History failed {symbol}: {hist}")
            return

        df = pd.DataFrame(hist['candles'], columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='s')

        current_price = float(df['close'].iloc[-1])

        # Indicators
        df['ema9']  = df['close'].ewm(span=9, adjust=False).mean()
        df['ema21'] = df['close'].ewm(span=21, adjust=False).mean()
        trend_up   = df['ema9'].iloc[-1] > df['ema21'].iloc[-1]
        trend_down = df['ema9'].iloc[-1] < df['ema21'].iloc[-1]

        tr = pd.concat([df['high'] - df['low'],
                        abs(df['high'] - df['close'].shift()),
                        abs(df['low'] - df['close'].shift())], axis=1).max(axis=1)
        atr = tr.rolling(14).mean().iloc[-1]
        vol_pct = (atr / current_price) * 100

        mom5  = (current_price - df['close'].iloc[-5]) / df['close'].iloc[-5] * 100 if len(df) >= 5 else 0
        mom10 = (current_price - df['close'].iloc[-10]) / df['close'].iloc[-10] * 100 if len(df) >= 10 else 0

        # ── OPTION CHAIN ───────────────────────────────────────────────────────
        chain_payload = {
            "symbol": symbol,                    # ← FIXED: full symbol "NSE:NIFTY50-INDEX"
            "strikecount": "10",                 # limited range = faster response
            "timestamp": ""
        }

        chain_resp = fyers.optionchain(chain_payload)

        if chain_resp.get('code') != 200:
            print(f"Optionchain failed {symbol}: {chain_resp.get('message', chain_resp)}")
            return

        if 'data' not in chain_resp or 'options' not in chain_resp['data']:
            print(f"Invalid optionchain response {symbol}: {chain_resp}")
            return

        options = chain_resp['data']['options']

        atm_strike = round(current_price / 50) * 50

        ce = None
        pe = None
        for opt in options:
            if opt.get('strike_price') == atm_strike:
                opt_type = opt.get('option_type')
                if opt_type == 'CE':
                    ce = opt
                elif opt_type == 'PE':
                    pe = opt

        if not ce or not pe:
            print(f"No ATM CE/PE found {symbol} @ {atm_strike}")
            return

        # ── SIGNAL ─────────────────────────────────────────────────────────────
        signal = None
        direction = None
        premium = None

        if (trend_up and mom5 > 0.4 and mom10 > 0.25 and vol_pct > 0.9 and
            60 <= ce.get('ltp', 0) <= 250):
            signal = True
            direction = "CE"
            premium = ce.get('ltp', 0)

        elif (trend_down and mom5 < -0.4 and mom10 < -0.25 and vol_pct > 0.9 and
              60 <= pe.get('ltp', 0) <= 250):
            signal = True
            direction = "PE"
            premium = pe.get('ltp', 0)

        if signal:
            lots, qty = calculate_position_size(INITIAL_CAPITAL, symbol)
            sl_pts = 45 if direction == "CE" else 50

            print("\n" + "═"*80)
            print("       PAPER SIGNAL - ATM MOMENTUM BUY")
            print("═"*80)
            print(f"Direction  : BUY {direction}")
            print(f"Underlying : {current_price:.2f}")
            print(f"Strike     : {atm_strike}")
            print(f"Premium    : ₹{premium:.1f}")
            print(f"SL dist    : {sl_pts} pts")
            print(f"Size       : {lots} lots ({qty} qty)")
            print(f"Vol %      : {vol_pct:.1f}")
            print(f"Mom 5/10   : {mom5:+.2f}% / {mom10:+.2f}%")
            print("═"*80 + "\n")

            # For tracking in spreadsheet
            print(f"CSV_LOG,{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')},{symbol},{direction},{current_price:.2f},{atm_strike},{premium:.1f},{mom5:.2f},{mom10:.2f},{vol_pct:.2f},{lots},{qty}")

    except Exception as e:
        print(f"Strategy error {symbol}: {str(e)}")

# ── MAIN LOOP + HEALTH ───────────────────────────────────────────────────────
def run_trading_logic():
    print("=== PAPER MODE - ATM MOMENTUM OPTION BUYER STARTED ===")
    fyers = connect_to_fyers()
    if not fyers:
        return

    while True:
        print(f"\nScan at {datetime.datetime.now().strftime('%H:%M:%S')}")
        for sym in SYMBOLS:
            check_strategy(fyers, sym)
        time.sleep(300)

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
