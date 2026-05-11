"""
Trading Tool — main entry point.

Usage:
    python run.py                 # default: top 500 by liquidity, opens dashboard
    python run.py --top 200       # smaller universe
    python run.py --no-open       # build dashboard but don't auto-open in browser
    python run.py --watchlist watchlist.txt   # include user watchlist tickers
    python run.py --fresh         # ignore cache, force fresh download
"""
from __future__ import annotations

import argparse
import datetime as dt
import webbrowser
from pathlib import Path

from screener.universe import load_universe
from screener.fetch import fetch_prices, fetch_benchmark, filter_by_liquidity
from screener.engine import run_screener
from screener.dashboard import render


HERE = Path(__file__).resolve().parent
CACHE_DIR = HERE / ".cache"
OUT_PATH = HERE / "dashboard.html"


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
    args = ap.parse_args()

    cache_minutes = 0 if args.fresh else 15

    print("Loading universe…")
    tickers = load_universe(watchlist_file=args.watchlist)
    print(f"  {len(tickers)} tickers in starting universe")

    print("Fetching SPY benchmark…")
    spy = fetch_benchmark("SPY", cache_dir=CACHE_DIR, cache_minutes=cache_minutes)
    if spy.empty:
        raise SystemExit("Failed to fetch SPY — check your internet connection.")

    print("Fetching ticker history…")
    prices = fetch_prices(tickers, cache_dir=CACHE_DIR, cache_minutes=cache_minutes)
    print(f"  Got history for {len(prices)} / {len(tickers)} tickers")

    if args.top and args.top < len(prices):
        prices = filter_by_liquidity(prices, top_n=args.top)
        print(f"  Filtered to top {len(prices)} by 30D dollar volume")

    print("Running screener…")
    regime, results = run_screener(prices, spy)
    print(f"  Market regime: {regime['regime']}  |  Suggested exposure: {regime['exposure']}")
    print(f"  {len(results)} tickers scored")
    print(f"  Top action picks: {(results['action'] == '1 ACTION BUY').sum()} ACTION BUY"
          f", {(results['action'] == '2 STARTER / SCALE-IN').sum()} STARTERS"
          f", {(results['action'] == '3 BUYABLE WATCH').sum()} BUYABLE WATCH")

    print(f"Rendering dashboard → {args.out}")
    out = render(regime, results, args.out, generated_at=dt.datetime.now())

    # Also save the raw results to CSV so you can do your own analysis
    csv_out = Path(args.out).with_suffix(".csv")
    results.to_csv(csv_out, index=False)
    print(f"Raw results → {csv_out}")

    if not args.no_open:
        webbrowser.open(out.as_uri())


if __name__ == "__main__":
    main()
