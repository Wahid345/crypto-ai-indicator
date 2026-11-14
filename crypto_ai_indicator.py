import os
import threading
from http.server import HTTPServer, SimpleHTTPRequestHandler
import requests
import ccxt
import pandas as pd
import numpy as np
import ta
import time
import schedule
import hashlib 
import hmac     
import json     
from datetime import datetime
from xgboost import XGBClassifier
from flask import Flask


# ==================== CONFIGURATION ====================
API_KEY = os.environ.get('GATE_API_KEY', 'a9c34f7d3ce1bdfb7d80ae11e8d69f73')
SECRET_KEY = os.environ.get('GATE_SECRET_KEY', '4782ce9313e6961419fd3f2bc8988d5d67d6000ec3b1aff817068fa410b8a5c7')

BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', '9052673855:AAGoX5xAiwah6EJaJHxbFgDNtoKMlOLsyhQ')
SIGNAL_CHAT_ID = '5461610444'   # Signal Group
TRADE_CHAT_ID = '-5394519129'   # Profit Update Group

GATE_HOST = "https://api.gateio.ws"
GATE_PREFIX = "/api/v4"
SYMBOLS = ['SOL/USDT']

exchange_config = {
    'apiKey': API_KEY,
    'secret': SECRET_KEY,
    'enableRateLimit': True,
    'options': {
        'defaultType': 'swap'   # USDT Perpetual Futures
    }
}

exchange = ccxt.gate(exchange_config)
exchange.load_markets()

app = Flask(__name__)

@app.route('/')
def home():
    return "SOL AI Trading Bot is Running 24/7", 200

def self_ping_loop():
    url = "https://crypto-ai-indicator.onrender.com"  
    while True:
        try:
            time.sleep(300)
            res = requests.get(url, timeout=10)
            print(f"🔄 Self-ping sent successfully. Status: {res.status_code}", flush=True)
        except Exception as e:
            print(f"⚠️ Self-ping error: {e}", flush=True)

def send_daily_profit_summary():
    try:
        print("📊 Generating daily profit summary...", flush=True)
    except Exception as e:
        print(f"❌ Error in daily summary: {e}", flush=True)

schedule.every().day.at("23:58").do(send_daily_profit_summary)

def start_dummy_server():
    try:
        port = int(os.environ.get("PORT", 10000))
        server = HTTPServer(("0.0.0.0", port), SimpleHTTPRequestHandler)
        print(f"✅ Dummy web server successfully bound to port {port}", flush=True)
        server.serve_forever()
    except Exception as e:
        print(f"❌ Dummy server bind error: {e}", flush=True)

def send_telegram_alert(message, chat_id):
    if not BOT_TOKEN or not chat_id:
        return
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": chat_id, "text": message, "parse_mode": "Markdown"}
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"Telegram Error: {e}", flush=True)

def fetch_crypto_data(symbol='SOL/USDT', timeframe='15m', limit=500):
    ohlcv = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
    df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
    return df

def prepare_features(df):
    df['RSI'] = ta.momentum.rsi(df['close'], window=14)
    df['EMA_20'] = ta.trend.ema_indicator(df['close'], window=20)
    df['EMA_50'] = ta.trend.ema_indicator(df['close'], window=50)
    
    macd = ta.trend.MACD(df['close'])
    df['MACD'] = macd.macd()
    df['MACD_Signal'] = macd.macd_signal()
    
    bollinger = ta.volatility.BollingerBands(df['close'])
    df['BB_High'] = bollinger.bollinger_hband()
    df['BB_Low'] = bollinger.bollinger_lband()
    df['BB_Width'] = df['BB_High'] - df['BB_Low']
    
    df['ATR'] = ta.volatility.average_true_range(df['high'], df['low'], df['close'], window=14)
    df['Body_Size'] = (df['close'] - df['open']).abs()
    df['Upper_Shadow'] = df['high'] - df[['open', 'close']].max(axis=1)
    df['Lower_Shadow'] = df[['open', 'close']].min(axis=1) - df['low']
    df['Return'] = df['close'].pct_change()
    
    df['Target'] = np.where(df['close'].shift(-1) > df['close'], 1, 0)
    df = df.dropna()
    return df

def train_and_predict(df):
    features = ['RSI', 'EMA_20', 'EMA_50', 'MACD', 'MACD_Signal', 'BB_Width', 'ATR', 'Body_Size', 'Upper_Shadow', 'Lower_Shadow', 'Return']
    X = df[features]
    y = df['Target']
    
    X_train, y_train = X.iloc[:-1], y.iloc[:-1]
    
    model = XGBClassifier(n_estimators=150, learning_rate=0.05, max_depth=4, random_state=42, eval_metric='logloss')
    model.fit(X_train, y_train)
    
    latest_features = X.iloc[[-1]]
    prediction = model.predict(latest_features)[0]
    probabilities = model.predict_proba(latest_features)[0]
    confidence = max(probabilities) * 100
    
    return prediction, confidence

