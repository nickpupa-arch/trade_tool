"""
Trading Tool — Screener Engine
==============================
Ports the logic of the original Google Sheets "Primary Trending Stock Screener"
to Python. All formulas (regime, MAs, returns, RS, ATR, Extension Ratio,
Leadership / Momentum / Blended / Final scores, Entry Grade, Signal, Action)
are reproduced 1:1.

The engine is data-source agnostic. Pass it a `price_data` dict:
    {ticker: pandas.DataFrame indexed by date with columns
     ['Open','High','Low','Close','Volume']}
and a benchmark DataFrame (SPY) with the same columns.

run_screener() returns a pandas.DataFrame with one row per ticker holding
every column the original sheet produced, plus a normalized Action column.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Market regime
# ---------------------------------------------------------------------------
def market_regime(spy: pd.DataFrame) -> dict:
    """Replicates Dashboard tab: regime + suggested exposure from SPY MAs."""
    close = spy["Close"].dropna()
    price = float(close.iloc[-1])
    ma20 = float(close.tail(20).mean())
    ma50 = float(close.tail(50).mean())
    ma200 = float(close.tail(200).mean())
    ret3m = float(price / close.iloc[-min(63, len(close))] - 1)

    if price > ma50 and price > ma200:
        regime = "Risk On"
        exposure = "80% - 100%"
        sizing = "5-8 names"
    elif price > ma50:
        regime = "Mixed"
        exposure = "40% - 70%"
        sizing = "3-5 names"
    else:
        regime = "Defensive"
        exposure = "0% - 30%"
        sizing = "0-2 names (most idle capital in BIL/SGOV)"

    return {
        "regime": regime,
        "spy_price": price,
        "spy_20d": ma20,
        "spy_50d": ma50,
        "spy_200d": ma200,
        "spy_3m_return": ret3m,
        "exposure": exposure,
        "sizing": sizing,
    }


# ---------------------------------------------------------------------------
# Per-ticker metrics
# ---------------------------------------------------------------------------
def _safe(series: pd.Series, periods: int) -> float | None:
    series = series.dropna()
    if len(series) < periods:
        return None
    return float(series.tail(periods).mean())


def _return_n_days_back(series: pd.Series, days: int) -> float | None:
    series = series.dropna()
    if len(series) < days + 1:
        return None
    return float(series.iloc[-1] / series.iloc[-days - 1] - 1)


def _atr14(df: pd.DataFrame) -> float | None:
    """Average True Range over the last 14 bars."""
    df = df.dropna(subset=["High", "Low", "Close"])
    if len(df) < 15:
        return None
    high = df["High"].values
    low = df["Low"].values
    close = df["Close"].values
    prev_close = np.roll(close, 1)
    tr = np.maximum.reduce([
        high - low,
        np.abs(high - prev_close),
        np.abs(low - prev_close),
    ])
    return float(np.mean(tr[-14:]))


# ---------------------------------------------------------------------------
# TradingView Trigger Layer
# ---------------------------------------------------------------------------
TV_TRIGGER_KEYS = (
    "squeeze_on",
    "squeeze_fire",
    "macd_bull_cross",
    "rsi_bull_divergence",
    "vol_surge",
)


def _ema(values: np.ndarray, period: int) -> np.ndarray:
    """Exponential moving average. Returns same length as input (NaN-padded start)."""
    alpha = 2.0 / (period + 1.0)
    out = np.full_like(values, np.nan, dtype=float)
    if len(values) < period:
        return out
    seed = np.mean(values[:period])
    out[period - 1] = seed
    for i in range(period, len(values)):
        out[i] = alpha * values[i] + (1.0 - alpha) * out[i - 1]
    return out


def _rsi(values: np.ndarray, period: int = 14) -> np.ndarray:
    """Wilder's RSI. Returns same length as input (NaN-padded start)."""
    out = np.full_like(values, np.nan, dtype=float)
    if len(values) < period + 1:
        return out
    diffs = np.diff(values)
    gains = np.where(diffs > 0, diffs, 0.0)
    losses = np.where(diffs < 0, -diffs, 0.0)
    avg_gain = np.mean(gains[:period])
    avg_loss = np.mean(losses[:period])
    if avg_loss == 0:
        out[period] = 100.0
    else:
        rs = avg_gain / avg_loss
        out[period] = 100.0 - (100.0 / (1.0 + rs))
    for i in range(period + 1, len(values)):
        avg_gain = (avg_gain * (period - 1) + gains[i - 1]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i - 1]) / period
        if avg_loss == 0:
            out[i] = 100.0
        else:
            rs = avg_gain / avg_loss
            out[i] = 100.0 - (100.0 / (1.0 + rs))
    return out


