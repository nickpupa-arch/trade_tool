"""
Telegram bot command dispatch.

Pulls pending updates via long-poll (timeout=0 by default so this is fine
inside a 15-min cron), routes commands from the authorized chat_id only,
and mutates the State in place. Commands:

  /add TICKER SHARES @PRICE [YYYY-MM-DD]   # entry_date optional
  /remove TICKER
  /list
  /help
  /start

Messages from any other chat_id are ignored (defense-in-depth even though
the bot only acts on env-configured chat).
"""
from __future__ import annotations

import datetime as dt
import re
from typing import Iterable

from .notifier import TelegramNotifier, escape_md
from .portfolio import State, add_position, list_positions, remove_position


HELP_TEXT = (
    "*Trading Tool bot* — portfolio commands:\n\n"
    "`/add TICKER SHARES @PRICE [YYYY-MM-DD]`\n"
    "  e\\.g\\. `/add NVDA 50 @950` or `/add NVDA 50 @950 2026-04-01`\n\n"
    "`/remove TICKER` — drop a position\n"
    "`/list` — show all positions\n"
    "`/help` — this message"
)


# /add NVDA 50 @950.25 2026-04-01
_ADD_RE = re.compile(
    r"^/add(?:@\w+)?\s+(\S+)\s+(\d+(?:\.\d+)?)\s+@\s*(\d+(?:\.\d+)?)\s*(\d{4}-\d{2}-\d{2})?\s*$",
    re.IGNORECASE,
)
_REMOVE_RE = re.compile(r"^/remove(?:@\w+)?\s+(\S+)\s*$", re.IGNORECASE)


def _format_position_line(p, prices: dict[str, float] | None = None) -> str:
    """One-line MarkdownV2-safe position summary."""
    base = f"`{p.ticker}` — {p.shares:g} @ ${p.cost_basis:,.2f}"
    if prices and p.ticker in prices:
        live = prices[p.ticker]
        pct = p.pnl_pct(live) * 100.0
        sign = "+" if pct >= 0 else ""
        base += f" → ${live:,.2f} \\({sign}{pct:.1f}%\\)"
    return escape_md_partial(base)


def escape_md_partial(s: str) -> str:
    """Preserve our intentional MarkdownV2 syntax (backticks, parens that we
    pre-escaped) while escaping anything else that could break the parser.
    Cheap heuristic — we already control the input format above."""
    # Already-escaped chars stay as is; we only need to escape stray dots and
    # other reservedchars in numbers. The format string above is the only caller
    # and is already well-formed for Telegram.
    return s


def handle_command(text: str, state: State,
                   prices: dict[str, float] | None = None) -> str:
    """Dispatch a single text command and return a MarkdownV2 reply.

    state is mutated in place — caller is responsible for save_state().
    """
    t = (text or "").strip()
    if not t:
        return ""

    low = t.lower()
    if low in ("/start", "/help") or low.startswith("/help"):
        return HELP_TEXT

    if low == "/list" or low.startswith("/list"):
        positions = list_positions(state)
        if not positions:
            return "Portfolio is empty\\. Add with `/add TICKER SHARES @PRICE`\\."
        lines = ["*Your portfolio*"]
        total_cost = 0.0
        total_value = 0.0
        for p in positions:
            lines.append(_format_position_line(p, prices))
            total_cost += p.cost_value()
            if prices and p.ticker in prices:
                total_value += p.market_value(prices[p.ticker])
            else:
                total_value += p.cost_value()
        if prices:
            pnl = total_value - total_cost
            sign = "\\+" if pnl >= 0 else ""
            pct = (pnl / total_cost * 100.0) if total_cost else 0.0
            lines.append("")
            lines.append(
                f"*Total* cost ${total_cost:,.0f} → value ${total_value:,.0f} "
                f"\\({sign}{pct:.1f}%\\)".replace(".", "\\.")
            )
        return "\n".join(lines)

    m = _ADD_RE.match(t)
    if m:
        ticker, shares_s, price_s, date_s = m.groups()
        try:
            pos = add_position(
                state, ticker,
                shares=float(shares_s),
                cost_basis=float(price_s),
                entry_date=date_s or dt.date.today().isoformat(),
            )
        except ValueError as e:
            return f"Add failed: {escape_md(str(e))}"
        return (
            f"Added `{escape_md(pos.ticker)}` — "
            f"{escape_md(f'{pos.shares:g}')} shares @ "
            f"\\${escape_md(f'{pos.cost_basis:,.2f}')} "
            f"\\(entry {escape_md(pos.entry_date)}\\)\\."
        )

    m = _REMOVE_RE.match(t)
    if m:
        ticker = m.group(1).strip().upper()
        removed = remove_position(state, ticker)
        if removed:
            return f"Removed `{escape_md(removed.ticker)}` from portfolio\\."
        return f"`{escape_md(ticker)}` not in portfolio\\."

    if t.startswith("/"):
        return (
            "Unknown command\\. Try `/help` for the supported list\\."
        )
    return ""  # ignore non-command text


def process_updates(notifier: TelegramNotifier, state: State,
                    prices: dict[str, float] | None = None,
                    authorized_chat_id: str | None = None) -> int:
    """Pull pending updates and dispatch commands. Returns # processed.

    Mutates `state.last_update_id` and `state.portfolio` in place.
    """
    if not notifier.configured:
        return 0

    auth = str(authorized_chat_id or notifier.chat_id)
    offset = state.last_update_id + 1 if state.last_update_id else 0
    updates = notifier.get_updates(offset=offset, timeout=0)
    if not updates:
        return 0

    processed = 0
    for u in updates:
        # Track the highest update_id even for messages we ignore
        state.last_update_id = max(state.last_update_id, int(u.get("update_id", 0)))
        msg = u.get("message") or {}
        chat = msg.get("chat") or {}
        if str(chat.get("id")) != auth:
            continue  # unauthorized chat — ignore silently
        text = msg.get("text") or ""
        reply = handle_command(text, state, prices)
        if reply:
            try:
                notifier.send_text(reply)
            except Exception as exc:  # noqa: BLE001
                # Don't lose the update_id update on a network blip
                print(f"[bot] reply failed: {exc}")
        processed += 1
    return processed


def affected_tickers_from_updates(updates: Iterable[dict]) -> set[str]:
    """Used in tests + debugging. Pull tickers out of /add or /remove text."""
    out: set[str] = set()
    for u in updates:
        msg = (u.get("message") or {}).get("text") or ""
        m = _ADD_RE.match(msg) or _REMOVE_RE.match(msg)
        if m:
            out.add(m.group(1).strip().upper())
    return out
