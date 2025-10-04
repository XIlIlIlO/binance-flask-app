# app.py — Railway (Flask) — CMC Top100 × Binance 1H Enriched + Range Slice

from flask import Flask, jsonify, request
from binance.client import Client
import threading
import time
import requests
from flask_cors import CORS
from datetime import datetime, timezone

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}})

# ======================================================
# 🔒 하드코딩 API 키 (현재 방식 유지)
# ======================================================
BINANCE_API_KEY    = "XH7JN637MfMSELLQjpviyLHuaiNvICWYTi2fssTVJQDDQu0lcdczaK64WFqI2xjQ"
BINANCE_API_SECRET = "CCDDXGfxD1PJCSubXTc406DbFP5pBTuDbZ9WzrrC4nicCpVLtcuQyIrjkl4IKQpr"
CMC_API_KEY        = "6c86676e-4853-4153-8158-310a4b271708"

# 연결 3초 / 응답 8초 타임아웃
client = Client(BINANCE_API_KEY, BINANCE_API_SECRET, requests_params={"timeout": (3, 8)})

# ======================================================
# 캐시
# ======================================================
volatility_cache_1m  = []   # 상위 N 표출용
volatility_cache_5m  = []
volatility_cache_15m = []
volatility_cache_1h  = []

# ✅ 조인용: 전심볼 1H 맵 & 선물 상장 세트
volatility_map_1h_all = {}  # { "BTCUSDT": {...} }
futures_symbols_set   = set()

# CMC 캐시 (이 변수명은 그대로 두되, 실제로 Top100 보관)
CMC_ENDPOINT       = "https://pro-api.coinmarketcap.com/v1/cryptocurrency/listings/latest"
cmc_top30_cache    = []  # [{rank, name, symbol, market_cap_usd}]  ← 실제로 Top100 저장
cmc_last_update_ts = 0


# ======================================================
# 도우미: 바이낸스 USDT 무기한 선물 심볼 목록
# ======================================================
def get_usdt_symbols():
    exchange_info = client.futures_exchange_info()
    syms = [
        s['symbol'] for s in exchange_info['symbols']
        if s.get('quoteAsset') == 'USDT'
        and s.get('contractType') == 'PERPETUAL'
        and not s['symbol'].startswith('LD')
    ]
    global futures_symbols_set
    futures_symbols_set = set(syms)  # 최신화
    return syms


# ======================================================
# 1분봉 klines n개 윈도우에서 변동성/거래대금/시가/종가 계산
#   kline 인덱스: [0:openTime, 1:open, 2:high, 3:low, 4:close, 5:volume,
#                 6:closeTime, 7:quoteAssetVolume, ...]
# ======================================================
def _calc_from_1m_klines(klines, n):
    if not klines or len(klines) < n:
        return None
    window = klines[-n:]
    try:
        highs = [float(k[2]) for k in window]
        lows  = [float(k[3]) for k in window]
        quote_vol_sum = sum(float(k[7]) for k in window)  # USDT 금액 합계

        open_price  = float(window[0][1])
        close_price = float(window[-1][4])
        high = max(highs)
        low  = max(min(lows), 1e-12)

        volatility = abs((high - low) / low) * 100.0
        color = "green" if close_price > open_price else "red"

        return {
            "volatility": volatility,
            "color": color,
            "quote_vol_sum": quote_vol_sum,
            "open": open_price,
            "close": close_price,
        }
    except Exception:
        return None


