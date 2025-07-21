from flask import Flask, jsonify
from binance.client import Client
import threading
import time
import os

app = Flask(__name__)

# ✅ API 키 환경변수로 받기
api_key = os.environ.get("XH7JN637MfMSELLQjpviyLHuaiNvICWYTi2fssTVJQDDQu0lcdczaK64WFqI2xjQ")
api_secret = os.environ.get("CCDDXGfxD1PJCSubXTc406DbFP5pBTuDbZ9WzrrC4nicCpVLtcuQyIrjkl4IKQpr")
client = Client(api_key, api_secret)

# ✅ 변동성 데이터 저장용 변수
volatility_cache = []

# ✅ USDT 선물 심볼 가져오기
def get_usdt_symbols():
    exchange_info = client.futures_exchange_info()
    symbols = [s['symbol'] for s in exchange_info['symbols']
               if s['quoteAsset'] == 'USDT' and s['contractType'] == 'PERPETUAL'
               and not s['symbol'].startswith('LD')]
    return symbols

# ✅ 15분간 변동성 계산
def get_15m_volatility(symbol):
    try:
        klines = client.futures_klines(symbol=symbol, interval=Client.KLINE_INTERVAL_1MINUTE, limit=15)
        highs = [float(k[2]) for k in klines]
        lows = [float(k[3]) for k in klines]
        open_price = float(klines[0][1])
        close_price = float(klines[-1][4])
        high = max(highs)
        low = min(lows)

        volatility = abs((high - low) / low) * 100
        color = "green" if close_price > open_price else "red"
        return {"symbol": symbol, "volatility": volatility, "color": color}
    except:
        return None

# ✅ 백그라운드에서 1분마다 실행될 함수
def update_volatility():
    global volatility_cache
    while True:
        symbols = get_usdt_symbols()
        results = []

        for sym in symbols:
            data = get_15m_volatility(sym)
            if data:
                results.append(data)

        top_30 = sorted(results, key=lambda x: x["volatility"], reverse=True)[:30]
        volatility_cache = top_30
        print(f"🔁 Updated top 30 at {time.strftime('%X')}")
        time.sleep(60)

@app.route("/")
def home():
    return "✅ Binance Volatility API is running"

@app.route("/top_volatility")
def top_volatility():
    return jsonify(volatility_cache)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))  # <-- 핵심!
    threading.Thread(target=update_volatility, daemon=True).start()
    app.run(host="0.0.0.0", port=port)