# ==================== LEVERAGE & MARGIN SETUP ====================
def set_isolated_leverage(symbol='SOL/USDT', leverage=10):
    endpoint = f"/futures/usdt/positions/{symbol.replace('/', '_')}/leverage"
    url = GATE_HOST + GATE_PREFIX + endpoint + f"?leverage={leverage}"
    
    timestamp = str(int(time.time()))
    hashed_payload = hashlib.sha512("".encode('utf-8')).hexdigest()
    s = f"POST\n{GATE_PREFIX + endpoint}\nleverage={leverage}\n{hashed_payload}\n{timestamp}"
    signature = hmac.new(SECRET_KEY.encode('utf-8'), s.encode('utf-8'), hashlib.sha512).hexdigest()
    
    headers = {
        'Accept': 'application/json',
        'Content-Type': 'application/json',
        'KEY': API_KEY,
        'Timestamp': timestamp,
        'SIGN': signature
    }
    try:
        resp = requests.post(url, headers=headers, timeout=10)
        if resp.status_code == 200:
            print(f"✅ Isolated Margin & {leverage}x Leverage successfully enforced for {symbol}", flush=True)
        else:
            print(f"❌ Leverage setting failed [{resp.status_code}]: {resp.text}", flush=True)
    except Exception as e:
        print(f"⚠️ Leverage setting error: {e}", flush=True)

# ==================== POSITION CHECK FUNCTION ====================
def check_existing_position(symbol):
    endpoint = f"/futures/usdt/positions/{symbol.replace('/', '_')}"
    url = GATE_HOST + GATE_PREFIX + endpoint
    
    timestamp = str(int(time.time()))
    hashed_payload = hashlib.sha512("".encode('utf-8')).hexdigest()
    s = f"GET\n{GATE_PREFIX + endpoint}\n\n{hashed_payload}\n{timestamp}"
    signature = hmac.new(SECRET_KEY.encode('utf-8'), s.encode('utf-8'), hashlib.sha512).hexdigest()
    
    headers = {
        'Accept': 'application/json',
        'Content-Type': 'application/json',
        'KEY': API_KEY,
        'Timestamp': timestamp,
        'SIGN': signature
    }
    
    try:
        response = requests.get(url, headers=headers, timeout=10)
        if response.status_code == 200:
            data = response.json()
            if isinstance(data, list):
                for pos_item in data:
                    if float(pos_item.get('size', 0)) != 0:
                        return True, pos_item
            elif isinstance(data, dict):
                if float(data.get('size', 0)) != 0:
                    return True, data
        return False, None
    except Exception as e:
        print(f"⚠️ Error checking position: {e}", flush=True)
        return False, None

# ==================== ORDER PLACEMENT (MAINNET) ====================
def send_gate_mainnet_order(symbol, side, contracts):
    endpoint = "/futures/usdt/orders"
    url = GATE_HOST + GATE_PREFIX + endpoint
    
    settle_symbol = symbol.replace('/', '_')
    size = int(contracts) if side == 'buy' else -int(contracts)
    
    body = {
        "contract": settle_symbol,
        "size": size,
        "price": "0",
        "tif": "ioc",
        "text": "t-ai-bot"
    }
    
    payload_str = json.dumps(body, separators=(',', ':'))
    timestamp = str(int(time.time()))
    
    hashed_payload = hashlib.sha512(payload_str.encode('utf-8')).hexdigest()
    s = f"POST\n{GATE_PREFIX + endpoint}\n\n{hashed_payload}\n{timestamp}"
    signature = hmac.new(SECRET_KEY.encode('utf-8'), s.encode('utf-8'), hashlib.sha512).hexdigest()
    
    headers = {
        'Accept': 'application/json',
        'Content-Type': 'application/json',
        'KEY': API_KEY,
        'Timestamp': timestamp,
        'SIGN': signature
    }
    
    try:
        response = requests.post(url, headers=headers, data=payload_str, timeout=10)
        result = response.json()
        
        if response.status_code in [200, 201] and 'id' in result:
            order_id = result.get('id')
            print(f"✅ Mainnet Order Placed Successfully! ID: {order_id}", flush=True)
            send_telegram_alert(f"🟢 *Mainnet Live Order Executed!*\nSymbol: `{symbol}`\nID: `{order_id}`", SIGNAL_CHAT_ID)
            return result
        else:
            print(f"❌ API Error [{response.status_code}]: {result}", flush=True)
            return None
    except Exception as e:
        print(f"❌ Request Exception: {e}", flush=True)
        return None


