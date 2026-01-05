import os
import time
import datetime
import pandas as pd
from fyers_apiv3 import fyersModel

# --- CONFIGURATION ---
client_id = os.environ.get("FYERS_CLIENT_ID")
access_token = os.environ.get("FYERS_ACCESS_TOKEN")

# --- DEBUG CONNECTION ---
def connect_to_fyers():
    print(f"--- DEBUG INFO ---")
    print(f"Client ID being used: {client_id}")
    print(f"Token length: {len(access_token) if access_token else 0}")
    
    try:
        fyers = fyersModel.FyersModel(client_id=client_id, token=access_token, log_path="")
        response = fyers.get_profile()
        
        # PRINT THE EXACT ERROR FROM FYERS
        print(f"FYERS RESPONSE: {response}")
        
        if response.get("code") == 200:
            print(f"SUCCESS: Connected as {response['data']['name']}")
            return fyers
        else:
            print("ERROR: Login Failed.")
            return None
    except Exception as e:
        print(f"CRITICAL ERROR: {e}")
        return None

def run_bot():
    print("--- BOT STARTING (DEBUG MODE) ---")
    fyers = connect_to_fyers()
    
    if not fyers:
        print("STOPPING BOT DUE TO ERROR.")
        return

    while True:
        print("Bot is alive...")
        time.sleep(60)

if __name__ == "__main__":
    run_bot()
