"""
Trading Tool — main entry point.

Usage:
    python run.py                 # default: top 500 by liquidity, opens dashboard
    python run.py --top 200       # smaller universe
    python run.py --no-open       # build dashboard but don't auto-open in browser
    python run.py --watchlist watchlist.txt   # include user watchlist tickers
    python run.py --fresh         # ignore cache, force fresh download
    python run.py --no-sectors    # skip the sector-ETF panel
    python run.py --notify        # process Telegram bot + send portfolio alerts
    python run.py --digest        # force-send the morning digest now
    python run.py --bot-only      # poll Telegram for commands only (no screener)
"""
from __future__ import annotations

import argparse
import datetime as dt
import webbrowser
from pathlib import Path

import pandas as pd

from screener.universe import load_universe
from screener.fetch import (
    fetch_prices,
    fetch_benchmark,
    filter_by_liquidity,
    SECTOR_ETFS,
)
from screener.engine import run_screener, TV_TRIGGER_KEYS
from screener.dashboard import render
from screener.triggers import generate_pine_watchlist_alert
from screener import alerts as alerts_mod
from screener.bot import process_updates
from screener.notifier import TelegramNotifier
from screener.portfolio import load_state, save_state


HERE = Path(__file__).resolve().parent
CACHE_DIR = HERE / ".cache"
OUT_PATH = HERE / "dashboard.html"
PINE_PATH = HERE / "dashboard_pine_alerts.pine"
INTRADAY_JSON = HERE / "intraday.json"


