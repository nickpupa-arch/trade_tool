"""
Persistent orchestrator for Fly.io deployment.

Runs four concurrent threads in one process:
  - intraday : 60s cadence during 4 AM-4 PM ET (incl. premarket)
  - daily    : 5-min cadence during 9:30 AM-4 PM ET (writes _site/)
  - bot      : 60s cadence always (processes /add /remove /list)
  - http     : stdlib server on $PORT (default 8080), serves _site/ + /health

Each worker thread spawns `python run.py ...` as a subprocess and forwards
stdout with a prefix. Why subprocess and not in-process: zero refactor of
working code, crash containment is free (subprocess exits, orchestrator
continues), behavior matches the existing GitHub Actions setup exactly.

State race protection: the three workers serialize through STATE_LOCK so
no two cycles can load → mutate → save state.enc concurrently. Daily
holds the lock ~45s once per 5 min (~15% contention during market hours);
intraday/bot wait briefly if daily is mid-cycle.

SIGTERM (Fly sends on deploy/restart) sets a shared SHUTDOWN event; all
loops poll it and exit cleanly within a few seconds.
"""
from __future__ import annotations

import datetime as dt
import http.server
import json
import os
import signal
import socketserver
import subprocess
import sys
import threading
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
SITE_DIR = HERE / "_site"
EASTERN = ZoneInfo("America/New_York")

# Cadences (seconds). Override via env for testing.
INTRADAY_PERIOD = int(os.environ.get("INTRADAY_PERIOD_S", "60"))
DAILY_PERIOD = int(os.environ.get("DAILY_PERIOD_S", "300"))
BOT_PERIOD = int(os.environ.get("BOT_PERIOD_S", "60"))

# A single mutex around all state-touching subprocesses. See module docstring.
STATE_LOCK = threading.Lock()
SHUTDOWN = threading.Event()
START_TIME = dt.datetime.now(dt.timezone.utc)

# Bounded counters surfaced by /health for cheap observability.
_STATS_LOCK = threading.Lock()
STATS = {
    "intraday": {"runs": 0, "errors": 0, "last_run": None, "last_ok": None},
    "daily":    {"runs": 0, "errors": 0, "last_run": None, "last_ok": None},
    "bot":      {"runs": 0, "errors": 0, "last_run": None, "last_ok": None},
}


# ---------------------------------------------------------------------------
# Market-hours helpers
# ---------------------------------------------------------------------------
def _now_et() -> dt.datetime:
    return dt.datetime.now(EASTERN)


def _in_window(now: dt.datetime, start: dt.time, end: dt.time) -> bool:
    """Mon-Fri only; time-of-day in [start, end). US Eastern."""
    if now.weekday() >= 5:
        return False
    t = now.time()
    return start <= t < end


def in_intraday_window(now: dt.datetime | None = None) -> bool:
    """4:00 AM - 4:00 PM ET, Mon-Fri (premarket + regular)."""
    return _in_window(now or _now_et(), dt.time(4, 0), dt.time(16, 0))


def in_regular_window(now: dt.datetime | None = None) -> bool:
    """9:30 AM - 4:00 PM ET, Mon-Fri."""
    return _in_window(now or _now_et(), dt.time(9, 30), dt.time(16, 0))


# ---------------------------------------------------------------------------
# Subprocess helpers
# ---------------------------------------------------------------------------
def _run_subprocess(name: str, argv: list[str], timeout: int) -> int:
    """Spawn `python run.py ...`, forward each line of stdout with a prefix,
    update STATS. Returns the process exit code (0 on success)."""
    with _STATS_LOCK:
        STATS[name]["runs"] += 1
        STATS[name]["last_run"] = _now_et().isoformat(timespec="seconds")

    full = [sys.executable, str(HERE / "run.py"), *argv]
    try:
        proc = subprocess.run(
            full,
            cwd=str(HERE),
            timeout=timeout,
            check=False,
            capture_output=True,
            text=True,
        )
    except subprocess.TimeoutExpired:
        print(f"[{name}] TIMEOUT after {timeout}s — killing", flush=True)
        with _STATS_LOCK:
            STATS[name]["errors"] += 1
        return -1

    # Forward stdout/stderr line-by-line with a worker prefix.
    for line in (proc.stdout or "").splitlines():
        print(f"[{name}] {line}", flush=True)
    for line in (proc.stderr or "").splitlines():
        print(f"[{name}:err] {line}", flush=True)

    if proc.returncode == 0:
        with _STATS_LOCK:
            STATS[name]["last_ok"] = _now_et().isoformat(timespec="seconds")
    else:
        with _STATS_LOCK:
            STATS[name]["errors"] += 1
        print(f"[{name}] subprocess exited {proc.returncode}", flush=True)
    return proc.returncode


# ---------------------------------------------------------------------------
# Worker loops
# ---------------------------------------------------------------------------
def _intraday_loop():
    print("[intraday] loop started", flush=True)
    while not SHUTDOWN.is_set():
        if in_intraday_window():
            with STATE_LOCK:
                _run_subprocess("intraday", ["--intraday"], timeout=120)
        SHUTDOWN.wait(INTRADAY_PERIOD)


