# main_strategy.py
# Nifty Monthly Options Buyer - Simulation / Forward Test
# PERSISTENCE UPGRADE: Firestore Cloud Integration
# TARGET: Late February Monthly Expiry (Automatic Roll)

import time
import datetime
import json
import os
import threading
import firebase_admin
from firebase_admin import credentials, firestore
from http.server import HTTPServer, BaseHTTPRequestHandler
from fyers_apiv3 import fyersModel
# Importing analytical Greeks directly for maximum stability
from py_vollib.black_scholes.greeks.analytical import delta, gamma, theta, vega

# ────────────────────────────────────────────────
# CONFIG
# ────────────────────────────────────────────────
SIMULATION_MODE = True
CAPITAL = 100000.0
LOT_SIZE = 75  # Standard Nifty lot
MAX_TRADES_PER_MONTH = 8
SL_POINTS = 60
SLIPPAGE_POINTS = 2.0 if not SIMULATION_MODE else 0.0

# Trailing upgrades
RR_UPGRADE_1 = 1.5
RR_UPGRADE_2 = 1.7
RR_TARGET = 2.0
BREAKEVEN_BUFFER = 8
TRAIL_LOOSE_POINTS = 35
TRAIL_TIGHT_POINTS = 20

# Greeks thresholds
DELTA_MIN = 0.42
DELTA_MAX = 0.62
GAMMA_MIN = 0.010
GAMMA_MAX = 0.028
THETA_MIN = -1.60
THETA_MAX = -0.70
VEGA_MIN = 10
VEGA_MAX = 28
IV_MIN = 13
IV_MAX = 21.5
MIN_PREMIUM = 30  
MAX_PREMIUM = 550 
MIN_DTE_AT_ENTRY = 40 
AVOID_LAST_WEEK_DAYS = 7

# Credentials from Render Environment Variables
CLIENT_ID = os.getenv("FYERS_CLIENT_ID", "").strip()
ACCESS_TOKEN = os.getenv("FYERS_ACCESS_TOKEN", "").strip()
FIREBASE_JSON = os.getenv("FIREBASE_SERVICE_ACCOUNT")

# Connect Fyers
fyers = fyersModel.FyersModel(client_id=CLIENT_ID, token=ACCESS_TOKEN, log_path="./")

# ────────────────────────────────────────────────
# 🗄️ CLOUD STATE MANAGEMENT (Firestore)
# ────────────────────────────────────────────────
db = None
app_id = "fyers-nifty-bot"

def init_firebase():
    global db
    if not FIREBASE_JSON:
        print("❌ ERROR: FIREBASE_SERVICE_ACCOUNT environment variable is missing!")
        return
    try:
        if not firebase_admin._apps:
            cred_dict = json.loads(FIREBASE_JSON)
            cred = credentials.Certificate(cred_dict)
            firebase_admin.initialize_app(cred)
        db = firestore.client()
        print("✅ SUCCESS: Firestore Cloud Database Connected!")
    except Exception as e:
        print(f"❌ ERROR: Firebase Init Failed: {e}")

def get_db_ref():
    # Strict Path: /artifacts/{appId}/public/data/{collectionName}
    return db.collection('artifacts').document(app_id).collection('public').document('data').collection('trading_state').document('current')

# Global variables to be loaded/saved
virtual_positions = []
trade_history = []
monthly_trades = 0
consec_losses = 0
current_month = datetime.datetime.now().month
equity_curve = [CAPITAL]

def load_state():
    global virtual_positions, trade_history, monthly_trades, consec_losses, current_month, equity_curve
    if not db:
        print("⚠️ Warning: No Cloud DB connection. Starting fresh.")
        return

    try:
        doc = get_db_ref().get()
        if doc.exists:
            data = doc.to_dict()
            virtual_positions = data.get('virtual_positions', [])
            trade_history = data.get('trade_history', [])
            monthly_trades = data.get('monthly_trades', 0)
            consec_losses = data.get('consec_losses', 0)
            current_month = data.get('current_month', datetime.datetime.now().month)
            equity_curve = data.get('equity_curve', [CAPITAL])
            print(f"📖 Cloud Memory Loaded | Open Trades: {len(virtual_positions)}")
        else:
            print("🆕 No cloud state found – starting fresh")
    except Exception as e:
        print(f"⚠️ Error loading from Cloud: {e}")