def main() -> None:
    ap = argparse.ArgumentParser(description="Real-time trend scanner")
    ap.add_argument("--top", type=int, default=500,
                    help="Top N tickers by 30-day dollar volume (default 500)")
    ap.add_argument("--watchlist", default=None,
                    help="Path to watchlist.txt (one ticker per line)")
    ap.add_argument("--no-open", action="store_true",
                    help="Don't auto-open the dashboard in a browser")
    ap.add_argument("--fresh", action="store_true",
                    help="Ignore cache and re-download all prices")
    ap.add_argument("--out", default=str(OUT_PATH),
                    help="Output HTML path (default ./dashboard.html)")
    ap.add_argument("--no-sectors", action="store_true",
                    help="Skip the sector-ETF screener panel")
    ap.add_argument("--export-pine", action="store_true",
                    help="Write a Pine Script v6 alert file for the top 25 "
                         "1 ACTION BUY tickers to dashboard_pine_alerts.pine")
    ap.add_argument("--notify", action="store_true",
                    help="Process Telegram bot commands and send portfolio alerts. "
                         "Requires TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, "
                         "and PORTFOLIO_ENCRYPTION_KEY env vars.")
    ap.add_argument("--digest", action="store_true",
                    help="Force-send the morning digest (resets last_digest_date "
                         "for this run).")
    ap.add_argument("--bot-only", action="store_true",
                    help="Skip the screener entirely. Just poll Telegram for "
                         "commands, reply, persist state. ~5s runtime — meant "
                         "for the 1-minute bot workflow.")
    ap.add_argument("--intraday", action="store_true",
                    help="Run one intraday fast-lane cycle: fetch 5m bars for "
                         "the focused watchlist, compute RVOL/gap/ROC/VWAP/ORB, "
                         "fire Telegram alerts, write intraday.json. No daily "
                         "screener. Meant for the 1-2 min intraday workflow.")
    args = ap.parse_args()

    if args.bot_only:
        _run_bot_only()
        return

    if args.intraday:
        run_intraday_cycle()
        return

    cache_minutes = 0 if args.fresh else 15

    print("Loading universe…")
    tickers = load_universe(watchlist_file=args.watchlist)
    print(f"  {len(tickers)} tickers in starting universe")

    print("Fetching SPY benchmark…")
    spy = fetch_benchmark("SPY", cache_dir=CACHE_DIR, cache_minutes=cache_minutes)
    if spy.empty:
        print("[skip] SPY data unavailable after retries (Yahoo likely rate-limiting). "
              "Keeping the last good dashboard live; exiting cleanly.")
        return

    print("Fetching ticker history…")
    prices = fetch_prices(tickers, cache_dir=CACHE_DIR, cache_minutes=cache_minutes)
    print(f"  Got history for {len(prices)} / {len(tickers)} tickers")
    if not prices:
        print("[skip] No ticker history fetched (data unavailable). "
              "Keeping the last good dashboard live; exiting cleanly.")
        return

    if args.top and args.top < len(prices):
        prices = filter_by_liquidity(prices, top_n=args.top)
        print(f"  Filtered to top {len(prices)} by 30D dollar volume")

    print("Running screener…")
    regime, results = run_screener(prices, spy)
    if results is None or results.empty:
        print("[skip] Screener produced no scored tickers (data unavailable). "
              "Keeping the last good dashboard live; exiting cleanly.")
        return
    print(f"  Market regime: {regime['regime']}  |  Suggested exposure: {regime['exposure']}")
    print(f"  {len(results)} tickers scored")
    print(f"  Top action picks: {(results['action'] == '1 ACTION BUY').sum()} ACTION BUY"
          f", {(results['action'] == '2 STARTER / SCALE-IN').sum()} STARTERS"
          f", {(results['action'] == '3 BUYABLE WATCH').sum()} BUYABLE WATCH")

    # TradingView trigger summary
    if not results.empty:
        parts = []
        for k in TV_TRIGGER_KEYS:
            n = int(results[k].sum()) if k in results.columns else 0
            parts.append(f"{k}: {n}")
        print("[triggers] " + " | ".join(parts))

    if args.export_pine:
        top_buys = results[results["action"] == "1 ACTION BUY"]["ticker"].head(25).tolist()
        if not top_buys:
            print("[pine] No 1 ACTION BUY tickers — skipping Pine export.")
        else:
            pine_src = generate_pine_watchlist_alert(top_buys)
            PINE_PATH.write_text(pine_src, encoding="utf-8")
            print(f"[pine] Wrote {PINE_PATH.name} for {len(top_buys)} ticker(s)")

    # Sector ETF screener (port of Excel "Sector Rankings" tab).
    # Optional panel — never let it fail the build. If the ETF fetch is sparse,
    # run_screener can return an empty frame with no 'ticker' column, so guard
    # every access and fall back to no panel on any error.
    sector_etfs = None
    if not args.no_sectors:
        try:
            print("Fetching sector ETFs…")
            etf_prices = fetch_prices(list(SECTOR_ETFS),
                                      cache_dir=CACHE_DIR, cache_minutes=cache_minutes,
                                      cache_name="sector_etfs")
            if etf_prices:
                _, sector_etfs = run_screener(etf_prices, spy)
                if sector_etfs is None or sector_etfs.empty or "ticker" not in sector_etfs.columns:
                    sector_etfs = None
                else:
                    sector_etfs = sector_etfs[sector_etfs["ticker"].isin(SECTOR_ETFS)].copy()
                    sector_etfs["sector_name"] = sector_etfs["ticker"].map(SECTOR_ETFS)
                    sector_etfs = sector_etfs.reset_index(drop=True)
                    if not sector_etfs.empty:
                        leader = sector_etfs.iloc[0]
                        print(f"  Sector leader: {leader['ticker']} ({leader['sector_name']})"
                              f"  3M={leader['ret_3m']*100:+.1f}%")
                    else:
                        sector_etfs = None
        except Exception as exc:  # noqa: BLE001
            print(f"[sectors] skipped (non-fatal): {exc}")
            sector_etfs = None

    # Intraday Movers panel — compute the fast-lane table for the focused
    # watchlist so the daily dashboard shows live RVOL/gap/ORB status. Alerts
    # are NOT fired here (that's intraday.yml's job) — this is render-only.
    # Fully isolated: a fetch hiccup must never block the daily build.
    movers = _compute_movers_for_dashboard(results)

    print(f"Rendering dashboard → {args.out}")
    out = render(regime, results, args.out,
                 generated_at=dt.datetime.now(), sector_etfs=sector_etfs,
                 movers=movers)

    csv_out = Path(args.out).with_suffix(".csv")
    results.to_csv(csv_out, index=False)
    print(f"Raw results → {csv_out}")

    # Notifications run LAST and are fully isolated: the dashboard is already
    # rendered + written above, so a bug in the bot/alert layer can never block
    # the build or the Pages deploy. Belt-and-suspenders on top of the internal
    # try/excepts in _run_notify_pipeline.
    if args.notify or args.digest:
        try:
            _run_notify_pipeline(regime, results, force_digest=args.digest)
        except Exception as exc:  # noqa: BLE001
            print(f"[notify] pipeline crashed (non-fatal, dashboard already built): {exc}")

    if not args.no_open:
        webbrowser.open(out.as_uri())


