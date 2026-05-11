"""
HTML dashboard generator — styled to match the TradeSchool framework.

Self-contained HTML file. No build step, no JS framework. Just vanilla JS
for filter pills and sortable tables. Color theme is slate-900 (#0F172A)
to mirror TradeSchool's `<meta name="theme-color" content="#0F172A">`.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
import math

import pandas as pd


# ---------------------------------------------------------------------------
# CSS — TradeSchool-inspired dark theme
# ---------------------------------------------------------------------------
CSS = """
:root {
  --bg: #0F172A;
  --bg-elev: #1E293B;
  --bg-elev-2: #334155;
  --border: #334155;
  --text: #F1F5F9;
  --text-dim: #94A3B8;
  --text-faint: #64748B;
  --accent: #38BDF8;
  --buy: #22C55E;
  --hold: #84CC16;
  --watch: #F59E0B;
  --sell: #EF4444;
  --action1: #16A34A;
  --action2: #84CC16;
  --action3: #38BDF8;
  --action4: #0EA5E9;
  --action5: #A855F7;
  --action6: #F59E0B;
  --action7: #F97316;
  --action8: #475569;
}
* { box-sizing: border-box; margin: 0; padding: 0; }
html, body {
  background: var(--bg);
  color: var(--text);
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  font-size: 14px;
  line-height: 1.45;
  -webkit-font-smoothing: antialiased;
}
a { color: var(--accent); text-decoration: none; }
a:hover { text-decoration: underline; }

/* ---- TOP NAV ---- */
.topnav {
  display: flex;
  align-items: center;
  gap: 24px;
  padding: 12px 20px;
  border-bottom: 1px solid var(--border);
  background: var(--bg);
  position: sticky;
  top: 0;
  z-index: 50;
  flex-wrap: wrap;
}
.brand {
  font-weight: 700;
  font-size: 17px;
  color: var(--text);
}
.brand span { color: var(--accent); }
.topnav .links {
  display: flex;
  gap: 4px;
  flex: 1;
  flex-wrap: wrap;
}
.topnav .links a {
  padding: 6px 12px;
  border-radius: 6px;
  color: var(--text-dim);
  font-size: 14px;
  font-weight: 500;
}
.topnav .links a.active {
  background: var(--bg-elev);
  color: var(--text);
}
.topnav .links a:hover { color: var(--text); text-decoration: none; }
.statuschips {
  display: flex;
  align-items: center;
  gap: 8px;
}
.chip {
  padding: 4px 10px;
  border-radius: 999px;
  background: var(--bg-elev);
  color: var(--text-dim);
  font-size: 12px;
  font-weight: 500;
  border: 1px solid var(--border);
}
.chip.active { background: var(--bg-elev-2); color: var(--text); }
.chip.risk-on { color: var(--buy); border-color: var(--buy); }
.chip.mixed { color: var(--watch); border-color: var(--watch); }
.chip.defensive { color: var(--sell); border-color: var(--sell); }

/* ---- MAIN ---- */
main { padding: 20px; max-width: 1400px; margin: 0 auto; padding-bottom: 100px; }
h1 { font-size: 28px; font-weight: 700; margin-bottom: 8px; }
h2 { font-size: 18px; font-weight: 600; margin: 24px 0 12px; }
.subtitle { color: var(--text-dim); margin-bottom: 20px; font-size: 14px; }

/* ---- REGIME PANEL ---- */
.regime-panel {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
  gap: 10px;
  background: var(--bg-elev);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 14px;
  margin-bottom: 24px;
}
.regime-cell .label {
  color: var(--text-faint);
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  margin-bottom: 4px;
}
.regime-cell .value {
  font-size: 18px;
  font-weight: 600;
}
.regime-cell .value.regime-risk-on { color: var(--buy); }
.regime-cell .value.regime-mixed { color: var(--watch); }
.regime-cell .value.regime-defensive { color: var(--sell); }

