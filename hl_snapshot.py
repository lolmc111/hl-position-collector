#!/usr/bin/env python3
"""
Hyperliquid position snapshot — one-shot run, designed for a GitHub Actions cron.

Each run:
  1. loads the persisted watchlist (addresses seen so far),
  2. does a short WebSocket burst on the trade tape to discover NEW active addresses,
  3. snapshots every watched address's clearinghouseState (positions + account value
     + liquidation price) via a thread pool,
  4. appends to three CSVs and re-saves the watchlist.

Over weeks this builds two backtestable datasets (see analyze.py):
  * SMART-MONEY  — rank addresses by account-value growth, then copy/fade their moves
  * LIQUIDATION  — per-coin notional sitting near its liquidation price -> cascade signal

No VPS needed: GitHub Actions runs it on a schedule for free and commits the data back.
Read-only, no keys, no orders.

    pip install requests websocket-client
    python hl_snapshot.py
"""
import json, os, time, csv
from concurrent.futures import ThreadPoolExecutor
import requests, websocket  # websocket-client

API = "https://api.hyperliquid.xyz/info"
WS = "wss://api.hyperliquid.xyz/ws"
HERE = os.path.dirname(os.path.abspath(__file__))

HARVEST_COINS = ["BTC", "ETH", "SOL", "HYPE", "XRP", "DOGE", "ZEC", "FARTCOIN",
                 "PUMP", "kPEPE", "AVAX", "LINK", "ENA", "SUI", "WLD", "TAO", "ADA", "AAVE"]
HARVEST_SECONDS = 12         # WS burst per run to find new addresses
MAX_WATCHLIST = 1200         # cap so a snapshot fits comfortably in a cron job
MIN_AV = 2000                # ignore dust accounts
POS_LOG_MIN_AV = 25000       # only log individual positions for accounts >= this (bounds file size)
NEAR_LIQ_PCT = 0.08          # "near liquidation" = within 8% of liq price
WORKERS = 8

WL_FILE = os.path.join(HERE, "watchlist.json")
POS_F = os.path.join(HERE, "positions_snapshots.csv")
EQ_F = os.path.join(HERE, "address_equity.csv")
LIQ_F = os.path.join(HERE, "liq_proximity.csv")


def post(body, retries=4):
    for i in range(retries):
        try:
            r = requests.post(API, json=body, timeout=15)
            if r.status_code == 200:
                return r.json()
            if r.status_code == 429:
                time.sleep(0.5 * (i + 1))
        except Exception:
            time.sleep(0.3)
    return None


def harvest(seconds):
    found = set()
    try:
        ws = websocket.create_connection(WS, timeout=10)
        for c in HARVEST_COINS:
            ws.send(json.dumps({"method": "subscribe", "subscription": {"type": "trades", "coin": c}}))
        ws.settimeout(2)
        t_end = time.time() + seconds
        while time.time() < t_end:
            try:
                m = json.loads(ws.recv())
            except Exception:
                continue
            if m.get("channel") == "trades":
                for t in m["data"]:
                    for u in t.get("users", []):
                        found.add(u)
        ws.close()
    except Exception as e:
        print("harvest error:", e)
    return found


def marks():
    d = post({"type": "metaAndAssetCtxs"})
    if not d:
        return {}
    meta, ctxs = d
    return {u["name"]: float(c.get("markPx") or c.get("oraclePx") or 0) for u, c in zip(meta["universe"], ctxs)}


def main():
    ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    watch = set(json.load(open(WL_FILE))) if os.path.exists(WL_FILE) else set()

    # 1) discover new addresses
    new = harvest(HARVEST_SECONDS)
    for a in new:
        if len(watch) < MAX_WATCHLIST:
            watch.add(a)
    json.dump(sorted(watch), open(WL_FILE, "w"))

    mk = marks()
    addrs = list(watch)

    # 2) snapshot everyone (threaded)
    def fetch(a):
        return a, post({"type": "clearinghouseState", "user": a})
    results = []
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        for a, st in ex.map(fetch, addrs):
            results.append((a, st))

    # 3) aggregate + write
    for f, hdr in [(EQ_F, ["ts", "addr", "account_value", "total_ntl_pos"]),
                   (POS_F, ["ts", "addr", "coin", "szi", "entryPx", "liqPx", "mark", "dist_to_liq", "uPnl", "lev", "acct_value"]),
                   (LIQ_F, ["ts", "coin", "mark", "long_ntl", "short_ntl", "near_liq_long", "near_liq_short", "n_pos"])]:
        if not os.path.exists(f):
            with open(f, "w", newline="") as fh:
                csv.writer(fh).writerow(hdr)

    agg = {}
    eq_rows, pos_rows = [], []
    for a, st in results:
        if not st:
            continue
        av = float(st.get("marginSummary", {}).get("accountValue", 0) or 0)
        ntl = float(st.get("marginSummary", {}).get("totalNtlPos", 0) or 0)
        if av < MIN_AV:
            continue
        eq_rows.append([ts, a, f"{av:.2f}", f"{ntl:.2f}"])
        for ap in st.get("assetPositions", []):
            p = ap.get("position", {})
            coin = p.get("coin"); szi = float(p.get("szi", 0) or 0)
            if not coin or szi == 0:
                continue
            mark = mk.get(coin, 0.0)
            liq = float(p.get("liquidationPx") or 0)
            pv = abs(szi) * mark
            near, dist = False, ""
            if liq and mark:
                dist = (mark - liq) / mark if szi > 0 else (liq - mark) / mark
                near = 0 < dist <= NEAR_LIQ_PCT
            g = agg.setdefault(coin, {"long": 0.0, "short": 0.0, "nl_long": 0.0, "nl_short": 0.0, "n": 0})
            g["n"] += 1
            if szi > 0:
                g["long"] += pv; g["nl_long"] += pv if near else 0
            else:
                g["short"] += pv; g["nl_short"] += pv if near else 0
            if av >= POS_LOG_MIN_AV:
                pos_rows.append([ts, a, coin, f"{szi}", p.get("entryPx", ""), liq or "", f"{mark}",
                                 (f"{dist:.4f}" if dist != "" else ""), p.get("unrealizedPnl", ""),
                                 (p.get("leverage") or {}).get("value", ""), f"{av:.0f}"])

    with open(EQ_F, "a", newline="") as fh:
        csv.writer(fh).writerows(eq_rows)
    with open(POS_F, "a", newline="") as fh:
        csv.writer(fh).writerows(pos_rows)
    with open(LIQ_F, "a", newline="") as fh:
        wr = csv.writer(fh)
        for coin, g in agg.items():
            wr.writerow([ts, coin, f"{mk.get(coin,0)}", f"{g['long']:.0f}", f"{g['short']:.0f}",
                         f"{g['nl_long']:.0f}", f"{g['nl_short']:.0f}", g["n"]])

    print(f"[{ts}] watchlist={len(watch)} (+{len(new)} new) | active_accts={len(eq_rows)} | "
          f"positions_logged={len(pos_rows)} | coins={len(agg)}")


if __name__ == "__main__":
    main()