def process_symbol(symbol='SOL/USDT'):
    print(f"Checking 4-Timeframe (1m, 5m, 15m, 1h) Confluence for {symbol}...", flush=True)
    
    try:
        has_position, pos_info = check_existing_position(symbol)
        
        if has_position:
            print(f"🔒 Position already active. Skipping new trade execution.", flush=True)
            return
        
        # ১. ৪টি টাইমফ্রেমের ডেটা ফেচ করা (4h বাদ দেওয়া হয়েছে)
        df_1h = fetch_crypto_data(symbol, timeframe='1h', limit=100)
        df_15m = fetch_crypto_data(symbol, timeframe='15m', limit=500)
        df_5m = fetch_crypto_data(symbol, timeframe='5m', limit=200)
        df_1m = fetch_crypto_data(symbol, timeframe='1m', limit=100)
        
        # ২. 15m এআই মডেল প্রেডিকশন
        df_15m_feat = prepare_features(df_15m)
        prediction, confidence = train_and_predict(df_15m_feat)
        signal_side = 'buy' if prediction == 1 else 'sell'
        
        # ৩. অন্যান্য টাইমফ্রেমের কনফার্মেশন (EMA20 দিয়ে ট্রেন্ড চেক)
        df_1h_feat = prepare_features(df_1h)
        df_5m_feat = prepare_features(df_5m)
        df_1m_feat = prepare_features(df_1m)
        
        close_1h = df_1h_feat['close'].iloc[-1]
        ema20_1h = df_1h_feat['EMA_20'].iloc[-1]
        
        close_5m = df_5m_feat['close'].iloc[-1]
        ema20_5m = df_5m_feat['EMA_20'].iloc[-1]
        
        close_1m = df_1m_feat['close'].iloc[-1]
        ema20_1m = df_1m_feat['EMA_20'].iloc[-1]
        
        h1_confirmed = (signal_side == 'buy' and close_1h >= ema20_1h) or (signal_side == 'sell' and close_1h <= ema20_1h)
        m5_confirmed = (signal_side == 'buy' and close_5m >= ema20_5m) or (signal_side == 'sell' and close_5m <= ema20_5m)
        m1_confirmed = (signal_side == 'buy' and close_1m >= ema20_1m) or (signal_side == 'sell' and close_1m <= ema20_1m)
        
        print(f"📊 15m Signal: {signal_side.upper()} ({confidence:.2f}%) | 1h: {h1_confirmed} | 5m: {m5_confirmed} | 1m: {m1_confirmed}", flush=True)
        
        # ৪টি টাইমফ্রেম (1h, 15m, 5m, 1m) কনফার্ম করলে ট্রেড ওপেন হবে
        if confidence >= 55.0 and h1_confirmed and m5_confirmed and m1_confirmed:
            print(f"✅ Executing Confirmed Signal: {signal_side.upper()} with {confidence:.2f}% confidence.", flush=True)
            send_gate_mainnet_order(symbol=symbol, side=signal_side, contracts=1)
        else:
            print(f"⚠️ 4-TF conditions not fully met. Skipping.", flush=True)
            
    except Exception as e:
        print(f"❌ Error in process_symbol: {e}", flush=True)
# ==================== MAIN TRADING LOOP ====================
def run_trading_bot():
    print("🚀 SOL-Focused AI Mainnet Trading Bot [Active with Daily Summary]...", flush=True)
    symbol = 'SOL/USDT'
    
    set_isolated_leverage(symbol, leverage=10)
    
    while True:
        try:
            schedule.run_pending()
            
            is_active, pos_data = check_existing_position(symbol)
            
            if is_active:
                pos_item = pos_data[0] if isinstance(pos_data, list) and len(pos_data) > 0 else pos_data
                unrealised_pnl = float(pos_item.get('unrealised_pnl', 0))
                size = float(pos_item.get('size', 0))
                mark_price = float(pos_item.get('mark_price', 0))
                
                print(f"📊 Active Position Live PnL: ${unrealised_pnl:.2f} USDT | Mark Price: ${mark_price}", flush=True)
                
                target_profit_usd = 0.50   
                max_loss_usd = -0.30       
                close_side = 'sell' if size > 0 else 'buy'
                
                if unrealised_pnl >= target_profit_usd:
                    print(f"🎯 Target profit of ${target_profit_usd} reached! Closing position...")
                    success = send_gate_mainnet_order(symbol=symbol, side=close_side, contracts=abs(int(size)))
                    if success:
                        send_telegram_alert(f"🎯 *Target Profit Reached & Closed!*\nSymbol: `{symbol}`\nProfit: `${unrealised_pnl:.2f}`", TRADE_CHAT_ID)
                
                elif unrealised_pnl <= max_loss_usd:
                    print(f"🛑 Stop-Loss triggered at ${unrealised_pnl:.2f}! Closing position...")
                    success = send_gate_mainnet_order(symbol=symbol, side=close_side, contracts=abs(int(size)))
                    if success:
                        send_telegram_alert(f"🛑 *Stop-Loss Triggered & Closed!*\nSymbol: `{symbol}`\nLoss: `${unrealised_pnl:.2f}`", TRADE_CHAT_ID)
                
                time.sleep(5)
                continue
            
            print(f"[{datetime.now()}] No open position. Running AI Signal Check...", flush=True)
            process_symbol(symbol)
            
            time.sleep(60)
            
        except Exception as e:
            print(f"Error in main loop: {e}", flush=True)
            time.sleep(10)
            
if __name__ == "__main__":
    threading.Thread(target=start_dummy_server, daemon=True).start()
    threading.Thread(target=self_ping_loop, daemon=True).start()
    run_trading_bot()