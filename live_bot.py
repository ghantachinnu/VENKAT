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
    try:
        fyers = fyersModel.FyersModel(client_id=client_id, token=access_token, log_path="")
        response = fyers.get_profile()
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
        # Get last 5 days of data to ensure we have enough for SMA calculation
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

        # Create Table
        candles = response['candles']
        df = pd.DataFrame(candles, columns=['date', 'open', 'high', 'low', 'close', 'volume'])
        
        # Calculate Indicators
        df['SMA9'] = df['close'].rolling(window=9).mean()
        df['SMA20'] = df['close'].rolling(window=20).mean()
        df['SMA50'] = df['close'].rolling(window=50).mean()
        
        # Get Latest Candle
        curr = df.iloc[-1]
        prev = df.iloc[-2]
        
        print(f"\n--- SCANNING {symbol} ---")
        print(f"Price: {curr['close']} | SMA9: {curr['SMA9']:.2f} | SMA20: {curr['SMA20']:.2f}")

        # LOGIC: SMA 9 Crossing ABOVE SMA 20
        # 1. Trend Align (9 > 20 > 50)
        is_uptrend = (curr['SMA9'] > curr['SMA20']) and (curr['SMA20'] > curr['SMA50'])
        
        # 2. Crossover (Previous candle was below, Current is above)
        crossover = (prev['SMA9'] < prev['SMA20']) and (curr['SMA9'] > curr['SMA20'])
        
        if is_uptrend and crossover:
            print(">>> BUY SIGNAL DETECTED! <<<")
            print("(Bot would buy a Call Option here)")
        else:
            print("Result: No Trade Signal")

    except Exception as e:
        print(f"Strategy Error: {e}")

# --- 3. MAIN LOOP ---
def run_bot():
    print("--- BOT STARTING (STRATEGY MODE) ---")
    fyers = connect_to_fyers()
    
    if not fyers:
        return

    while True:
        print(f"\nTime: {datetime.datetime.now()}")
        for sym in SYMBOLS:
            check_strategy(fyers, sym)
        
        # Wait 60 seconds
        time.sleep(60)

if __name__ == "__main__":
    run_bot()