def _swing_lows(values: np.ndarray) -> list[int]:
    """Indices of local minima where v[i] < v[i-2] and v[i] < v[i+2]."""
    idx = []
    for i in range(2, len(values) - 2):
        if np.isnan(values[i]):
            continue
        if values[i] < values[i - 2] and values[i] < values[i + 2]:
            idx.append(i)
    return idx


def _compute_tv_triggers(df: pd.DataFrame,
                         close: pd.Series,
                         volume: pd.Series) -> dict:
    """Compute the 5 TradingView confirmation triggers.

    Returns a dict with keys TV_TRIGGER_KEYS, all bools, defaulting to False
    when data is insufficient or computation fails.
    """
    triggers = {k: False for k in TV_TRIGGER_KEYS}

    try:
        c = close.to_numpy(dtype=float)
        if len(c) < 30:
            return triggers

        # ---- TTM Squeeze: BB (20, 2σ) inside KC (20 EMA ± 1.5 ATR) ----
        bb_period = 20
        kc_period = 20
        bb_mult = 2.0
        kc_mult = 1.5

        sma20 = pd.Series(c).rolling(bb_period).mean().to_numpy()
        std20 = pd.Series(c).rolling(bb_period).std(ddof=0).to_numpy()
        bb_upper = sma20 + bb_mult * std20
        bb_lower = sma20 - bb_mult * std20

        ema20 = _ema(c, kc_period)
        high = df["High"].to_numpy(dtype=float) if "High" in df.columns else c
        low = df["Low"].to_numpy(dtype=float) if "Low" in df.columns else c
        # True range series
        prev_close = np.roll(c, 1)
        prev_close[0] = c[0]
        tr = np.maximum.reduce([
            high - low,
            np.abs(high - prev_close),
            np.abs(low - prev_close),
        ])
        atr_series = pd.Series(tr).rolling(kc_period).mean().to_numpy()
        kc_upper = ema20 + kc_mult * atr_series
        kc_lower = ema20 - kc_mult * atr_series

        squeeze_arr = (bb_upper < kc_upper) & (bb_lower > kc_lower)
        # Mask out positions where any input is NaN
        valid = ~(np.isnan(bb_upper) | np.isnan(kc_upper) |
                  np.isnan(bb_lower) | np.isnan(kc_lower))
        squeeze_arr = squeeze_arr & valid

        if len(squeeze_arr) >= 2:
            triggers["squeeze_on"] = bool(squeeze_arr[-1])

            # Momentum histogram: linear regression of (close - midpoint) over 20 bars
            mom_window = 20
            if len(c) >= mom_window:
                highest_high = pd.Series(high).rolling(mom_window).max().to_numpy()
                lowest_low = pd.Series(low).rolling(mom_window).min().to_numpy()
                midpoint = (highest_high + lowest_low) / 2.0
                ma_close = pd.Series(c).rolling(mom_window).mean().to_numpy()
                avg_mid = (midpoint + ma_close) / 2.0
                src = c - avg_mid
                # Linear regression slope-based "value" (Pine: linreg(src, 20, 0))
                x = np.arange(mom_window, dtype=float)
                x_mean = x.mean()
                denom = ((x - x_mean) ** 2).sum()
                last_src = src[-mom_window:]
                if not np.any(np.isnan(last_src)) and denom > 0:
                    y_mean = last_src.mean()
                    slope = ((x - x_mean) * (last_src - y_mean)).sum() / denom
                    intercept = y_mean - slope * x_mean
                    mom_val = intercept + slope * (mom_window - 1)
                    if squeeze_arr[-2] and not squeeze_arr[-1] and mom_val > 0:
                        triggers["squeeze_fire"] = True

        # ---- MACD bullish cross in last 3 bars + above 200D MA ----
        if len(c) >= 35:
            ema12 = _ema(c, 12)
            ema26 = _ema(c, 26)
            macd_line = ema12 - ema26
            # Signal line = 9-EMA of MACD (skip NaNs)
            macd_valid = macd_line[~np.isnan(macd_line)]
            if len(macd_valid) >= 9:
                sig_valid = _ema(macd_valid, 9)
                signal_line = np.full_like(macd_line, np.nan)
                start = len(macd_line) - len(sig_valid)
                signal_line[start:] = sig_valid
                hist = macd_line - signal_line

                if len(c) >= 200:
                    ma200 = float(pd.Series(c).tail(200).mean())
                    above_200 = c[-1] > ma200
                else:
                    above_200 = False

                if above_200 and len(hist) >= 4:
                    last3 = hist[-3:]
                    prior = hist[-4:-1]
                    crossed = False
                    for i in range(3):
                        if (not np.isnan(prior[i]) and not np.isnan(last3[i])
                                and prior[i] < 0 and last3[i] > 0):
                            crossed = True
                            break
                    triggers["macd_bull_cross"] = crossed

        # ---- RSI hidden bullish divergence over last 30 bars ----
        if len(c) >= 30:
            window = 30
            c_window = c[-window:]
            rsi = _rsi(c, 14)
            rsi_window = rsi[-window:]
            price_lows = _swing_lows(c_window)
            rsi_lows = _swing_lows(rsi_window)
            if len(price_lows) >= 2 and len(rsi_lows) >= 2:
                p_last, p_prev = price_lows[-1], price_lows[-2]
                r_last, r_prev = rsi_lows[-1], rsi_lows[-2]
                if (c_window[p_last] > c_window[p_prev]
                        and not np.isnan(rsi_window[r_last])
                        and not np.isnan(rsi_window[r_prev])
                        and rsi_window[r_last] < rsi_window[r_prev]):
                    triggers["rsi_bull_divergence"] = True

        # ---- Volume surge + breakout above prior day's high ----
        if volume is not None and not volume.dropna().empty and "High" in df.columns:
            v = volume.to_numpy(dtype=float)
            h = df["High"].to_numpy(dtype=float)
            if len(v) >= 21 and not np.all(np.isnan(v)):
                vol_avg20 = pd.Series(v).rolling(20).mean().to_numpy()
                if (not np.isnan(v[-1]) and not np.isnan(vol_avg20[-2])
                        and vol_avg20[-2] > 0
                        and v[-1] > 1.5 * vol_avg20[-2]
                        and not np.isnan(h[-2])
                        and c[-1] > h[-2]):
                    triggers["vol_surge"] = True
    except Exception:
        # Never break a screener run for a trigger-layer issue
        return {k: False for k in TV_TRIGGER_KEYS}

    return triggers