/* ---- FILTERS ---- */
.filters {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-bottom: 14px;
  align-items: center;
}
.filter-label {
  color: var(--text-faint);
  font-size: 12px;
  margin-right: 4px;
  text-transform: uppercase;
  letter-spacing: 0.05em;
}
.pill {
  padding: 5px 12px;
  border-radius: 999px;
  background: var(--bg-elev);
  color: var(--text-dim);
  font-size: 12px;
  border: 1px solid var(--border);
  cursor: pointer;
  user-select: none;
}
.pill:hover { background: var(--bg-elev-2); color: var(--text); }
.pill.on { background: var(--accent); color: var(--bg); border-color: var(--accent); font-weight: 600; }
.search {
  background: var(--bg-elev);
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 6px 10px;
  color: var(--text);
  font-size: 13px;
  margin-left: auto;
  min-width: 180px;
}
.search:focus { outline: none; border-color: var(--accent); }

/* ---- TABLE ---- */
.tablewrap {
  background: var(--bg-elev);
  border: 1px solid var(--border);
  border-radius: 10px;
  overflow-x: auto;
}
table { width: 100%; border-collapse: collapse; }
th, td {
  padding: 8px 10px;
  text-align: right;
  font-variant-numeric: tabular-nums;
  border-bottom: 1px solid var(--border);
  white-space: nowrap;
}
th {
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  color: var(--text-faint);
  font-weight: 500;
  background: var(--bg-elev);
  cursor: pointer;
  user-select: none;
  position: sticky;
  top: 0;
  z-index: 1;
}
th:hover { color: var(--text); }
th.sorted::after { content: " ▾"; color: var(--accent); }
th.sorted.asc::after { content: " ▴"; }
td:first-child, th:first-child { text-align: left; }
td.ticker { font-weight: 600; color: var(--text); }
td.desc { color: var(--text-dim); text-align: left; max-width: 200px; overflow: hidden; text-overflow: ellipsis; }
tr:hover td { background: rgba(56, 189, 248, 0.05); }
td.pos { color: var(--buy); }
td.neg { color: var(--sell); }

a.trade {
  display: inline-block;
  margin-left: 6px;
  padding: 1px 6px;
  font-size: 10px; font-weight: 600;
  background: var(--bg-elev-2);
  color: var(--text-dim);
  border-radius: 4px;
  text-decoration: none;
}
a.trade:hover { background: var(--accent); color: var(--bg); text-decoration: none; }
.card a.trade-cta {
  display: inline-block;
  margin-top: 8px;
  padding: 5px 10px;
  font-size: 11px; font-weight: 600;
  background: var(--accent); color: var(--bg);
  border-radius: 4px;
  text-decoration: none;
}
.card a.trade-cta:hover { background: var(--buy); text-decoration: none; }

.signal-badge, .action-badge {
  display: inline-block;
  padding: 2px 8px;
  border-radius: 4px;
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.02em;
}
.signal-BUY { background: var(--buy); color: var(--bg); }
.signal-HOLD { background: var(--hold); color: var(--bg); }
.signal-WATCH { background: var(--watch); color: var(--bg); }
.signal-SELL { background: var(--sell); color: var(--text); }

.action-1 { background: var(--action1); color: var(--bg); }
.action-2 { background: var(--action2); color: var(--bg); }
.action-3 { background: var(--action3); color: var(--bg); }
.action-4 { background: var(--action4); color: var(--bg); }
.action-5 { background: var(--action5); color: var(--text); }
.action-6 { background: var(--action6); color: var(--bg); }
.action-7 { background: var(--action7); color: var(--bg); }
.action-8 { background: var(--action8); color: var(--text-dim); }

/* ---- DISCLAIMER ---- */
.disclaimer {
  margin-top: 32px;
  padding: 14px;
  background: var(--bg-elev);
  border-left: 3px solid var(--watch);
  border-radius: 6px;
  color: var(--text-dim);
  font-size: 12px;
  line-height: 1.55;
}

/* ---- BOTTOM NAV (mobile-friendly) ---- */
.bottomnav {
  position: fixed;
  bottom: 0;
  left: 0;
  right: 0;
  background: var(--bg-elev);
  border-top: 1px solid var(--border);
  display: flex;
  justify-content: space-around;
  padding: 8px 0;
  z-index: 100;
}
.bottomnav a {
  color: var(--text-dim);
  font-size: 12px;
  font-weight: 500;
  text-decoration: none;
  padding: 4px 10px;
}
.bottomnav a.active { color: var(--accent); }

