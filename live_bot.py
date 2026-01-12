# main_strategy.py
# Nifty Monthly Options Buyer - Simulation / Forward Test
# For Render Background Worker - hands-off automation
import time
import datetime
import json
import os
from fyers_apiv3 import fyersModel
from fyers_apiv3.FyersWebsocket import data_ws
from py_vollib.black_scholes.greeks.analytical import delta, gamma, theta, vega
import numpy as np  # for any math

# ────────────────────────────────────────────────
# CONFIG
# ────────────────────────────────────────────────
SIMULATION_MODE = True  # Flip to False for real trades (after simulation)
CAPITAL = 100000.0
LOT_SIZE = 65  # 2026 Nifty lot size
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
THETA_MIN = -1.60  # least negative
THETA_MAX = -0.70
VEGA_MIN = 10
VEGA_MAX = 28
IV_MIN = 13
IV_MAX = 21.5
MIN_PREMIUM = 90
MAX_PREMIUM = 380
MIN_DTE_AT_ENTRY = 22
# Avoid last week
AVOID_LAST_WEEK_DAYS = 7
# Files for persistence on Render (use /data if mounted, else current dir)
STATE_FILE = "strategy_state.json"
TRADE_LOG_FILE = "trade_log.json"
# Fyers credentials from env vars
CLIENT_ID = os.getenv("FYERS_CLIENT_ID", "YOUR_CLIENT_ID")
ACCESS_TOKEN = os.getenv("FYERS_ACCESS_TOKEN", "YOUR_ACCESS_TOKEN")
fyers = fyersModel.FyersModel(client_id=CLIENT_ID, token=ACCESS_TOKEN, log_path="logs/")

# ────────────────────────────────────────────────
# STATE
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
        with open(STATE_FILE, 'r') as f:
            data = json.load(f)
            virtual_positions = data.get('virtual_positions', [])
            trade_history = data.get('trade_history', [])
            monthly_trades = data.get('monthly_trades', 0)
            consec_losses = data.get('consec_losses', 0)
            current_month = data.get('current_month', datetime.datetime.now().month)
            equity_curve = data.get('equity_curve', [CAPITAL])
        print(f"Loaded state | Open: {len(virtual_positions)} | Closed: {len(trade_history)}")
    else:
        print("No state file found – starting fresh")

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
    print("Saved state")

def log_trade(trade):
    with open(TRADE_LOG_FILE, 'a') as f:
        f.write(json.dumps(trade, default=str) + '\n')
    print("Logged trade")

def is_new_month():
    global monthly_trades, consec_losses, current_month
    m = datetime.datetime.now().month
    if m != current_month:
        monthly_trades = 0
        consec_losses = 0
        current_month = m
        save_state()
        print(f"New month: {current_month}")

