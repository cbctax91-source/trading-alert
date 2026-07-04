# -*- coding: utf-8 -*-
"""
================================================================================
 MACD + RSI 매매 신호 → 카카오톡 알림 봇 [GitHub Actions 클라우드 버전]
 - 토큰 자동 갱신(refresh_token) 포함 → 장기 무인 자동화
 - 설정값은 코드에 직접 안 적고 GitHub Secrets(환경변수)에서 읽음
================================================================================

[일반 PC 버전과의 차이]
 1) 액세스 토큰을 6시간마다 자동 갱신 (refresh_token 사용)
 2) 카카오 키/토큰을 코드가 아닌 환경변수(GitHub Secrets)에서 읽음 → 보안
 3) 신호 상태를 파일이 아닌 '당일 신호만' 판정 방식으로 단순화
    (클라우드는 매 실행이 독립적이라 파일 저장이 불안정 → 중복은 하루 1회 실행으로 방지)

[중요] 이 알림은 신호일 뿐 매수/매도 명령이 아닙니다.
       알림이 와도 차트로 매물대·손절선을 직접 확인하고 본인이 판단하세요.
================================================================================
"""

import os
import sys
import json
import datetime as dt

try:
    import yfinance as yf
    import pandas as pd
    import requests
except ImportError:
    print("pip install yfinance pandas requests 필요")
    sys.exit(1)

# 한투 REST 현재가 모듈 (같은 폴더의 kis_price.py). 없으면 야후만 사용.
try:
    import kis_price
    KIS_AVAILABLE = True
except Exception:
    KIS_AVAILABLE = False

# ==============================================================================
# 1. 설정 — 환경변수(GitHub Secrets)에서 읽음
# ==============================================================================
KAKAO_REST_API_KEY = os.environ.get("KAKAO_REST_API_KEY", "")
KAKAO_REFRESH_TOKEN = os.environ.get("KAKAO_REFRESH_TOKEN", "")
KAKAO_CLIENT_SECRET = os.environ.get("KAKAO_CLIENT_SECRET", "")

WATCHLIST = {
    # --- 기존 9종목 ---
    "비트코인":       "BTC-USD",
    "이더리움":       "ETH-USD",
    "삼성전자":       "005930.KS",
    "SK하이닉스":     "000660.KS",
    "ASML":          "ASML",
    "테슬라":         "TSLA",
    "알파벳A":        "GOOGL",
    "PTC":           "PTC",
    "메드트로닉":     "MDT",
    "일루미나":       "ILMN",
    # --- 추가 16종목 ---
    "엔비디아":       "NVDA",
    "스페이스X":      "SPCX",
    "오라클":         "ORCL",
    "심보틱":         "SYM",
    "마이크로소프트":  "MSFT",
    "마이크론":       "MU",
    "화이자":         "PFE",
    "뱅크오브아메리카": "BAC",
    "애플":           "AAPL",
    "테라다인":       "TER",
    "크리스퍼":       "CRSP",
    "로쿠":           "ROKU",
    "팔란티어":       "PLTR",
    "메타":           "META",
    "셰브론":         "CVX",
    "옥시덴털":       "OXY",
    # --- 로테이션 대체축 보강 (기술주와 저상관) ---
    "코스트코":       "COST",
    "치폴레":         "CMG",
    "P&G":           "PG",
    "듀크에너지":     "DUK",
    "넥스트에라":     "NEE",
    "GE버노바":       "GEV",
    "캐터필러":       "CAT",
    # --- 정치테마 보강: 전력(원전)·국방방산·헬스케어 ---
    "컨스텔레이션":   "CEG",
    "록히드마틴":     "LMT",
    "RTX":           "RTX",
    "일라이릴리":     "LLY",
    "유나이티드헬스": "UNH",
    # --- 추가 (소비재·미디어) ---
    "코카콜라":       "KO",
    "넷플릭스":       "NFLX",
    "소니":           "SONY",
    "디즈니":         "DIS",
}

