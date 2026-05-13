# Trading Tool — Handoff to Claude Code

Status as of **2026-05-13** (v2 — portfolio + Telegram). Author: Nick + Claude (Cowork).

## v2: Portfolio + Telegram alerts

New modules under `screener/`:

- `portfolio.py` — Fernet-encrypted state CRUD. `State` dataclass holds the portfolio
  dict, last Telegram update_id, last digest date, and prior-run snapshots used by
  the diff-based alerts. Plaintext never lands on disk.
- `notifier.py` — `TelegramNotifier` wrapping the Bot API. MarkdownV2-escaped sends.
  Auto-splits messages > 4096 chars.
- `bot.py` — Cron-polling Telegram bot. Commands: `/add`, `/remove`, `/list`, `/help`.
  Only acts on the configured chat_id; ignores everything else.
- `alerts.py` — Pure functions returning MarkdownV2 strings for 4 alert types:
  morning digest, action-bucket transitions on holdings, TV triggers firing on
  holdings, fresh top-20 ACTION BUYs.

`run.py` adds `--notify` and `--digest` flags. The GH Actions workflow runs
`--notify` every 15 min during market hours, processes bot commands, sends alerts,
and commits the updated `state.enc` back to main with `[skip ci]`.

**Setup**: see how-to.html §10 for the BotFather + chat_id + Fernet-key walkthrough.
Three GitHub repo secrets required: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`,
`PORTFOLIO_ENCRYPTION_KEY`. Without them the screener still runs and deploys
normally — only the notify pipeline is skipped.

---



The goal: take the logic of `Copy of Primary Trending Stock Screener_04.19.26.xlsx`
(originally built by a friend in Google Sheets, using GOOGLEFINANCE) and re-implement
it as a self-hosted Python tool that produces a TradeSchool-styled HTML dashboard.
Informational only — trades are placed manually in Fidelity.

---

## What's already built

```
Trading Tool/
├── run.py                 # CLI entry point — fully working
├── requirements.txt
├── screener/
│   ├── __init__.py
│   ├── engine.py          # Screener math — 1:1 port of the Excel formulas
│   ├── universe.py        # S&P 500 + Nasdaq 100 ticker lists + watchlist loader
│   ├── fetch.py           # yfinance downloader with 15-min parquet cache
│   └── dashboard.py       # HTML generator, TradeSchool dark theme
└── (HANDOFF.md, this file)
```

### `engine.py` — the heart
Reproduces every formula from the `S&P 500` sheet:

| Excel column | Function / variable |
| --- | --- |
| Dashboard regime (B4) | `market_regime()` returns Risk On / Mixed / Defensive |
| 9D/20D/50D/200D MAs | `per_ticker_metrics()` |
| 3M/6M/12M/5D returns | `_return_n_days_back()` |
| Leadership Score (Q) | `compute_scores()` — weights match the sheet (0.3 / 0.25 / 0.1 / 0.2 / 0.05 / 0.1) |
| Momentum Score (Y) | `compute_scores()` — 0.6 * 20D-above + 0.4 * 5D return |
| Blended Score (AA) | `compute_scores()` — rank-weighted 0.4 / 0.25 / 0.35 |
| ATR (AD) | `_atr14()` — 14-bar true-range average |
| Extension Ratio (AE) | `per_ticker_metrics()` — `(price - 9SMA) / ATR` |
| Entry Grade (AI/AJ) | `_entry_grade()` — A Ideal → G Do Not Chase |
| Final Score (AH) | `compute_scores()` — `(Blended*0.75)+(Loc*0.25)`, × 0.7 if extension > 3 |
| Signal (S) | `_signal()` — BUY/HOLD/WATCH/SELL combining rank, MAs, regime, entry grade |
| Action (AK) | `_action()` — 1 ACTION BUY → 8 PASS combining signal × entry × rule check |
| Rule Check (AL) | `_rule_check()` — Above 200D / Above 50D only / Below 50D |

### `dashboard.py` — TradeSchool framework
Matches `https://tradeschool.fly.dev/` style:
- Slate-900 (`#0F172A`) dark theme, same `<meta theme-color>` as TradeSchool
- Top nav: brand + section links + status chips (regime, exposure, timestamp)
- Bottom nav for mobile (matches TradeSchool's repeated short-name nav)
- Regime panel (7 cells) with color-coded value
- "Top action picks" cards for the highest-ranked 1 ACTION BUY / STARTER / BUYABLE WATCH names
- Full sortable, pill-filterable scanner table with all signal columns
- Disclaimer panel at the bottom (informational, not advice)
- Apple PWA meta tags so you can save to home screen

### `fetch.py` — data
- Uses `yfinance` (matches what TradeSchool uses on its Reference page)
- 14 months of daily history (enough for 200D MA + 12M return)
- Downloads in chunks of 50 tickers, threaded
- 15-minute parquet cache in `.cache/` so re-runs in the same session are instant
- `filter_by_liquidity()` keeps top N tickers by 30-day average dollar volume

### `universe.py` — tickers
- Hard-coded S&P 500 and Nasdaq 100 lists (≈600 unique names)
- Supports an optional `watchlist.txt` to add custom tickers (one per line, `#` comments)

---

## What's NOT done — pick up in Claude Code

1. **End-to-end test on real data.** Cowork sandbox can't reach Yahoo Finance.
   First thing in Claude Code:
   ```bash
   cd "Trading Tool"
   python3 -m venv .venv && source .venv/bin/activate
   pip install -r requirements.txt
   python run.py --top 200
   ```
   Expect: console summary, `dashboard.html` opens in browser, `dashboard.csv` written.

2. **Verify against the Excel sheet.** Pick 3–5 tickers from the original sheet
   (e.g., MRVL, NVDA, AAPL) and confirm:
   - Same regime label (Risk On / Mixed / Defensive)
   - Final Score within ~5% (intraday vs end-of-day data will differ)
   - Same Signal (BUY/HOLD/WATCH/SELL) and Action bucket
   - Same Entry Grade for the same Extension Ratio

3. **Known things to look at:**
   - The Excel `RANK(score, range, 1)` is ascending (lower = better) — the Python
     `_rank_descending()` matches. Double-check rank direction matches your intuition.
   - `pyarrow` is required for the parquet cache; if install is painful on
     Apple Silicon, drop it and switch `fetch.py` cache to CSV.
   - `yfinance` occasionally returns empty DataFrames for delisted/edge tickers —
     `engine.py` already skips them with a `[warn]` print.

4. **Nice-to-haves not started:**
   - Real-time refresh (e.g., a scheduled task running every 15 min during market hours)
   - PWA install (`manifest.json` + service worker) so it lives on your phone home screen like TradeSchool
   - Portfolio tracker page (paste current positions, get signal-vs-action overlay)
   - Sector rankings panel (the Excel `Sector Rankings` sheet still has more logic to port)
   - Backtest mode against the historical Excel `BACK TEST` tab

---

## Useful reference

- Original Excel: `~/AppData/.../uploads/Copy of Primary Trending Stock Screener_04.19.26.xlsx`
- TradeSchool live site: https://tradeschool.fly.dev/
- Yahoo Finance per-ticker URL pattern: `https://finance.yahoo.com/quote/{TICKER}`

## Rules (from the Process tab — verbatim, useful for the Methodology page)

| Rule | Description |
| --- | --- |
| Review cadence | Weekly or monthly; monthly is simpler and reduces churn. |
| Buy rule | BUY = top rank, price > 50D, price > 200D, regime not Defensive. |
| Hold rule | HOLD = rank ≤ 12 and price > 50D. |
| Watch rule | WATCH = rank ≤ 20 and price within ~2% of 50D average. |
| Sell rule | SELL = weak rank or price < 50D. |
| Defensive | When regime = Defensive, idle capital in BIL/SGOV or MM funds. |
| Sizing | Risk On 5–8 names, Mixed 3–5, Defensive 0–2. |
| Holding | 1–6 months typical, sometimes longer. Hold while rank + trend favorable. |
| Mindset | Buy strength, not cheapness. Follow leadership, not bottoms. |
