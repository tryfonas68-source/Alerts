import os, sys, time
import requests

HL = "https://api.hyperliquid.xyz/info"
TG_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TG_CHAT = os.environ.get("TELEGRAM_CHAT_ID", "")

DEXES = ["", "xyz"]
MIN_USD_VOL_24H = 500_000
VOL_MULT = 2.0
MAX_MOVE_24H = 0.25
MIN_RISK, MAX_RISK = 0.004, 0.08
RISK_USD = 20
MAX_SIGNALS = 5


def hl(p):
    r = requests.post(HL, json=p, timeout=20)
    r.raise_for_status()
    return r.json()


def send(text):
    if not TG_TOKEN or not TG_CHAT:
        print(text + "\n")
        return
    requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                  json={"chat_id": TG_CHAT, "text": text}, timeout=20)


def coins():
    out = []
    for dex in DEXES:
        try:
            meta = hl({"type": "meta", "dex": dex}) if dex else hl({"type": "meta"})
            out += [u["name"] for u in meta["universe"] if not u.get("isDelisted")]
        except Exception as e:
            print(f"dex '{dex or 'main'}' error: {e}")
    return out


def check(coin, now):
    c = hl({"type": "candleSnapshot", "req": {"coin": coin, "interval": "1h",
            "startTime": now - 27 * 3600 * 1000, "endTime": now}})
    closed = [x for x in c if int(x["T"]) < now]
    if len(closed) < 25:
        return None
    last, prev = closed[-1], closed[-25:-1]
    o, h, l, cl, v = (float(last[k]) for k in ("o", "h", "l", "c", "v"))
    hi = max(float(x["h"]) for x in prev)
    lo = min(float(x["l"]) for x in prev)
    vols = sorted(float(x["v"]) for x in prev)
    med = vols[len(vols) // 2]
    usd_vol = sum(float(x["v"]) * float(x["c"]) for x in prev)
    move = abs(cl / float(prev[0]["o"]) - 1)
    if med <= 0 or usd_vol < MIN_USD_VOL_24H or move > MAX_MOVE_24H or v < VOL_MULT * med:
        return None
    if cl > hi:
        side, stop = "LONG", l * 0.998
        risk = (cl - stop) / cl
        tp = cl * (1 + 2 * risk)
        level = hi
    elif cl < lo:
        side, stop = "SHORT", h * 1.002
        risk = (stop - cl) / cl
        tp = cl * (1 - 2 * risk)
        level = lo
    else:
        return None
    if not (MIN_RISK <= risk <= MAX_RISK):
        return None
    return dict(coin=coin, side=side, entry=cl, stop=stop, tp=tp, risk=risk,
                level=level, ratio=v / med, usd_vol=usd_vol)


def fmt(x):
    return f"{x:,.6g}"


def main():
    now = int(time.time() * 1000)
    found = []
    for coin in coins():
        try:
            s = check(coin, now)
            if s:
                found.append(s)
        except Exception:
            pass
        time.sleep(0.1)
    found.sort(key=lambda s: s["ratio"], reverse=True)
    stamp = time.strftime("%d/%m %H:%M", time.gmtime(time.time() + 3 * 3600))
    if not found:
        print("No signal", stamp)
        return 0
    for s in found[:MAX_SIGNALS]:
        notional = RISK_USD / s["risk"]
        icon = "🟢" if s["side"] == "LONG" else "🔴"
        send("\n".join([
            f"{icon} {s['coin']} {s['side']} (κλείσιμο ώρας πέρα από {fmt(s['level'])})",
            f"Είσοδος ~{fmt(s['entry'])} | Όγκος {s['ratio']:.1f}x | 24ωρο ~${s['usd_vol']/1e6:.1f}M",
            f"Stop {fmt(s['stop'])} ({s['risk']*100:.1f}%) | TP {fmt(s['tp'])}",
            f"Θέση ~${notional:,.0f} → margin ${notional/10:,.0f} (10x) ή ${notional/5:,.0f} (5x)",
            f"Ρίσκο −${RISK_USD} / κέρδος ~+${RISK_USD*2}",
            "⚠️ Στείλε το στο Claude πριν μπεις.",
            f"🕒 {stamp}",
        ]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
