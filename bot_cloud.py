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

# ==============================================================================
# 1. 설정 — 환경변수(GitHub Secrets)에서 읽음
# ==============================================================================
KAKAO_REST_API_KEY = os.environ.get("KAKAO_REST_API_KEY", "")
KAKAO_REFRESH_TOKEN = os.environ.get("KAKAO_REFRESH_TOKEN", "")
KAKAO_CLIENT_SECRET = os.environ.get("KAKAO_CLIENT_SECRET", "")

WATCHLIST = {
    # --- 기존 ---
    "비트코인":     "BTC-USD",
    "삼성전자":     "005930.KS",
    "SK하이닉스":   "000660.KS",
    "ASML":        "ASML",
    "테슬라":       "TSLA",
    "알파벳A":      "GOOGL",
    "PTC":         "PTC",
    "메드트로닉":   "MDT",
    "일루미나":     "ILMN",
    # --- 신규 추가 ---
    "엔비디아":     "NVDA",
    "스페이스X":    "SPCX",
    "오라클":       "ORCL",
    "심보틱":       "SYM",
    "마이크로소프트": "MSFT",
    "마이크론":     "MU",
    "화이자":       "PFE",
    "뱅크오브아메리카": "BAC",
    "애플":         "AAPL",
    "테라다인":     "TER",
    "크리스퍼":     "CRSP",
    "로쿠":         "ROKU",
    "팔란티어":     "PLTR",
    "메타":         "META",
    "셰브론":       "CVX",
    "옥시덴털":     "OXY",
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
def detect_signals(macd, sig, rsi):
    signals = []
    macd_now, macd_prev = macd.iloc[-1], macd.iloc[-2]
    sig_now,  sig_prev  = sig.iloc[-1],  sig.iloc[-2]
    rsi_now,  rsi_prev  = rsi.iloc[-1],  rsi.iloc[-2]

    golden = (macd_prev <= sig_prev) and (macd_now > sig_now)
    dead   = (macd_prev >= sig_prev) and (macd_now < sig_now)
    rsi_up50 = (rsi_prev < 50) and (rsi_now >= 50)
    rsi_dn50 = (rsi_prev >= 50) and (rsi_now < 50)
    above0 = macd_now > 0

    if golden and (50 <= rsi_now <= 70):
        tag = "0선위(신뢰↑)" if above0 else "0선아래(신뢰↓)"
        signals.append(("진입", f"전략A 진입조건 충족(골든크로스+RSI{rsi_now:.0f}) [{tag}]"))
    elif (macd_now > sig_now) and rsi_up50 and (rsi_now <= 70):
        tag = "0선위(신뢰↑)" if above0 else "0선아래(신뢰↓)"
        signals.append(("진입", f"RSI 50 상향돌파(정배열 유지) [{tag}]"))
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
def main():
    print(f"=== 신호 체크: {dt.datetime.now():%Y-%m-%d %H:%M} (UTC) ===")
    token = refresh_access_token()
    alerts = []

    for name, ticker in WATCHLIST.items():
        try:
            df = yf.download(ticker, period="6mo", interval="1d",
                             progress=False, auto_adjust=True)
            if df is None or len(df) < 35:
                print(f"  {name}: 데이터부족"); continue
            close = df["Close"]
            if hasattr(close, "columns"):
                close = close.iloc[:, 0]
            macd, sig, _ = calc_macd(close)
            rsi = calc_rsi(close)
            signals, info = detect_signals(macd, sig, rsi)
            price = round(float(close.iloc[-1]), 2)
            if signals:
                for kind, msg in signals:
                    icon = {"진입":"🟢","청산":"🔴","주의":"🟡","관찰":"🔵"}.get(kind,"")
                    alerts.append(f"{icon}[{name}] {kind}\n  {msg}\n  현재가 {price}/RSI {info['rsi']}/MACD {info['macd']}")
                print(f"  {name}: {len(signals)}건")
            else:
                print(f"  {name}: 신호없음(RSI {info['rsi']})")
        except Exception as e:
            print(f"  {name} 오류: {e}")

    if alerts:
        header = f"📊 매매신호 ({dt.datetime.now():%m/%d})\n{'='*18}\n"
        footer = f"\n{'='*18}\n※신호일뿐 매수/매도 명령아님. 차트·손절선 직접확인 후 판단."
        full = header + "\n\n".join(alerts) + footer
        # 카톡 텍스트 길이 제한(약 2000자) 대비 분할
        if len(full) > 1900:
            chunks, cur = [], header
            for a in alerts:
                if len(cur)+len(a) > 1700:
                    chunks.append(cur); cur = ""
                cur += a + "\n\n"
            chunks.append(cur + footer)
            for c in chunks:
                send_kakao(token, c)
            print(f"  → 발송 {len(chunks)}개 메시지로 분할")
        else:
            send_kakao(token, full)
            print("  → 발송 완료")
    else:
        print("  신규 신호 없음 (무신호일은 발송 안 함)")
    print("=== 종료 ===")

if __name__ == "__main__":
    main()