# ==============================================================================
# 2. 카카오 토큰 자동 갱신
# ==============================================================================
def refresh_access_token():
    """refresh_token으로 새 access_token 발급. 항상 fresh 토큰을 받아서 사용."""
    if not KAKAO_REST_API_KEY or not KAKAO_REFRESH_TOKEN:
        print("  [경고] 카카오 키/리프레시토큰 환경변수 미설정 → 발송 생략(미리보기 모드)")
        return None
    url = "https://kauth.kakao.com/oauth/token"
    data = {
        "grant_type": "refresh_token",
        "client_id": KAKAO_REST_API_KEY,
        "refresh_token": KAKAO_REFRESH_TOKEN,
    }
    if KAKAO_CLIENT_SECRET:
        data["client_secret"] = KAKAO_CLIENT_SECRET
    try:
        r = requests.post(url, data=data, timeout=10)
        j = r.json()
        if "access_token" in j:
            # 참고: refresh_token도 갱신되어 올 때가 있음(2개월마다). 그때는 Secret 업데이트 필요.
            if "refresh_token" in j:
                print("  [알림] 새 refresh_token이 발급됨 → GitHub Secret 업데이트 권장")
            return j["access_token"]
        else:
            print(f"  [토큰 갱신 실패] {j}")
            return None
    except Exception as e:
        print(f"  [토큰 갱신 오류] {e}")
        return None

# ==============================================================================
# 3. 지표 계산
# ==============================================================================
def calc_macd(close, fast=12, slow=26, signal=9):
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    macd = ema_fast - ema_slow
    sig = macd.ewm(span=signal, adjust=False).mean()
    return macd, sig, macd - sig

def calc_rsi(close, period=14):
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    ag = gain.ewm(alpha=1/period, adjust=False).mean()
    al = loss.ewm(alpha=1/period, adjust=False).mean()
    return 100 - (100 / (1 + ag/al))

# ==============================================================================
# 4. 신호 감지 (매뉴얼 전략 A/B)
# ==============================================================================
def detect_signals(macd, sig, rsi, vol_ratio=None, above_ma=None):
    """vol_ratio: 당일거래량/20일평균 (>1.5면 거래량 급증)
       above_ma: 종가가 추세 이평선(예:60일선) 위인지 (True/False/None)"""
    signals = []
    macd_now, macd_prev = macd.iloc[-1], macd.iloc[-2]
    sig_now,  sig_prev  = sig.iloc[-1],  sig.iloc[-2]
    rsi_now,  rsi_prev  = rsi.iloc[-1],  rsi.iloc[-2]

    golden = (macd_prev <= sig_prev) and (macd_now > sig_now)
    dead   = (macd_prev >= sig_prev) and (macd_now < sig_now)
    rsi_up50 = (rsi_prev < 50) and (rsi_now >= 50)
    rsi_dn50 = (rsi_prev >= 50) and (rsi_now < 50)
    above0 = macd_now > 0

    # --- 보조 필터: 신뢰도 등급 계산 ---
    def confidence_tag():
        # 0선 위치 + 거래량 + 추세이평 종합 → 신뢰도 별점
        score = 0
        parts = []
        if above0:
            score += 1; parts.append("0선위")
        else:
            parts.append("0선아래")
        if vol_ratio is not None:
            if vol_ratio >= 1.5:
                score += 1; parts.append(f"거래량급증({vol_ratio:.1f}배)")
            elif vol_ratio >= 1.0:
                parts.append(f"거래량보통({vol_ratio:.1f}배)")
            else:
                parts.append(f"거래량부족({vol_ratio:.1f}배)")
        if above_ma is True:
            score += 1; parts.append("추세위")
        elif above_ma is False:
            parts.append("추세아래(역추세주의)")
        grade = "신뢰상★★★" if score >= 3 else ("신뢰중★★" if score == 2 else "신뢰하★")
        return grade, " · ".join(parts)

    if golden and (50 <= rsi_now <= 70):
        grade, detail = confidence_tag()
        signals.append(("진입", f"전략A 진입조건 충족(골든크로스+RSI{rsi_now:.0f}) [{grade}: {detail}]"))
    elif (macd_now > sig_now) and rsi_up50 and (rsi_now <= 70):
        grade, detail = confidence_tag()
        signals.append(("진입", f"RSI 50 상향돌파(정배열 유지) [{grade}: {detail}]"))
    elif golden:
        if rsi_now < 50:
            signals.append(("관찰", f"골든크로스 떴으나 RSI{rsi_now:.0f}(<50) 대기"))
        elif rsi_now > 70:
            signals.append(("관찰", f"골든크로스 떴으나 RSI{rsi_now:.0f}(>70) 과열·추격금지"))

    if dead:
        signals.append(("청산", "MACD 데드크로스 → 보유시 청산검토"))
    if rsi_dn50:
        signals.append(("청산", f"RSI 50 이탈({rsi_now:.0f}) → 보유시 청산검토"))

    if rsi_now >= 70:
        signals.append(("주의", f"RSI{rsi_now:.0f} 과매수·추격금지"))
    elif rsi_now <= 30:
        signals.append(("주의", f"RSI{rsi_now:.0f} 과매도(반등가능, 단 더빠질수도)"))

    info = dict(macd=round(float(macd_now),4), sig=round(float(sig_now),4), rsi=round(float(rsi_now),1))
    if vol_ratio is not None:
        info["vol"] = round(float(vol_ratio),1)
    if above_ma is not None:
        info["trend"] = "위" if above_ma else "아래"
    return signals, info