def _prices_from_last_csv() -> dict[str, float]:
    """Read live prices from the most recent dashboard.csv (written by the
    15-min screener run). Used by --bot-only so /list still shows P/L
    without re-running the screener."""
    candidates = [
        Path(__file__).resolve().parent / "dashboard.csv",
        Path(__file__).resolve().parent / "_site" / "dashboard.csv",
    ]
    for path in candidates:
        if not path.exists():
            continue
        try:
            import csv
            out: dict[str, float] = {}
            with path.open() as fh:
                reader = csv.DictReader(fh)
                for row in reader:
                    t = row.get("ticker")
                    p = row.get("live_price")
                    if t and p:
                        try:
                            out[t] = float(p)
                        except ValueError:
                            continue
            return out
        except Exception as exc:  # noqa: BLE001
            print(f"[bot-only] Could not read prices from {path}: {exc}")
            continue
    return {}


def _run_bot_only() -> None:
    """Lightweight Telegram poll — no screener, no dashboard.

    Loads encrypted state, processes any pending /add /remove /list /help
    commands, then persists state. Designed to run from a 1-minute cron so
    bot replies arrive in ~60-90s instead of every 15 min.

    For /list price lookups, reuses the last dashboard.csv written by the
    15-min screener (so P/L numbers reflect last screener tick, not live).
    """
    try:
        state = load_state()
    except Exception as exc:  # noqa: BLE001
        print(f"[bot-only] Could not load state ({exc}). Aborting.")
        return

    notifier = TelegramNotifier()
    if not notifier.configured:
        print("[bot-only] TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set — exit.")
        return

    prices = _prices_from_last_csv()
    if prices:
        print(f"[bot-only] Loaded {len(prices)} prices from last screener CSV")
    else:
        print("[bot-only] No dashboard.csv found yet — /list will show cost basis only")

    try:
        n = process_updates(notifier, state, prices=prices)
    except Exception as exc:  # noqa: BLE001
        print(f"[bot-only] Command poll failed: {exc}")
        # Still try to save state to capture any partial update_id progress
        n = 0

    if n:
        print(f"[bot-only] Dispatched {n} bot command(s)")
    else:
        print("[bot-only] No pending commands")

    try:
        save_state(state)
        print("[bot-only] state.enc updated")
    except Exception as exc:  # noqa: BLE001
        print(f"[bot-only] Could not save state: {exc}")


def _compute_movers_for_dashboard(results):
    """Compute the intraday Movers table for the daily dashboard panel.

    Render-only — does NOT fire alerts (intraday.yml owns alerting). Best-effort
    watchlist: portfolio holdings (if state loads) + top-30 by final_rank from
    the just-computed daily results. Any failure returns None so the daily build
    is never blocked.
    """
    try:
        from screener.fetch import fetch_intraday
        from screener.intraday import build_watchlist, compute_intraday_table

        portfolio_tickers = []
        try:
            portfolio_tickers = list(load_state().portfolio.keys())
        except Exception:  # noqa: BLE001
            pass  # no key / no state — top-ranked names still populate the panel

        ranked, prior_closes = [], {}
        if results is not None and not results.empty:
            for _, r in results.iterrows():
                t = r.get("ticker")
                if t:
                    ranked.append((t, r.get("final_rank")))
                    if r.get("prev_close") is not None:
                        prior_closes[t] = float(r["prev_close"])

        watchlist = build_watchlist(portfolio_tickers, ranked=ranked, top_n=30)
        if not watchlist:
            return None
        data = fetch_intraday(watchlist)
        if not data:
            print("[movers] no intraday data (market closed / rate-limited)")
            return None
        table = compute_intraday_table(data, prior_closes)
        n = int((table["intraday_trigger_count"] > 0).sum()) if not table.empty else 0
        print(f"[movers] {len(table)} watched, {n} with active intraday triggers")
        return table
    except Exception as exc:  # noqa: BLE001
        print(f"[movers] skipped (non-fatal): {exc}")
        return None


