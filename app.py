from flask import Flask, jsonify
from binance.client import Client
import threading
import time
import os
from flask_cors import CORS

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}})

# API 키 환경변수
api_key = os.environ.get("XH7JN637MfMSELLQjpviyLHuaiNvICWYTi2fssTVJQDDQu0lcdczaK64WFqI2xjQ")
api_secret = os.environ.get("CCDDXGfxD1PJCSubXTc406DbFP5pBTuDbZ9WzrrC4nicCpVLtcuQyIrjkl4IKQpr")
client = Client(api_key, api_secret)

# 변동성 캐시
volatility_cache_15m = []
volatility_cache_1m = []
volatility_cache_1h = []
volatility_cache_5m = []

# USDT 페어 심볼 목록 가져오기
def get_usdt_symbols():
    exchange_info = client.futures_exchange_info()
    return [
        s['symbol'] for s in exchange_info['symbols']
        if s['quoteAsset'] == 'USDT' and s['contractType'] == 'PERPETUAL'
        and not s['symbol'].startswith('LD')
    ]

# 변동성 계산 함수
def get_volatility(symbol, interval, limit):
    try:
        klines = client.futures_klines(symbol=symbol, interval=interval, limit=limit)
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

# 15분 변동성 업데이트
def update_volatility_15m():
    global volatility_cache_15m
    while True:
        start = time.time()
        symbols = get_usdt_symbols()
        results = []

        for sym in symbols:
            data = get_volatility(sym, Client.KLINE_INTERVAL_1MINUTE, 15)
            if data:
                results.append(data)

        top_30 = sorted(results, key=lambda x: x["volatility"], reverse=True)[:30]
        volatility_cache_15m = top_30
        print(f"[15m] 🔁 Updated at {time.strftime('%X')} with {len(top_30)} entries")
        time.sleep(max(0, 60 - (time.time() - start)))

# 1분 변동성 업데이트
def update_volatility_1m():
    global volatility_cache_1m
    while True:
        start = time.time()
        symbols = get_usdt_symbols()
        results = []

        for sym in symbols:
            data = get_volatility(sym, Client.KLINE_INTERVAL_1MINUTE, 1)
            if data:
                results.append(data)

        top_30 = sorted(results, key=lambda x: x["volatility"], reverse=True)[:30]
        volatility_cache_1m = top_30
        print(f"[1m] 🔁 Updated at {time.strftime('%X')} with {len(top_30)} entries")
        time.sleep(max(0, 60 - (time.time() - start)))

# 1시간 변동성 업데이트
def update_volatility_1h():
    global volatility_cache_1h
    while True:
        start = time.time()
        symbols = get_usdt_symbols()
        results = []

        for sym in symbols:
            data = get_volatility(sym, Client.KLINE_INTERVAL_1MINUTE, 60)
            if data:
                results.append(data)

        top_30 = sorted(results, key=lambda x: x["volatility"], reverse=True)[:30]
        volatility_cache_1h = top_30
        print(f"[1h] 🔁 Updated at {time.strftime('%X')} with {len(top_30)} entries")
        time.sleep(max(0, 60 - (time.time() - start)))

def update_volatility_5m():
    global volatility_cache_5m
    while True:
        start = time.time()
        symbols = get_usdt_symbols()
        results = []

        for sym in symbols:
            data = get_volatility(sym, Client.KLINE_INTERVAL_1MINUTE, 5)
            if data:
                results.append(data)

        top_30 = sorted(results, key=lambda x: x["volatility"], reverse=True)[:30]
        volatility_cache_5m = top_30
        print(f"[5m] 🔁 Updated at {time.strftime('%X')} with {len(top_30)} entries")
        time.sleep(max(0, 60 - (time.time() - start)))

# API 엔드포인트
@app.route("/top_volatility")
def top_volatility_15m():
    return jsonify(volatility_cache_15m)

@app.route("/top_volatility_1m")
def top_volatility_1m():
    return jsonify(volatility_cache_1m)

@app.route("/top_volatility_1h")
def top_volatility_1h():
    return jsonify(volatility_cache_1h)

@app.route("/top_volatility_5m")
def top_volatility_5m():
    return jsonify(volatility_cache_5m)


# 서버 실행
if __name__ == "__main__":
    threading.Thread(target=update_volatility_15m, daemon=True).start()
    threading.Thread(target=update_volatility_1m, daemon=True).start()
    threading.Thread(target=update_volatility_1h, daemon=True).start()
    threading.Thread(target=update_volatility_5m, daemon=True).start()
    app.run(host="0.0.0.0", port=8080)