/* ---- KPI CARDS for top picks ---- */
.cards {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
  gap: 12px;
  margin-bottom: 24px;
}
.card {
  background: var(--bg-elev);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 14px;
}
.card .top {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  margin-bottom: 8px;
}
.card .tkr { font-size: 18px; font-weight: 700; }
.card .desc-sm { color: var(--text-faint); font-size: 11px; margin-top: 2px; }
.card .price { font-size: 20px; font-weight: 600; margin-bottom: 6px; }
.card .meta { display: flex; gap: 10px; font-size: 11px; color: var(--text-dim); }
.card .meta b { color: var(--text); font-weight: 600; }

/* ---- SECTOR TILES ---- */
.sectors {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(170px, 1fr));
  gap: 10px;
  margin-bottom: 24px;
}
.sector-tile {
  background: var(--bg-elev);
  border: 1px solid var(--border);
  border-left: 3px solid var(--accent);
  border-radius: 8px;
  padding: 10px 12px;
  position: relative;
}
.sector-tile.hot   { border-left-color: var(--buy); }
.sector-tile.warm  { border-left-color: var(--hold); }
.sector-tile.cool  { border-left-color: var(--watch); }
.sector-tile.cold  { border-left-color: var(--sell); }
.sector-tile .s-rank {
  position: absolute; top: 8px; right: 10px;
  color: var(--text-faint); font-size: 11px; font-weight: 600;
}
.sector-tile .s-name { font-size: 13px; font-weight: 600; margin-bottom: 6px; padding-right: 28px; }
.sector-tile .s-score { font-size: 18px; font-weight: 700; }
.sector-tile .s-meta {
  display: flex; gap: 8px; margin-top: 4px;
  font-size: 11px; color: var(--text-dim);
}
.sector-tile .s-meta b { color: var(--text); font-weight: 600; }
.sector-tile .s-top { font-size: 11px; color: var(--text-faint); margin-top: 4px; }
.sector-tile .s-top b { color: var(--accent); }

