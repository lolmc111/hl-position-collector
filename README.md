# HL position collector

Collects Hyperliquid's public per-address position data on a schedule (via GitHub
Actions — no server needed) to build two backtestable datasets over a few weeks:

- **Smart-money**: rank addresses by account-value growth, then copy/fade their moves.
- **Liquidation-proximity**: per-coin notional sitting near its liquidation price → cascade signal (the real version of the liquidation-fade; the hourly-candle price proxy showed no edge).

## How it runs
`hl_snapshot.py` is a one-shot job that GitHub Actions runs **every hour**: it discovers
active addresses from the trade tape (short WebSocket burst), snapshots each watched
address's `clearinghouseState` (positions + account value + liquidation price), and
appends to CSVs that are committed back to this repo. Watchlist persists in `watchlist.json`.

Data files (grow over time):
- `address_equity.csv` — (ts, addr, account_value, total_ntl) → smart-money ranking
- `liq_proximity.csv` — (ts, coin, mark, long/short ntl, near-liq long/short) → liquidation signal
- `positions_snapshots.csv` — per-position detail for accounts ≥ $25k

## Setup (one-time, same as the paper-trade repo)
1. Push this folder to a new **private** GitHub repo.
2. Settings → Actions → General → Workflow permissions → **Read and write** → Save.
3. Actions tab → "HL position collector" → **Run workflow** once to seed it. Then it runs hourly.

Private repos have ~2000 free Actions minutes/month; hourly runs (~2 min each) fit under that.
For a finer cadence, make the repo public (unlimited minutes — the data is all public on-chain info).

## Analyzing (after a few days/weeks)
```
pip install pandas numpy
python analyze.py
```
Prints the smart-money leaderboard and the liquidation-proximity signal test (correlation
of near-liq imbalance with forward returns + a simple fade backtest). It degrades gracefully
while data is still thin.

Read-only, no keys, no orders — this collects data; trading decisions come after the backtest.
