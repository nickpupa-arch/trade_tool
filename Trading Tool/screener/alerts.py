"""
Alert generators — pure functions that take state + screener results and
return formatted MarkdownV2 strings ready for TelegramNotifier.send_text().

Four kinds:
  - morning_digest(...)            once-per-day summary
  - action_transition_alerts(...)  diff prev vs current bucket per holding
  - tv_trigger_alerts(...)         TV triggers firing on a holding
  - new_top20_buys(...)            fresh 1 ACTION BUYs in the top 20 ranks

All four return a list of MarkdownV2 strings. Empty list = nothing to send.
"""
from __future__ import annotations

import datetime as dt
from typing import Iterable

import pandas as pd

from .engine import TV_TRIGGER_KEYS
from .notifier import escape_md
from .portfolio import Position, State


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _row_for(df: pd.DataFrame, ticker: str) -> pd.Series | None:
    if df is None or df.empty:
        return None
    sub = df[df["ticker"] == ticker]
    if sub.empty:
        return None
    return sub.iloc[0]


def _fmt_pct(v) -> str:
    if v is None or pd.isna(v):
        return "—"
    sign = "+" if v >= 0 else ""
    return f"{sign}{v * 100:.1f}%"


def _fmt_money(v) -> str:
    if v is None or pd.isna(v):
        return "—"
    return f"${v:,.2f}"


# ---------------------------------------------------------------------------
# 1) Morning digest — once per ET trading day
# ---------------------------------------------------------------------------
def morning_digest(regime: dict, state: State, results: pd.DataFrame,
                   today_iso: str | None = None) -> list[str]:
    """Returns [text] (single-element list) if it's a fresh day, else []."""
    today = today_iso or dt.date.today().isoformat()
    if state.last_digest_date == today:
        return []  # already sent today

    lines = [
        f"*🌅 Morning digest — {escape_md(today)}*",
        "",
        f"Regime: *{escape_md(regime.get('regime', '—'))}* "
        f"\\(suggested {escape_md(regime.get('exposure', '—'))}\\)",
        f"SPY {escape_md(_fmt_money(regime.get('spy_price')))} "
        f"vs 50D {escape_md(_fmt_money(regime.get('spy_50d')))} / "
        f"200D {escape_md(_fmt_money(regime.get('spy_200d')))}",
    ]

    # Portfolio rollup
    positions = sorted(state.portfolio.values(), key=lambda p: p.ticker)
    if positions:
        lines.append("")
        lines.append("*Your positions*")
        total_cost = 0.0
        total_value = 0.0
        for p in positions:
            row = _row_for(results, p.ticker)
            price = float(row["live_price"]) if row is not None and pd.notna(row.get("live_price")) else p.cost_basis
            action = row["action"] if row is not None else ""
            triggers = row.get("tv_trigger_label", "") if row is not None else ""
            pnl_pct = p.pnl_pct(price) * 100.0
            sign = "+" if pnl_pct >= 0 else ""
            total_cost += p.cost_value()
            total_value += p.market_value(price)
            line = (
                f"`{p.ticker}` {p.shares:g}@\\${p.cost_basis:,.2f} → "
                f"\\${price:,.2f} \\({sign}{pnl_pct:.1f}%\\)"
            )
            if action:
                line += f" · {escape_md(action)}"
            if triggers:
                line += f" · 🔔 {escape_md(triggers)}"
            lines.append(_safe_md(line))
        pnl = total_value - total_cost
        sign = "+" if pnl >= 0 else ""
        pct = (pnl / total_cost * 100.0) if total_cost else 0.0
        lines.append("")
        lines.append(
            _safe_md(
                f"*Total*: cost \\${total_cost:,.0f} → "
                f"value \\${total_value:,.0f} \\({sign}{pct:.1f}%\\)"
            )
        )
    else:
        lines.append("")
        lines.append("_Portfolio is empty\\. Add with_ `/add TICKER SHARES @PRICE`")

    # Top ACTION BUYs
    if results is not None and not results.empty:
        buys = results[results["action"] == "1 ACTION BUY"].head(5)
        if not buys.empty:
            lines.append("")
            lines.append("*Top ACTION BUYs today*")
            for _, r in buys.iterrows():
                lines.append(_safe_md(
                    f"`{r['ticker']}` rank \\#{int(r['final_rank'])} · "
                    f"\\${r['live_price']:,.2f} · ext {r['extension_ratio']:.2f}"
                ))

    # Mark digest sent
    state.last_digest_date = today
    return ["\n".join(lines)]


def _safe_md(line: str) -> str:
    """Escape any stray '.' or '-' in numeric text that aren't already escaped.

    We assemble messages with intentional MarkdownV2 syntax already; this just
    catches the common case of decimals and dashes in numbers that would
    otherwise break the parser. Avoid double-escaping characters we already
    escaped with a backslash.
    """
    out = []
    i = 0
    while i < len(line):
        ch = line[i]
        if ch == "\\" and i + 1 < len(line):
            out.append(ch + line[i + 1])
            i += 2
            continue
        if ch in ".-=":
            out.append("\\" + ch)
        else:
            out.append(ch)
        i += 1
    return "".join(out)