/* ---- Responsive ---- */
@media (max-width: 700px) {
  .topnav { padding: 10px 12px; gap: 12px; }
  .topnav .links a { padding: 6px 8px; font-size: 13px; }
  .statuschips .chip { display: none; }
  .statuschips .chip:first-child { display: inline; }
  main { padding: 14px; padding-bottom: 80px; }
  h1 { font-size: 22px; }
  th, td { padding: 6px 8px; font-size: 12px; }
}
"""


# ---------------------------------------------------------------------------
# JS — pill filtering & sortable table
# ---------------------------------------------------------------------------
JS = """
(function () {
  const rows = Array.from(document.querySelectorAll('#scanner tbody tr'));
  const search = document.getElementById('search');
  const actionPills = Array.from(document.querySelectorAll('.pill[data-filter]'));
  const filterState = { action: 'all', signal: 'all', query: '' };

  function apply() {
    rows.forEach(r => {
      const a = r.dataset.action || '';
      const s = r.dataset.signal || '';
      const t = (r.dataset.search || '').toLowerCase();
      const showA = filterState.action === 'all' || a.startsWith(filterState.action);
      const showS = filterState.signal === 'all' || s === filterState.signal;
      const showQ = !filterState.query || t.includes(filterState.query);
      r.style.display = (showA && showS && showQ) ? '' : 'none';
    });
  }

  actionPills.forEach(p => {
    p.addEventListener('click', () => {
      const group = p.dataset.group;
      document.querySelectorAll(`.pill[data-group="${group}"]`).forEach(o => o.classList.remove('on'));
      p.classList.add('on');
      filterState[group] = p.dataset.filter;
      apply();
    });
  });

  search.addEventListener('input', () => {
    filterState.query = search.value.trim().toLowerCase();
    apply();
  });

  // sortable table
  const table = document.getElementById('scanner');
  const headers = table.querySelectorAll('th');
  headers.forEach((th, i) => {
    th.addEventListener('click', () => {
      const isAsc = th.classList.contains('sorted') && !th.classList.contains('asc');
      headers.forEach(h => h.classList.remove('sorted', 'asc'));
      th.classList.add('sorted');
      if (isAsc) th.classList.add('asc');
      const dir = isAsc ? 1 : -1;
      const numeric = th.dataset.type === 'number';
      const tbody = table.querySelector('tbody');
      const sorted = Array.from(tbody.querySelectorAll('tr')).sort((a, b) => {
        let av = a.children[i].dataset.sort ?? a.children[i].textContent;
        let bv = b.children[i].dataset.sort ?? b.children[i].textContent;
        if (numeric) { av = parseFloat(av); bv = parseFloat(bv); if (isNaN(av)) av = -Infinity; if (isNaN(bv)) bv = -Infinity; }
        return av < bv ? -dir : av > bv ? dir : 0;
      });
      sorted.forEach(r => tbody.appendChild(r));
    });
  });
})();
"""


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------
def _fmt_pct(v) -> str:
    if v is None or pd.isna(v):
        return "—"
    return f"{v*100:+.1f}%"


def _fmt_money(v) -> str:
    if v is None or pd.isna(v):
        return "—"
    return f"${v:,.2f}"


def _fmt_num(v, digits: int = 2) -> str:
    if v is None or pd.isna(v):
        return "—"
    return f"{v:,.{digits}f}"


def _pct_class(v) -> str:
    if v is None or pd.isna(v):
        return ""
    return "pos" if v >= 0 else "neg"


def _signal_badge(s: str) -> str:
    if not s:
        return ""
    return f'<span class="signal-badge signal-{s}">{s}</span>'


def _action_badge(a: str) -> str:
    if not a:
        return ""
    num = a.split()[0] if a else "8"
    return f'<span class="action-badge action-{num}">{a}</span>'


# ---------------------------------------------------------------------------
# Dashboard renderer
# ---------------------------------------------------------------------------
def render(regime: dict,
           df: pd.DataFrame,
           out_path: str | Path,
           generated_at: dt.datetime | None = None,
           sector_etfs: pd.DataFrame | None = None) -> Path:
    """Render the full HTML dashboard and write it to out_path."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    generated_at = generated_at or dt.datetime.now()

    regime_cls = {
        "Risk On": "regime-risk-on",
        "Mixed": "regime-mixed",
        "Defensive": "regime-defensive",
    }.get(regime["regime"], "")

    chips_cls = {
        "Risk On": "risk-on",
        "Mixed": "mixed",
        "Defensive": "defensive",
    }.get(regime["regime"], "")

    # ---- Sector ETF tiles (port of Excel "Sector Rankings" tab) ----
    sectors_html = ""
    if sector_etfs is not None and not sector_etfs.empty:
        n = len(sector_etfs)
        for i, s in sector_etfs.iterrows():
            if i < n * 0.34:
                heat = "hot"
            elif i < n * 0.67:
                heat = "warm"
            else:
                heat = "cold"
            name = s.get("sector_name")
            if name is None or pd.isna(name) or not name:
                name = s["ticker"]
            sectors_html += f"""
            <div class="sector-tile {heat}">
              <div class="s-rank">#{int(s['final_rank'])}</div>
              <div class="s-name">{name}<span style="color:var(--text-faint);font-weight:500;font-size:11px"> ({s['ticker']})</span></div>
              <div class="s-score">{_fmt_pct(s['ret_3m'])}<span style="font-size:11px;color:var(--text-faint);font-weight:500"> 3M</span></div>
              <div class="s-meta">
                <span class="{_pct_class(s['ret_5d'])}">5D {_fmt_pct(s['ret_5d'])}</span>
                <span class="{_pct_class(s['above_20sma_pct'])}">vs 20D {_fmt_pct(s['above_20sma_pct'])}</span>
              </div>
              <div class="s-top">{_signal_badge(s['signal'])}<span style="margin-left:6px;color:var(--text-faint)">{s['trade_setup'] or '—'}</span></div>
            </div>
            """

    # ---- Top "raw score" picks (port of Excel "Rankings" tab) ----
    raw_top_html = ""
    if "raw_rank" in df.columns:
        raw_top = df.sort_values("raw_rank").head(5)
        for _, r in raw_top.iterrows():
            raw_top_html += f"""
            <div class="card">
              <div class="top">
                <div>
                  <div class="tkr">{r['ticker']}</div>
                  <div class="desc-sm">Raw score #{int(r['raw_rank'])} &middot; {_fmt_num(r['raw_score'], 2)}</div>
                </div>
              </div>
              <div class="price">{_fmt_money(r['live_price'])}</div>
              <div class="meta">
                <span>3M <b class="{_pct_class(r['ret_3m'])}">{_fmt_pct(r['ret_3m'])}</b></span>
                <span>6M <b class="{_pct_class(r['ret_6m'])}">{_fmt_pct(r['ret_6m'])}</b></span>
                <span>12M <b class="{_pct_class(r['ret_12m'])}">{_fmt_pct(r['ret_12m'])}</b></span>
              </div>
            </div>
            """

    # ---- Top action picks (cards) ----
    top_picks = df[df["action"].isin([
        "1 ACTION BUY", "2 STARTER / SCALE-IN", "3 BUYABLE WATCH"
    ])].head(8)

    cards_html = ""
    for _, r in top_picks.iterrows():
        cards_html += f"""
        <div class="card">
          <div class="top">
            <div>
              <div class="tkr">{r['ticker']}</div>
            </div>
            {_action_badge(r['action'])}
          </div>
          <div class="price">{_fmt_money(r['live_price'])}
            <span class="{_pct_class(r['day_change_pct'])}" style="font-size:13px;margin-left:6px;">
              {_fmt_pct(r['day_change_pct'])}
            </span>
          </div>
          <div class="meta">
            <span>Rank <b>#{int(r['final_rank']) if pd.notna(r['final_rank']) else '—'}</b></span>
            <span>Ext <b>{_fmt_num(r['extension_ratio'])}</b></span>
            <span>{_signal_badge(r['signal'])}</span>
          </div>
          <a class="trade-cta" href="https://digital.fidelity.com/prgw/digital/trade-equity/?symbol={r['ticker']}" target="_blank">Trade {r['ticker']} in Fidelity ↗</a>
        </div>
        """

    # ---- Full scanner table ----
    columns = [
        ("Ticker",        "ticker",                  "string", "ticker"),
        ("Action",        "action",                  "string", None),
        ("Signal",        "signal",                  "string", None),
        ("Entry",         "trade_setup",             "string", None),
        ("Price",         "live_price",              "number", "money"),
        ("Day %",         "day_change_pct",          "number", "pct"),
        ("5D %",          "ret_5d",                  "number", "pct"),
        ("3M %",          "ret_3m",                  "number", "pct"),
        ("6M %",          "ret_6m",                  "number", "pct"),
        ("12M %",         "ret_12m",                 "number", "pct"),
        ("RS vs SPY 3M",  "rs_3m_vs_spy",            "number", "pct"),
        ("Above 9D",      "above_9sma_pct",          "number", "pct"),
        ("Above 20D",     "above_20sma_pct",         "number", "pct"),
        ("Ext (ATR)",     "extension_ratio",         "number", "num2"),
        ("ATR %",         "atr_pct",                 "number", "pct"),
        ("Lead Rank",     "leadership_rank",         "number", "int"),
        ("Mom Rank",      "momentum_rank",           "number", "int"),
        ("Final Rank",    "final_rank",              "number", "int"),
        ("Raw Rank",      "raw_rank",                "number", "int"),
        ("vs MAs",        "rule_check",              "string", None),
    ]
    th_html = ""
    for label, key, typ, _ in columns:
        cls = "sorted asc" if key == "final_rank" else ""
        th_html += f'<th data-type="{typ}" class="{cls}">{label}</th>'

    body_rows = ""
    for _, r in df.iterrows():
        cells = ""
        for label, key, typ, fmt in columns:
            v = r[key] if key in r else None
            sort_v = ""
            if typ == "number" and v is not None and not pd.isna(v):
                sort_v = f' data-sort="{v}"'
            elif typ == "string" and v:
                sort_v = f' data-sort="{v}"'
            if key == "action":
                cell = _action_badge(v)
            elif key == "signal":
                cell = _signal_badge(v)
            elif fmt == "money":
                cell = _fmt_money(v)
            elif fmt == "pct":
                cls = _pct_class(v)
                cell = f'<span class="{cls}">{_fmt_pct(v)}</span>' if cls else _fmt_pct(v)
            elif fmt == "int":
                cell = str(int(v)) if v is not None and not pd.isna(v) else "—"
            elif fmt == "num2":
                cell = _fmt_num(v, 2)
            elif fmt == "ticker":
                cell = (f'<a href="https://finance.yahoo.com/quote/{v}" target="_blank">{v}</a>'
                        f'<a class="trade" href="https://digital.fidelity.com/prgw/digital/trade-equity/?symbol={v}" target="_blank" title="Trade {v} in Fidelity">Trade&nbsp;↗</a>')
            else:
                cell = str(v) if v else "—"
            td_cls = "ticker" if key == "ticker" else ""
            cells += f'<td class="{td_cls}"{sort_v}>{cell}</td>'

        body_rows += (
            f'<tr data-action="{(r.get("action") or "").split()[0] if r.get("action") else ""}" '
            f'data-signal="{r.get("signal") or ""}" '
            f'data-search="{r["ticker"]}">'
            f'{cells}</tr>'
        )

    # ---- Build page ----
    html = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Trading Tool — Trend Scanner</title>
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
  <meta name="theme-color" content="#0F172A">
  <meta http-equiv="refresh" content="300">
  <meta name="apple-mobile-web-app-capable" content="yes">
  <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
  <meta name="apple-mobile-web-app-title" content="Trading Tool">
  <meta name="description" content="Self-hosted dashboard for real-time stock trend scanning — informs, never executes.">
  <link rel="manifest" href="manifest.webmanifest">
  <link rel="apple-touch-icon" href="icon.svg">
  <link rel="icon" type="image/svg+xml" href="icon.svg">
  <style>{CSS}</style>
