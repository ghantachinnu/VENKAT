# main_strategy.py
# Nifty Monthly Options Buyer - Simulation / Forward Test
# Fixed for Render: Added Web Server & Fixed Option Chain Types
# TARGET: Late February Monthly Expiry (Automatic Roll)
# DEBUGGED: Fixed "'str' object cannot be interpreted as an integer" crash.
# FIXED: Symbol matching now uses float-safe strike comparison and robust name detection.
# ADDED: HEAD support for Render health checks.

import time
import datetime
import json
import os
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from fyers_apiv3 import fyersModel
# Importing analytical Greeks directly for maximum stability
from py_vollib.black_scholes.greeks.analytical import delta, gamma, theta, vega

# ────────────────────────────────────────────────
# CONFIG
# ────────────────────────────────────────────────
SIMULATION_MODE = True  # Change to False only when ready for real trades
CAPITAL = 100000.0
LOT_SIZE = 65  # Nifty lot size in 2026
MAX_TRADES_PER_MONTH = 8
SL_POINTS = 60
SLIPPAGE_POINTS = 2.0 if not SIMULATION_MODE else 0.0

# Trailing upgrades (premium multiples)
RR_UPGRADE_1 = 1.5  # breakeven + buffer
RR_UPGRADE_2 = 1.7  # loose trail
RR_TARGET = 2.0  # tight trail
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
# IV Range
IV_MIN = 13
IV_MAX = 21.5

# Premium Range
MIN_PREMIUM = 30  
MAX_PREMIUM = 550 # Catching Monthly ATM contracts (~385)

# --- DYNAMIC EXPIRY SELECTION ---
# Setting this to 40 ensures we skip weeklies and find the Far-Month contract.
# Logic: Today is Jan 13 -> skips Jan and early Feb weeklies -> Hits Feb 26.
MIN_DTE_AT_ENTRY = 40 

# Avoid last week of month
AVOID_LAST_WEEK_DAYS = 7

# Files
STATE_FILE = "strategy_state.json"
TRADE_LOG_FILE = "trade_log.json"

# Fyers credentials from Render Environment Variables
CLIENT_ID = os.getenv("FYERS_CLIENT_ID", "").strip()
ACCESS_TOKEN = os.getenv("FYERS_ACCESS_TOKEN", "").strip()

# Connect
fyers = fyersModel.FyersModel(
    client_id=CLIENT_ID,
    token=ACCESS_TOKEN,
    log_path="./"
)

# ────────────────────────────────────────────────
# STATE MANAGEMENT
# ────────────────────────────────────────────────
virtual_positions = []
trade_history = []
monthly_trades = 0
consec_losses = 0
current_month = datetime.datetime.now().month
equity_curve = [CAPITAL]

def load_state():
    global virtual_positions, trade_history, monthly_trades, consec_losses, current_month, equity_curve
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, 'r') as f:
                data = json.load(f)
                virtual_positions = data.get('virtual_positions', [])
                trade_history = data.get('trade_history', [])
                monthly_trades = data.get('monthly_trades', 0)
                consec_losses = data.get('consec_losses', 0)
                current_month = data.get('current_month', datetime.datetime.now().month)
                equity_curve = data.get('equity_curve', [CAPITAL])
            print(f"Loaded state | Open Trades: {len(virtual_positions)}")
        except Exception:
            print("State file empty or new - starting fresh")
    else:
        print("No state file – starting fresh")

def save_state():
    data = {
        'virtual_positions': virtual_positions,
        'trade_history': trade_history,
        'monthly_trades': monthly_trades,
        'consec_losses': consec_losses,
        'current_month': current_month,
        'equity_curve': equity_curve
    }
    with open(STATE_FILE, 'w') as f:
        json.dump(data, f, default=str)

def log_trade(trade):
    with open(TRADE_LOG_FILE, 'a') as f:
        f.write(json.dumps(trade, default=str) + '\n')
    print("Logged trade info")

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
    if m == 12:
        next_month = datetime.date(y + 1, 1, 1)
    else:
        next_month = datetime.date(y, m + 1, 1)
    last_day = next_month - datetime.timedelta(days=1)
    offset = (last_day.weekday() - 1) % 7 
    last_tue = last_day - datetime.timedelta(days=offset)
    if last_tue.month != m:
        last_tue -= datetime.timedelta(days=7)
    dte = (last_tue - now).days
    return dte

def can_trade():
    is_new_month()
    if monthly_trades >= MAX_TRADES_PER_MONTH:
        print("Monthly trade cap reached.")
        return False
    if consec_losses >= 3:
        print("Paused due to 3 consecutive losses.")
        return False
    dte = get_last_tuesday_dte()
    if dte <= AVOID_LAST_WEEK_DAYS:
        print(f"Avoiding last week of month (DTE {dte})")
        return False
    return True

