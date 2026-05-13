"""
Encrypted portfolio + bot state persistence.

The blob on disk (state.enc) holds three things:
  - portfolio: {ticker: {shares, cost_basis, entry_date, added_at}}
  - bot_state: {last_update_id, last_digest_date}
  - action_snapshot: {ticker: action_bucket}  (used by alerts.diff_actions)

All read/write goes through load_state() / save_state(), which Fernet-encrypt
under PORTFOLIO_ENCRYPTION_KEY (env var). Plaintext never touches disk.

Generate a key with:
    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
"""
from __future__ import annotations

import datetime as dt
import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


STATE_FILE = Path(__file__).resolve().parent.parent / "state.enc"
ENV_KEY = "PORTFOLIO_ENCRYPTION_KEY"


@dataclass
class Position:
    ticker: str
    shares: float
    cost_basis: float                # average $/share
    entry_date: str                  # ISO date (when the position was opened)
    added_at: str                    # ISO timestamp (when /add was issued)

    def market_value(self, price: float) -> float:
        return self.shares * price

    def cost_value(self) -> float:
        return self.shares * self.cost_basis

    def pnl_dollars(self, price: float) -> float:
        return self.market_value(price) - self.cost_value()

    def pnl_pct(self, price: float) -> float:
        if self.cost_basis <= 0:
            return 0.0
        return (price - self.cost_basis) / self.cost_basis


@dataclass
class State:
    portfolio: dict[str, Position] = field(default_factory=dict)
    last_update_id: int = 0          # Telegram getUpdates offset
    last_digest_date: str = ""       # ISO date — so digest fires once per day
    action_snapshot: dict[str, str] = field(default_factory=dict)
    trigger_snapshot: dict[str, list[str]] = field(default_factory=dict)
    top20_snapshot: list[str] = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps({
            "portfolio": {t: asdict(p) for t, p in self.portfolio.items()},
            "last_update_id": self.last_update_id,
            "last_digest_date": self.last_digest_date,
            "action_snapshot": self.action_snapshot,
            "trigger_snapshot": self.trigger_snapshot,
            "top20_snapshot": self.top20_snapshot,
        }, indent=2, sort_keys=True)

    @classmethod
    def from_json(cls, raw: str) -> "State":
        data: dict[str, Any] = json.loads(raw) if raw else {}
        portfolio = {
            t: Position(**p) for t, p in (data.get("portfolio") or {}).items()
        }
        return cls(
            portfolio=portfolio,
            last_update_id=int(data.get("last_update_id") or 0),
            last_digest_date=data.get("last_digest_date") or "",
            action_snapshot=data.get("action_snapshot") or {},
            trigger_snapshot=data.get("trigger_snapshot") or {},
            top20_snapshot=data.get("top20_snapshot") or [],
        )


def _fernet():
    from cryptography.fernet import Fernet  # lazy import
    key = os.environ.get(ENV_KEY)
    if not key:
        raise RuntimeError(
            f"{ENV_KEY} env var is not set. Generate one with: "
            "python -c \"from cryptography.fernet import Fernet; "
            "print(Fernet.generate_key().decode())\""
        )
    return Fernet(key.encode() if isinstance(key, str) else key)


def load_state(path: Path | str | None = None) -> State:
    """Decrypt + deserialize state. Returns empty State if file is missing."""
    path = Path(path) if path else STATE_FILE
    if not path.exists():
        return State()
    blob = path.read_bytes()
    if not blob:
        return State()
    raw = _fernet().decrypt(blob).decode("utf-8")
    return State.from_json(raw)


def save_state(state: State, path: Path | str | None = None) -> Path:
    """Serialize + encrypt state to disk."""
    path = Path(path) if path else STATE_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = state.to_json().encode("utf-8")
    path.write_bytes(_fernet().encrypt(raw))
    return path


# ---------------------------------------------------------------------------
# Position CRUD
# ---------------------------------------------------------------------------
def add_position(state: State, ticker: str, shares: float,
                 cost_basis: float, entry_date: str | None = None) -> Position:
    """Add or replace a position. Replaces by ticker (no averaging in v1)."""
    ticker = ticker.strip().upper()
    if not ticker:
        raise ValueError("ticker is required")
    if shares <= 0:
        raise ValueError("shares must be > 0")
    if cost_basis <= 0:
        raise ValueError("cost_basis must be > 0")
    now = dt.datetime.utcnow().isoformat(timespec="seconds")
    pos = Position(
        ticker=ticker,
        shares=float(shares),
        cost_basis=float(cost_basis),
        entry_date=entry_date or dt.date.today().isoformat(),
        added_at=now,
    )
    state.portfolio[ticker] = pos
    return pos


def remove_position(state: State, ticker: str) -> Position | None:
    return state.portfolio.pop(ticker.strip().upper(), None)


def list_positions(state: State) -> list[Position]:
    return sorted(state.portfolio.values(), key=lambda p: p.ticker)
