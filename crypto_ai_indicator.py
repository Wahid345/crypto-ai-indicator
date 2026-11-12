import os
import csv
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

# ==================== CONFIGURATION ====================
api_key = 'a9c34f7d3ce1bdfb7d80ae11e8d69f73'
secret_key = '4782ce9313e6961419fd3f2bc8988d5d67d6000ec3b1aff817068fa410b8a5c7'

BOT_TOKEN = '9052673855:AAGoX5xAiwah6EJaJHxbFgDNtoKMlOLsyhQ'
SIGNAL_CHAT_ID = '5461610444'   # Signal Group
TRADE_CHAT_ID = '-5394519129'    # Profit Update Group

CSV_FILE = "trade_history.csv"
SYMBOLS = ['SOL/USDT']

exchange_config = {
    'apiKey': api_key,
    'secret': secret_key,
    'enableRateLimit': True,
    'options': {
        'defaultType': 'swap'   # USDT Perpetual Futures
    }
}

exchange = ccxt.gate(exchange_config)
exchange.load_markets()

# Render Web Service-এর জন্য ফেক পোর্ট সার্ভার
def start_dummy_server():
    try:
        port = int(os.environ.get("PORT", 10000))
        server = HTTPServer(("0.0.0.0", port), SimpleHTTPRequestHandler)
        print(f"✅ Dummy web server successfully bound to port {port}", flush=True)
        server.serve_forever()
    except Exception as e:
        print(f"❌ Dummy server bind error: {e}", flush=True)

threading.Thread(target=start_dummy_server, daemon=True).start()

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

def train_and_predict(symbol, timeframe='15m'):
    df_raw = fetch_crypto_data(symbol, timeframe, limit=500)
    df = prepare_features(df_raw)
    features = ['RSI', 'EMA_20', 'EMA_50', 'MACD', 'MACD_Signal', 'BB_Width', 'ATR', 'Body_Size', 'Upper_Shadow', 'Lower_Shadow', 'Return']
    X = df[features]
    y = df['Target']
    
    split = int(len(df) * 0.8)
    X_train, X_test = X.iloc[:split], X.iloc[split:]
    y_train, y_test = y.iloc[:split], y.iloc[split:]
    
    model = XGBClassifier(n_estimators=150, learning_rate=0.05, max_depth=4, random_state=42, eval_metric='logloss')
    model.fit(X_train, y_train)
    
    latest_features = X.iloc[[-1]]
    prediction = model.predict(latest_features)[0]
    probabilities = model.predict_proba(latest_features)[0]
    confidence = max(probabilities) * 100
    
    return prediction, confidence

# ==================== POSITION CHECK FUNCTION ====================
def check_existing_position(symbol):
    host = "https://api-testnet.gateapi.io"
    prefix = "/api/v4"
    endpoint = f"/futures/usdt/positions/{symbol.replace('/', '_')}"
    url = host + prefix + endpoint
    
    timestamp = str(int(time.time()))
    hashed_payload = hashlib.sha512("".encode('utf-8')).hexdigest()
    s = f"GET\n{prefix + endpoint}\n\n{hashed_payload}\n{timestamp}"
    signature = hmac.new(secret_key.encode('utf-8'), s.encode('utf-8'), hashlib.sha512).hexdigest()
    
    headers = {
        'Accept': 'application/json',
        'Content-Type': 'application/json',
        'KEY': api_key,
        'Timestamp': timestamp,
        'SIGN': signature
    }
    
    try:
        response = requests.get(url, headers=headers, timeout=10)
        if response.status_code == 200:
            data = response.json()
            # Gate.io পজিশন অবজেক্ট বা লিস্ট হ্যান্ডেল করা
            pos_item = data[0] if isinstance(data, list) and len(data) > 0 else data
            size = float(pos_item.get('size', 0))
            
            if size != 0:
                print(f"🔍 Existing position detected! Size: {size}", flush=True)
                return True, pos_item
        return False, None
    except Exception as e:
        print(f"⚠️ Error checking position: {e}", flush=True)
        return False, None

# ==================== ORDER PLACEMENT ====================
def send_gate_testnet_order(symbol, side, contracts):
    host = "https://api-testnet.gateapi.io"
    prefix = "/api/v4"
    endpoint = "/futures/usdt/orders"
    url = host + prefix + endpoint
    
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
    s = f"POST\n{prefix + endpoint}\n\n{hashed_payload}\n{timestamp}"
    signature = hmac.new(secret_key.encode('utf-8'), s.encode('utf-8'), hashlib.sha512).hexdigest()
    
    headers = {
        'Accept': 'application/json',
        'Content-Type': 'application/json',
        'KEY': api_key,
        'Timestamp': timestamp,
        'SIGN': signature
    }
    
    try:
        response = requests.post(url, headers=headers, data=payload_str, timeout=10)
        result = response.json()
        
        if response.status_code in [200, 201] and 'id' in result:
            order_id = result.get('id')
            print(f"✅ Order Placed Successfully! ID: {order_id}", flush=True)
            send_telegram_alert(f"🟢 *Test Order Placed Successfully!*\nSymbol: `{symbol}`\nID: `{order_id}`", SIGNAL_CHAT_ID)
            return result
        else:
            print(f"❌ API Error [{response.status_code}]: {result}", flush=True)
            return None
    except Exception as e:
        print(f"❌ Request Exception: {e}", flush=True)
        return None

