"""
Price-history fetcher using yfinance.

We download ~14 months of daily history so we have enough data for the
200-day moving average and 12-month return. After downloading we filter
to the top N tickers by average 30-day dollar volume.
"""
from __future__ import annotations

import time
from pathlib import Path

import pandas as pd


def fetch_prices(tickers: list[str],
                 period: str = "14mo",
                 cache_dir: str | Path | None = None,
                 cache_minutes: int = 15,
                 chunk_size: int = 50) -> dict[str, pd.DataFrame]:
    """Download OHLCV history for many tickers.

    Returns {ticker: DataFrame} with columns Open/High/Low/Close/Volume.
    Caches the raw parquet to `cache_dir` and reuses if younger than
    `cache_minutes` (set to 0 to disable cache).
    """
    import yfinance as yf  # imported lazily so engine.py doesn't need it

    cache_path = None
    if cache_dir:
        cache_path = Path(cache_dir) / "prices.parquet"
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        if cache_path.exists() and cache_minutes > 0:
            age_min = (time.time() - cache_path.stat().st_mtime) / 60
            if age_min < cache_minutes:
                df = pd.read_parquet(cache_path)
                return _split_by_ticker(df)

    # Download in chunks to be polite & robust
    frames = []
    for i in range(0, len(tickers), chunk_size):
        chunk = tickers[i:i + chunk_size]
        print(f"  Downloading {i+1}-{i+len(chunk)} of {len(tickers)}...")
        df = yf.download(
            chunk,
            period=period,
            interval="1d",
            group_by="ticker",
            auto_adjust=True,
            threads=True,
            progress=False,
        )
        if df.empty:
            continue
        # multi-ticker → MultiIndex columns; single ticker → flat
        if isinstance(df.columns, pd.MultiIndex):
            df = df.stack(level=0, future_stack=True).reset_index()
            df = df.rename(columns={"level_1": "Ticker", "Ticker": "Ticker"})
        else:
            df = df.reset_index()
            df["Ticker"] = chunk[0]
        frames.append(df)

    if not frames:
        return {}

    full = pd.concat(frames, ignore_index=True)
    # standardize column names
    full.columns = [c if isinstance(c, str) else c[0] for c in full.columns]
    full = full.rename(columns={"Date": "Date"})

    if cache_path:
        full.to_parquet(cache_path, index=False)

    return _split_by_ticker(full)


def _split_by_ticker(df: pd.DataFrame) -> dict[str, pd.DataFrame]:
    out = {}
    for t, sub in df.groupby("Ticker"):
        sub = sub.sort_values("Date").set_index("Date")
        sub = sub[["Open", "High", "Low", "Close", "Volume"]].dropna(how="all")
        if not sub.empty:
            out[t] = sub
    return out


def fetch_benchmark(symbol: str = "SPY",
                    period: str = "14mo",
                    cache_dir: str | Path | None = None,
                    cache_minutes: int = 15) -> pd.DataFrame:
    """Fetch a single benchmark ticker (SPY by default)."""
    import yfinance as yf

    cache_path = None
    if cache_dir:
        cache_path = Path(cache_dir) / f"{symbol}.parquet"
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        if cache_path.exists() and cache_minutes > 0:
            age_min = (time.time() - cache_path.stat().st_mtime) / 60
            if age_min < cache_minutes:
                return pd.read_parquet(cache_path)

    df = yf.download(symbol, period=period, interval="1d",
                     auto_adjust=True, progress=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    if cache_path is not None:
        df.to_parquet(cache_path)
    return df


def filter_by_liquidity(price_data: dict[str, pd.DataFrame],
                        top_n: int = 500,
                        lookback_days: int = 30) -> dict[str, pd.DataFrame]:
    """Keep only the top-N tickers by average dollar volume over `lookback_days`."""
    if top_n <= 0 or top_n >= len(price_data):
        return price_data

    scores = []
    for t, df in price_data.items():
        tail = df.tail(lookback_days)
        if len(tail) < 5:
            continue
        avg_dv = (tail["Close"] * tail["Volume"]).mean()
        scores.append((t, avg_dv))

    scores.sort(key=lambda x: x[1], reverse=True)
    keep = {t for t, _ in scores[:top_n]}
    return {t: df for t, df in price_data.items() if t in keep}
