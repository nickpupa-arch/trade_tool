"""
Intraday signal engine — the "fast lane" that catches moves while they develop.

The daily screener (engine.py) ranks the universe on daily bars; by design it
can't see intraday momentum until the day's bar completes. This module computes
the leading indicators professional momentum scanners use, on 5-minute bars, so
moves are flagged during their first leg instead of after the close.

Pure compute — no Telegram, no file I/O beyond the data fetch. The single entry
point compute_intraday_table() takes intraday OHLCV + prior daily closes and
returns a per-ticker DataFrame of signals + trigger booleans. That keeps it
reusable from a GitHub Action today and a persistent Fly.io loop later.

Signals (all derived from 5m bars already downloadable via fetch.py's yfinance):
  rvol           time-of-day relative volume  (cum vol now / avg cum vol by now)
  gap_pct        today's open (or last premarket) vs prior daily close
  roc_15m        % change over the trailing ~15 min  (momentum burst)
  vwap           session VWAP; price_vs_vwap = (last - vwap)/vwap
  or_high/low    opening-range high/low (first N min of regular session)
  orb_state      "long" / "short" / "" breakout of the opening range + VWAP

Trigger booleans (mirrors engine.py's TV trigger layer, intraday flavor):
  vol_spike          rvol >= RVOL_TRIGGER  (default 2.0)
  gap_up             gap_pct >= GAP_TRIGGER (default 0.04)
  momentum_burst     roc_15m >= ROC_TRIGGER and rvol >= 1.5
  orb_break_long     opening-range high broken while above VWAP, rvol >= 1.5
"""
from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd


EASTERN = ZoneInfo("America/New_York")
REG_OPEN = dt.time(9, 30)
REG_CLOSE = dt.time(16, 0)

# Default thresholds — tune after watching a few live sessions.
RVOL_TRIGGER = 2.0
GAP_TRIGGER = 0.04          # 4%
ROC_TRIGGER = 0.03          # 3% over the ROC window
ORB_RVOL_MIN = 1.5
MIN_REGULAR_MINUTES = 10    # don't compute RVOL on the first ~2 bars (noisy)
OR_MINUTES = 30             # opening-range definition window
ROC_MINUTES = 15

INTRADAY_TRIGGER_KEYS = (
    "vol_spike",
    "gap_up",
    "momentum_burst",
    "orb_break_long",
)


# ---------------------------------------------------------------------------
# Time helpers
# ---------------------------------------------------------------------------
def _to_eastern(df: pd.DataFrame) -> pd.DataFrame:
    """Return a copy with a tz-aware DatetimeIndex in US/Eastern."""
    idx = df.index
    if not isinstance(idx, pd.DatetimeIndex):
        idx = pd.to_datetime(idx)
    if idx.tz is None:
        idx = idx.tz_localize("UTC")
    out = df.copy()
    out.index = idx.tz_convert(EASTERN)
    return out


def _minutes_since_open(ts: pd.Timestamp) -> float:
    """Minutes elapsed since 09:30 ET for a regular-session timestamp."""
    return (ts.hour - 9) * 60 + (ts.minute - 30) + ts.second / 60.0


def _session_mask(idx: pd.DatetimeIndex, kind: str) -> np.ndarray:
    """Boolean mask selecting 'regular' (09:30-16:00) or 'premarket' bars."""
    t = idx.time
    if kind == "regular":
        return np.array([REG_OPEN <= x < REG_CLOSE for x in t])
    if kind == "premarket":
        return np.array([dt.time(4, 0) <= x < REG_OPEN for x in t])
    return np.ones(len(idx), dtype=bool)


# ---------------------------------------------------------------------------
# RVOL — time-of-day adjusted relative volume
# ---------------------------------------------------------------------------
def _time_of_day_rvol(df_et: pd.DataFrame, today: dt.date) -> float | None:
    """Cumulative volume so far today vs. the average cumulative volume by the
    same minutes-since-open across prior sessions.

    Comparing 10am volume to a *full-day* average is noise; comparing it to
    "typical cumulative volume by 10am" is what flags early acceleration.
    Returns None if there isn't enough regular-session data yet.
    """
    reg = df_et[_session_mask(df_et.index, "regular")]
    if reg.empty or "Volume" not in reg.columns:
        return None

    reg = reg.assign(
        _date=[ix.date() for ix in reg.index],
        _mso=[_minutes_since_open(ix) for ix in reg.index],
    )
    today_rows = reg[reg["_date"] == today]
    if today_rows.empty:
        return None
    elapsed = float(today_rows["_mso"].max())
    if elapsed < MIN_REGULAR_MINUTES:
        return None

    today_cum = float(today_rows["Volume"].fillna(0).sum())
    if today_cum <= 0:
        return None

    # For each prior session, cumulative volume up to `elapsed` minutes.
    prior = reg[reg["_date"] < today]
    if prior.empty:
        return None
    cum_by_day = (
        prior[prior["_mso"] <= elapsed]
        .groupby("_date")["Volume"]
        .apply(lambda s: float(s.fillna(0).sum()))
    )
    cum_by_day = cum_by_day[cum_by_day > 0]
    if cum_by_day.empty:
        return None
    avg_cum = float(cum_by_day.mean())
    if avg_cum <= 0:
        return None
    return today_cum / avg_cum


# ---------------------------------------------------------------------------
# VWAP / opening range / ROC
# ---------------------------------------------------------------------------
def _session_vwap(today_reg: pd.DataFrame) -> float | None:
    if today_reg.empty:
        return None
    tp = (today_reg["High"] + today_reg["Low"] + today_reg["Close"]) / 3.0
    vol = today_reg["Volume"].fillna(0)
    denom = float(vol.sum())
    if denom <= 0:
        return None
    return float((tp * vol).sum() / denom)