# ---------------------------------------------------------------------------
# 2) Action-bucket transitions on holdings
# ---------------------------------------------------------------------------
def action_transition_alerts(state: State, results: pd.DataFrame) -> list[str]:
    """One alert per holding whose action bucket changed since last snapshot.

    Also updates state.action_snapshot in place to current values.
    """
    if results is None or results.empty or not state.portfolio:
        # Still snapshot what we know so next run has a baseline
        if results is not None and not results.empty:
            state.action_snapshot = dict(zip(results["ticker"], results["action"].fillna("")))
        return []

    new_snapshot = dict(zip(results["ticker"], results["action"].fillna("")))
    alerts: list[str] = []
    for ticker in sorted(state.portfolio.keys()):
        new_action = new_snapshot.get(ticker, "")
        old_action = state.action_snapshot.get(ticker, "")
        if not new_action or new_action == old_action:
            continue
        row = _row_for(results, ticker)
        price = float(row["live_price"]) if row is not None else 0.0
        arrow = "→"
        emoji = _transition_emoji(old_action, new_action)
        alerts.append(_safe_md(
            f"{emoji} *{escape_md(ticker)}* moved "
            f"{escape_md(old_action) if old_action else '_new_'} {arrow} "
            f"*{escape_md(new_action)}*\n"
            f"Price \\${price:,.2f}"
        ))

    state.action_snapshot = new_snapshot
    return alerts


def _transition_emoji(old: str, new: str) -> str:
    """Pick an emoji based on whether the move is up the ladder or down."""
    def bucket_num(a: str) -> int:
        try:
            return int(a.split()[0]) if a else 8
        except (ValueError, IndexError):
            return 8
    if not old:
        return "🆕"
    o, n = bucket_num(old), bucket_num(new)
    if n < o:
        return "⬆️"
    if n > o:
        return "⬇️"
    return "↔️"


# ---------------------------------------------------------------------------
# 3) TV trigger fires on a holding
# ---------------------------------------------------------------------------
def tv_trigger_alerts(state: State, results: pd.DataFrame,
                      prev_triggers: dict[str, set[str]] | None = None
                      ) -> list[str]:
    """Alert when one of the 5 triggers fires on a held ticker.

    To avoid re-alerting on the same trigger every 15-min run, callers should
    pass prev_triggers (per-ticker set of trigger names that were already
    firing as of the previous run). We only emit alerts for newly-firing
    triggers.

    For v2.0 simplicity, prev_triggers is just derived inline from the prior
    snapshot; if not provided, we send every firing trigger (noisy first run).
    """
    if results is None or results.empty or not state.portfolio:
        return []
    prev = prev_triggers or {}
    alerts: list[str] = []
    for ticker in sorted(state.portfolio.keys()):
        row = _row_for(results, ticker)
        if row is None:
            continue
        firing = {k for k in TV_TRIGGER_KEYS if bool(row.get(k))}
        new_firing = firing - prev.get(ticker, set())
        if not new_firing:
            continue
        names = ", ".join(sorted(new_firing))
        price = float(row["live_price"]) if pd.notna(row.get("live_price")) else 0.0
        alerts.append(_safe_md(
            f"🔔 *{escape_md(ticker)}* triggers firing: *{escape_md(names)}*\n"
            f"Price \\${price:,.2f} · rank \\#{int(row['final_rank'])}"
        ))
    return alerts


def snapshot_triggers(results: pd.DataFrame) -> dict[str, set[str]]:
    """Helper to capture currently-firing triggers per ticker for next run."""
    if results is None or results.empty:
        return {}
    out: dict[str, set[str]] = {}
    for _, r in results.iterrows():
        firing = {k for k in TV_TRIGGER_KEYS if bool(r.get(k))}
        if firing:
            out[r["ticker"]] = firing
    return out


# ---------------------------------------------------------------------------
# 4) Fresh top-20 ACTION BUYs
# ---------------------------------------------------------------------------
def new_top20_buys(results: pd.DataFrame, prev_top20: Iterable[str]) -> list[str]:
    """Alert when a new ticker enters the top-20 1 ACTION BUY list."""
    if results is None or results.empty:
        return []
    buys = results[
        (results["action"] == "1 ACTION BUY")
        & (results["final_rank"] <= 20)
    ].sort_values("final_rank").head(20)
    if buys.empty:
        return []
    prev_set = set(prev_top20 or [])
    fresh = [t for t in buys["ticker"].tolist() if t not in prev_set]
    if not fresh:
        return []
    lines = ["*✨ Fresh top\\-20 ACTION BUYs*"]
    for t in fresh:
        r = _row_for(results, t)
        lines.append(_safe_md(
            f"`{t}` rank \\#{int(r['final_rank'])} · "
            f"\\${r['live_price']:,.2f} · ext {r['extension_ratio']:.2f}"
        ))
    return ["\n".join(lines)]


def current_top20_buys(results: pd.DataFrame) -> list[str]:
    """Helper — returns the ticker symbols currently in the top-20 BUYs."""
    if results is None or results.empty:
        return []
    buys = results[
        (results["action"] == "1 ACTION BUY")
        & (results["final_rank"] <= 20)
    ].sort_values("final_rank").head(20)
    return buys["ticker"].tolist()