def _ranked_and_closes_from_csv() -> tuple[list[tuple[str, float]], dict[str, float]]:
    """Read (ticker, final_rank) and prior daily closes from the last
    dashboard.csv written by the daily screener. Used to seed the intraday
    watchlist + gap reference without re-running the 500-ticker screener."""
    import csv
    candidates = [HERE / "dashboard.csv", HERE / "_site" / "dashboard.csv"]
    for path in candidates:
        if not path.exists():
            continue
        try:
            ranked: list[tuple[str, float]] = []
            closes: dict[str, float] = {}
            with path.open() as fh:
                for row in csv.DictReader(fh):
                    t = (row.get("ticker") or "").strip()
                    if not t:
                        continue
                    try:
                        ranked.append((t, float(row["final_rank"])))
                    except (KeyError, ValueError, TypeError):
                        pass
                    try:
                        closes[t] = float(row["prev_close"])
                    except (KeyError, ValueError, TypeError):
                        pass
            return ranked, closes
        except Exception as exc:  # noqa: BLE001
            print(f"[intraday] could not read {path}: {exc}")
    return [], {}


def run_intraday_cycle() -> None:
    """One intraday fast-lane cycle. Reusable from a GitHub Action today and a
    persistent Fly.io loop later — it's a single self-contained call.

    Loads state → builds the focused watchlist → fetches 5m bars → computes
    signals → fires deduped Telegram alerts → writes intraday.json → persists
    state ONLY if the dedup record changed (so calm cycles produce no commit).
    Every failure is caught: the cycle must never crash the workflow.
    """
    import json
    from screener.fetch import fetch_intraday
    from screener.intraday import build_watchlist, compute_intraday_table

    try:
        state = load_state()
    except Exception as exc:  # noqa: BLE001
        print(f"[intraday] could not load state ({exc}); aborting cycle.")
        return

    before = json.dumps(state.intraday_snapshot, sort_keys=True)

    # Prefer the snapshot the daily run persisted into state.enc; fall back to
    # a local dashboard.csv (works on dev machines where the daily run wrote
    # one). The state path is what makes the GitHub Actions job work.
    ranked, prior_closes = [], {}
    today_iso = dt.date.today().isoformat()
    if state.daily_top_ranked and state.daily_snapshot_date == today_iso:
        ranked = [(t, r) for t, r in state.daily_top_ranked]
        prior_closes = dict(state.daily_prior_closes)
        print(f"[intraday] seed: state snapshot ({len(ranked)} ranked, dated {state.daily_snapshot_date})")
    else:
        ranked, prior_closes = _ranked_and_closes_from_csv()
        if ranked:
            print(f"[intraday] seed: dashboard.csv fallback ({len(ranked)} ranked)")

    watchlist = build_watchlist(
        portfolio_tickers=list(state.portfolio.keys()),
        ranked=ranked,
        top_n=30,
    )
    if not watchlist:
        print("[intraday] empty watchlist (no portfolio + no daily snapshot in state.enc) — "
              "wait for next daily refresh, then this will populate.")
        return
    print(f"[intraday] watching {len(watchlist)} tickers")

    try:
        data = fetch_intraday(watchlist)
    except Exception as exc:  # noqa: BLE001
        print(f"[intraday] fetch failed ({exc}); keeping last state.")
        return
    if not data:
        print("[intraday] no intraday data returned (market closed / rate-limited).")
        return

    table = compute_intraday_table(data, prior_closes)
    n_firing = int((table["intraday_trigger_count"] > 0).sum()) if not table.empty else 0
    print(f"[intraday] {len(table)} scored, {n_firing} with active triggers")

    # Telegram alerts (deduped). Isolated — never crashes the cycle.
    notifier = TelegramNotifier()
    try:
        messages = alerts_mod.intraday_alerts(state, table)
    except Exception as exc:  # noqa: BLE001
        print(f"[intraday] alert generation failed: {exc}")
        messages = []
    if notifier.configured:
        for m in messages:
            try:
                notifier.send_text(m)
            except Exception as exc:  # noqa: BLE001
                print(f"[intraday] send failed: {exc}")
        if messages:
            print(f"[intraday] sent {len(messages)} alert(s)")
    elif messages:
        print(f"[intraday] {len(messages)} alert(s) suppressed — Telegram not configured")

    # Write intraday.json for the dashboard Movers panel (PR D consumes it).
    try:
        _write_intraday_json(table)
    except Exception as exc:  # noqa: BLE001
        print(f"[intraday] could not write intraday.json: {exc}")

    # Persist state ONLY when the dedup record changed — keeps calm 1-2 min
    # cycles from spamming commits onto main.
    after = json.dumps(state.intraday_snapshot, sort_keys=True)
    if after != before:
        try:
            save_state(state)
            print("[intraday] state.enc updated (new alerts recorded)")
        except Exception as exc:  # noqa: BLE001
            print(f"[intraday] could not save state: {exc}")
    else:
        print("[intraday] no new alerts — state unchanged")