def get_spot():
    try:
        resp = fyers.quotes({"symbols": "NSE:NIFTY50-INDEX"})
        if resp.get('s') == 'ok':
            return resp['d'][0]['v']['lp']
    except Exception as e:
        print("Spot fetch error:", e)
    return None

def get_option_quote(symbol):
    try:
        resp = fyers.quotes({"symbols": symbol})
        if resp.get('s') == 'ok':
            d = resp['d'][0]['v']
            return {
                'ltp': d.get('lp', 0),
                'iv': d.get('impliedVolatility', 0),
                'oi': d.get('oi', 0)
            }
    except Exception as e:
        print(f"Quote error for {symbol}:", e)
    return None

def get_greeks(flag, spot, strike, dte, iv_pct, r=0.065, q=0.012):
    t = dte / 365.0
    if t <= 0:
        return {'delta': 1 if flag == 'c' else -1, 'gamma': 0, 'theta_daily': 0, 'vega': 0}
    sigma = max(0.01, iv_pct / 100.0) 
    try:
        return {
            'delta': round(delta(flag, spot, strike, t, r, sigma), 4),
            'gamma': round(gamma(flag, spot, strike, t, r, sigma), 4),
            'theta_daily': round(theta(flag, spot, strike, t, r, sigma) * 365, 3),
            'vega': round(vega(flag, spot, strike, t, r, sigma), 2)
        }
    except Exception as e:
        print("Greeks calculation failed:", e)
        return {'delta': None, 'gamma': None, 'theta_daily': None, 'vega': None}

def filter_entry(greeks, premium, iv, dte):
    if greeks['delta'] is None:
        return False
    ok = (
        DELTA_MIN <= greeks['delta'] <= DELTA_MAX and
        GAMMA_MIN <= greeks['gamma'] <= GAMMA_MAX and
        THETA_MIN <= greeks['theta_daily'] <= THETA_MAX and
        VEGA_MIN <= greeks['vega'] <= VEGA_MAX and
        IV_MIN <= iv <= IV_MAX and
        MIN_PREMIUM <= premium <= MAX_PREMIUM and
        dte >= MIN_DTE_AT_ENTRY
    )
    if not ok:
        print(f"Filter Fail: D:{greeks['delta']} G:{greeks['gamma']} IV:{iv} Prem:{premium}")
    else:
        print("--- Entry Filters PASS ---")
    return ok

# ────────────────────────────────────────────────
# ENTRY & MANAGEMENT LOGIC
# ────────────────────────────────────────────────
def manage_positions():
    global consec_losses, equity_curve
    for pos in virtual_positions[:]:
        if pos['status'] != 'open':
            continue
        quote = get_option_quote(pos['symbol'])
        if not quote or quote['ltp'] == 0:
            continue
        ltp = quote['ltp']
        pnl_points = ltp - pos['entry_premium']
        pnl_rs = pnl_points * LOT_SIZE
        
        if ltp <= pos['current_sl']:
            pos['status'] = 'closed_sl'
            pos['exit_premium'] = ltp
            pos['pnl_rs'] = -SL_POINTS * LOT_SIZE
            trade_history.append(pos.copy())
            log_trade(pos)
            consec_losses += 1
            equity_curve.append(equity_curve[-1] + pos['pnl_rs'])
            print(f"❌ [SIM SL HIT] {pos['symbol']} exit @ {ltp:.1f}")
            virtual_positions.remove(pos)
            save_state()
            continue
            
        mult = ltp / pos['entry_premium'] if pos['entry_premium'] > 0 else 0
        if mult >= RR_TARGET:
            new_sl = ltp - TRAIL_TIGHT_POINTS
        elif mult >= RR_UPGRADE_2:
            new_sl = ltp - TRAIL_LOOSE_POINTS
        elif mult >= RR_UPGRADE_1:
            new_sl = pos['entry_premium'] + BREAKEVEN_BUFFER
        else:
            new_sl = pos['current_sl']
            
        if new_sl > pos['current_sl']:
            pos['current_sl'] = new_sl
            print(f"📈 [SIM TRAIL] {pos['symbol']} new SL moved to: {new_sl:.1f}")