def _daily_loop():
    print("[daily] loop started", flush=True)
    while not SHUTDOWN.is_set():
        if in_regular_window():
            with STATE_LOCK:
                _run_subprocess(
                    "daily",
                    ["--notify", "--no-open", "--out", str(SITE_DIR / "dashboard.html")],
                    timeout=600,  # generous: Yahoo rate-limiting + 500-ticker fetch
                )
        SHUTDOWN.wait(DAILY_PERIOD)


def _bot_loop():
    print("[bot] loop started", flush=True)
    while not SHUTDOWN.is_set():
        with STATE_LOCK:
            _run_subprocess("bot", ["--bot-only"], timeout=30)
        SHUTDOWN.wait(BOT_PERIOD)


# ---------------------------------------------------------------------------
# HTTP server — serves _site/ + /health
# ---------------------------------------------------------------------------
class _DashboardHandler(http.server.SimpleHTTPRequestHandler):
    """Serves _site/ as the document root; intercepts /health for Fly."""

    def __init__(self, *args, **kwargs):
        # SimpleHTTPRequestHandler expects `directory=` in Python 3.7+.
        super().__init__(*args, directory=str(SITE_DIR), **kwargs)

    def do_GET(self):  # noqa: N802
        if self.path == "/health" or self.path == "/healthz":
            return self._health()
        # Convenience: bare "/" goes to dashboard.html if no index.html yet.
        if self.path == "/" and not (SITE_DIR / "index.html").exists():
            dash = SITE_DIR / "dashboard.html"
            if dash.exists():
                self.path = "/dashboard.html"
        return super().do_GET()

    def _health(self):
        with _STATS_LOCK:
            payload = {
                "ok": True,
                "uptime_s": int((dt.datetime.now(dt.timezone.utc) - START_TIME).total_seconds()),
                "now_et": _now_et().isoformat(timespec="seconds"),
                "windows": {
                    "intraday": in_intraday_window(),
                    "regular": in_regular_window(),
                },
                "workers": STATS,
            }
        body = json.dumps(payload, indent=2).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):  # noqa: A003
        # Use stdout (Fly captures it) instead of stderr's default.
        sys.stdout.write(f"[http] {self.address_string()} - {fmt % args}\n")
        sys.stdout.flush()


class _ReusableTCPServer(socketserver.ThreadingTCPServer):
    """Fly's deploy churn benefits from quick socket reuse."""
    allow_reuse_address = True
    daemon_threads = True


def _http_thread():
    port = int(os.environ.get("PORT", "8080"))
    SITE_DIR.mkdir(parents=True, exist_ok=True)
    with _ReusableTCPServer(("0.0.0.0", port), _DashboardHandler) as srv:
        print(f"[http] serving {SITE_DIR} on :{port}", flush=True)
        # serve_forever with a poll interval so we notice SHUTDOWN.
        while not SHUTDOWN.is_set():
            srv.handle_request_with_timeout()


def _patch_handle_request():
    """Add a timeout-based handle_request so we can shut down promptly."""
    def handle_request_with_timeout(self, poll_interval=0.5):
        import select
        r, _, _ = select.select([self], [], [], poll_interval)
        if r:
            self._handle_request_noblock()
    _ReusableTCPServer.handle_request_with_timeout = handle_request_with_timeout


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------
def main():
    _patch_handle_request()
    SITE_DIR.mkdir(parents=True, exist_ok=True)

    def _handle_sigterm(*_):
        print("[loop] SIGTERM/SIGINT — initiating shutdown", flush=True)
        SHUTDOWN.set()

    signal.signal(signal.SIGTERM, _handle_sigterm)
    signal.signal(signal.SIGINT, _handle_sigterm)

    threads = [
        threading.Thread(target=_intraday_loop, name="intraday", daemon=True),
        threading.Thread(target=_daily_loop, name="daily", daemon=True),
        threading.Thread(target=_bot_loop, name="bot", daemon=True),
        threading.Thread(target=_http_thread, name="http", daemon=True),
    ]
    for t in threads:
        t.start()

    print(f"[loop] started; intraday={INTRADAY_PERIOD}s daily={DAILY_PERIOD}s "
          f"bot={BOT_PERIOD}s site={SITE_DIR}", flush=True)

    # Block here until SIGTERM. Workers are daemons; they'll be torn down on exit.
    while not SHUTDOWN.is_set():
        SHUTDOWN.wait(1)

    # Give in-flight subprocesses a brief window to finish.
    print("[loop] waiting for in-flight workers (up to 10s)…", flush=True)
    deadline = dt.datetime.now() + dt.timedelta(seconds=10)
    while dt.datetime.now() < deadline:
        if STATE_LOCK.acquire(timeout=0.5):
            STATE_LOCK.release()
            break
    print("[loop] bye", flush=True)


if __name__ == "__main__":
    main()
