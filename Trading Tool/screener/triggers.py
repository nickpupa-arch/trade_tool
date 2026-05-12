"""
Pine Script v6 generator for the TradingView Trigger Layer.

Mirrors the five booleans computed in engine._compute_tv_triggers():
  - squeeze_on            (TTM-style: BB inside KC)
  - squeeze_fire          (squeeze release with positive momentum)
  - macd_bull_cross       (MACD hist crossed >0 in last 3 bars, above 200D MA)
  - rsi_bull_divergence   (hidden bullish divergence over last 30 bars)
  - vol_surge             (today's volume > 1.5 * 20-bar avg AND close > prior high)

Build a watchlist alert script by calling generate_pine_watchlist_alert() with
the tickers you want to monitor. Paste into TradingView's Pine editor, save the
indicator, then create a webhook alert with `alert() any alert() function call`.
"""
from __future__ import annotations


def generate_pine_watchlist_alert(tickers: list[str]) -> str:
    """Return a self-contained Pine Script v6 indicator source string.

    The script computes the 5 triggers on whatever symbol it's loaded on. The
    `tickers` list is rendered into the source as a constant array so users can
    see which symbols this script was generated for; alerts fire on a
    per-chart basis (TradingView limitation — one indicator instance per chart).
    """
    clean = [str(t).strip().upper() for t in tickers if str(t).strip()]
    # de-dupe while preserving order
    seen = set()
    deduped = []
    for t in clean:
        if t not in seen:
            seen.add(t)
            deduped.append(t)

    arr_literal = ", ".join(f'"{t}"' for t in deduped) if deduped else '""'
    ticker_count = len(deduped)
    ticker_preview = ", ".join(deduped[:10]) + ("…" if ticker_count > 10 else "")

    return f"""// @version=6
// =========================================================================
// USAGE:
//   1. Open TradingView, switch to the Pine Editor (bottom panel).
//   2. Paste this entire script and click "Add to chart".
//   3. The 5 trigger booleans are computed on the chart's symbol/timeframe.
//      Use 1D timeframe to match the Python screener.
//   4. To wire up alerts:
//        - Right-click on the chart → "Add alert".
//        - Condition: "TV Trigger Layer" → pick one of the alertcondition()
//          entries below (Squeeze Fire, MACD Bull Cross, RSI Bullish Div,
//          Volume Surge, or Any Trigger).
//        - Set "Options" → "Once Per Bar Close" (avoid intrabar flicker).
//        - Webhook URL: paste your webhook receiver (Discord/Slack/N8N/etc.).
//        - Message: defaults below already include {{{{ticker}}}} and {{{{close}}}}.
//   5. Repeat per symbol. TradingView fires one alert per indicator instance
//      per chart. For multi-symbol monitoring, use a TradingView screener
//      preset alongside this indicator.
//
// GENERATED for {ticker_count} ticker(s): {ticker_preview}
// =========================================================================
indicator("TV Trigger Layer", overlay=false, max_labels_count=50)

// --- The hardcoded ticker watchlist (informational; alerts run per chart) ---
var string[] watchlist = array.from({arr_literal})

// =========================================================================
// 1) TTM-style Squeeze: Bollinger Bands inside Keltner Channels
// =========================================================================
bbLength = input.int(20, "BB Length")
bbMult   = input.float(2.0, "BB Mult")
kcLength = input.int(20, "KC Length")
kcMult   = input.float(1.5, "KC Mult (ATR)")

basis = ta.sma(close, bbLength)
dev   = bbMult * ta.stdev(close, bbLength)
bbUpper = basis + dev
bbLower = basis - dev

emaBasis = ta.ema(close, kcLength)
atrVal   = ta.atr(kcLength)
kcUpper  = emaBasis + kcMult * atrVal
kcLower  = emaBasis - kcMult * atrVal

squeezeOn = (bbUpper < kcUpper) and (bbLower > kcLower)

// =========================================================================
// 2) Squeeze Fire — squeeze was on last bar, off this bar, with positive mom
// =========================================================================
momLength = input.int(20, "Momentum Length")
highestHigh = ta.highest(high, momLength)
lowestLow   = ta.lowest(low,  momLength)
midRange    = (highestHigh + lowestLow) / 2.0
midPrice    = (midRange + ta.sma(close, momLength)) / 2.0
momVal      = ta.linreg(close - midPrice, momLength, 0)

squeezeFire = squeezeOn[1] and not squeezeOn and momVal > 0

// =========================================================================
// 3) MACD Bullish Cross (last 3 bars) AND price above 200D MA
// =========================================================================
[macdLine, signalLine, macdHist] = ta.macd(close, 12, 26, 9)
ma200 = ta.sma(close, 200)

bullCrossNow   = ta.crossover(macdHist, 0)
bullCrossLast3 = bullCrossNow or bullCrossNow[1] or bullCrossNow[2]
macdBullCross  = bullCrossLast3 and close > ma200

// =========================================================================
// 4) RSI hidden bullish divergence over last 30 bars
//    Price makes higher low while RSI makes lower low.
// =========================================================================
rsiLen = input.int(14, "RSI Length")
rsiVal = ta.rsi(close, rsiLen)

// Simple swing-low detector: bar[i] < bar[i-2] AND bar[i] < bar[i+2].
// Pine evaluates left-to-right; checking offset 2 means the swing point is
// confirmed 2 bars later. We compare the two most recent confirmed swing lows.
isSwingLow(series float src) =>
    src[2] < src[4] and src[2] < src[0]

priceSwing = isSwingLow(low)
rsiSwing   = isSwingLow(rsiVal)

var float lastPriceLow = na
var float prevPriceLow = na
var float lastRsiLow   = na
var float prevRsiLow   = na
var int   lastPriceBar = na
var int   lastRsiBar   = na

if priceSwing
    prevPriceLow := lastPriceLow
    lastPriceLow := low[2]
    lastPriceBar := bar_index - 2
if rsiSwing
    prevRsiLow := lastRsiLow
    lastRsiLow := rsiVal[2]
    lastRsiBar := bar_index - 2

withinWindow = not na(lastPriceBar) and not na(lastRsiBar)
    and (bar_index - lastPriceBar) <= 30 and (bar_index - lastRsiBar) <= 30

rsiBullDivergence = withinWindow
    and not na(prevPriceLow) and not na(prevRsiLow)
    and lastPriceLow > prevPriceLow
    and lastRsiLow   < prevRsiLow

// =========================================================================
// 5) Volume surge: today vol > 1.5 * 20-bar avg AND close > prior day's high
// =========================================================================
volAvg20 = ta.sma(volume, 20)
volSurge = not na(volume) and not na(volAvg20[1]) and volAvg20[1] > 0
    and volume > 1.5 * volAvg20[1]
    and close > high[1]

// =========================================================================
// Plots for visual confirmation
// =========================================================================
plot(squeezeOn ? 1 : 0, "Squeeze On", color=color.new(color.orange, 50), style=plot.style_columns)
plotshape(squeezeFire,        title="Squeeze Fire",   location=location.bottom, style=shape.triangleup,   color=color.new(#F59E0B, 0), size=size.small)
plotshape(macdBullCross,      title="MACD Bull Cross",location=location.bottom, style=shape.triangleup,   color=color.new(#22C55E, 0), size=size.small)
plotshape(rsiBullDivergence,  title="RSI Bull Div",   location=location.bottom, style=shape.triangleup,   color=color.new(#22D3EE, 0), size=size.small)
plotshape(volSurge,           title="Vol Surge",      location=location.bottom, style=shape.triangleup,   color=color.new(#A78BFA, 0), size=size.small)

// =========================================================================
// Alerts — use {{{{ticker}}}} and {{{{close}}}} placeholders in messages.
// =========================================================================
alertcondition(squeezeFire,
     title="Squeeze Fire (bullish release)",
     message="🔥 {{{{ticker}}}} squeeze FIRED bullish @ {{{{close}}}}")

alertcondition(squeezeOn,
     title="Squeeze On (compression)",
     message="⏳ {{{{ticker}}}} is in a squeeze (compression) @ {{{{close}}}}")

alertcondition(macdBullCross,
     title="MACD Bull Cross (above 200D MA)",
     message="📈 {{{{ticker}}}} MACD bullish cross above 200D @ {{{{close}}}}")

alertcondition(rsiBullDivergence,
     title="RSI Hidden Bullish Divergence",
     message="↗ {{{{ticker}}}} RSI hidden bullish divergence @ {{{{close}}}}")

alertcondition(volSurge,
     title="Volume Surge Breakout",
     message="📊 {{{{ticker}}}} volume surge breakout above prior high @ {{{{close}}}}")

anyTrigger = squeezeFire or macdBullCross or rsiBullDivergence or volSurge
alertcondition(anyTrigger,
     title="Any TV Trigger",
     message="⚡ {{{{ticker}}}} TV Trigger fired @ {{{{close}}}}")
"""