# ======================================================
# 주기 업데이트: 1분봉 60개로 1/5/15/60분 결과 동시 계산 (1분 주기)
#  - 엔드포인트 표출용 상위 N (26)
#  - 조인용 전심볼 1H 맵 (volatility_map_1h_all)
#  - 최적화: CMC Top100 × "SYMBOLUSDT" 교집합만 1분봉 수집
# ======================================================
def update_volatility_all():
    global volatility_cache_1m, volatility_cache_5m, volatility_cache_15m, volatility_cache_1h, volatility_map_1h_all
    N = 26
    while True:
        start = time.time()
        try:
            all_fut_symbols = get_usdt_symbols()
        except Exception as e:
            print("[ALL] exchange_info error:", e)
            all_fut_symbols = []

        # CMC Top100의 심볼 집합 (예: {"BTC","ETH",...})
        cmc_symbols = {row["symbol"] for row in cmc_top30_cache if row.get("symbol")}
        # 우리가 조인에 쓰는 표준 선물 티커는 "SYMBOLUSDT" 뿐이므로, 그 교집합만 수집
        target = []
        if cmc_symbols and futures_symbols_set:
            for sym in cmc_symbols:
                fut = f"{sym}USDT"
                if fut in futures_symbols_set:
                    target.append(fut)

        # 초기 구동 등으로 target이 비면 전체 수행 (안전망)
        symbols = target if target else all_fut_symbols

        res_1m, res_5m, res_15m, res_1h_list = [], [], [], []
        map_1h_all = {}

        for sym in symbols:
            try:
                kl = client.futures_klines(symbol=sym, interval=Client.KLINE_INTERVAL_1MINUTE, limit=60)
            except Exception:
                kl = None

            r1  = _calc_from_1m_klines(kl, 1)
            r5  = _calc_from_1m_klines(kl, 5)
            r15 = _calc_from_1m_klines(kl, 15)
            r60 = _calc_from_1m_klines(kl, 60)

            if r1:
                res_1m.append({"symbol": sym, "volatility": r1["volatility"], "color": r1["color"], "volume_usdt": r1["quote_vol_sum"]})
            if r5:
                res_5m.append({"symbol": sym, "volatility": r5["volatility"], "color": r5["color"], "volume_usdt": r5["quote_vol_sum"]})
            if r15:
                res_15m.append({"symbol": sym, "volatility": r15["volatility"], "color": r15["color"], "volume_usdt": r15["quote_vol_sum"]})
            if r60:
                entry_1h = {
                    "symbol": sym,
                    "volatility": r60["volatility"],
                    "color": r60["color"],             # ✅ 1시간 시가/종가 기반
                    "volume_usdt": r60["quote_vol_sum"],
                    "open": r60["open"],               # ✅ 1시간 시가
                    "close": r60["close"],             # ✅ 1시간 종가
                }
                res_1h_list.append(entry_1h)
                map_1h_all[sym] = entry_1h

        # 정렬
        res_1m.sort(key=lambda x: x["volatility"], reverse=True)
        res_5m.sort(key=lambda x: x["volatility"], reverse=True)
        res_15m.sort(key=lambda x: x["volatility"], reverse=True)
        res_1h_list.sort(key=lambda x: x["volatility"], reverse=True)

        # 원자 교체
        volatility_map_1h_all = map_1h_all
        volatility_cache_1m  = res_1m[:N]
        volatility_cache_5m  = res_5m[:N]
        volatility_cache_15m = res_15m[:N]
        volatility_cache_1h  = res_1h_list[:N]

        print(f"[ALL] 🔁 {time.strftime('%X')} (symbols:{len(symbols)} | 1m:{len(res_1m)} / 5m:{len(res_5m)} / 15m:{len(res_15m)} / 1h:{len(res_1h_list)})")
        elapsed = time.time() - start
        time.sleep(60 - elapsed if elapsed < 60 else 1.0)


# ======================================================
# CMC Top100 (5분 주기)
# ======================================================
def _fetch_cmc_top100():
    headers = {"Accepts": "application/json", "X-CMC_PRO_API_KEY": CMC_API_KEY}
    params  = {"start": "1", "limit": "100", "convert": "USD", "sort": "market_cap", "sort_dir": "desc"}
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


