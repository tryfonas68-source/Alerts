import json, os, sys, time
from datetime import datetime, timezone, timedelta
import requests

HL = "https://api.hyperliquid.xyz/info"
TG_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TG_CHAT = os.environ.get("TELEGRAM_CHAT_ID", "")
BASE = os.path.dirname(os.path.abspath(__file__))
ATHENS = timezone(timedelta(hours=3))

def hl(p):
    r = requests.post(HL, json=p, timeout=20)
    r.raise_for_status()
    return r.json()

def volume_ratio(coin):
    now = int(time.time() * 1000)
    c = hl({"type": "candleSnapshot", "req": {"coin": coin, "interval": "15m",
            "startTime": now - 7 * 3600 * 1000, "endTime": now}})
    c = [x for x in c if int(x["T"]) < now]
    if len(c) < 6:
        return None
    last = float(c[-1]["v"])
    prev = sorted(float(x["v"]) for x in c[-21:-1])
    med = prev[len(prev) // 2]
    return last / med if med else 0

def send(text):
    if not TG_TOKEN or not TG_CHAT:
        print(text)
        return
    requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                  json={"chat_id": TG_CHAT, "text": text}, timeout=20)

def load(name, default):
    try:
        with open(os.path.join(BASE, name), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default

def hit(kind, price, level):
    return price >= level if kind in ("breakout_long", "above") else price <= level

def rearm(kind, price, level):
    return price < level * 0.995 if kind in ("breakout_long", "above") else price > level * 1.005

def main():
    cfg = load("setups.json", {"setups": []})
    state = load("state.json", {})
    mids = {k: float(v) for k, v in hl({"type": "allMids"}).items()}
    now = datetime.now(ATHENS)
    stamp = now.strftime("%d/%m %H:%M")
    today = now.strftime("%Y-%m-%d")

    if now.hour >= 8 and state.get("_hb") != today:
        send(f"✅ Alerts ενεργά ({stamp}). BTC {mids.get('BTC', '?')}")
        state["_hb"] = today

    for s in cfg["setups"]:
        if not s.get("active", True):
            continue
        coin, kind, level, key = s["coin"], s["type"], float(s["level"]), s["id"]
        price = mids.get(coin)
        if price is None:
            continue
        if state.get(key) == "fired":
            if rearm(kind, price, level):
                state[key] = "armed"
            continue
        if not hit(kind, price, level):
            continue
        lines = [f"{s.get('emoji', '🔔')} {coin} {s.get('label', kind)}",
                 f"Τιμή {price:g} (όριο {level:g})"]
        if s.get("needs_volume"):
            r = volume_ratio(coin)
            if r is not None:
                lines.append(("✅ ΜΕ όγκο" if r >= 1.5 else "⚠️ ΧΩΡΙΣ όγκο") + f" ({r:.1f}x)")
        for k, name in (("stop", "Stop"), ("tp", "TP ½"), ("margin", "Margin"), ("note", "👉")):
            if s.get(k):
                lines.append(f"{name}: {s[k]}")
        lines.append(f"🕒 {stamp}")
        send("\n".join(lines))
        state[key] = "fired"

    with open(os.path.join(BASE, "state.json"), "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1)

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("Σφάλμα:", e)
        sys.exit(1)
