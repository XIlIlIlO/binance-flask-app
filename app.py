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

# ====== 선물 티커 예외 매핑/배제 설정 (원하는 대로 수정) ======
# 예: SHIB -> 1000SHIBUSDT, PEPE -> 1000PEPEUSDT
FUTURES_SYMBOL_OVERRIDES = {
    "SHIB": "1000SHIBUSDT",
    "PEPE": "1000PEPEUSDT",
    "BONK": "1000BONKUSDT"
    # 필요시 추가/삭제
}

# 선물 취급에서 제외할 현물 심볼(스테이블 등)
FUTURES_FORCE_EXCLUDE = {
    # 가령 옆처럼"USDT", "USDC", "USDe", "FDUSD", "DAI",
}

def to_futures_symbol(spot_symbol: str):
    """CMC 심볼 -> 바이낸스 USDT 무기한 선물 심볼 (예외/배제 적용).
       배제되면 None 반환."""
    if not spot_symbol:
        return None
    if spot_symbol in FUTURES_FORCE_EXCLUDE:
        return None
    return FUTURES_SYMBOL_OVERRIDES.get(spot_symbol, f"{spot_symbol}USDT")



# ======================================================
# 도우미: 바이낸스 USDT 무기한 선물 심볼 목록
# ======================================================
def get_usdt_symbols():
    exchange_info = client.futures_exchange_info()
    syms = [
        s['symbol'] for s in exchange_info['symbols']
        if s.get('quoteAsset') == 'USDT'
        and s.get('contractType') == 'PERPETUAL'
        
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

        hi = max(highs)
        lo = max(min(lows), 1e-12)

        volatility = abs((hi - lo) / lo) * 100.0
        color = "green" if close_price > open_price else "red"

        return {
            "volatility": volatility,
            "color": color,
            "quote_vol_sum": quote_vol_sum,
            "open": open_price,
            "close": close_price,
            "hi": hi,
            "lo": lo,
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
                fut = to_futures_symbol(sym)   # ← KEY POINT
                if fut in futures_symbols_set:
                    target.append(fut)

        # 초기 구동 등으로 target이 비면 전체 수행 (안전망)
        symbols = list(sorted(set(all_fut_symbols)))

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
                    "hi": r60["hi"],      # ✅ 추가
                    "lo": r60["lo"]       # ✅ 추가
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
    for row in cmc_top30_cache:  # 실제 Top100
        symbol = row["symbol"]            # 예: BTC
        fut    = to_futures_symbol(symbol)  # ★ 예외/배제 반영

        listed = fut in futures_symbols_set
        oneh   = volatility_map_1h_all.get(fut) if listed else None

        out.append({
            "rank": row["rank"],
            "name": row["name"],
            "symbol": symbol,
            "market_cap_usd": row["market_cap_usd"],

            "futures_symbol": fut if listed else None,   # ★ 미상장은 None
            "volume_usdt_1h": oneh.get("volume_usdt") if oneh else 0,
            "open_1h": oneh.get("open")   if oneh else None,
            "close_1h": oneh.get("close") if oneh else None,
            "color_1h": ("gray" if (not oneh or (oneh.get("volume_usdt") or 0) <= 0) else oneh.get("color")),  # 프론트에서 회색 처리
        })

    # 랭크 순서 유지
    out.sort(key=lambda x: x["rank"] if x["rank"] is not None else 10**9)

    return jsonify({
        "last_updated_unix": cmc_last_update_ts,
        "last_updated_utc": datetime.fromtimestamp(cmc_last_update_ts, tz=timezone.utc).isoformat(),
        "count": len(out),
        "data": out,
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

    start_rank = max(1, start_rank)
    end_rank   = min(100, end_rank)
    if start_rank > end_rank:
        start_rank, end_rank = 1, 25

    out = []
    for row in cmc_top30_cache:  # 실제 Top100
        rank = row.get("rank")
        if rank is None or rank < start_rank or rank > end_rank:
            continue

        symbol = row["symbol"]
        fut    = to_futures_symbol(symbol)  # ★ 예외/배제 반영

        listed = fut in futures_symbols_set
        oneh   = volatility_map_1h_all.get(fut) if listed else None

        out.append({
            "rank": rank,
            "name": row["name"],
            "symbol": symbol,
            "market_cap_usd": row["market_cap_usd"],
            "futures_symbol": fut if listed else None,   # ★ 미상장은 None
            "volume_usdt_1h": oneh.get("volume_usdt") if oneh else 0,
            "open_1h": oneh.get("open")   if oneh else None,
            "close_1h": oneh.get("close") if oneh else None,
            "color_1h": ("gray" if (not oneh or (oneh.get("volume_usdt") or 0) <= 0) else oneh.get("color")),

        })

    out.sort(key=lambda x: x["rank"])

    return jsonify({
        "last_updated_unix": cmc_last_update_ts,
        "last_updated_utc": datetime.fromtimestamp(cmc_last_update_ts, tz=timezone.utc).isoformat(),
        "start": start_rank,
        "end": end_rank,
        "count": len(out),   # ★ 항상 25에 가까움(랭크가 비는 경우 제외)
        "data": out,
    })

# ======================================================
# 🔹 상장 3개월 / 3~6개월 이내 코인 분류 (+ Max 변동폭)
# ======================================================
recent_3m, recent_3to6m = [], []

# ✅ NEW: 3개월 & 3~6개월 코인의 Max 변동폭 랭킹(1분 갱신)
recent_3m_maxrank = []     # [{symbol, max_range_pct, avg_turnover_usdt, color}]
recent_3to6m_maxrank = []  # ✅ 추가

def update_recent_listings():
    global recent_3m, recent_3to6m, recent_3m_maxrank, recent_3to6m_maxrank
    while True:
        start = time.time()
        r3, r36 = [], []
        r3_rank, r36_rank = [], []  # ✅ NEW

        for sym in futures_symbols_set:
            try:
                kl = client.futures_klines(symbol=sym, interval="1d", limit=200)
                if not kl:
                    continue
                d = len(kl)

                highs = [float(k[2]) for k in kl]
                lows  = [float(k[3]) for k in kl]
                closes = [float(k[4]) for k in kl]
                qsum  = sum(float(k[7]) for k in kl)

                hi = max(highs)
                lo = max(min(lows), 1e-12)
                cur = closes[-1]

                # ✅ Max 변동폭 (%)
                max_range_pct = (hi / lo - 1.0) * 100.0
                # ✅ 일평균 거래대금(USDT)
                avg_turnover_usdt = qsum / d if d > 0 else 0.0
                # ✅ 색상 규칙
                cond_pct = ((cur - lo) / lo) * 100.0 * 2.0
                color = "red" if cond_pct <= max_range_pct else "green"

                info_base = {
                    "symbol": sym,
                    "days": d,
                    "max_range_pct": round(max_range_pct, 2)
                }

                if d <= 90:
                    r3.append(info_base)
                    r3_rank.append({
                        "symbol": sym,
                        "max_range_pct": round(max_range_pct, 2),
                        "avg_turnover_usdt": round(avg_turnover_usdt, 2),
                        "color": color
                    })
                elif 90 < d <= 180:
                    r36.append(info_base)
                    # ✅ 3~6개월용 랭킹 추가
                    r36_rank.append({
                        "symbol": sym,
                        "max_range_pct": round(max_range_pct, 2),
                        "avg_turnover_usdt": round(avg_turnover_usdt, 2),
                        "color": color
                    })

            except:
                continue

        # ✅ 캐시 교체
        recent_3m, recent_3to6m = r3, r36

        # ✅ 내림차순 정렬 (MAX 변동폭 높은 순)
        r3_rank.sort(key=lambda x: x["max_range_pct"], reverse=True)
        r36_rank.sort(key=lambda x: x["max_range_pct"], reverse=True)

        # ✅ 결과 저장
        recent_3m_maxrank = r3_rank
        recent_3to6m_maxrank = r36_rank

        print(f"[RECENT] 3m:{len(r3)} / 3~6m:{len(r36)} | 3m-rank:{len(r3_rank)} / 3~6m-rank:{len(r36_rank)}")

        elapsed = time.time() - start
        time.sleep(60 - elapsed if elapsed < 60 else 1.0)


@app.route("/recent_3m")
def get_recent_3m():
    return jsonify(recent_3m)

@app.route("/recent_3to6m")
def get_recent_3to6m():
    return jsonify(recent_3to6m)

@app.route("/recent_3m_maxrange_ranked")
def get_recent_3m_maxrange_ranked():
    return jsonify({
        "last_updated_unix": int(time.time()),
        "count": len(recent_3m_maxrank),
        "data": recent_3m_maxrank
    })

# ✅ NEW: 3~6개월 MAX 변동폭 랭킹용 엔드포인트
@app.route("/recent_3to6m_maxrange_ranked")
def get_recent_3to6m_maxrange_ranked():
    return jsonify({
        "last_updated_unix": int(time.time()),
        "count": len(recent_3to6m_maxrank),
        "data": recent_3to6m_maxrank
    })



# ======================================================
# 🔹 1시간 구간 급등/급락 (전종목 / 신규코인) — 1분 주기 재가공
#    기존 volatility_map_1h_all(hi/lo/close/volume_usdt 포함)을 재활용 → 추가 API 호출 없음
# ======================================================
spike_all_1h_cache   = []  # 전종목 급등
dump_all_1h_cache    = []  # 전종목 급락
spike_new_1h_cache   = []  # 신규코인 급등 (recent_3m 대상)
dump_new_1h_cache    = []  # 신규코인 급락 (recent_3m 대상)

def _compute_spike_dump_from_snapshot(snapshot_entries, limit=26):
    """volatility_map_1h_all의 value 리스트(snapshot)를 입력받아
       급등/급락 랭킹을 계산해 (상위 limit) 반환한다."""
    res_spike, res_dump = [], []
    for e in snapshot_entries:
        try:
            hi = float(e.get("hi") or 0.0)
            lo = float(e.get("lo") or 0.0)
            cur = float(e.get("close") or 0.0)
            qsum = float(e.get("volume_usdt") or 0.0)
            if hi <= 0.0 or lo <= 0.0 or cur <= 0.0:
                continue

            # ✅ 정의
            spike_pct = ((cur - lo) / lo) * 100.0
            dump_pct  = ((hi - cur) / hi) * 100.0
            range_pct = ((hi - lo) / lo) * 100.0

            # ✅ 색상 규칙
            color = "red" if (spike_pct * 2.0 <= range_pct) else "green"

            base = {
                "symbol": e.get("symbol"),
                "volume_usdt_1h": round(qsum, 2),
                "color": color
            }
            res_spike.append({**base, "spike_pct": round(spike_pct, 2)})
            res_dump.append({**base, "dump_pct":  round(dump_pct,  2)})

        except Exception:
            continue

    # 정렬 & 상위 제한
    res_spike.sort(key=lambda x: x["spike_pct"], reverse=True)
    res_dump.sort(key=lambda x: x["dump_pct"],  reverse=True)
    return res_spike[:limit], res_dump[:limit]


def update_spike_dump_views():
    """1분마다 현재 캐시 스냅샷을 읽어 급등/급락(전종목/신규) 4종 캐시를 갱신."""
    global spike_all_1h_cache, dump_all_1h_cache, spike_new_1h_cache, dump_new_1h_cache
    N = 26
    while True:
        start = time.time()
        try:
            # 스냅샷 확보 (원자 교체를 가정 → 얕은 복사)
            snapshot_all = list(volatility_map_1h_all.values())

            # 전종목 급등/급락
            spike_all, dump_all = _compute_spike_dump_from_snapshot(snapshot_all, limit=N)
            spike_all_1h_cache = spike_all
            dump_all_1h_cache  = dump_all

            # 신규코인(최근 3개월) 심볼셋
            # recent_3m: [{symbol, days, max_range_pct}, ...] 형태
            new_symbols = {row["symbol"] for row in recent_3m if row.get("symbol")}
            snapshot_new = [e for e in snapshot_all if e.get("symbol") in new_symbols]

            spike_new, dump_new = _compute_spike_dump_from_snapshot(snapshot_new, limit=N)
            spike_new_1h_cache = spike_new
            dump_new_1h_cache  = dump_new

            print(f"[SPIKE/DUMP VIEWS] all(sp:{len(spike_all)} du:{len(dump_all)}) "
                  f"/ new(sp:{len(spike_new)} du:{len(dump_new)})")

        except Exception as e:
            print("[SPIKE/DUMP VIEWS] error:", e)

        elapsed = time.time() - start
        time.sleep(60 - elapsed if elapsed < 60 else 1.0)


# =========================
# 📡 엔드포인트 4종
# =========================
@app.route("/top_spike_1h_all")
def api_top_spike_1h_all():
    return jsonify({
        "last_updated_unix": int(time.time()),
        "count": len(spike_all_1h_cache),
        "data": spike_all_1h_cache
    })

@app.route("/top_dump_1h_all")
def api_top_dump_1h_all():
    return jsonify({
        "last_updated_unix": int(time.time()),
        "count": len(dump_all_1h_cache),
        "data": dump_all_1h_cache
    })

@app.route("/top_spike_1h_recent")
def api_top_spike_1h_recent():
    return jsonify({
        "last_updated_unix": int(time.time()),
        "count": len(spike_new_1h_cache),
        "data": spike_new_1h_cache
    })

@app.route("/top_dump_1h_recent")
def api_top_dump_1h_recent():
    return jsonify({
        "last_updated_unix": int(time.time()),
        "count": len(dump_new_1h_cache),
        "data": dump_new_1h_cache
    })


# ======================================================
# 실행
# ======================================================
if __name__ == "__main__":
    threading.Thread(target=update_volatility_all, daemon=True).start()
    threading.Thread(target=update_cmc_top30,    daemon=True).start()
    threading.Thread(target=update_recent_listings, daemon=True).start()  # ✅ 추가
    threading.Thread(target=update_spike_dump_views, daemon=True).start()   # ✅ 추가
    app.run(host="0.0.0.0", port=8080)





















