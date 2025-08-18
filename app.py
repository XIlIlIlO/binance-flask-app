from flask import Flask, jsonify
from binance.client import Client
import threading
import time
import os
from flask_cors import CORS

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}})

# =========================
# 1) API 키 + 타임아웃 추가
# =========================
api_key = os.environ.get("XH7JN637MfMSELLQjpviyLHuaiNvICWYTi2fssTVJQDDQu0lcdczaK64WFqI2xjQ")
api_secret = os.environ.get("CCDDXGfxD1PJCSubXTc406DbFP5pBTuDbZ9WzrrC4nicCpVLtcuQyIrjkl4IKQpr")

# 연결 3초 / 응답 8초 타임아웃
client = Client(api_key, api_secret, requests_params={"timeout": (3, 8)})

# =========================
# 캐시 (이름 그대로 유지)
# =========================
volatility_cache_15m = []
volatility_cache_1m = []
volatility_cache_1h = []
volatility_cache_5m = []

# =========================
# USDT 페어 심볼 목록 (기존 유지)
# =========================
def get_usdt_symbols():
    exchange_info = client.futures_exchange_info()
    return [
        s['symbol'] for s in exchange_info['symbols']
        if s['quoteAsset'] == 'USDT' and s['contractType'] == 'PERPETUAL'
        and not s['symbol'].startswith('LD')
    ]

# =========================
# 1분봉 klines에서 n개 윈도우로 변동성 계산
# =========================
def _calc_from_1m_klines(klines, n):
    if not klines or len(klines) < n:
        return None
    window = klines[-n:]
    try:
        highs = [float(k[2]) for k in window]
        lows  = [float(k[3]) for k in window]
        open_price  = float(window[0][1])
        close_price = float(window[-1][4])
        high = max(highs)
        low  = max(min(lows), 1e-12)  # 0 나눗셈 방지
        volatility = abs((high - low) / low) * 100
        color = "green" if close_price > open_price else "red"
        return volatility, color
    except:
        return None

# ======================================================
# ✅ 최소 수정 핵심
#    심볼당 60개(1분봉)만 받아서 1/5/15/60분을 한 번에 계산
#    (기존 4개 스레드 → 단일 스레드 루프)
# ======================================================
def update_volatility_all():
    global volatility_cache_1m, volatility_cache_5m, volatility_cache_15m, volatility_cache_1h
    while True:
        start = time.time()
        try:
            symbols = get_usdt_symbols()
        except Exception as e:
            print("[ALL] exchange_info error:", e)
            symbols = []

        res_1m, res_5m, res_15m, res_1h = [], [], [], []

        for sym in symbols:
            # 심볼당 1번만 60개 1분봉 요청
            try:
                kl = client.futures_klines(
                    symbol=sym,
                    interval=Client.KLINE_INTERVAL_1MINUTE,
                    limit=60
                )
            except Exception as e:
                # 타임아웃/네트워크 오류 등은 이번 라운드에서만 누락
                # 다음 라운드에서 재시도됨
                # print(f"[ALL] klines error {sym}:", e)
                kl = None

            # 네 윈도우 계산
            r1  = _calc_from_1m_klines(kl, 1)
            r5  = _calc_from_1m_klines(kl, 5)
            r15 = _calc_from_1m_klines(kl, 15)
            r60 = _calc_from_1m_klines(kl, 60)

            if r1:
                vol, col = r1
                res_1m.append({"symbol": sym, "volatility": vol, "color": col})
            if r5:
                vol, col = r5
                res_5m.append({"symbol": sym, "volatility": vol, "color": col})
            if r15:
                vol, col = r15
                res_15m.append({"symbol": sym, "volatility": vol, "color": col})
            if r60:
                vol, col = r60
                res_1h.append({"symbol": sym, "volatility": vol, "color": col})

        # 정렬 후 상위 30개로 캐시 교체 (원자적)
        res_1m.sort(key=lambda x: x["volatility"], reverse=True)
        res_5m.sort(key=lambda x: x["volatility"], reverse=True)
        res_15m.sort(key=lambda x: x["volatility"], reverse=True)
        res_1h.sort(key=lambda x: x["volatility"], reverse=True)

        volatility_cache_1m  = res_1m[:30]
        volatility_cache_5m  = res_5m[:30]
        volatility_cache_15m = res_15m[:30]
        volatility_cache_1h  = res_1h[:30]

        print(f"[ALL] 🔁 Updated at {time.strftime('%X')} "
              f"(1m:{len(volatility_cache_1m)} / 5m:{len(volatility_cache_5m)} / "
              f"15m:{len(volatility_cache_15m)} / 1h:{len(volatility_cache_1h)})")

        # 60초 주기 유지 (오버런 시 1초만 쉬고 재시작)
        elapsed = time.time() - start
        time.sleep(60 - elapsed if elapsed < 60 else 1.0)

# =========================
# API 엔드포인트 (그대로)
# =========================
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

# =========================
# 서버 실행
# =========================
if __name__ == "__main__":
    # 기존 4개 스레드 대신 단일 스레드로 네 구간 동시 갱신
    threading.Thread(target=update_volatility_all, daemon=True).start()
    app.run(host="0.0.0.0", port=8080)



