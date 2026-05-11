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

    df["rule_check"] = df.apply(
        lambda r: _rule_check(r["live_price"], r["ma50"], r["ma200"]), axis=1
    )
    df["signal"] = df.apply(lambda r: _signal(r, regime["regime"]), axis=1)
    df["action"] = df.apply(
        lambda r: _action(r["rule_check"], r["signal"], r["trade_setup"]), axis=1
    )

    df = df.sort_values("final_rank", ascending=True).reset_index(drop=True)
    return regime, df
