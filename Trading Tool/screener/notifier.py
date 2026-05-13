"""
Telegram notifier — thin wrapper around the Bot API.

Reads two env vars:
  TELEGRAM_BOT_TOKEN  — from @BotFather
  TELEGRAM_CHAT_ID    — your private chat id (string or int)

The class is the abstraction boundary for future Discord / Slack / Twilio
backends — anything that implements send_text(text) can replace it.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request


# MarkdownV2 escape — Telegram's MarkdownV2 has a long list of reserved chars.
# https://core.telegram.org/bots/api#markdownv2-style
_MD_V2_ESCAPE = "_*[]()~`>#+-=|{}.!"


def escape_md(text: str) -> str:
    """Escape a string for safe use inside Telegram MarkdownV2 text spans."""
    out = []
    for ch in text:
        if ch in _MD_V2_ESCAPE:
            out.append("\\" + ch)
        else:
            out.append(ch)
    return "".join(out)


class TelegramNotifier:
    BASE = "https://api.telegram.org/bot{token}/{method}"

    def __init__(self, token: str | None = None,
                 chat_id: str | int | None = None,
                 timeout: float = 15.0):
        self.token = token or os.environ.get("TELEGRAM_BOT_TOKEN", "")
        cid = chat_id or os.environ.get("TELEGRAM_CHAT_ID", "")
        self.chat_id = str(cid) if cid else ""
        self.timeout = timeout

    @property
    def configured(self) -> bool:
        return bool(self.token and self.chat_id)

    def _call(self, method: str, payload: dict) -> dict:
        url = self.BASE.format(token=self.token, method=method)
        data = urllib.parse.urlencode(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            raise RuntimeError(
                f"Telegram {method} failed: HTTP {e.code} — {body}"
            ) from None

    def send_text(self, text: str, *, parse_mode: str = "MarkdownV2",
                  disable_preview: bool = True) -> dict:
        if not self.configured:
            raise RuntimeError(
                "Telegram is not configured. Set TELEGRAM_BOT_TOKEN and "
                "TELEGRAM_CHAT_ID env vars."
            )
        # Telegram caps single messages at 4096 chars — split if needed
        if len(text) <= 4096:
            return self._call("sendMessage", {
                "chat_id": self.chat_id,
                "text": text,
                "parse_mode": parse_mode,
                "disable_web_page_preview": "true" if disable_preview else "false",
            })
        # naive split on double newline
        chunks: list[str] = []
        buf = ""
        for paragraph in text.split("\n\n"):
            if len(buf) + len(paragraph) + 2 > 4000:
                if buf:
                    chunks.append(buf)
                buf = paragraph
            else:
                buf = (buf + "\n\n" + paragraph) if buf else paragraph
        if buf:
            chunks.append(buf)
        results = []
        for c in chunks:
            results.append(self._call("sendMessage", {
                "chat_id": self.chat_id,
                "text": c,
                "parse_mode": parse_mode,
                "disable_web_page_preview": "true" if disable_preview else "false",
            }))
        return results[-1]

    def get_updates(self, offset: int = 0, timeout: int = 0) -> list[dict]:
        """Long-poll Telegram for new messages. offset = last update_id + 1."""
        if not self.configured:
            return []
        # Use GET for getUpdates since urllib's POST encoding is awkward with ints
        params = urllib.parse.urlencode({
            "offset": offset,
            "timeout": timeout,
            "allowed_updates": json.dumps(["message"]),
        })
        url = self.BASE.format(token=self.token, method="getUpdates") + "?" + params
        try:
            with urllib.request.urlopen(url, timeout=self.timeout) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            raise RuntimeError(
                f"Telegram getUpdates failed: HTTP {e.code} — {body}"
            ) from None
        if not payload.get("ok"):
            raise RuntimeError(f"Telegram getUpdates not ok: {payload}")
        return payload.get("result") or []