def update_cmc_top30():  # 함수명은 호환을 위해 유지 (실제로 Top100)
    global cmc_top30_cache, cmc_last_update_ts
    while True:
        start = time.time()
        rows = _fetch_cmc_top100()      # ★ Top100으로 확장
        if rows:
            cmc_top30_cache    = rows   # ★ 변수명은 유지하되, 내용은 Top100
            cmc_last_update_ts = int(time.time())
            print(f"[CMC] ✅ updated {len(rows)} at {time.strftime('%X')}")
        else:
            print("[CMC] ⚠️ update skipped (empty)")
        elapsed = time.time() - start
        sleep_for = 300 - elapsed if elapsed < 300 else 5  # 5분 주기
        time.sleep(max(1, sleep_for))


# ======================================================
# 엔드포인트 (기존 유지 + 범위 전용 추가)
# ======================================================
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

# ✅ CMC Top100 × Binance 1H 교집합 병합 (기존 엔드포인트: 전체 반환)
@app.route("/top_marketcap_enriched")
def top_marketcap_enriched():
    out = []
    for row in cmc_top30_cache:  # 실제로 Top100
        symbol = row["symbol"]              # 예: BTC, ETH, ...
        fut    = symbol + "USDT"            # 예: BTCUSDT

        # 교집합: 실제 바이낸스 선물에 있어야 병합
        if fut not in futures_symbols_set:
            continue

        oneh = volatility_map_1h_all.get(fut)
        if not oneh:
            continue  # 아직 1H 데이터가 로드 전일 수 있음

        out.append({
            "rank": row["rank"],
            "name": row["name"],
            "symbol": symbol,
            "market_cap_usd": row["market_cap_usd"],

            "futures_symbol": fut,
            "volume_usdt_1h": oneh.get("volume_usdt"),
            "open_1h": oneh.get("open"),
            "close_1h": oneh.get("close"),
            "color_1h": oneh.get("color"),  # green/red (1시간 구간)
        })

    # 시총 순서 유지(이미 rank 정렬). 필요 시 아래로 재정렬 가능
    # out.sort(key=lambda x: (x["market_cap_usd"] is None, -(x["market_cap_usd"] or 0)))

    return jsonify({
        "last_updated_unix": cmc_last_update_ts,
        "last_updated_utc": datetime.fromtimestamp(cmc_last_update_ts, tz=timezone.utc).isoformat(),
        "count": len(out),
        "data": out,
    })


# ✅ 새 엔드포인트: 랭크 범위 슬라이싱 (예: ?start=1&end=25)
@app.route("/top_marketcap_enriched_range")
def top_marketcap_enriched_range():
    try:
        start_rank = int(request.args.get("start", 1))
        end_rank   = int(request.args.get("end", 25))
    except ValueError:
        start_rank, end_rank = 1, 25

    # 범위 가드
    start_rank = max(1, start_rank)
    end_rank   = min(100, end_rank)
    if start_rank > end_rank:
        start_rank, end_rank = 1, 25

    out = []
    for row in cmc_top30_cache:  # 실제로 Top100
        rank = row.get("rank")
        if rank is None or rank < start_rank or rank > end_rank:
            continue

        symbol = row["symbol"]
        fut    = symbol + "USDT"

        if fut not in futures_symbols_set:
            continue

        oneh = volatility_map_1h_all.get(fut)
        if not oneh:
            continue

        out.append({
            "rank": rank,
            "name": row["name"],
            "symbol": symbol,
            "market_cap_usd": row["market_cap_usd"],
            "futures_symbol": fut,
            "volume_usdt_1h": oneh.get("volume_usdt"),
            "open_1h": oneh.get("open"),
            "close_1h": oneh.get("close"),
            "color_1h": oneh.get("color"),
        })

    out.sort(key=lambda x: x["rank"])

    return jsonify({
        "last_updated_unix": cmc_last_update_ts,
        "last_updated_utc": datetime.fromtimestamp(cmc_last_update_ts, tz=timezone.utc).isoformat(),
        "start": start_rank,
        "end": end_rank,
        "count": len(out),
        "data": out,
    })


# ======================================================
# 실행
# ======================================================
if __name__ == "__main__":
    threading.Thread(target=update_volatility_all, daemon=True).start()
    threading.Thread(target=update_cmc_top30,    daemon=True).start()
    app.run(host="0.0.0.0", port=8080)