def save_state():
    if not db: return
    try:
        data = {
            'virtual_positions': virtual_positions,
            'trade_history': trade_history,
            'monthly_trades': monthly_trades,
            'consec_losses': consec_losses,
            'current_month': current_month,
            'equity_curve': equity_curve,
            'last_sync': str(datetime.datetime.now())
        }
        get_db_ref().set(data)
    except Exception as e:
        print(f"⚠️ Error syncing to Cloud: {e}")

# ────────────────────────────────────────────────
# 📊 UTILITIES & FILTERS (Original Logic)
# ────────────────────────────────────────────────
def log_trade(trade):
    # We still print to logs for visibility in Render
    print(f"Trade Event Logged: {trade['symbol']} | Status: {trade['status']}")

def is_new_month():
    global monthly_trades, consec_losses, current_month
    m = datetime.datetime.now().month
    if m != current_month:
        monthly_trades = 0
        consec_losses = 0
        current_month = m
        save_state()
        print(f"New month started: {current_month}")

def get_last_tuesday_dte():
    now = datetime.date.today()
    y, m = now.year, now.month
    if m == 12: next_month = datetime.date(y + 1, 1, 1)
    else: next_month = datetime.date(y, m + 1, 1)
    last_day = next_month - datetime.timedelta(days=1)
    offset = (last_day.weekday() - 1) % 7 
    last_tue = last_day - datetime.timedelta(days=offset)
    if last_tue.month != m: last_tue -= datetime.timedelta(days=7)
    return (last_tue - now).days

def can_trade():
    is_new_month()
    if monthly_trades >= MAX_TRADES_PER_MONTH: return False
    if consec_losses >= 3: return False
    if get_last_tuesday_dte() <= AVOID_LAST_WEEK_DAYS: return False
    return True

def get_spot():
    try:
        resp = fyers.quotes({"symbols": "NSE:NIFTY50-INDEX"})
        if resp.get('s') == 'ok': return resp['d'][0]['v']['lp']
    except: return None

def get_option_quote(symbol):
    try:
        resp = fyers.quotes({"symbols": symbol})
        if resp.get('s') == 'ok':
            d = resp['d'][0]['v']
            return {'ltp': d.get('lp', 0), 'iv': d.get('impliedVolatility', 0), 'oi': d.get('oi', 0)}
    except: return None

def get_greeks(flag, spot, strike, dte, iv_pct, r=0.065, q=0.012):
    t = dte / 365.0
    if t <= 0: return {'delta': 1 if flag == 'c' else -1, 'gamma': 0, 'theta_daily': 0, 'vega': 0}
    sigma = max(0.01, iv_pct / 100.0) 
    try:
        return {
            'delta': round(delta(flag, spot, strike, t, r, sigma), 4),
            'gamma': round(gamma(flag, spot, strike, t, r, sigma), 4),
            'theta_daily': round(theta(flag, spot, strike, t, r, sigma) * 365, 3),
            'vega': round(vega(flag, spot, strike, t, r, sigma), 2)
        }
    except: return {'delta': None, 'gamma': None, 'theta_daily': None, 'vega': None}

def filter_entry(greeks, premium, iv, dte):
    if greeks['delta'] is None: return False
    return (
        DELTA_MIN <= greeks['delta'] <= DELTA_MAX and
        GAMMA_MIN <= greeks['gamma'] <= GAMMA_MAX and
        THETA_MIN <= greeks['theta_daily'] <= THETA_MAX and
        VEGA_MIN <= greeks['vega'] <= VEGA_MAX and
        IV_MIN <= iv <= IV_MAX and
        MIN_PREMIUM <= premium <= MAX_PREMIUM and
        dte >= MIN_DTE_AT_ENTRY
    )

# ────────────────────────────────────────────────
# ENTRY & MANAGEMENT LOGIC
# ────────────────────────────────────────────────
def manage_positions():
    global consec_losses, equity_curve
    for pos in virtual_positions[:]:
        if pos['status'] != 'open': continue
        quote = get_option_quote(pos['symbol'])
        if not quote or quote['ltp'] == 0: continue
        ltp = quote['ltp']
        
        if ltp <= pos['current_sl']:
            pos['status'] = 'closed_sl'
            pos['exit_premium'] = ltp
            pos['pnl_rs'] = -SL_POINTS * LOT_SIZE
            trade_history.append(pos.copy())
            consec_losses += 1
            equity_curve.append(equity_curve[-1] + pos['pnl_rs'])
            print(f"❌ [SIM SL HIT] {pos['symbol']} @ {ltp}")
            virtual_positions.remove(pos)
            save_state()
            continue
            
        mult = ltp / pos['entry_premium'] if pos['entry_premium'] > 0 else 0
        if mult >= RR_TARGET: new_sl = ltp - TRAIL_TIGHT_POINTS
        elif mult >= RR_UPGRADE_2: new_sl = ltp - TRAIL_LOOSE_POINTS
        elif mult >= RR_UPGRADE_1: new_sl = pos['entry_premium'] + BREAKEVEN_BUFFER
        else: new_sl = pos['current_sl']
            
        if new_sl > pos['current_sl']:
            pos['current_sl'] = new_sl
            print(f"📈 [SIM TRAIL] {pos['symbol']} SL -> {new_sl:.1f}")

