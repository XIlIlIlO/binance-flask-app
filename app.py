# app.py (Railway) — 하드코딩 버전

from flask import Flask, jsonify
from binance.client import Client
import threading
import time
import os
from flask_cors import CORS
import requests

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}})

# =========================
# 🔒 하드코딩 API 키
# =========================
BINANCE_API_KEY    = "XH7JN637MfMSELLQjpviyLHuaiNvICWYTi2fssTVJQDDQu0lcdczaK64WFqI2xjQ"
BINANCE_API_SECRET = "CCDDXGfxD1PJCSubXTc406DbFP5pBTuDbZ9WzrrC4nicCpVLtcuQyIrjkl4IKQpr"
CMC_API_KEY        = "6c86676e-4853-4153-8158-310a4b271708"

# 연결 3초 / 응답 8초 타임아웃
client = Client(BINANCE_API_KEY, BINANCE_API_SECRET, requests_params={"timeout": (3, 8)})

# =========================
# 캐시
# =========================
volatility_cache_15m = []
volatility_cache_1m = []
volatility_cache_1h = []
volatility_cache_5m = []

# =========================
# USDT 페어 심볼
# =========================
def get_usdt_symbols():
    exchange_info = client.futures_exchange_info()
    return [
        s['symbol'] for s in exchange_info['symbols']
        if s['quoteAsset'] == 'USDT' and s['contractType'] == 'PERPETUAL'
        and not s['symbol'].startswith('LD')
    ]

# =========================
# 1분봉 klines에서 n개 윈도우 변동성 + 거래대금(quote_vol 합)
# =========================
def _calc_from_1m_klines(klines, n):
    if not klines or len(klines) < n:
        return None
    window = klines[-n:]
    try:
        highs = [float(k[2]) for k in window]
        lows  = [float(k[3]) for k in window]
        quote_vol_sum = sum(float(k[7]) for k in window)  # USDT 금액
        open_price  = float(window[0][1])
        close_price = float(window[-1][4])
        high = max(highs)
        low  = max(min(lows), 1e-12)
        volatility = abs((high - low) / low) * 100.0
        color = "green" if close_price > open_price else "red"
        return volatility, color, quote_vol_sum
    except:
        return None

# ======================================================
# ✅ 1분봉 60개로 1/5/15/60분 동시 계산 (1분 주기)
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
            try:
                kl = client.futures_klines(
                    symbol=sym,
                    interval=Client.KLINE_INTERVAL_1MINUTE,
                    limit=60
                )
            except Exception:
                kl = None

            r1  = _calc_from_1m_klines(kl, 1)
            r5  = _calc_from_1m_klines(kl, 5)
            r15 = _calc_from_1m_klines(kl, 15)
            r60 = _calc_from_1m_klines(kl, 60)

            if r1:
                vol, col, qv = r1
                res_1m.append({"symbol": sym, "volatility": vol, "color": col, "volume_usdt": qv})
            if r5:
                vol, col, qv = r5
                res_5m.append({"symbol": sym, "volatility": vol, "color": col, "volume_usdt": qv})
            if r15:
                vol, col, qv = r15
                res_15m.append({"symbol": sym, "volatility": vol, "color": col, "volume_usdt": qv})
            if r60:
                vol, col, qv = r60
                res_1h.append({"symbol": sym, "volatility": vol, "color": col, "volume_usdt": qv})

        res_1m.sort(key=lambda x: x["volatility"], reverse=True)
        res_5m.sort(key=lambda x: x["volatility"], reverse=True)
        res_15m.sort(key=lambda x: x["volatility"], reverse=True)
        res_1h.sort(key=lambda x: x["volatility"], reverse=True)

        volatility_cache_1m  = res_1m[:30]
        volatility_cache_5m  = res_5m[:30]
        volatility_cache_15m = res_15m[:30]
        volatility_cache_1h  = res_1h[:30]

        print(f"[ALL] 🔁 {time.strftime('%X')} (1m:{len(res_1m)} / 5m:{len(res_5m)} / 15m:{len(res_15m)} / 1h:{len(res_1h)})")
        elapsed = time.time() - start
        time.sleep(60 - elapsed if elapsed < 60 else 1.0)

# ======================================================
# ✅ CMC Top30 (5분 주기) — 하드코딩 키 사용
# ======================================================
CMC_ENDPOINT = "https://pro-api.coinmarketcap.com/v1/cryptocurrency/listings/latest"
cmc_top30_cache = []
cmc_last_update_ts = 0

def _fetch_cmc_top30() -> list:
    headers = {
        "Accepts": "application/json",
        "X-CMC_PRO_API_KEY": CMC_API_KEY
    }
    params = {
        "start": "1",
        "limit": "30",
        "convert": "USD",
        "sort": "market_cap",
        "sort_dir": "desc"
    }
    try:
        r = requests.get(CMC_ENDPOINT, headers=headers, params=params, timeout=20)
        r.raise_for_status()
        payload = r.json()
        if payload.get("status", {}).get("error_code"):
            print("[CMC] error:", payload["status"].get("error_message"))
            return []
        rows = []
        for item in payload.get("data", []):
            usd = item.get("quote", {}).get("USD", {})
            rows.append({
                "rank": item.get("cmc_rank"),
                "name": item.get("name"),
                "symbol": item.get("symbol"),
                "market_cap_usd": usd.get("market_cap")
            })
        rows.sort(key=lambda x: (x["market_cap_usd"] is None, -(x["market_cap_usd"] or 0)))
        return rows
    except requests.RequestException as e:
        print("[CMC] req error:", e)
        return []

def update_cmc_top30():
    global cmc_top30_cache, cmc_last_update_ts
    while True:
        start = time.time()
        rows = _fetch_cmc_top30()
        if rows:
            cmc_top30_cache = rows
            cmc_last_update_ts = int(time.time())
            print(f"[CMC] ✅ updated {len(rows)} at {time.strftime('%X')}")
        else:
            print("[CMC] ⚠️ update skipped (empty)")
        elapsed = time.time() - start
        sleep_for = 300 - elapsed if elapsed < 300 else 5
        time.sleep(max(1, sleep_for))

# =========================
# 조인 엔드포인트 (시총+1H 거래대금/색)
# =========================
@app.route("/top_marketcap_enriched")
def top_marketcap_enriched():
    out = []
    for row in cmc_top30_cache:
        symbol = row["symbol"]
        fut = symbol + "USDT"
        oneh = next((x for x in volatility_cache_1h if x["symbol"] == fut), None)
        out.append({
            "rank": row["rank"],
            "name": row["name"],
            "symbol": symbol,
            "market_cap_usd": row["market_cap_usd"],
            "futures_symbol": fut,
            "volume_usdt_1h": (oneh or {}).get("volume_usdt"),
            "color_1h": (oneh or {}).get("color")
        })
    return jsonify({
        "last_updated": cmc_last_update_ts,
        "data": out
    })

# 기존 변동성 엔드포인트
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
    threading.Thread(target=update_volatility_all, daemon=True).start()
    threading.Thread(target=update_cmc_top30, daemon=True).start()
    app.run(host="0.0.0.0", port=8080)