def per_ticker_metrics(ticker: str, df: pd.DataFrame, regime: dict) -> dict:
    """Compute every metric the Excel sheet computes for one ticker."""
    out: dict = {"ticker": ticker}
    close = df["Close"].dropna()
    if len(close) < 30:
        return None  # not enough history

    live = float(close.iloc[-1])
    prev = float(close.iloc[-2])
    out.update({
        "live_price": live,
        "prev_close": prev,
        "day_change_pct": (live - prev) / prev if prev else None,
    })

    out["ma9"] = _safe(close, 9)
    out["ma20"] = _safe(close, 20)
    out["ma50"] = _safe(close, 50)
    out["ma200"] = _safe(close, 200)

    out["above_9sma_pct"] = (live - out["ma9"]) / out["ma9"] if out["ma9"] else None
    out["above_20sma_pct"] = (live - out["ma20"]) / out["ma20"] if out["ma20"] else None

    out["ret_3m"] = _return_n_days_back(close, 63)
    out["ret_6m"] = _return_n_days_back(close, 126)
    out["ret_12m"] = _return_n_days_back(close, 252)
    out["ret_5d"] = _return_n_days_back(close, 5)

    # Relative strength vs SPY
    out["rs_3m_vs_spy"] = (
        out["ret_3m"] - regime["spy_3m_return"]
        if out["ret_3m"] is not None else None
    )

    # ATR & extension
    out["atr14"] = _atr14(df)
    if out["atr14"] and out["ma9"]:
        out["extension_ratio"] = (live - out["ma9"]) / out["atr14"]
        out["atr_pct"] = out["atr14"] / live
    else:
        out["extension_ratio"] = None
        out["atr_pct"] = None

    # TradingView trigger layer — 5 confirmation booleans
    volume = df["Volume"] if "Volume" in df.columns else pd.Series(dtype=float)
    out.update(_compute_tv_triggers(df, close, volume))

    return out