def try_entry():
    if not can_trade(): return
    spot = get_spot()
    if not spot: return
    
    try:
        exp_resp = fyers.optionchain({"symbol": "NSE:NIFTY50-INDEX", "strikecount": 50})
        if exp_resp.get('s') == 'ok':
            expiries = exp_resp['data']['expiryData']
            target_ts, target_dte, expiry_month_name, expiry_year_code = None, 0, "", ""

            for exp in expiries:
                try: exp_val = int(float(exp['expiry']))
                except: continue
                exp_date = datetime.datetime.fromtimestamp(exp_val)
                dte = (exp_date - datetime.datetime.now()).days
                if dte >= MIN_DTE_AT_ENTRY:
                    target_ts, target_dte = exp_val, dte
                    expiry_month_name, expiry_year_code = exp_date.strftime('%b').upper(), exp_date.strftime('%y')
                    break
            
            if not target_ts: return

            target_strike = int(round(spot / 50) * 50)
            final_symbol = None
            options_list = exp_resp['data']['optionsChain']
            
            for opt in options_list:
                opt_strike = int(float(opt.get('strike_price', 0)))
                opt_expiry = int(float(opt.get('expiry', 0)))
                if opt_strike == target_strike and opt.get('option_type', '') == 'CE':
                    if opt_expiry == target_ts:
                        final_symbol = opt.get('symbol', '')
                        break
                    if expiry_month_name in opt.get('symbol', '') and expiry_year_code in opt.get('symbol', ''):
                        final_symbol = opt.get('symbol', '')
                        break

            if not final_symbol: return
            quote = get_option_quote(final_symbol)
            if not quote: return
            
            premium, iv = quote['ltp'], quote['iv']
            greeks = get_greeks('c', spot, target_strike, target_dte, iv)
            
            if not filter_entry(greeks, premium, iv, target_dte): return
                
            global monthly_trades, consec_losses
            monthly_trades += 1
            consec_losses = 0
            
            entry_record = {
                'symbol': final_symbol, 'entry_premium': premium, 'qty': LOT_SIZE,
                'entry_time': datetime.datetime.now().isoformat(),
                'current_sl': premium - SL_POINTS, 'status': 'open',
                'greeks': greeks, 'iv': iv, 'dte': target_dte
            }
            
            virtual_positions.append(entry_record)
            print(f"✅ [SIM ENTRY SUCCESS] {final_symbol} @ ₹{premium:.1f}")
            save_state()
            
    except Exception as e:
        print("Logic Error:", str(e))

# ────────────────────────────────────────────────
# MAIN THREADS
# ────────────────────────────────────────────────
def run_bot_logic():
    print("Nifty Monthly Option Buyer - STARTING...")
    init_firebase()
    load_state()
    while True:
        try:
            now = datetime.datetime.now()
            # Indian Market Hours (Approx 9:15 to 3:30)
            if (now.hour == 9 and now.minute >= 15) or (10 <= now.hour < 15) or (now.hour == 15 and now.minute <= 30):
                if can_trade():
                    try_entry()
                manage_positions()
                save_state()
            else:
                if now.minute % 30 == 0:
                    print(f"Market Closed. Time: {now.strftime('%H:%M:%S')}")
        except Exception as e:
            print("Main loop error:", e)
        time.sleep(300)

class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        status = "Cloud Connected" if db else "Local Only"
        self.wfile.write(f"Bot Status: {status}".encode())
    def do_HEAD(self):
        self.send_response(200)
        self.end_headers()

def start_web_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), HealthCheckHandler)
    print(f"Health Web Server started on port {port}")
    server.serve_forever()

if __name__ == "__main__":
    t = threading.Thread(target=run_bot_logic, daemon=True)
    t.start()
    start_web_server()
