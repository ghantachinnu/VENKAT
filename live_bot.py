import os
import time
import datetime
import pandas as pd
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from fyers_apiv3 import fyersModel

# --- CONFIGURATION ---
client_id = os.environ.get("FYERS_CLIENT_ID", "").strip()
access_token = os.environ.get("FYERS_ACCESS_TOKEN", "").strip()

# --- STRATEGY SETTINGS (Your Rules) ---
SYMBOLS = ["NSE:NIFTY50-INDEX", "NSE:NIFTYBANK-INDEX"]
TIMEFRAME = "1"             # 1-minute candles
INITIAL_CAPITAL = 50000     # Starting capital
LOT_SIZE_NIFTY = 75         # Nifty Lot Size
STOP_LOSS_POINTS = 35       # Fixed SL Points

# --- 1. RISK MANAGEMENT LOGIC ---
def calculate_position_size(current_capital, symbol):
    """
    Calculates number of lots based on capital compounding.
    Rule: 1 Lot for initial 50k, add 1 lot for every 50k profit.
    """
    if current_capital < INITIAL_CAPITAL:
        lots = 1
    else:
        # Integer division to find how many 50ks we have
        additional_lots = int((current_capital - INITIAL_CAPITAL) // 50000)
        lots = 1 + additional_lots
    
    # Determine quantity based on symbol
    if "NIFTY50" in symbol:
        qty = lots * LOT_SIZE_NIFTY
    else:
        qty = lots * 15 # Bank Nifty Lot Size
        
    return lots, qty

# --- 2. CONNECT TO FYERS ---
def connect_to_fyers():
    print(f"--- DEBUG INFO ---")
    if len(access_token) > 10:
        print(f"Token Check: {access_token[:5]}...{access_token[-5:]}")
    else:
        print("Token Check: INVALID/EMPTY")

    try:
        fyers = fyersModel.FyersModel(client_id=client_id, token=access_token, log_path="")
        response = fyers.get_profile()
        if response.get("code") == 200:
            print(f"SUCCESS: Connected as {response['data']['name']}")
            return fyers
        else:
            print(f"LOGIN FAILED: {response}")
            return None
    except Exception as e:
        print(f"CRITICAL ERROR: {e}")
        return None

# --- 3. MARKET SCANNER (Strategy) ---
def check_strategy(fyers, symbol):
    try:
        today = datetime.date.today()
        start = today - datetime.timedelta(days=5)
        
        data = {
            "symbol": symbol,
            "resolution": TIMEFRAME,
            "date_format": "0",
            "range_from": start.strftime('%Y-%m-%d'),
            "range_to": today.strftime('%Y-%m-%d'),
            "cont_flag": "1"
        }
        
        response = fyers.history(data=data)
        if response.get('s') != 'ok': return

        df = pd.DataFrame(response['candles'], columns=['date', 'open', 'high', 'low', 'close', 'vol'])
        
        # Calculate Indicators
        df['SMA9'] = df['close'].rolling(9).mean()
        df['SMA20'] = df['close'].rolling(20).mean()
        df['SMA50'] = df['close'].rolling(50).mean()
        
        curr = df.iloc[-1]
        prev = df.iloc[-2]
        
        # Calculate Logic
        uptrend = (curr['SMA9'] > curr['SMA20']) and (curr['SMA20'] > curr['SMA50'])
        crossover = (prev['SMA9'] < prev['SMA20']) and (curr['SMA9'] > curr['SMA20'])
        
        # Calculate Risk Sizing
        # (In real trading, we would fetch actual balance via API)
        # For now, using INITIAL_CAPITAL to demonstrate logic
        lots, qty = calculate_position_size(INITIAL_CAPITAL, symbol)
        
        print(f"Scanning {symbol} | Price: {curr['close']} | SMA9: {curr['SMA9']:.2f}")

        if uptrend and crossover:
            sl_price = curr['close'] - STOP_LOSS_POINTS
            print(f"\n>>> BUY SIGNAL DETECTED! <<<")
            print(f"Instrument: {symbol}")
            print(f"Entry Price: {curr['close']}")
            print(f"Stop Loss:   {sl_price} (-{STOP_LOSS_POINTS} pts)")
            print(f"Position:    {lots} Lots ({qty} Qty)")
            print("---------------------------------")

    except Exception as e:
        print(f"Strategy Error: {e}")

# --- 4. TRADING LOOP ---
def run_trading_logic():
    print("--- BOT STARTING (STRATEGY + RISK MODE) ---")
    fyers = connect_to_fyers()
    
    if not fyers: return

    while True:
        print(f"\nTime: {datetime.datetime.now()}")
        for sym in SYMBOLS:
            check_strategy(fyers, sym)
        time.sleep(60)

# --- 5. WEB SERVER (Render Keep-Alive) ---
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is running!")

def start_web_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), HealthCheckHandler)
    print(f"Web Server started on port {port}")
    server.serve_forever()

if __name__ == "__main__":
    t = threading.Thread(target=run_trading_logic)
    t.daemon = True
    t.start()
    start_web_server()
