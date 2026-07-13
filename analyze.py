#!/usr/bin/env python3
"""
Analyze the data collected by hl_snapshot.py. Run locally after a few days/weeks:

    pip install pandas numpy
    python analyze.py

Two analyses:

  (1) SMART-MONEY leaderboard — rank addresses by account-value growth over the
      collected window (with enough observations). These are candidates whose NEW
      positions you'd copy/fade. NOTE: raw equity growth conflates PnL with deposits/
      withdrawals; treat as a shortlist to inspect, not ground truth, until we add
      fill-based PnL.

  (2) LIQUIDATION-PROXIMITY signal — for each coin, test whether a lopsided cluster
      of positions near their liquidation price predicts the direction of the next
      move (using the mark prices recorded in liq_proximity.csv as the price series).
      Hypothesis: heavy near-liq LONGS -> downside cascade; heavy near-liq SHORTS ->
      upside squeeze. Reports correlation + a simple signed-signal backtest.

It degrades gracefully when there isn't much data yet (tells you to keep collecting).
"""
import os, sys
import numpy as np, pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
EQ_F = os.path.join(HERE, "address_equity.csv")
LIQ_F = os.path.join(HERE, "liq_proximity.csv")
FWD_STEPS = 2          # forward horizon in snapshots (e.g. 2 snapshots ahead)
MIN_OBS = 5            # min snapshots to include an address / coin


def smart_money():
    if not os.path.exists(EQ_F):
        print("no address_equity.csv yet — keep collecting."); return
    df = pd.read_csv(EQ_F, parse_dates=["ts"])
    if df.empty:
        print("address_equity.csv empty."); return
    g = df.sort_values("ts").groupby("addr")
    rows = []
    for addr, d in g:
        if len(d) < MIN_OBS:
            continue
        av0, av1 = d["account_value"].iloc[0], d["account_value"].iloc[-1]
        if av0 <= 0:
            continue
        growth = av1 / av0 - 1
        rows.append((addr, len(d), round(av0), round(av1), round(growth * 100, 1),
                     round(d["account_value"].mean())))
    if not rows:
        print(f"smart-money: not enough per-address history yet (need >={MIN_OBS} snapshots)."); return
    lb = pd.DataFrame(rows, columns=["addr", "obs", "av_first", "av_last", "growth_%", "av_mean"])
    lb = lb.sort_values("growth_%", ascending=False)
    span = (df["ts"].max() - df["ts"].min())
    print(f"\n=== SMART-MONEY leaderboard (window {span}, {df['addr'].nunique()} addrs) ===")
    print("Top growers:")
    print(lb.head(15).to_string(index=False))
    print("\nWorst (fade candidates):")
    print(lb.tail(8).to_string(index=False))
    print("\n(next step once more data: join with positions_snapshots.csv and test copying "
          "the top cohort's NEW positions vs fading the bottom cohort.)")


def liq_signal():
    if not os.path.exists(LIQ_F):
        print("\nno liq_proximity.csv yet — keep collecting."); return
    df = pd.read_csv(LIQ_F, parse_dates=["ts"])
    if df.empty:
        print("\nliq_proximity.csv empty."); return
    print(f"\n=== LIQUIDATION-PROXIMITY signal (fwd={FWD_STEPS} snapshots) ===")
    all_sig, all_fwd = [], []
    per_coin = []
    for coin, d in df.sort_values("ts").groupby("coin"):
        d = d.reset_index(drop=True)
        if len(d) < MIN_OBS + FWD_STEPS:
            continue
        mark = d["mark"].astype(float).values
        # signal: net near-liq imbalance normalized by total near-liq (+ = longs more at risk -> bearish)
        nl_long = d["near_liq_long"].astype(float).values
        nl_short = d["near_liq_short"].astype(float).values
        denom = nl_long + nl_short
        sig = np.where(denom > 0, (nl_long - nl_short) / denom, 0.0)
        fwd = np.full(len(mark), np.nan)
        for i in range(len(mark) - FWD_STEPS):
            if mark[i] > 0:
                fwd[i] = mark[i + FWD_STEPS] / mark[i] - 1
        m = ~np.isnan(fwd)
        if m.sum() < MIN_OBS:
            continue
        # expect NEGATIVE correlation (longs-at-risk -> price falls)
        corr = np.corrcoef(sig[m], fwd[m])[0, 1] if np.std(sig[m]) > 0 else np.nan
        per_coin.append((coin, int(m.sum()), round(float(corr), 3) if corr == corr else None))
        all_sig.extend(sig[m]); all_fwd.extend(fwd[m])
    if not per_coin:
        print("not enough per-coin history yet — keep collecting."); return
    pc = pd.DataFrame(per_coin, columns=["coin", "obs", "corr(sig,fwd)"]).sort_values("corr(sig,fwd)")
    print(pc.to_string(index=False))
    if len(all_sig) > 20:
        a, f = np.array(all_sig), np.array(all_fwd)
        pooled = np.corrcoef(a, f)[0, 1]
        # simple strategy: trade -sign(signal) (fade the at-risk side); mean fwd return
        pnl = -np.sign(a) * f
        print(f"\npooled corr(signal, fwd_ret) = {pooled:+.3f}  (want negative)")
        print(f"fade strategy mean fwd return/trade = {pnl.mean()*1e4:+.1f} bps  over {len(pnl)} obs")
        print("(negative pooled corr + positive fade PnL = the liquidation-cascade edge is real; "
              "keep collecting to tighten it and add costs.)")
    else:
        print("\nneed more observations for a pooled test — keep collecting.")


if __name__ == "__main__":
    smart_money()
    liq_signal()
