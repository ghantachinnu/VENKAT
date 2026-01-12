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

SYMBOLS = ["NSE:NIFTYBANK-INDEX", "NSE:NIFTY50-INDEX"]
TIMEFRAME = "1"

# --- 1. CONNECT TO FYERS ---
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

# --- 2. STRATEGY LOGIC ---
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
        
        # --- DEBUG PRINT: Show error if data fails ---
        if response.get('s') != 'ok': 
            print(f"DATA ERROR for {symbol}: {response}")
            return

        df = pd.DataFrame(response['candles'], columns=['date', 'open', 'high', 'low', 'close', 'vol'])
        df['SMA9'] = df['close'].rolling(9).mean()
        df['SMA20'] = df['close'].rolling(20).mean()
        
        curr = df.iloc[-1]
        prev = df.iloc[-2]
        
        print(f"Scanning {symbol} | Price: {curr['close']} | SMA9: {curr['SMA9']:.2f}")

        uptrend = (curr['SMA9'] > curr['SMA20'])
        cross = (prev['SMA9'] < prev['SMA20']) and (curr['SMA9'] > curr['SMA20'])
        
        if uptrend and cross:
            print(">>> BUY SIGNAL DETECTED! <<<")

    except Exception as e:
        print(f"Strategy Error: {e}")

# --- 3. TRADING LOOP ---
def run_trading_logic():
    print("--- BOT STARTING (DEBUG MODE) ---")
    fyers = connect_to_fyers()
    
    if not fyers: return

    while True:
        print(f"\nTime: {datetime.datetime.now()}")
        for sym in SYMBOLS:
            check_strategy(fyers, sym)
        time.sleep(60)

# --- 4. WEB SERVER ---
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