def get_last_tuesday_dte():
    now = datetime.date.today()
    y, m = now.year, now.month
    next_month = datetime.date(y + (m // 12), (m % 12) + 1, 1)
    last_day = next_month - datetime.timedelta(days=1)
    offset = (last_day.weekday() - 1) % 7  # Tuesday = 1
    last_tue = last_day - datetime.timedelta(days=offset)
    if last_tue.month != m:
        last_tue -= datetime.timedelta(days=7)
    dte = (last_tue - now).days
    return dte

def can_trade():
    is_new_month()
    if monthly_trades >= MAX_TRADES_PER_MONTH:
        print("Monthly cap reached")
        return False
    if consec_losses >= 3:
        print("3 losses → paused")
        return False
    dte = get_last_tuesday_dte()
    if dte <= AVOID_LAST_WEEK_DAYS:
        print(f"Avoiding last week (DTE {dte})")
        return False
    return True

def get_spot():
    try:
        resp = fyers.quotes({"symbols": "NSE:NIFTY50-INDEX"})
        if resp['s'] == 'ok':
            return resp['d'][0]['v']['lp']
    except:
        pass
    return None

def get_option_quote(symbol):
    try:
        resp = fyers.quotes({"symbols": symbol})
        if resp['s'] == 'ok':
            d = resp['d'][0]['v']
            return {
                'ltp': d.get('lp', 0),
                'iv': d.get('impliedVolatility', 0),
                'oi': d.get('oi', 0)
            }
    except:
        pass
    return None

def get_greeks(flag, spot, strike, dte, iv_pct, r=0.065, q=0.012):
    t = dte / 365.0
    if t <= 0:
        return {'delta': 1 if flag == 'c' else -1, 'gamma': 0, 'theta_daily': 0, 'vega': 0}
    sigma = iv_pct / 100.0
    try:
        return {
            'delta': round(delta(flag, spot, strike, t, r, sigma), 4),
            'gamma': round(gamma(flag, spot, strike, t, r, sigma), 4),
            'theta_daily': round(theta(flag, spot, strike, t, r, sigma) * 365, 3),  # corrected to daily
            'vega': round(vega(flag, spot, strike, t, r, sigma), 2)
        }
    except:
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
        print(f"Filter fail | Δ:{greeks['delta']} Γ:{greeks['gamma']} Θ:{greeks['theta_daily']} IV:{iv} DTE:{dte}")
    else:
        print("Entry filter OK")
    return ok

# ────────────────────────────────────────────────
# POSITION MANAGEMENT
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
        # SL check
        if ltp <= pos['current_sl']:
            pos['status'] = 'closed_sl'
            pos['exit_premium'] = ltp
            pos['pnl_rs'] = -SL_POINTS * LOT_SIZE
            trade_history.append(pos.copy())
            log_trade(pos)
            consec_losses += 1
            equity_curve.append(equity_curve[-1] + pos['pnl_rs'])
            print(f"[SIM SL] {pos['symbol']} @ {ltp:.1f} PnL: {pos['pnl_rs']:+.0f}")
            virtual_positions.remove(pos)
            save_state()
            continue
        # Trailing
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
            print(f"[SIM TRAIL] {pos['symbol']} SL to {new_sl:.1f} mult {mult:.2f}x")

# ────────────────────────────────────────────────
# WEBSOCKET
# ────────────────────────────────────────────────
def on_message(ws, message):
    try:
        data = json.loads(message)
        if 'symbol' in data and 'ltp' in data:
            symbol = data['symbol']
            ltp = data['ltp']
            print(f"Tick {symbol} {ltp:.1f}")
            manage_positions()  # check trailing/SL on every tick
    except Exception as e:
        print("WS message error:", e)

def on_open(ws):
    symbols = ["NSE:NIFTY50-INDEX"]
    for p in virtual_positions:
        if p['status'] == 'open':
            symbols.append(p['symbol'])
    if symbols:
        ws.subscribe(symbols=symbols, data_type="SymbolUpdate")
        print(f"Subscribed to {len(symbols)} symbols")

def on_error(ws, message):
    print("WS error:", message)

def on_close(ws):
    print("WS closed")

# ────────────────────────────────────────────────
# ENTRY LOGIC
# ────────────────────────────────────────────────
def try_entry():
    if not can_trade():
        return
    spot = get_spot()
    if not spot:
        return
    # Get monthly expiry from Fyers (first expiry > MIN_DTE_AT_ENTRY)
    try:
        exp_resp = fyers.optionchain({"symbol": "NSE:NIFTY50-INDEX", "strikecount": "", "timestamp": ""})
        if exp_resp['s'] == 'ok':
            expiries = exp_resp['data']['expiryData']
            for exp in expiries:
                exp_date = datetime.datetime.fromtimestamp(exp['expiry'])
                dte = (exp_date - datetime.datetime.now()).days
                if dte >= MIN_DTE_AT_ENTRY:
                    expiry_code = exp_date.strftime('%d%b').upper()
                    break
    except:
        print("Expiry fetch fail")
        return
    # Slight OTM call (change to PE for put)
    strike = round(spot / 50) * 50 + 100  # OTM example
    symbol = f"NSE:NIFTY{expiry_code}{int(strike)}CE"  # or PE
    quote = get_option_quote(symbol)
    if not quote or quote['ltp'] < MIN_PREMIUM:
        return
    premium = quote['ltp']
    iv = quote['iv']
    dte = get_last_tuesday_dte()
    greeks = get_greeks('c', spot, strike, dte, iv)
    if not filter_entry(greeks, premium, iv, dte):
        return
    # Momentum check (REPLACE WITH YOUR REAL 5-MIN BREAKOUT + VOLUME LOGIC)
    momentum_ok = True  # placeholder
    if not momentum_ok:
        return
    # Entry
    global monthly_trades, consec_losses
    monthly_trades += 1
    consec_losses = 0
    sl_premium = premium - SL_POINTS - SLIPPAGE_POINTS
    entry_record = {
        'symbol': symbol,
        'entry_premium': premium,
        'qty': LOT_SIZE,
        'entry_time': datetime.datetime.now().isoformat(),
        'current_sl': sl_premium,
        'status': 'open',
        'greeks': greeks,
        'iv': iv,
        'dte': dte
    }
    if SIMULATION_MODE:
        virtual_positions.append(entry_record)
        print(f"[SIM ENTRY] {symbol} @ {premium:.1f} SL {sl_premium:.1f}")
    else:
        # Real order (add fyers.place_order logic here when ready)
        print("[LIVE ENTRY] Would place order")
    save_state()

# ────────────────────────────────────────────────
# MAIN
# ────────────────────────────────────────────────
if __name__ == "__main__":
    print("Nifty Monthly Option Buyer - Simulation Mode")
    load_state()
    # Websocket setup
    ws = data_ws.FyersDataWebSocket(access_token=ACCESS_TOKEN, log_level="ERROR")
    ws.on_message = on_message
    ws.on_open = on_open
    ws.on_error = on_error
    ws.on_close = on_close
    ws.connect()
    # Main loop for entry checks
    while True:
        try:
            try_entry()
            manage_positions()
            save_state()
        except Exception as e:
            print("Loop error:", e)
        time.sleep(300)  # 5 min