# ==============================================================================
# 5. 카카오 발송
# ==============================================================================
def send_kakao(token, text):
    if not token:
        print("  ----- 발송 미리보기 -----")
        print(text)
        print("  ------------------------")
        return False
    url = "https://kapi.kakao.com/v2/api/talk/memo/default/send"
    headers = {"Authorization": f"Bearer {token}"}
    template = {"object_type":"text","text":text,
                "link":{"web_url":"https://kr.tradingview.com"},"button_title":"차트 확인"}
    data = {"template_object": json.dumps(template, ensure_ascii=False)}
    try:
        r = requests.post(url, headers=headers, data=data, timeout=10)
        if r.status_code == 200:
            return True
        print(f"  [발송실패] {r.status_code}: {r.text}")
        return False
    except Exception as e:
        print(f"  [발송오류] {e}")
        return False

# ==============================================================================
# 6. 메인
# ==============================================================================
def get_live_close_series(ticker):
    """확정 일봉에 '당일 실시간 현재가'를 진행 중 봉으로 반영한 종가 시리즈 반환.
       - 장중이면 오늘 실시간가가 마지막 봉이 되어 신호가 실시간 반영됨
       - 장 마감/휴장이면 야후가 주는 최신 봉을 그대로 사용"""
    import datetime as dt
    df = yf.download(ticker, period="6mo", interval="1d",
                     progress=False, auto_adjust=True)
    if df is None or len(df) < 35:
        return None, None, None
    close = df["Close"]
    if hasattr(close, "columns"):
        close = close.iloc[:, 0]
    close = close.dropna()

    # 실시간 현재가 취득 시도
    live = None

    # 1) 국내주식이면 한투 REST 우선 (정확한 실시간)
    if KIS_AVAILABLE:
        kis_code = kis_price.to_kis_code(ticker)
        if kis_code:
            try:
                live = kis_price.get_kis_domestic_price(kis_code)
            except Exception:
                live = None

    # 2) 한투로 못 받았으면(미국주식/코인/실패) 야후로 폴백
    if not live:
        try:
            tk = yf.Ticker(ticker)
            fi = getattr(tk, "fast_info", None)
            if fi:
                live = fi.get("last_price") or fi.get("lastPrice")
            if not live:
                intr = tk.history(period="1d", interval="1m")
                if intr is not None and len(intr) > 0:
                    live = float(intr["Close"].dropna().iloc[-1])
        except Exception:
            live = None

    if live and live > 0:
        live = float(live)
        today = pd.Timestamp(dt.date.today())
        last_date = close.index[-1].normalize()
        if last_date == today:
            # 오늘 봉이 이미 있으면 종가를 실시간가로 교체
            close.iloc[-1] = live
        else:
            # 오늘 봉이 없으면(=진행중이나 아직 미반영) 실시간가를 새 봉으로 추가
            close.loc[today] = live

    # --- 보조지표: 거래량 비율 + 추세 이평선 위치 ---
    vol_ratio = None
    above_ma = None
    try:
        vol = df["Volume"]
        if hasattr(vol, "columns"):
            vol = vol.iloc[:, 0]
        vol = vol.dropna()
        if len(vol) >= 20:
            vol_ma20 = vol.iloc[-20:].mean()
            if vol_ma20 > 0:
                vol_ratio = float(vol.iloc[-1]) / float(vol_ma20)
    except Exception:
        vol_ratio = None
    try:
        # 60일선 기준 추세(장기추세 필터). 종가가 60일선 위면 상승추세.
        if len(close) >= 60:
            ma60 = close.iloc[-60:].mean()
            above_ma = bool(float(close.iloc[-1]) > float(ma60))
    except Exception:
        above_ma = None

    return close, vol_ratio, above_ma


