import os
import csv
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
api_key = '09c361b00b7927ec102b5df12dc40cd6'
secret_key = '9e4a4ac91726b85854efe88aa03e7bd4da4c5b9c16280313c24e21db4eae2b98'

BOT_TOKEN = '9052673855:AAGoX5xAiwah6EJaJHxbFgDNtoKMlOLsyhQ'
SIGNAL_CHAT_ID = '5461610444'   # Signal Group
TRADE_CHAT_ID = '-5394519129'    # Profit Update Group

# গ্লোবাল ভ্যারিয়েবল দিয়ে ট্রেড স্ট্যাটাস ট্র্যাক করার জন্য
active_trade_in_progress = False

CSV_FILE = "trade_history.csv"
SYMBOLS = ['SOL/USDT']  # শুধুমাত্র SOL নিয়ে কাজ করবে

exchange_config = {
    'apiKey': api_key,
    'secret': secret_key,
    'enableRateLimit': True,
    'options': {
        'defaultType': 'swap'  # USDT Perpetual Futures
    }
}

exchange = ccxt.gate(exchange_config)
exchange.set_sandbox_mode(False)

def send_telegram_alert(message, chat_id):
    if not BOT_TOKEN or not chat_id:
        return
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": chat_id, "text": message, "parse_mode": "Markdown"}
    try:
        requests.post(url, json=payload)
    except Exception as e:
        print(f"Telegram Error: {e}")

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
    accuracy = model.score(X_test, y_test) * 100
    
    latest_features = X.iloc[[-1]]
    prediction = model.predict(latest_features)[0]
    probabilities = model.predict_proba(latest_features)[0]
    confidence = max(probabilities) * 100
    
    latest_candle = df.iloc[-1]
    return latest_candle, prediction, accuracy, confidence

def init_trade_log():
    if not os.path.exists(CSV_FILE):
        with open(CSV_FILE, mode='w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow([
                'Timestamp', 'Symbol', 'Signal', 'Entry_Price', 
                'Contracts', 'Leverage', 'Confidence', 'Take_Profit', 'Stop_Loss'
            ])