def _write_intraday_json(table) -> None:
    """Serialize the movers table to intraday.json (sorted by RVOL)."""
    import json
    cols = ["ticker", "last_price", "rvol", "gap_pct", "roc_15m",
            "price_vs_vwap", "orb_state", "session",
            "intraday_trigger_label", "intraday_trigger_count"]
    movers = []
    if table is not None and not table.empty:
        for _, r in table.iterrows():
            rec = {}
            for c in cols:
                v = r.get(c)
                if v is None or (isinstance(v, float) and v != v):  # NaN
                    rec[c] = None
                elif hasattr(v, "item"):
                    rec[c] = v.item()
                else:
                    rec[c] = v
            movers.append(rec)
    payload = {
        "generated_at": dt.datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "movers": movers,
    }
    INTRADAY_JSON.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _run_notify_pipeline(regime: dict, results, *, force_digest: bool) -> None:
    """Process Telegram commands and send portfolio alerts.

    Failures are caught and printed — they never break the screener run.
    """
    try:
        state = load_state()
    except Exception as exc:  # noqa: BLE001
        print(f"[notify] Could not load state ({exc}). Skipping notifications.")
        return

    notifier = TelegramNotifier()
    if not notifier.configured:
        print("[notify] TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set — "
              "skipping. State will still be persisted.")

    # Build a live-price lookup once for /list replies and alert bodies
    prices = {}
    if results is not None and not results.empty:
        for _, r in results.iterrows():
            if r.get("live_price") is not None:
                prices[r["ticker"]] = float(r["live_price"])

    # 1) Pull and dispatch any pending /add /remove /list /help commands
    if notifier.configured:
        try:
            n = process_updates(notifier, state, prices=prices)
            if n:
                print(f"[notify] Dispatched {n} bot command(s)")
        except Exception as exc:  # noqa: BLE001
            print(f"[notify] Command poll failed: {exc}")

    # 2) Generate alerts. Each helper mutates state where appropriate.
    prev_triggers = {t: set(v) for t, v in state.trigger_snapshot.items()}
    prev_top20 = list(state.top20_snapshot)

    messages: list[str] = []
    if force_digest:
        state.last_digest_date = ""  # force re-send
    messages.extend(alerts_mod.morning_digest(regime, state, results))
    messages.extend(alerts_mod.action_transition_alerts(state, results))
    messages.extend(alerts_mod.tv_trigger_alerts(state, results, prev_triggers))
    messages.extend(alerts_mod.new_top20_buys(results, prev_top20))

    # 3) Send (if configured)
    if notifier.configured:
        for m in messages:
            try:
                notifier.send_text(m)
            except Exception as exc:  # noqa: BLE001
                print(f"[notify] send failed: {exc}")
    elif messages:
        print(f"[notify] Would have sent {len(messages)} alert(s) — Telegram not configured")

    # 4) Snapshot triggers + top-20 so next run can diff
    state.trigger_snapshot = {
        t: sorted(list(s)) for t, s in alerts_mod.snapshot_triggers(results).items()
    }
    state.top20_snapshot = alerts_mod.current_top20_buys(results)

    # 4b) Snapshot top-N ranked + their prior closes so the intraday workflow
    # (which checks out a fresh repo without dashboard.csv) can seed its
    # watchlist from state.enc.
    if results is not None and not results.empty:
        top = results.nsmallest(50, "final_rank")
        state.daily_top_ranked = [
            [r["ticker"], float(r["final_rank"])]
            for _, r in top.iterrows()
            if r.get("ticker") and pd.notna(r.get("final_rank"))
        ]
        state.daily_prior_closes = {
            r["ticker"]: float(r["prev_close"])
            for _, r in top.iterrows()
            if r.get("ticker") and pd.notna(r.get("prev_close"))
        }
        state.daily_snapshot_date = dt.date.today().isoformat()

    # 5) Persist
    try:
        save_state(state)
        print("[notify] state.enc updated")
    except Exception as exc:  # noqa: BLE001
        print(f"[notify] Could not save state: {exc}")


if __name__ == "__main__":
    main()