def process_symbol(symbol='SOL/USDT'):
    print(f"Checking AI signal and trend for {symbol}...", flush=True)
    
    try:
        # ১. ডেটা ফেচ এবং ট্রেন্ড ফিল্টার সহ ফিচার প্রস্তুত করা
        df_raw = fetch_crypto_data(symbol, timeframe='15m', limit=500)
        df = prepare_features(df_raw)
        
        # ট্রেন্ড ফিল্টার কনফার্মেশনের জন্য ইএমএ চেক
        current_close = df['close'].iloc[-1]
        fast_ema = df['EMA_20'].iloc[-1]
        slow_ema = df['EMA_50'].iloc[-1]
        
        # বাজার বুলিশ না বেয়ারিশ তা নির্ধারণ
        if fast_ema > slow_ema and current_close > fast_ema:
            market_trend = "BULLISH"
        elif fast_ema < slow_ema and current_close < fast_ema:
            market_trend = "BEARISH"
        else:
            market_trend = "SIDEWAYS"
            
        print(f"📊 Market Trend Detected: {market_trend} (Fast EMA: {fast_ema:.2f}, Slow EMA: {slow_ema:.2f})", flush=True)
        
        # ২. এআই মডেল থেকে প্রেডিকশন ও কনফিডেন্স নেওয়া
        prediction, confidence = train_and_predict(symbol, timeframe='15m')
        signal_side = 'buy' if prediction == 1 else 'sell'
        
        # ৩. ট্রেন্ড এবং কনফিডেন্স ম্যাচ করলে তবেই ট্রেড এক্সিকিউট করা
        if confidence >= 55.0:
            # ট্রেন্ড ফিল্টার রুল: বুলিশ ট্রেন্ড হলে শুধু BUY অথবা বেয়ারিশ ট্রেন্ড হলে শুধু SELL এলাউ করবে
            if (market_trend == "BULLISH" and signal_side == 'buy') or (market_trend == "BEARISH" and signal_side == 'sell'):
                print(f"✅ AI Signal Confirmed & Aligned with Trend: {signal_side.upper()} with {confidence:.2f}% confidence.")
                send_gate_testnet_order(symbol=symbol, side=signal_side, contracts=1)
            else:
                print(f"⚠️ Signal ({signal_side.upper()}) conflicts with market trend ({market_trend}). Skipping trade.")
        else:
            print(f"⚠️ Confidence too low ({confidence:.2f}% < 55%). Skipping.")
            
    except Exception as e:
        print(f"❌ Error in process_symbol: {e}", flush=True)

         
# ==================== MAIN TRADING LOOP ====================
def run_trading_bot():
    print("🚀 SOL-Focused AI Mainnet Trading Bot [Active with Daily Summary]...", flush=True)
    symbol = 'SOL/USDT'
    
    set_isolated_leverage(symbol, leverage=5)
    
    while True:
        try:
            # শিডিউল চেক করা (প্রতিদিন নির্দিষ্ট সময়ের কাজ পরিচালনা করার জন্য)
            schedule.run_pending()
            
            is_active, pos_data = check_existing_position(symbol)
            
            if is_active:
                pos_item = pos_data[0] if isinstance(pos_data, list) and len(pos_data) > 0 else pos_data
                unrealised_pnl = float(pos_item.get('unrealised_pnl', 0))
                size = float(pos_item.get('size', 0))
                mark_price = float(pos_item.get('mark_price', 0))
                
                print(f"📊 Active Position Live PnL: ${unrealised_pnl:.2f} USDT | Mark Price: ${mark_price}", flush=True)
                
                target_profit_usd = 1.0   
                max_loss_usd = -0.50      
                close_side = 'sell' if size > 0 else 'buy'
                
                if unrealised_pnl >= target_profit_usd:
                    print(f"🎯 Target profit of ${target_profit_usd} reached! Closing position...")
                    success = send_gate_mainnet_order(symbol=symbol, side=close_side, contracts=abs(int(size)))
                    if success:
                        # তাত্ক্ষণিক প্রফিট অ্যালার্ট টেলিগ্রামে পাঠানো (যা ইতিমধ্যে কোডে আছে)
                        send_telegram_alert(f"🎯 *Target Profit Reached & Closed!*\nSymbol: `{symbol}`\nProfit: `${unrealised_pnl:.2f}`", TRADE_CHAT_ID)
                
                elif unrealised_pnl <= max_loss_usd:
                    print(f"🛑 Stop-Loss triggered at ${unrealised_pnl:.2f}! Closing position...")
                    success = send_gate_mainnet_order(symbol=symbol, side=close_side, contracts=abs(int(size)))
                    if success:
                        send_telegram_alert(f"🛑 *Stop-Loss Triggered & Closed!*\nSymbol: `{symbol}`\nLoss: `${unrealised_pnl:.2f}`", TRADE_CHAT_ID)
                
                time.sleep(10)
                continue
            
            print(f"[{datetime.now()}] No open position. Running AI & Trend Filter...", flush=True)
            process_symbol(symbol)
            
            time.sleep(60)
            
        except Exception as e:
            print(f"Error in main loop: {e}", flush=True)
            time.sleep(10)
            
# ==================== MAIN ENTRY POINT ====================
if __name__ == "__main__":
    run_trading_bot()