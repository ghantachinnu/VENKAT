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
LOT_SIZE_NIFTY = 25
LOT_SIZE_BANKNIFTY = 15

# Paper trading simulation settings
MAX_HOLD_MINUTES = 60
PROFIT_TARGET_PCT = 80.0
STOP_LOSS_PCT = -50.0

# ── GLOBAL STATE for paper trades ────────────────────────────────────────────
active_trades = []  # list of dicts for open simulated positions

# ── POSITION SIZING ──────────────────────────────────────────────────────────
def calculate_position_size(current_capital, symbol):
    if current_capital < INITIAL_CAPITAL:
        lots = 1
    else:
        additional = int((current_capital - INITIAL_CAPITAL) // 100000)
        lots = 1 + additional

    qty = lots * (LOT_SIZE_NIFTY if "NIFTY50" in symbol else LOT_SIZE_BANKNIFTY)
    return lots, qty

# ── FYERS CONNECTION ─────────────────────────────────────────────────────────
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

# ── MAIN STRATEGY + PAPER SIMULATION ─────────────────────────────────────────
def check_strategy(fyers, symbol):
    global active_trades
    try:
        now = datetime.datetime.now()
        today = now.date()
        start = today - datetime.timedelta(days=6)

        # History data
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
            print(f"History failed {symbol}: {hist.get('message', hist)}")
            return

        df = pd.DataFrame(hist['candles'], columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='s')

        if len(df) < 20:
            print(f"Not enough data {symbol}")
            return

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

        mom5  = (current_price - df['close'].iloc[-5]) / df['close'].iloc[-5] * 100
        mom10 = (current_price - df['close'].iloc[-10]) / df['close'].iloc[-10] * 100

        # Option chain
        chain_payload = {
            "symbol": symbol,
            "strikecount": "30",   # increased to improve ATM match chance
            "timestamp": ""
        }

        chain_resp = fyers.optionchain(chain_payload)

        if chain_resp.get('code') != 200:
            print(f"Optionchain failed {symbol}: {chain_resp.get('message', chain_resp)}")
            return

        if 'data' not in chain_resp or 'optionsChain' not in chain_resp['data']:
            print(f"Invalid chain structure {symbol}")
            return

        options = chain_resp['data']['optionsChain']

        # Find closest ATM strike
        target_strike = round(current_price / 50) * 50
        closest_opt = min(options, key=lambda x: abs(x.get('strike_price', 0) - target_strike))
        atm_strike = closest_opt.get('strike_price')

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
            print(f"No CE/PE at strike {atm_strike} for {symbol}")
            return

        # ── ENTRY SIGNAL ───────────────────────────────────────────────────────
        direction = None
        entry_premium = None

        if (trend_up and mom5 > 0.4 and mom10 > 0.25 and vol_pct > 0.9 and
            60 <= ce.get('ltp', 0) <= 250):
            direction = "CE"
            entry_premium = ce.get('ltp', 0)

        elif (trend_down and mom5 < -0.4 and mom10 < -0.25 and vol_pct > 0.9 and
              60 <= pe.get('ltp', 0) <= 250):
            direction = "PE"
            entry_premium = pe.get('ltp', 0)

        if direction:
            lots, qty = calculate_position_size(INITIAL_CAPITAL, symbol)

            trade = {
                'entry_time': now,
                'symbol': symbol,
                'direction': direction,
                'underlying': current_price,
                'strike': atm_strike,
                'entry_premium': entry_premium,
                'lots': lots,
                'qty': qty,
                'status': 'OPEN'
            }
            active_trades.append(trade)

            print("\n" + "═"*80)
            print("       PAPER TRADE OPENED")
            print("═"*80)
            print(f"Time         : {now.strftime('%H:%M:%S')}")
            print(f"Direction    : BUY {direction}")
            print(f"Underlying   : {current_price:.2f}")
            print(f"Strike       : {atm_strike}")
            print(f"Entry Prem   : ₹{entry_premium:.1f}")
            print(f"Position     : {lots} lots ({qty} qty)")
            print("═"*80 + "\n")

            print(f"CSV_ENTRY,{now.strftime('%Y-%m-%d %H:%M:%S')},{symbol},{direction},{current_price:.2f},{atm_strike},{entry_premium:.1f},{lots},{qty}")

        # ── CHECK EXISTING TRADES FOR EXIT ─────────────────────────────────────
        to_close = []
        for trade in active_trades:
            if trade['status'] != 'OPEN':
                continue

            minutes_held = (now - trade['entry_time']).total_seconds() / 60

            # Rough exit approximation (no real-time LTP polling)
            current_premium = trade['entry_premium']   # conservative: assume no change

            pnl_pct = ((current_premium - trade['entry_premium']) / trade['entry_premium']) * 100 if trade['entry_premium'] > 0 else 0

            exit_reason = None
            if pnl_pct >= PROFIT_TARGET_PCT:
                exit_reason = f"PROFIT TARGET +{PROFIT_TARGET_PCT}%"
            elif pnl_pct <= STOP_LOSS_PCT:
                exit_reason = f"STOP LOSS {STOP_LOSS_PCT}%"
            elif minutes_held >= MAX_HOLD_MINUTES:
                exit_reason = f"HOLD TIME {MAX_HOLD_MINUTES} min"

            if exit_reason:
                pnl_points = current_premium - trade['entry_premium']
                pnl_rupees = pnl_points * trade['qty']

                print("\n" + "═"*80)
                print("       PAPER TRADE CLOSED")
                print("═"*80)
                print(f"Entry time   : {trade['entry_time'].strftime('%H:%M:%S')}")
                print(f"Direction    : {trade['direction']}")
                print(f"Strike       : {trade['strike']}")
                print(f"Entry Prem   : ₹{trade['entry_premium']:.1f}")
                print(f"Exit Prem    : ₹{current_premium:.1f} (approx)")
                print(f"PnL points   : {pnl_points:+.1f}")
                print(f"PnL rupees   : ₹{pnl_rupees:+,.0f}")
                print(f"Reason       : {exit_reason}")
                print("═"*80 + "\n")

                print(f"CSV_EXIT,{now.strftime('%Y-%m-%d %H:%M:%S')},{trade['symbol']},{trade['direction']},{pnl_points:.1f},{pnl_rupees:+,.0f},{exit_reason}")

                trade['status'] = 'CLOSED'
                to_close.append(trade)

        # Remove closed trades
        for t in to_close:
            active_trades.remove(t)

        # Debug why no signal
        if not direction:
            print(f"DEBUG {symbol} | Price: {current_price:.2f} | EMA up: {trend_up} | Mom5/10: {mom5:.2f}/{mom10:.2f} | Vol%: {vol_pct:.2f} | No signal")

    except Exception as e:
        print(f"ERROR {symbol}: {str(e)}")

# ── MAIN LOOP ────────────────────────────────────────────────────────────────
def run_trading_logic():
    print("=== PAPER TRADING SIMULATION STARTED ===")
    print(f"Rules: max hold {MAX_HOLD_MINUTES} min | TP {PROFIT_TARGET_PCT}% | SL {STOP_LOSS_PCT}%")
    fyers = connect_to_fyers()
    if not fyers:
        return

    while True:
        print(f"\nScan at {datetime.datetime.now().strftime('%H:%M:%S')}")
        for sym in SYMBOLS:
            check_strategy(fyers, sym)
        time.sleep(300)  # 5 minutes

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
