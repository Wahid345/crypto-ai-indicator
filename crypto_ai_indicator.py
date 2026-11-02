import os
import requests
import ccxt
import pandas as pd
import numpy as np
import ta
from xgboost import XGBClassifier

def send_telegram_alert(message):
    bot_token = '9052673855:AAGoX5xAiwah6EJaJHxbFgDNtoKMlOLsyhQ'
    chat_id = '5461610444'
    
    if not bot_token or not chat_id:
        print("⚠️ Telegram credentials missing. Skipping notification (Terminal output only).")
        return

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "Markdown"
    }
    try:
        response = requests.post(url, json=payload)
        if response.status_code == 200:
            print("✅ Telegram notification sent successfully!")
        else:
            print(f"❌ Failed to send Telegram msg: {response.text}")
    except Exception as e:
        print(f"Error sending Telegram message: {e}")

def fetch_crypto_data(symbol='BTC/USDT', timeframe='1h', limit=500):
    exchange = ccxt.gateio({
    'enableRateLimit': True,
})
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
    
    df['Body_Size'] = (df['close'] - df['open']).abs()
    df['Upper_Shadow'] = df['high'] - df[['open', 'close']].max(axis=1)
    df['Lower_Shadow'] = df[['open', 'close']].min(axis=1) - df['low']
    df['Return'] = df['close'].pct_change()
    
    df['Target'] = np.where(df['close'].shift(-1) > df['close'], 1, 0)
    df = df.dropna()
    return df

def analyze_timeframe(symbol, timeframe):
    df_raw = fetch_crypto_data(symbol, timeframe, limit=500)
    latest_candle_raw = df_raw.iloc[-1]
    
    df = prepare_features(df_raw)
    
    features = [
        'RSI', 'EMA_20', 'EMA_50', 
        'MACD', 'MACD_Signal', 'BB_Width', 
        'Body_Size', 'Upper_Shadow', 'Lower_Shadow', 'Return'
    ]
    X = df[features]
    y = df['Target']
    
    split = int(len(df) * 0.8)
    X_train, X_test = X.iloc[:split], X.iloc[split:]
    y_train, y_test = y.iloc[:split], y.iloc[split:]
    
    model = XGBClassifier(
        n_estimators=150, 
        learning_rate=0.05, 
        max_depth=4, 
        random_state=42,
        eval_metric='logloss'
    )
    model.fit(X_train, y_train)
    accuracy = model.score(X_test, y_test) * 100
    
    latest_features = X.iloc[[-1]]
    prediction = model.predict(latest_features)[0]
    probabilities = model.predict_proba(latest_features)[0]
    confidence = max(probabilities) * 100
    
    return latest_candle_raw, prediction, accuracy, confidence

def main():
    print("🚀 Starting Separate Telegram Messages BTC/USDT Analyzer...")
    timeframes = ['15m', '1h', '4h', '1d']
    symbol = 'BTC/USDT'
    
    for tf in timeframes:
        try:
            print(f"\n🔄 Analyzing {tf} timeframe...")
            candle, pred, acc, conf = analyze_timeframe(symbol, tf)
            direction = "BULLISH (GREEN) 📈" if pred == 1 else "BEARISH (RED) 📉"
            status = "(Valid ✅)" if conf >= 65.0 else "(Low Conf ⚠️)"
            
            # প্রতি টাইমফ্রেমের জন্য আলাদা মেসেজ ফরম্যাট
            msg = (
                f"📊 *BTC/USDT AI Alert*\n"
                f"⏱ Timeframe: `{tf}`\n"
                f"• Time: `{candle['timestamp']}`\n"
                f"• Open: `${candle['open']:.2f}` | Close: `${candle['close']:.2f}`\n"
                f"• High: `${candle['high']:.2f}` | Low: `${candle['low']:.2f}`\n"
                f"• Pred: *{direction}*\n"
                f"• Conf: `{conf:.1f}%` {status} | Acc: `{acc:.1f}%`"
            )
            
            print(msg)
            # লুপের ভেতরেই একেকটি টাইমফ্রেমের মেসেজ আলাদাভাবে সেন্ড করা হচ্ছে
            send_telegram_alert(msg)
            
        except Exception as e:
            print(f"Error processing {tf}: {e}")
            err_msg = f"❌ *BTC/USDT Error*\nTimeframe: `{tf}`\nError: {e}"
            send_telegram_alert(err_msg)

if __name__ == '__main__':
    main()