def _opening_range(today_reg: pd.DataFrame) -> tuple[float | None, float | None]:
    if today_reg.empty:
        return None, None
    mso = np.array([_minutes_since_open(ix) for ix in today_reg.index])
    orb = today_reg[mso < OR_MINUTES]
    if orb.empty:
        return None, None
    return float(orb["High"].max()), float(orb["Low"].min())


def _roc(df_et: pd.DataFrame, minutes: int) -> float | None:
    """% change over the trailing `minutes` using the last bars (incl premarket)."""
    closes = df_et["Close"].dropna()
    if len(closes) < 2:
        return None
    last_ts = closes.index[-1]
    window_start = last_ts - pd.Timedelta(minutes=minutes)
    window = closes[closes.index >= window_start]
    if len(window) < 2:
        return None
    first = float(window.iloc[0])
    last = float(window.iloc[-1])
    if first <= 0:
        return None
    return (last - first) / first


# ---------------------------------------------------------------------------
# Per-ticker signal computation
# ---------------------------------------------------------------------------
def compute_ticker_signals(df: pd.DataFrame,
                           prior_close: float | None,
                           today: dt.date | None = None) -> dict:
    """Compute the intraday signal dict for one ticker.

    df: intraday OHLCV (5m), ideally several sessions deep, tz-aware or naive.
    prior_close: prior *daily* close, for gap computation.
    """
    sig = {
        "rvol": None, "gap_pct": None, "roc_15m": None,
        "vwap": None, "price_vs_vwap": None,
        "or_high": None, "or_low": None, "orb_state": "",
        "last_price": None, "session": "closed",
    }
    if df is None or df.empty or "Close" not in df.columns:
        return _with_triggers(sig)

    try:
        df_et = _to_eastern(df)
    except Exception:
        return _with_triggers(sig)

    if today is None:
        today = df_et.index[-1].date()

    last_price = float(df_et["Close"].dropna().iloc[-1]) if not df_et["Close"].dropna().empty else None
    sig["last_price"] = last_price

    reg_mask = _session_mask(df_et.index, "regular")
    today_mask = np.array([ix.date() == today for ix in df_et.index])
    today_reg = df_et[reg_mask & today_mask]
    last_ts = df_et.index[-1]
    if REG_OPEN <= last_ts.time() < REG_CLOSE:
        sig["session"] = "regular"
    elif dt.time(4, 0) <= last_ts.time() < REG_OPEN:
        sig["session"] = "premarket"

    # Gap: regular open vs prior close, or last premarket vs prior close.
    if prior_close and prior_close > 0:
        if not today_reg.empty:
            ref = float(today_reg["Open"].dropna().iloc[0]) if not today_reg["Open"].dropna().empty else last_price
        else:
            ref = last_price  # premarket
        if ref:
            sig["gap_pct"] = (ref - prior_close) / prior_close

    sig["rvol"] = _time_of_day_rvol(df_et, today)
    sig["roc_15m"] = _roc(df_et, ROC_MINUTES)

    vwap = _session_vwap(today_reg)
    sig["vwap"] = vwap
    if vwap and last_price:
        sig["price_vs_vwap"] = (last_price - vwap) / vwap

    or_high, or_low = _opening_range(today_reg)
    sig["or_high"], sig["or_low"] = or_high, or_low
    if last_price and or_high and last_price > or_high:
        sig["orb_state"] = "long"
    elif last_price and or_low and last_price < or_low:
        sig["orb_state"] = "short"

    return _with_triggers(sig)


def _with_triggers(sig: dict) -> dict:
    """Derive the boolean trigger flags from a raw signal dict."""
    rvol = sig.get("rvol")
    gap = sig.get("gap_pct")
    roc = sig.get("roc_15m")
    pvv = sig.get("price_vs_vwap")

    sig["vol_spike"] = bool(rvol is not None and rvol >= RVOL_TRIGGER)
    sig["gap_up"] = bool(gap is not None and gap >= GAP_TRIGGER)
    sig["momentum_burst"] = bool(
        roc is not None and roc >= ROC_TRIGGER
        and rvol is not None and rvol >= 1.5
    )
    sig["orb_break_long"] = bool(
        sig.get("orb_state") == "long"
        and pvv is not None and pvv >= 0
        and rvol is not None and rvol >= ORB_RVOL_MIN
    )
    sig["intraday_trigger_count"] = int(sum(bool(sig[k]) for k in INTRADAY_TRIGGER_KEYS))
    sig["intraday_trigger_label"] = ", ".join(
        k for k in INTRADAY_TRIGGER_KEYS if sig[k]
    )
    return sig


# ---------------------------------------------------------------------------
# Table builder
# ---------------------------------------------------------------------------
def compute_intraday_table(intraday_data: dict[str, pd.DataFrame],
                           prior_closes: dict[str, float],
                           today: dt.date | None = None) -> pd.DataFrame:
    """Compute the per-ticker intraday signal table.

    intraday_data: {ticker: 5m OHLCV DataFrame}
    prior_closes:  {ticker: prior daily close}
    Returns a DataFrame with one row per ticker, sorted by rvol desc.
    """
    rows = []
    for ticker, df in intraday_data.items():
        try:
            sig = compute_ticker_signals(df, prior_closes.get(ticker), today=today)
        except Exception as exc:  # noqa: BLE001
            print(f"[intraday] {ticker}: {exc}")
            sig = _with_triggers({"session": "closed"})
        sig["ticker"] = ticker
        rows.append(sig)

    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    # Stable, useful default ordering: highest RVOL first.
    df["_rvol_sort"] = df["rvol"].fillna(-1.0)
    df = df.sort_values("_rvol_sort", ascending=False).drop(columns="_rvol_sort")
    return df.reset_index(drop=True)