# ---------------------------------------------------------------------------
# Scoring (matches Excel formulas exactly)
# ---------------------------------------------------------------------------
def _entry_grade(extension_ratio: float | None) -> tuple[str, str, int]:
    """Returns (entry_grade, trade_setup_label, entry_location_score)."""
    e = extension_ratio
    if e is None:
        return ("N/A", "N/A", 0)
    if e < 0:
        return ("Below 9D", "D Pullback / Needs Reclaim", 5)
    if e <= 0.5:
        return ("A Entry", "A Ideal Entry", 0)
    if e <= 1.0:
        return ("B Entry", "B Good Entry", 2)
    if e <= 1.5:
        return ("C Entry", "C Acceptable Entry", 5)
    if e <= 2.0:
        return ("Watch", "E Watch Only", 10)
    if e <= 3.0:
        return ("Extended", "F Extended", 20)
    return ("Chase", "G Do Not Chase", 30)


def _rank_ascending(series: pd.Series) -> pd.Series:
    """RANK(x, range, 0) in Sheets = descending rank, 1 = highest."""
    return series.rank(method="min", ascending=False)


def _rank_descending(series: pd.Series) -> pd.Series:
    """RANK(x, range, 1) in Sheets = ascending rank, 1 = lowest."""
    return series.rank(method="min", ascending=True)


def compute_scores(df: pd.DataFrame) -> pd.DataFrame:
    """Add Leadership/Momentum/Blended/Final scores and ranks to the frame."""
    # --- Leadership Score (Q column in sheet) ---
    #   (3M*0.3)+(6M*0.25)+(12M*0.1)+(RS_3M*0.2)+(MIN(20D_above,0.3)*0.05)+(MIN(5D,0.15)*0.1)
    df["leadership_score"] = (
        df["ret_3m"].fillna(0) * 0.30
        + df["ret_6m"].fillna(0) * 0.25
        + df["ret_12m"].fillna(0) * 0.10
        + df["rs_3m_vs_spy"].fillna(0) * 0.20
        + df["above_20sma_pct"].fillna(0).clip(upper=0.30) * 0.05
        + df["ret_5d"].fillna(0).clip(upper=0.15) * 0.10
    )
    df["leadership_rank"] = _rank_ascending(df["leadership_score"])

    # --- Momentum Score (Y column) = (20D_above*0.6)+(5D*0.4) ---
    df["momentum_score"] = (
        df["above_20sma_pct"].fillna(0) * 0.6
        + df["ret_5d"].fillna(0) * 0.4
    )
    df["momentum_rank"] = _rank_ascending(df["momentum_score"])

    # --- RS vs SPY 20D rank (T/U columns) ---
    df["rs_20d_rank"] = _rank_ascending(df["above_20sma_pct"].fillna(-9))

    # --- Blended Score (AA) = (LeadRank*0.4)+(RS20Rank*0.25)+(MomRank*0.35) ---
    # NOTE: sheet uses *ranks* here, lower rank # = better
    df["blended_score"] = (
        df["leadership_rank"] * 0.40
        + df["rs_20d_rank"] * 0.25
        + df["momentum_rank"] * 0.35
    )
    df["blended_rank"] = _rank_descending(df["blended_score"])  # lower = better

    # --- Entry Grade / Location Score ---
    grades = df["extension_ratio"].apply(_entry_grade)
    df["entry_grade"] = grades.str[0]
    df["trade_setup"] = grades.str[1]
    df["entry_location_score"] = grades.str[2]

    # --- Final Score (AH) = (Blended*0.75)+(Loc*0.25), * 0.7 if extended >3 ---
    df["final_score"] = (
        df["blended_score"] * 0.75 + df["entry_location_score"] * 0.25
    ) * np.where(df["extension_ratio"].fillna(0) > 3, 0.7, 1.0)
    # rank ascending (lower final_score = better, like in sheet)
    df["final_rank"] = _rank_descending(df["final_score"])

    # --- TradingView trigger composite ---
    for k in TV_TRIGGER_KEYS:
        if k not in df.columns:
            df[k] = False
        df[k] = df[k].fillna(False).astype(bool)
    df["tv_trigger_count"] = df[list(TV_TRIGGER_KEYS)].sum(axis=1).astype(int)
    df["tv_trigger_label"] = df[list(TV_TRIGGER_KEYS)].apply(
        lambda r: ", ".join(k for k in TV_TRIGGER_KEYS if r[k]), axis=1
    )

    return df


