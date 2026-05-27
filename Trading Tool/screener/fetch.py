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


def _download_with_retry(download_fn, *, label: str,
                         attempts: int = 4, base_delay: float = 2.0):
    """Call a yfinance download, retrying on empty/exception with backoff.

    Yahoo frequently rate-limits or transiently 403s automated clients
    (esp. CI runner IP blocks). A single empty response would otherwise
    cascade into a hard failure, so retry with 2s/4s/8s backoff before
    giving up. Returns an empty DataFrame if all attempts fail — callers
    decide how to handle that.
    """
    last_err = "empty result"
    for i in range(attempts):
        try:
            df = download_fn()
            if df is not None and not df.empty:
                return df
        except Exception as exc:  # noqa: BLE001
            last_err = str(exc)
        if i < attempts - 1:
            delay = base_delay * (2 ** i)
            print(f"  [retry] {label}: {last_err} — retrying in {delay:.0f}s "
                  f"(attempt {i + 1}/{attempts})")
            time.sleep(delay)
    print(f"  [retry] {label}: giving up after {attempts} attempts ({last_err})")
    return pd.DataFrame()


# The 11 GICS sector SPDR ETFs that Excel's "Sector Rankings" tab tracks.
# Same screener math is applied to these — rank the sectors against each other.
SECTOR_ETFS = {
    "XLK":  "Technology",
    "XLY":  "Consumer Discretionary",
    "XLC":  "Communication Services",
    "XLP":  "Consumer Staples",
    "XLE":  "Energy",
    "XLF":  "Financials",
    "XLI":  "Industrials",
    "XLV":  "Health Care",
    "XLB":  "Materials",
    "XLRE": "Real Estate",
    "XLU":  "Utilities",
}


def fetch_prices(tickers: list[str],
                 period: str = "14mo",
                 cache_dir: str | Path | None = None,
                 cache_minutes: int = 15,
                 chunk_size: int = 50,
                 cache_name: str = "prices") -> dict[str, pd.DataFrame]:
    """Download OHLCV history for many tickers.

    Returns {ticker: DataFrame} with columns Open/High/Low/Close/Volume.
    Caches the raw parquet to `cache_dir/{cache_name}.parquet` and reuses
    if younger than `cache_minutes` (set to 0 to disable cache). Use a
    distinct `cache_name` per ticker set — otherwise a fetch for one set
    will reuse stale data from a different set.
    """
    import yfinance as yf  # imported lazily so engine.py doesn't need it

    requested = set(tickers)
    cache_path = None
    if cache_dir:
        cache_path = Path(cache_dir) / f"{cache_name}.parquet"
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        if cache_path.exists() and cache_minutes > 0:
            age_min = (time.time() - cache_path.stat().st_mtime) / 60
            if age_min < cache_minutes:
                df = pd.read_parquet(cache_path)
                cached = _split_by_ticker(df)
                # Only use cache if it covers all the tickers we asked for.
                # Prevents a previous run's larger universe from masking a
                # smaller, different request (e.g. sector ETFs).
                if requested.issubset(cached.keys()):
                    return {t: cached[t] for t in cached if t in requested}

    # Download in chunks to be polite & robust
    frames = []
    for i in range(0, len(tickers), chunk_size):
        chunk = tickers[i:i + chunk_size]
        print(f"  Downloading {i+1}-{i+len(chunk)} of {len(tickers)}...")
        df = _download_with_retry(
            lambda chunk=chunk: yf.download(
                chunk,
                period=period,
                interval="1d",
                group_by="ticker",
                auto_adjust=True,
                threads=True,
                progress=False,
            ),
            label=f"chunk {i+1}-{i+len(chunk)}",
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
    full = _normalize_date_column(full)

    if cache_path:
        full.to_parquet(cache_path, index=False)

    return _split_by_ticker(full)


def _normalize_date_column(df: pd.DataFrame) -> pd.DataFrame:
    """Ensure the date column is named 'Date'.

    yfinance's index name has drifted across versions ('Date', 'Datetime',
    sometimes unnamed → 'index'/'level_0' after reset_index). Rename whatever
    holds the dates to 'Date'. Falls back to the first datetime-typed column so
    we're robust to future renames too.
    """
    if "Date" in df.columns:
        return df
    for cand in ("Datetime", "datetime", "date", "index", "level_0", "Unnamed: 0"):
        if cand in df.columns:
            return df.rename(columns={cand: "Date"})
    for c in df.columns:
        try:
            if pd.api.types.is_datetime64_any_dtype(df[c]):
                return df.rename(columns={c: "Date"})
        except (TypeError, ValueError):
            continue
    return df


def _split_by_ticker(df: pd.DataFrame) -> dict[str, pd.DataFrame]:
    df = _normalize_date_column(df)
    if "Date" not in df.columns or "Ticker" not in df.columns:
        print(f"[fetch] unexpected columns from yfinance: {list(df.columns)}")
        return {}
    out = {}
    for t, sub in df.groupby("Ticker"):
        sub = sub.sort_values("Date").set_index("Date")
        keep = [c for c in ("Open", "High", "Low", "Close", "Volume") if c in sub.columns]
        sub = sub[keep].dropna(how="all")
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

    df = _download_with_retry(
        lambda: yf.download(symbol, period=period, interval="1d",
                            auto_adjust=True, progress=False),
        label=f"benchmark {symbol}",
    )
    if df.empty:
        # Fall back to a stale cache if we have one — better than nothing.
        if cache_path is not None and cache_path.exists():
            print(f"  [retry] benchmark {symbol}: using stale cache as fallback")
            return pd.read_parquet(cache_path)
        return df
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