def main():
    print(f"=== 신호 체크: {dt.datetime.now():%Y-%m-%d %H:%M} (UTC) ===")
    token = refresh_access_token()
    alerts = []
    no_signal_brief = []
    all_status = []   # 전 종목 상태(RSI) 수집

    for name, ticker in WATCHLIST.items():
        try:
            close, vol_ratio, above_ma = get_live_close_series(ticker)
            if close is None or len(close) < 35:
                print(f"  {name}: 데이터부족")
                all_status.append(f"❔{name}: 데이터부족")
                continue
            macd, sig, _ = calc_macd(close)
            rsi = calc_rsi(close)
            signals, info = detect_signals(macd, sig, rsi, vol_ratio, above_ma)
            price = round(float(close.iloc[-1]), 2)
            rv = info['rsi']
            if signals:
                # 대표 신호 아이콘 하나(우선순위: 진입>청산>주의>관찰)
                kinds = [k for k,_ in signals]
                mark = "🟢" if "진입" in kinds else ("🔴" if "청산" in kinds else ("🟡" if "주의" in kinds else "🔵"))
                for kind, msg in signals:
                    icon = {"진입":"🟢","청산":"🔴","주의":"🟡","관찰":"🔵"}.get(kind,"")
                    alerts.append(f"{icon}[{name}] {kind}\n  {msg}\n  현재가 {price}/RSI {rv}/MACD {info['macd']}")
                    print(f"  {icon} {name}: [{kind}] {msg} (현재가 {price}/RSI {rv}/MACD {info['macd']})")
                all_status.append(f"{mark}{name}: RSI {rv} ({kinds[0]})")
            else:
                print(f"  {name}: 신호없음(RSI {rv})")
                all_status.append(f"⚪{name}: RSI {rv}")
                try:
                    if float(rv) <= 35 or float(rv) >= 65:
                        no_signal_brief.append(f"{name}{float(rv):.0f}")
                except Exception:
                    pass
        except Exception as e:
            print(f"  {name} 오류: {e}")

    # ===== 항상 전 종목 현황을 발송 (신호 있으면 상단에 상세 첨부) =====
    now = dt.datetime.now()
    sig_count = len(alerts)
    MAXLEN = 800   # 카카오 실제 한도 대비 보수적

    # 발송할 "블록" 리스트를 순서대로 구성 → 800자 기준으로 이어붙여 분할
    blocks = []
    if sig_count > 0:
        blocks.append(f"🔔 신규 신호 {sig_count}건 발생!\n【신호 상세】")
        blocks.extend(alerts)           # 각 신호를 개별 블록으로
        blocks.append(f"{'-'*18}")
    else:
        blocks.append("🔵 신규 진입/청산 신호 없음")
    blocks.append(f"【전 종목 현황 ({len(WATCHLIST)}종목)】")
    blocks.extend(all_status)           # 각 종목을 개별 블록으로

    head = f"📊 매매신호 ({now:%m/%d %H:%M})\n{'='*18}\n"
    footer = f"\n{'='*18}\n※신호일뿐 매수/매도 명령아님. 차트·손절선 직접확인."

    # 블록을 800자 기준으로 메시지로 묶기 (첫 메시지에만 head)
    chunks, cur = [], ""
    for b in blocks:
        add = b + "\n"
        if len(cur) + len(add) > MAXLEN and cur:
            chunks.append(cur); cur = ""
        cur += add
    if cur.strip():
        chunks.append(cur)
    # head/footer 부착
    chunks[0] = head + chunks[0]
    chunks[-1] = chunks[-1] + footer

    import time
    for i, c in enumerate(chunks):
        tail = f"\n({i+1}/{len(chunks)})" if len(chunks) > 1 else ""
        ok = send_kakao(token, c + tail)
        print(f"    메시지 {i+1}/{len(chunks)} 발송: {'성공' if ok else '실패'} ({len(c)}자)")
        if i < len(chunks) - 1:
            time.sleep(1.5)   # 카카오 연속발송 제한 회피
    print(f"  → 전 종목 현황 발송 (신호 {sig_count}건, 카톡 {len(chunks)}개)")
    print("=== 종료 ===")

if __name__ == "__main__":
    main()