</head>
<body>

<nav class="topnav">
  <div class="brand">Trading<span>Tool</span></div>
  <div class="links">
    <a href="#" class="active">Scanner</a>
    <a href="#sectors">Sectors</a>
    <a href="#top-picks">Top Picks</a>
    <a href="#raw-score">Raw Score</a>
    <a href="how-to.html">How to use</a>
    <a href="#methodology">Methodology</a>
  </div>
  <div class="statuschips">
    <span class="chip {chips_cls} active">{regime['regime']}</span>
    <span class="chip">Exposure {regime['exposure']}</span>
    <span class="chip">{generated_at.strftime('%b %d %I:%M %p')}</span>
  </div>
</nav>

<main>
  <h1>Scanner</h1>
  <p class="subtitle">
    Real-time trend-following stock screener. Signals are informational —
    you place trades in your broker (Fidelity, etc.). Data via Yahoo Finance
    (delayed up to 15 min on some symbols).
  </p>

  <section class="regime-panel">
    <div class="regime-cell">
      <div class="label">Market regime</div>
      <div class="value {regime_cls}">{regime['regime']}</div>
    </div>
    <div class="regime-cell">
      <div class="label">SPY price</div>
      <div class="value">{_fmt_money(regime['spy_price'])}</div>
    </div>
    <div class="regime-cell">
      <div class="label">SPY 50D MA</div>
      <div class="value">{_fmt_money(regime['spy_50d'])}</div>
    </div>
    <div class="regime-cell">
      <div class="label">SPY 200D MA</div>
      <div class="value">{_fmt_money(regime['spy_200d'])}</div>
    </div>
    <div class="regime-cell">
      <div class="label">SPY 3M return</div>
      <div class="value {_pct_class(regime['spy_3m_return'])}">{_fmt_pct(regime['spy_3m_return'])}</div>
    </div>
    <div class="regime-cell">
      <div class="label">Suggested exposure</div>
      <div class="value">{regime['exposure']}</div>
    </div>
    <div class="regime-cell">
      <div class="label">Position sizing</div>
      <div class="value" style="font-size:14px">{regime['sizing']}</div>
    </div>
  </section>

  {('<h2 id="sectors">Sector rankings</h2>' + '<p class="subtitle">11 SPDR sector ETFs run through the same screener. Top tile = strongest sector. Use it to spot regime rotation before individual names.</p>' + f'<div class="sectors">{sectors_html}</div>') if sectors_html else ''}

  <h2 id="top-picks">Top action picks</h2>
  <p class="subtitle">Highest-ranked names currently flashing ACTION BUY, STARTER / SCALE-IN, or BUYABLE WATCH.</p>
  <div class="cards">{cards_html or '<div class="card"><div class="desc-sm">No actionable picks right now — market may be defensive or extended. Check back later.</div></div>'}</div>

  {('<h2 id="raw-score">Top by raw Score</h2>' + '<p class="subtitle">Pure return-weighted leaderboard (3M*0.35 + 6M*0.35 + 12M*0.2 + RS*0.1). No entry-quality penalty. Lots of overlap with action picks, but flags extended names too.</p>' + f'<div class="cards">{raw_top_html}</div>') if raw_top_html else ''}

  <h2>Full scanner</h2>
  <div class="filters">
    <span class="filter-label">Action</span>
    <span class="pill on" data-group="action" data-filter="all">All</span>
    <span class="pill" data-group="action" data-filter="1">1 Buy</span>
    <span class="pill" data-group="action" data-filter="2">2 Starter</span>
    <span class="pill" data-group="action" data-filter="3">3 Watch+</span>
    <span class="pill" data-group="action" data-filter="4">4 Watch</span>
    <span class="pill" data-group="action" data-filter="5">5 Reclaim</span>
    <span class="pill" data-group="action" data-filter="6">6 Stalk</span>
    <span class="pill" data-group="action" data-filter="7">7 Low</span>
    <span class="pill" data-group="action" data-filter="8">8 Pass</span>
    <span class="filter-label" style="margin-left:14px">Signal</span>
    <span class="pill on" data-group="signal" data-filter="all">All</span>
    <span class="pill" data-group="signal" data-filter="BUY">Buy</span>
    <span class="pill" data-group="signal" data-filter="HOLD">Hold</span>
    <span class="pill" data-group="signal" data-filter="WATCH">Watch</span>
    <span class="pill" data-group="signal" data-filter="SELL">Sell</span>
    <input id="search" class="search" placeholder="Search ticker…" />
  </div>

  <div class="tablewrap">
    <table id="scanner">
      <thead><tr>{th_html}</tr></thead>
      <tbody>{body_rows}</tbody>
    </table>
  </div>

  <h2 id="methodology">Methodology</h2>
  <p class="subtitle" style="max-width:800px">
    Trend-following framework. Each ticker is ranked across Leadership (long-term momentum + relative strength),
    Momentum (recent moves), and Entry Quality (distance from 9-day SMA in ATR units). Composite scores
    feed into a Signal (BUY/HOLD/WATCH/SELL) and an Action (1–8). The market regime acts as a master switch:
    in <b>Defensive</b> regimes, no BUYs are issued — most idle capital goes to cash equivalents (BIL/SGOV).
  </p>

  <div class="disclaimer">
    This tool is for informational and educational use only. It does not place trades. All order entry happens
    in your brokerage app (Fidelity, etc.). Stock trading carries real risk of loss — only trade with capital
    you can afford to lose. Signals reflect historical momentum patterns and offer no guarantee of future returns.
    Always do your own research.
  </div>
</main>

<nav class="bottomnav">
  <a href="#" class="active">Scanner</a>
  <a href="#sectors">Sectors</a>
  <a href="#top-picks">Picks</a>
  <a href="how-to.html">How</a>
</nav>

<script>{JS}</script>
<script>
  if ('serviceWorker' in navigator) {{
    window.addEventListener('load', () => navigator.serviceWorker.register('sw.js').catch(() => {{}}));
  }}
</script>
</body>
</html>
"""
    out_path.write_text(html, encoding="utf-8")
    return out_path