def compute_raw_score(df: pd.DataFrame) -> pd.DataFrame:
    """The simpler 'Rankings' tab score: pure return-weighted.

    Score = 3M*0.35 + 6M*0.35 + 12M*0.20 + RS_3M*0.10  (Excel `Rankings!M`)
    Ranked descending (highest score = rank 1).
    """
    df["raw_score"] = (
        df["ret_3m"].fillna(0) * 0.35
        + df["ret_6m"].fillna(0) * 0.35
        + df["ret_12m"].fillna(0) * 0.20
        + df["rs_3m_vs_spy"].fillna(0) * 0.10
    )
    df["raw_rank"] = _rank_ascending(df["raw_score"])
    return df


# ---------------------------------------------------------------------------
# Signal & Action (matches Excel S2 / AK2 / AL2 formulas)
# ---------------------------------------------------------------------------
def _rule_check(price, ma50, ma200) -> str:
    if any(v is None or pd.isna(v) for v in (price, ma50, ma200)):
        return ""
    if price > ma200:
        return "Above 200D"
    if price > ma50:
        return "Above 50D only"
    return "Below 50D"


def _signal(row, regime: str) -> str:
    price, ma50, ma200 = row["live_price"], row["ma50"], row["ma200"]
    setup = row["trade_setup"]
    rank = row["final_rank"]
    if any(pd.isna(v) for v in (price, ma50, ma200, rank)) or not setup:
        return ""

    good_entries = ("A Ideal Entry", "B Good Entry")
    ok_entries = good_entries + ("C Acceptable Entry", "E Watch Only")

    if regime != "Defensive" and rank <= 20 and price > ma50 and price > ma200 and setup in good_entries:
        return "BUY"
    if rank <= 40 and price > ma50 and price > ma200 and setup in ok_entries:
        return "HOLD"
    if rank <= 100 and price > ma50 * 0.98 and price > ma200:
        return "WATCH"
    return "SELL"


def _action(rule_check: str, signal: str, setup: str) -> str:
    if not (rule_check and signal and setup):
        return ""
    good = ("A Ideal Entry", "B Good Entry")
    ok = good + ("C Acceptable Entry",)
    pullback = ("D Pullback / Needs Reclaim",)
    overcooked = ("E Watch Only", "F Extended", "G Do Not Chase")
    if rule_check == "Above 200D":
        if signal == "BUY" and setup in good:
            return "1 ACTION BUY"
        if signal == "BUY" and setup == "C Acceptable Entry":
            return "2 STARTER / SCALE-IN"
        if signal == "HOLD" and setup in ok:
            return "3 BUYABLE WATCH"
        if signal == "WATCH" and setup in ok:
            return "4 SECONDARY WATCH"
        if signal in ("BUY", "HOLD", "WATCH") and setup in pullback:
            return "5 WAIT FOR RECLAIM"
        if signal in ("BUY", "HOLD", "WATCH") and setup in overcooked:
            return "6 STALK PULLBACK"
        if signal == "SELL" and setup in good:
            return "7 LOW PRIORITY"
    return "8 PASS"


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------
def run_screener(price_data: dict[str, pd.DataFrame],
                 spy: pd.DataFrame) -> tuple[dict, pd.DataFrame]:
    """Main entry point.

    price_data: {ticker: OHLCV DataFrame indexed by date}
    spy:        OHLCV DataFrame for SPY benchmark

    Returns (regime_dict, results_dataframe).
    """
    regime = market_regime(spy)

    rows = []
    for ticker, df in price_data.items():
        try:
            metrics = per_ticker_metrics(ticker, df, regime)
            if metrics:
                rows.append(metrics)
        except Exception as exc:  # never let one ticker break the whole run
            print(f"[warn] {ticker}: {exc}")

    if not rows:
        return regime, pd.DataFrame()

    df = pd.DataFrame(rows)
    df = compute_scores(df)
    df = compute_raw_score(df)

    df["rule_check"] = df.apply(
        lambda r: _rule_check(r["live_price"], r["ma50"], r["ma200"]), axis=1
    )
    df["signal"] = df.apply(lambda r: _signal(r, regime["regime"]), axis=1)
    df["action"] = df.apply(
        lambda r: _action(r["rule_check"], r["signal"], r["trade_setup"]), axis=1
    )

    df = df.sort_values("final_rank", ascending=True).reset_index(drop=True)
    return regime, df