def log_trade_to_csv(symbol, signal_side, entry_price, contracts, leverage, confidence, tp_price, sl_price):
    try:
        init_trade_log()
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        with open(CSV_FILE, mode='a', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow([
                timestamp, symbol, signal_side, f"{entry_price:.2f}",
                contracts, leverage, f"{confidence:.1f}%", f"{tp_price:.2f}", f"{sl_price:.2f}"
            ])
    except Exception as e:
        print(f"⚠️ Failed to log trade to CSV: {e}")

# ==================== DIRECT GATE.IO TESTNET API ORDER ====================
def send_gate_testnet_order(symbol, side, contracts):
    host = "https://api-testnet.gateapi.io"
    prefix = "/api/v4"
    endpoint = "/futures/usdt/orders"
    url = host + prefix + endpoint
    
    settle_symbol = symbol.replace('/', '_')
    size = contracts if side == 'buy' else -contracts
    
    query_param = ""
    body = {
        "contract": settle_symbol,
        "size": size,
        "price": "0",  # মার্কেট অর্ডারের জন্য price "0" দিতে হবে
        "tif": "ioc",
        "text": "t-ai-bot"
    }
    
    payload_str = json.dumps(body)
    timestamp = str(int(time.time()))
    
    hashed_payload = hashlib.sha512(payload_str.encode('utf-8')).hexdigest()
    s = f"POST\n{prefix + endpoint}\n{query_param}\n{hashed_payload}\n{timestamp}"
    signature = hmac.new(secret_key.encode('utf-8'), s.encode('utf-8'), hashlib.sha512).hexdigest()
    
    headers = {
        'Accept': 'application/json',
        'Content-Type': 'application/json',
        'KEY': api_key,
        'Timestamp': timestamp,
        'SIGN': signature
    }
    
    try:
        response = requests.post(url, headers=headers, data=payload_str)
        result = response.json()
        if response.status_code == 200 or 'id' in result:
            print(f"✅ Direct API Order Placed Successfully! ID: {result.get('id')}")
            return result
        else:
            print(f"❌ Direct API Error: {result}")
            return None
    except Exception as e:
        print(f"❌ Request Exception: {e}")
        return None

def execute_futures_trade(symbol, signal_side, current_price, current_atr, confidence, leverage=50):
    try:
        # লেভারেজ ৫০x সেট করা
        try:
            exchange.set_leverage(leverage, symbol)
            print(f"⚙️ Leverage successfully set to {leverage}x for {symbol}")
        except Exception as lev_err:
            print(f"⚠️ Leverage set warning (may already be set): {lev_err}")

        atr_multiplier_sl = 2.0
        atr_multiplier_tp = 4.0

        if signal_side == 'BULLISH':
            order_side = 'buy'
            stop_loss_price = current_price - (current_atr * atr_multiplier_sl)
            take_profit_price = current_price + (current_atr * atr_multiplier_tp)
        else:
            order_side = 'sell'
            stop_loss_price = current_price + (current_atr * atr_multiplier_sl)
            take_profit_price = current_price - (current_atr * atr_multiplier_tp)

        # 🚀 কন্টাক্ট সাইজ নির্দিষ্টভাবে ২০ SOL করা হলো
        contracts = 20  
        
        # Direct Gate.io Testnet API call
        order_res = send_gate_testnet_order(symbol, order_side, contracts)
        
        if order_res:
            log_trade_to_csv(symbol, signal_side, current_price, contracts, leverage, confidence, take_profit_price, stop_loss_price)

            side_emoji = "🟢 LONG" if signal_side == 'BULLISH' else "🔴 SHORT"
            trade_msg = (
                f"{side_emoji} *SOL OPENED FUTURES TRADE (20 SOL / 50x)*\n"
                f"• Symbol: `{symbol}`\n"
                f"• Entry: `${current_price:.2f}`\n"
                f"• Contracts: `{contracts} SOL` ({leverage}x)\n"
                f"• 🎯 TP Target: `${take_profit_price:.2f}`\n"
                f"• 🛡️ SL Target: `${stop_loss_price:.2f}`"
            )
            send_telegram_alert(trade_msg, chat_id=TRADE_CHAT_ID)
            return True
    except Exception as e:
        print(f"❌ Futures Trade Error for {symbol}: {e}")
    return False

def check_existing_position(symbol):
    global active_trade_in_progress
    
    host = "https://api-testnet.gateapi.io"
    prefix = "/api/v4"
    settle_symbol = symbol.replace('/', '_')
    endpoint = f"/futures/usdt/positions/{settle_symbol}"
    url = host + prefix + endpoint
    
    timestamp = str(int(time.time()))
    query_param = ""
    s = f"GET\n{prefix + endpoint}\n{query_param}\n\n{timestamp}"
    signature = hmac.new(secret_key.encode('utf-8'), s.encode('utf-8'), hashlib.sha512).hexdigest()
    
    headers = {
        'Accept': 'application/json',
        'KEY': api_key,
        'Timestamp': timestamp,
        'SIGN': signature
    }
    try:
        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            data = response.json()
            pos_size = 0
            if isinstance(data, list) and len(data) > 0:
                pos_size = float(data[0].get('size', 0))
            elif isinstance(data, dict):
                pos_size = float(data.get('size', 0))
                
            if pos_size != 0:
                active_trade_in_progress = True
                return True, data
    except Exception as e:
        print(f"❌ Position Check Error: {e}")
        
    active_trade_in_progress = False
    return False, None

# ================= DIRECT GATE.IO CLOSE API =================
def close_gate_position_direct(symbol, size, signal_side='buy'):
    host = "https://api-testnet.gateapi.io"
    prefix = "/api/v4"
    endpoint = "/futures/usdt/orders"
    url = host + prefix + endpoint
    
    settle_symbol = symbol.replace('/', '_')
    
    # যদি আগের এন্ট্রি BUY (Long) হয়, তবে ক্লোজ করতে হবে SELL (Negative size) দিয়ে
    # আর যদি আগের এন্ট্রি SELL (Short) হয়, তবে ক্লোজ করতে হবে BUY (Positive size) দিয়ে
    if signal_side.lower() == 'buy':
        close_size = -int(abs(size))  # নেগেটিভ সাইজ দিয়ে লং ক্লোজ
    else:
        close_size = int(abs(size))   # পজিটিভ সাইজ দিয়ে শর্ট ক্লোজ
    
    body = {
        "contract": settle_symbol,
        "size": close_size,
        "price": "0",  # মার্কেট অর্ডারের জন্য "0"
        "tif": "ioc",
        "reduce_only": True,
        "text": "t-close-bot"
    }
    
    payload_str = json.dumps(body)
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
        response = requests.post(url, headers=headers, data=payload_str)
        result = response.json()
        
        if response.status_code == 200 or 'id' in result:
            print(f"✅ Direct Close Order Placed Successfully! ID: {result.get('id')}")
            return True
        else:
            print(f"❌ Direct Close API Error: {result}")
            return False
    except Exception as e:
        print(f"❌ Close Request Exception: {e}")
        return False


# ================= UPDATED MONITOR & CLOSE FUNCTION =================
def monitor_and_close_position(symbol, target_profit_usd=1.0):
    global active_trade_in_progress
    
    is_active, pos_data = check_existing_position(symbol)
    if not is_active:
        active_trade_in_progress = False
        return
        
    try:
        pos_item = pos_data[0] if isinstance(pos_data, list) and len(pos_data) > 0 else pos_data
        unrealised_pnl = float(pos_item.get('unrealised_pnl', 0))
        size = float(pos_item.get('size', 0))
        mark_price = float(pos_item.get('mark_price', 0))
        
        print(f"📊 Active Trade Live PnL: ${unrealised_pnl:.2f} USDT (Target: ${target_profit_usd})")
        
        # 🎯 $1 বা তার বেশি প্রফিট হলে সঙ্গে সঙ্গে ক্লোজ করবে
        if unrealised_pnl >= target_profit_usd:
            print(f"🎯 Target profit of ${target_profit_usd} reached! Closing position via Direct API...")
            
            # সরাসরি Gate.io API কল দিয়ে পজিশন ক্লোজ করা
            success = close_gate_position_direct(symbol, size)
            
            if success:
                active_trade_in_progress = False
                print("✅ Position successfully closed at $1+ profit! Lock released.")
                
                close_msg = (
                    f"🤖 *AUTO CLOSED SOL POSITION ($1+ PROFIT)*\n"
                    f"• Symbol: `{symbol}`\n"
                    f"• Profit Reached: `${unrealised_pnl:.2f}`\n"
                    f"• Mark Price: `${mark_price}`"
                )
                send_telegram_alert(close_msg, chat_id=TRADE_CHAT_ID)
            else:
                print("⚠️ Failed to close position through Direct API, will retry in next cycle.")
            
    except Exception as e:
        print(f"⚠️ Position Monitoring Error: {e}")

# 🤖 Real AI / Technical Indicator Signal Function
def get_ai_signal(symbol):
    # 1. Fetch live market candles
    df = fetch_ohlcv_data(symbol, timeframe='15m', limit=100)
    
    # 2. Calculate Indicators / ML Model Prediction
    rsi = calculate_rsi(df)
    ema_fast = calculate_ema(df, 9)
    ema_slow = calculate_ema(df, 21)
    
    # 3. Determine Signal & Confidence
    if rsi.iloc[-1] < 30 and ema_fast.iloc[-1] > ema_slow.iloc[-1]:
        signal = "BUY"
        confidence = 0.65  # 65% Confidence
    elif rsi.iloc[-1] > 70 and ema_fast.iloc[-1] < ema_slow.iloc[-1]:
        signal = "SELL"
        confidence = 0.65
    else:
        signal = "HOLD"
        confidence = 0.40  # Low confidence
        
    return signal, confidence


def place_order_direct(symbol='SOL/USDT', size=20, leverage=50):
    try:
        print(f"🔄 Attempting to open {size} SOL order on {symbol} with {leverage}x leverage...")
        
        # CCXT দিয়ে মার্কেট অর্ডার প্লেস করার সঠিক মেথড
        response = exchange.create_order(
            symbol=symbol,
            type='market',
            side='buy',
            amount=size,
            params={
                'leverage': leverage,
                'margin_mode': 'isolated'
            }
        )
        
        print(f"✅ Order Placed Successfully! ID: {response.get('id', 'N/A')}")
        return True

    except Exception as e:
        print(f"❌ Failed to place order: {e}")
        return False

def process_symbol(symbol='SOL/USDT'):
    print(f"Checking AI signal for {symbol}...")
    
    # ১. এআই মডেল থেকে সিগন্যাল ও কনফিডেন্স নেওয়া 
    # (আপনার প্রজেক্টের নিয়ম অনুযায়ী train_and_predict বা get_ai_signal ফাংশন ব্যবহার করবেন)
    # নিচে উদাহরণ হিসেবে আপনার মডেলের আউটপুট ধরা হলো:
    prediction, confidence = train_and_predict(symbol, timeframe='15m')
    
    # সিগন্যাল সাইড নির্ধারণ (ধরুন prediction 1 হলে BUY, অন্যথায় SELL)
    signal_side = 'BUY' if prediction == 1 else 'SELL'
    
    # ২. কনফিডেন্স ৬০% বা তার বেশি হলে তবেই রিয়েল ট্রেড এক্সিকিউট করবে
    if confidence >= 0.60:
        print(f"✅ AI Signal Confirmed: {signal_side} with {confidence*100:.2f}% confidence.")
        
        # বর্তমান মার্কেট প্রাইস ফেচ করা
        ticker = exchange.fetch_ticker(symbol)
        current_price = ticker['last']
        
        # ৩. আপনার ১০ ডলার ক্যাপিটাল ও ১০x লেভারেজের জন্য কন্ট্রাক্ট অ্যামাউন্ট 
        # (SOL এর ক্ষেত্রে যেমন 0.1 বা আপনার নির্ধারিত সেফ অ্যামাউন্ট)
        amount = 0.1 
        
        print(f"🚀 Executing live trade on Gate.io with 10x Isolated Margin...")
        
        # ৪. মার্জিন, লেভারেজ এবং এসএল/টিপি সহ অর্ডার প্লেস করার ফাংশন কল
        order_result = place_futures_order_with_sl_tp(
            exchange=exchange, 
            symbol=symbol, 
            side=signal_side, 
            amount=amount, 
            entry_price=current_price, 
            sl_percent=0.015,  # ১.৫% হার্ড স্টপ-লস
            tp_percent=0.03    # ৩% টেক-প্রফিট
        )
        
        if order_result and 'id' in order_result:
            print(f"🎯 Order successfully placed and secured with SL/TP! ID: {order_result.get('id')}\n")
        else:
            print(f"❌ Failed to execute order on exchange.\n")
            
    else:
        print(f"⚠️ Signal confidence too low ({confidence*100:.2f}% < 60%). Skipping trade in this cycle.\n")

def set_margin_and_leverage(exchange, symbol, leverage=10):
    try:
        # ১. মার্জিন মোড 'isolated' সেট করা (Gate.io ফিউচারের জন্য)
        # নোট: CCXT-এ অনেক সময় এটি পজিশন ওপেন করার সময় বা আলাদা এন্ডপয়েন্টে হ্যান্ডেল করতে হয়
        try:
            exchange.set_margin_mode('isolated', symbol)
            print(f"Margin mode set to ISOLATED for {symbol}")
        except Exception as e:
            print(f"Note on margin mode (might already be isolated): {e}")

        # ২. লেভারেজ সেট করা
        exchange.set_leverage(leverage, symbol)
        print(f"Leverage successfully set to {leverage}x for {symbol}")
        
    except Exception as e:
        print(f"Error in setting margin/leverage: {e}")

def place_futures_order_with_sl_tp(exchange, symbol, side, amount, entry_price, sl_percent=0.015, tp_percent=0.03):
    try:
        # প্রথমে মার্জিন ও লেভারেজ কনফার্ম করে নেওয়া
        set_margin_and_leverage(exchange, symbol, leverage=10)

        # পজিশন সাইড অনুযায়ী এসএল (Stop Loss) এবং টিপি (Take Profit) প্রাইস হিসাব
        if side.upper() == 'BUY': # Long Position
            tp_price = entry_price * (1 + tp_percent)
            sl_price = entry_price * (1 - sl_percent)
            reduce_side = 'sell'
        else: # Short Position
            tp_price = entry_price * (1 - tp_percent)
            sl_price = entry_price * (1 + sl_percent)
            reduce_side = 'buy'

        print(f"Placing {side} Market Order for {symbol}...")
        
        # মূল মার্কেট অর্ডার প্লেস করা
        market_order = exchange.create_order(
            symbol=symbol,
            type='market',
            side=side,
            amount=amount,
            params={'margin_mode': 'isolated'} # কিছু এক্সচেঞ্জের ক্ষেত্রে এখানে মার্জিন মোড পাস করতে হয়
        )
        
        print(f"Order Executed! ID: {market_order.get('id')}")
        
        # হার্ড স্টপ-লস এবং টেক-প্রফিট ট্রিগার অর্ডার যুক্ত করার অংশ
        # (আপনার প্রয়োজন অনুযায়ী Gate.io-এর conditional/stop অর্ডার প্যারামিটার এখানে যুক্ত করতে পারেন)
        print(f"Target Set -> TP: {tp_price:.4f}, SL: {sl_price:.4f}")
        
        return market_order

    except Exception as e:
        print(f"Failed to place futures order: {e}")
        return None


def job():
    print(f"\n⏰ [{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Running SOL market cycle (20 SOL, 50x)...")
    monitor_and_close_position('SOL/USDT', target_profit_usd=1.0)
    process_symbol('SOL/USDT')

# ================= SCHEDULE & MAIN LOOP =================
if __name__ == "__main__":
    print("🚀 SOL-Focused AI Trading Bot [AI + Timed Cycle Mode Active]...")
    
    # প্রতি ৫ মিনিট পরপর এআই মডেল চেক করবে এবং সিগন্যাল পেলে ট্রেড সাইকেল রান করবে
    schedule.every(5).minutes.do(lambda: process_symbol('SOL/USDT'))
    
    # বট স্টার্ট হওয়ার সাথে সাথে প্রথমবার টেস্ট করার জন্য:
    process_symbol('SOL/USDT')

    while True:
        schedule.run_pending()
        time.sleep(1)