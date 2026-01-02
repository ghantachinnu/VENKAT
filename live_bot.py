import os
import time
from fyers_apiv2 import fyersModel
import pandas as pd
import datetime

# --- CONFIGURATION ---
# We get these keys from Render (Environment Variables)
client_id = os.environ.get("FYERS_CLIENT_ID")
access_token = os.environ.get("FYERS_ACCESS_TOKEN")

# --- CONNECT TO FYERS ---
def connect_to_fyers():
    try:
        # Initialize Fyers Model
        fyers = fyersModel.FyersModel(client_id=client_id, token=access_token, log_path="")
        
        # Check profile to confirm connection
        profile = fyers.get_profile()
        if profile.get("code") == 200:
            print(f"SUCCESS: Connected to Fyers as {profile['data']['name']}")
            return fyers
        else:
            print(f"ERROR: Connection Failed. Reason: {profile.get('message')}")
            return None
    except Exception as e:
        print(f"ERROR: Exception during connection: {e}")
        return None

# --- MAIN LOOP ---
def run_bot():
    print("--- BOT STARTED ---")
    print(f"Time: {datetime.datetime.now()}")
    
    # 1. Connect
    fyers = connect_to_fyers()
    
    # 2. Loop Forever
    if fyers:
        while True:
            print(f"Bot is running... {datetime.datetime.now()}")
            # This is where we will add the trading strategy later!
            time.sleep(60) # Sleep for 1 minute
    else:
        print("Bot failed to connect. Please check your Token in Render.")

if __name__ == "__main__":
    run_bot()
# --- This is file 1: `generate_token.py` ---
# You MUST run this file on your OWN PC every morning.
# DO NOT UPLOAD THIS TO GITHUB.

import fyers_api.fyers_connect as fyers
import webbrowser
import os

# --- !!! YOUR DETAILS !!! ---
# --- PASTE YOUR REAL CLIENT_ID, SECRET_KEY, AND REDIRECT_URL HERE ---
# You get these from your Fyers API Dashboard
CLIENT_ID = "YOUR_CLIENT_ID_GOES_HERE"
SECRET_KEY = "YOUR_SECRET_KEY_GOES_HERE"
REDIRECT_URL = "https://localhost"  # Or whatever you set in your Fyers App

# --- 1. Create a Session ---
# This object will help us connect.
session = fyers.SessionModel(
    client_id=CLIENT_ID,
    secret_key=SECRET_KEY,
    redirect_uri=REDIRECT_URL,
    response_type="code",
    grant_type="authorization_code"
)

# --- 2. Get the Login URL ---
# This generates the special login link for Fyers.
login_url = session.generate_authcode()

# --- 3. Open the Browser ---
print(f"Opening this URL in your browser: {login_url}")
webbrowser.open(login_url)
print("---")
print("--- !!! ACTION REQUIRED !!! ---")
print("1. Log in to Fyers in the browser window that just opened.")
print("2. You will be redirected to your REDIRECT_URL (e.g., https://localhost).")
print("3. The URL in your browser bar will look like this: ")
print("   https://localhost/?s=ok&code=...&auth_code=...YOUR_AUTH_CODE_ENDS_HERE...")
print("4. Copy the ENTIRE auth_code (the long part after 'auth_code=')")
print("---")

# --- 4. Paste the Auth Code ---
auth_code = input("Paste your auth_code here and press Enter: ")

# --- 5. Exchange Auth Code for Access Token ---
session.set_token(auth_code)
response = session.generate_token()

if response.get("access_token"):
    access_token = response["access_token"]
    print("---")
    print("SUCCESS! Got your Access Token.")
    print("---")
    print("COPY THIS ENTIRE TOKEN (it is very long):")
    print("---")
    print(access_token)
    print("---")
    print("You must paste this token into the 'FYERS_ACCESS_TOKEN' Environment Variable on Render.com")
    
else:
    print("--- ERROR ---")
    print("Failed to get token. Response:")
    print(response)

