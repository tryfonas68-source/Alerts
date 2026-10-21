"""
Market scanner v3 (Hyperliquid crypto + xyz stocks/commodities) -> Telegram
"""
import json, os, sys, time
import requests

HL = "https://api.hyperliquid.xyz/info"
TG_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TG_CHAT = os.environ.get("TELEGRAM_CHAT_ID", "")
BASE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(BASE, "scanner_state.json")

INTERVAL = "15m"
CANDLE_MS = 15 * 60 * 1000
LOOKBACK = 96
VOL_MULT = 2.0
MIN_USD_VOL_24H = 5_000_000
MAX_MOVE_24H = 0.25
MIN_STOP_PCT = 0.015
ATR_MULT = 1.5
TP_R = 2.5
FEE = 0.0006 * 2
MIN_NET_RR = 1.7
MAX_DRIFT = 0.01
RISK_USD = 10
LEV = 3
MAX_MARGIN = 180
COOLDOWN_H = 6
MAX_SIGNALS = 5


def hl(p):
    r = requests.post(HL, json=p, timeout=20)
    r.raise_for_status()
    return r.json()


def send(text):
    if not TG_TOKEN or not TG_CHAT:
        print(text)
        return
    requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                  json={"chat_id": TG_CHAT, "text": text}, timeout=20)


def load_state():
    try:
        with open(STATE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def universe():
    coins = []
    for dex in (None, "xyz"):
        p = {"type": "meta"} if dex is None else {"type": "meta", "dex": dex}
        try:
            meta = hl(p)
        except Exception:
            continue
        for u in meta.get("universe", []):
            if not u.get("isDelisted"):
                coins.append(u["name"])
    return coins


def mids():
    out = {}
    for dex in (None, "xyz"):
        p = {"type": "allMids"} if dex is None else {"type": "allMids", "dex": dex}
        try:
            out.update({k: float(v) for k, v in hl(p).items()})
        except Exception:
            pass
    return out


def atr(c, n=14):
    trs = []
    for i in range(1, len(c)):
        h, l, pc = float(c[i]["h"]), float(c[i]["l"]), float(c[i - 1]["c"])
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    return sum(trs[-n:]) / min(n, len(trs)) if trs else 0.0


def check(coin, now):
    start = now - (LOOKBACK + 20) * CANDLE_MS
    c = hl({"type": "candleSnapshot",
            "req": {"coin": coin, "interval": INTERVAL, "startTime": start, "endTime": now}})
    closed = [x for x in c if int(x["T"]) < now]
    if len(closed) < LOOKBACK + 1:
        return None
    last, prev = closed[-1], closed[-(LOOKBACK + 1):-1]
    hi = max(float(x["h"]) for x in prev)
    lo = min(float(x["l"]) for x in prev)
    vols = sorted(float(x["v"]) for x in prev)
    med = vols[len(vols) // 2]
    close, vol = float(last["c"]), float(last["v"])
    usd_vol = sum(float(x["v"]) * float(x["c"]) for x in prev)
    first = float(prev[0]["o"]) if "o" in prev[0] else float(prev[0]["c"])
    move = abs(close / first - 1) if first else 0
    if med <= 0 or vol < VOL_MULT * med or usd_vol < MIN_USD_VOL_24H or move > MAX_MOVE_24H:
        return None
    a = atr(closed)
    if close > hi:
        side, level = "LONG", hi
        stop = min(float(last["l"]), close - ATR_MULT * a, close * (1 - MIN_STOP_PCT))
    elif close < lo:
        side, level = "SHORT", lo
        stop = max(float(last["h"]), close + ATR_MULT * a, close * (1 + MIN_STOP_PCT))
    else:
        return None
    return {"coin": coin, "side": side, "level": level, "close": close,
            "stop": stop, "ratio": vol / med, "usd_vol": usd_vol}


def finalize(s, price):
    if not price:
        return None
    if abs(price / s["close"] - 1) > MAX_DRIFT:
        return None
    long = s["side"] == "LONG"
    risk = (price - s["stop"]) if long else (s["stop"] - price)
    if risk <= 0:
        return None
    risk_pct = risk / price
    tp = s["close"] + TP_R * (s["close"] - s["stop"]) if long else s["close"] - TP_R * (s["stop"] - s["close"])
    rew_pct = ((tp - price) if long else (price - tp)) / price
    net_rr = (rew_pct - FEE) / (risk_pct + FEE)
    if net_rr < MIN_NET_RR:
        return None
    notional = min(RISK_USD / (risk_pct + FEE), MAX_MARGIN * LEV)
    s.update({"price": price, "tp": tp, "risk_pct": risk_pct, "net_rr": net_rr,
              "notional": notional, "margin": notional / LEV,
              "loss": notional * (risk_pct + FEE), "gain": notional * (rew_pct - FEE)})
    return s


def fmt(x):
    return f"{x:.6g}"


def main():
    now = int(time.time() * 1000)
    state = load_state()
    found = []
    for coin in universe():
        if now - state.get(coin, 0) < COOLDOWN_H * 3600 * 1000:
            continue
        try:
            s = check(coin, now)
        except Exception:
            s = None
        if s:
            found.append(s)
        time.sleep(0.1)
    if not found:
        return 0
    m = mids()
    ready = [f for f in (finalize(s, m.get(s["coin"])) for s in found) if f]
    ready.sort(key=lambda s: s["net_rr"], reverse=True)
    stamp = time.strftime("%d/%m %H:%M", time.gmtime(time.time() + 3 * 3600))
    for s in ready[:MAX_SIGNALS]:
        icon = "🟢" if s["side"] == "LONG" else "🔴"
        text = "\n".join([
            f"{icon} {s['coin']} {s['side']} (κερί 15' πέρα από {fmt(s['level'])})",
            f"Τιμή ΤΩΡΑ ~{fmt(s['price'])} | Όγκος {s['ratio']:.1f}x | 24ωρο ~${s['usd_vol']/1e6:.1f}M",
            f"Stop {fmt(s['stop'])} ({s['risk_pct']*100:.1f}%) | TP {fmt(s['tp'])}",
            f"Καθαρό R/R {s['net_rr']:.2f} (μετά τέλη)",
            f"Θέση ~${s['notional']:,.0f} → margin ${s['margin']:,.0f} ({LEV}x)",
            f"Ρίσκο −${s['loss']:.0f} / κέρδος ~+${s['gain']:.0f}",
            "⚠️ Στείλε το στο Claude πριν μπεις. Περίμενε retest αν μπορείς.",
            f"🕒 {stamp}",
        ])
        send(text)
        state[s["coin"]] = now
    with open(STATE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
