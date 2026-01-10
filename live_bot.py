import os
import time
import datetime
import pandas as pd
from fyers_apiv3 import fyersModel

# --- CONFIGURATION ---
client_id = os.environ.get("FYERS_CLIENT_ID")
access_token = os.environ.get("FYERS_ACCESS_TOKEN")

# SYMBOLS TO TRACK (Spot Indices)
SYMBOLS = ["NSE:NIFTYBANK-INDEX", "NSE:NIFTY50-INDEX"]
TIMEFRAME = "1"  # 1-minute candles

# --- 1. CONNECT ---
def connect_to_fyers():
    print(f"--- DEBUG INFO ---")
    print(f"Client ID being used: {client_id}")
    print(f"Token length: {len(access_token) if access_token else 0}")
    
    try:
        fyers = fyersModel.FyersModel(client_id=client_id, token=access_token, log_path="")
        response = fyers.get_profile()
        print(f"FYERS RESPONSE: {response}")
        
        if response.get("code") == 200:
            print(f"SUCCESS: Connected as {response['data']['name']}")
            return fyers
        else:
            print("ERROR: Connection Failed. Check Token in Render.")
            return None
    except Exception as e:
        print(f"CRITICAL ERROR: {e}")
        return None

# --- 2. STRATEGY LOGIC ---
def check_strategy(fyers, symbol):
    try:
        today = datetime.date.today()
        start_date = today - datetime.timedelta(days=5)
        
        data = {
            "symbol": symbol,
            "resolution": TIMEFRAME,
            "date_format": "0",
            "range_from": start_date.strftime('%Y-%m-%d'),
            "range_to": today.strftime('%Y-%m-%d'),
            "cont_flag": "1"
        }
        
        response = fyers.history(data=data)
        
        if response.get('s') != 'ok':
            print(f"No Data for {symbol}")
            return
        
        candles = response['candles']
        df = pd.DataFrame(candles, columns=['date', 'open', 'high', 'low', 'close', 'volume'])
        
        df['SMA9'] = df['close'].rolling(window=9).mean()
        df['SMA20'] = df['close'].rolling(window=20).mean()
        df['SMA50'] = df['close'].rolling(window=50).mean()
        
        curr = df.iloc[-1]
        prev = df.iloc[-2]
        
        print(f"\n--- SCANNING {symbol} ---")
        print(f"Price: {curr['close']} | SMA9: {curr['SMA9']:.2f} | SMA20: {curr['SMA20']:.2f}")
        
        is_uptrend = (curr['SMA9'] > curr['SMA20']) and (curr['SMA20'] > curr['SMA50'])
        crossover = (prev['SMA9'] < prev['SMA20']) and (curr['SMA9'] > curr['SMA20'])
        
        if is_uptrend and crossover:
            print(">>> BUY SIGNAL DETECTED! <<<")
        else:
            print("Result: No Trade Signal")
    except Exception as e:
        print(f"Strategy Error: {e}")

# --- 3. MAIN LOOP ---
def run_bot():
    print("--- BOT STARTING ---")
    fyers = connect_to_fyers()
    
    if not fyers:
        print("STOPPING BOT DUE TO ERROR.")
        return
    
    while True:
        print(f"\nTime: {datetime.datetime.now()}")
        for sym in SYMBOLS:
            check_strategy(fyers, sym)
        
        print("Bot is alive...")
        time.sleep(60)

if __name__ == "__main__":
    run_bot()
