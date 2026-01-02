import os
import time
from fyers_apiv3 import fyersModel
import datetime

# --- CONFIGURATION ---
client_id = os.environ.get("FYERS_CLIENT_ID")
access_token = os.environ.get("FYERS_ACCESS_TOKEN")

def connect_to_fyers():
    try:
        # V3 Connection
        fyers = fyersModel.FyersModel(client_id=client_id, token=access_token)
        
        # Test Connection (Get Profile)
        profile = fyers.get_profile()
        print(f"DEBUG RESPONSE: {profile}") # Print full response to see what happens
        
        if profile.get("code") == 200:
            print(f"SUCCESS: Connected to Fyers! Name: {profile['data']['name']}")
            return fyers
        else:
            print("ERROR: Connection failed.")
            return None
    except Exception as e:
        print(f"CRITICAL ERROR: {e}")
        return None

def run_bot():
    print("--- BOT STARTING (V3) ---")
    fyers = connect_to_fyers()
    
    # Keep the server alive forever
    while True:
        if fyers:
            print(f"Bot Heartbeat: {datetime.datetime.now()} - Connection OK")
        else:
            print("Bot Heartbeat: No Connection. Please check Render Environment Variables.")
        time.sleep(60)

if __name__ == "__main__":
    run_bot()