def try_entry():
    if not can_trade():
        return
    spot = get_spot()
    if not spot:
        return
    
    print(f"--- SCANNING: Nifty Spot is {spot} ---")
        
    try:
        # 50 strikes ensures ATM is always captured
        exp_resp = fyers.optionchain({"symbol": "NSE:NIFTY50-INDEX", "strikecount": 50})
        
        if exp_resp.get('s') == 'ok':
            expiries = exp_resp['data']['expiryData']
            target_ts = None
            target_dte = 0
            expiry_month_name = ""
            expiry_year_code = ""

            for exp in expiries:
                # FORCE CONVERSION TO INT to fix 'str object cannot be interpreted as integer'
                try:
                    exp_val = int(float(exp['expiry'])) 
                except:
                    continue
                    
                exp_date = datetime.datetime.fromtimestamp(exp_val)
                dte = (exp_date - datetime.datetime.now()).days
                if dte >= MIN_DTE_AT_ENTRY:
                    target_ts = exp_val
                    target_dte = dte
                    expiry_month_name = exp_date.strftime('%b').upper() # e.g. "FEB"
                    expiry_year_code = exp_date.strftime('%y')         # e.g. "26"
                    print(f"Selected Expiry: {exp_date.strftime('%d%b')} ({target_dte} days left)")
                    break
            
            if not target_ts:
                print(f"No suitable monthly expiry found (DTE < {MIN_DTE_AT_ENTRY})")
                return

            # Target ATM Strike
            target_strike = int(round(spot / 50) * 50)
            final_symbol = None
            options_list = exp_resp['data']['optionsChain']
            
            # --- IMPROVED ROBUST SYMBOL MATCHING ---
            for opt in options_list:
                # Get strike and expiry safely as integers
                opt_strike = int(float(opt.get('strike_price', 0)))
                opt_expiry = int(float(opt.get('expiry', 0)))
                opt_type = opt.get('option_type', '')
                opt_symbol = opt.get('symbol', '')

                # Match Strike and Type first
                if opt_strike == target_strike and opt_type == 'CE':
                    # Check 1: Match by exact timestamp (Best)
                    if opt_expiry == target_ts:
                        final_symbol = opt_symbol
                        break
                    # Check 2: Match by Month/Year in string (Fallback for Monthlies)
                    # Monthly format: NIFTY26FEB25750CE
                    if expiry_month_name in opt_symbol and expiry_year_code in opt_symbol:
                        final_symbol = opt_symbol
                        break

            if not final_symbol:
                print(f"DEBUG: Could not find symbol for {target_strike} CE for {expiry_month_name}")
                return

            print(f"Checking Real Symbol: {final_symbol}")
            quote = get_option_quote(final_symbol)
            if not quote: return
            
            print(f"Market Price Found: ₹{quote['ltp']}")
            
            if quote['ltp'] < MIN_PREMIUM or quote['ltp'] > MAX_PREMIUM:
                print(f"SKIP: ₹{quote['ltp']} is outside allowed Premium range.")
                return
                
            premium = quote['ltp']
            iv = quote['iv']
            greeks = get_greeks('c', spot, target_strike, target_dte, iv)
            
            print(f"Greeks for {final_symbol}: Delta: {greeks.get('delta')}")
            
            if not filter_entry(greeks, premium, iv, target_dte):
                return
                
            global monthly_trades, consec_losses
            monthly_trades += 1
            consec_losses = 0
            
            entry_record = {
                'symbol': final_symbol, 'entry_premium': premium, 'qty': LOT_SIZE,
                'entry_time': datetime.datetime.now().isoformat(),
                'current_sl': premium - SL_POINTS, 'status': 'open',
                'greeks': greeks, 'iv': iv, 'dte': target_dte
            }
            
            if SIMULATION_MODE:
                virtual_positions.append(entry_record)
                print(f"✅ [SIM ENTRY SUCCESS] {final_symbol} @ ₹{premium:.1f}")
            else:
                print("[LIVE MODE] Order logic would trigger here.")
            save_state()
            
        else:
            print("Fyers Option Chain Error:", exp_resp)
            return
            
    except Exception as e:
        print("Logic Execution Error:", str(e))
        return

# ────────────────────────────────────────────────
# MAIN THREADS
# ────────────────────────────────────────────────
def run_bot_logic():
    print("Nifty Monthly Option Buyer - SIMULATION START")
    load_state()
    while True:
        try:
            now = datetime.datetime.now()
            # Indian Market Hours (9:15 to 3:30)
            if (now.hour == 9 and now.minute >= 15) or (10 <= now.hour < 15) or (now.hour == 15 and now.minute <= 30):
                print(f"\nScan at {now.strftime('%H:%M:%S')}")
                if can_trade():
                    try_entry()
                manage_positions()
                save_state()
            else:
                if now.minute % 30 == 0:
                    print(f"Market Closed. Current Time: {now.strftime('%H:%M:%S')}")
        except Exception as e:
            print("Main loop error:", e)
        time.sleep(300) # Scan every 5 minutes

class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is running")
    def do_HEAD(self): # Fixes Render 501 error
        self.send_response(200)
        self.end_headers()

def start_web_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), HealthCheckHandler)
    print(f"Health Web Server started on port {port}")
    server.serve_forever()

if __name__ == "__main__":
    t = threading.Thread(target=run_bot_logic)
    t.daemon = True
    t.start()
    start_web_server()
