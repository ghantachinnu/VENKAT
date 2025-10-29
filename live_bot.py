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

