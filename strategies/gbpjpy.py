"""
GBPJPY Confluence Strategy — based on KJ_GBPJPY_Strategy.md
Top-down: Monthly R/S flip → Weekly bullish intent → Daily validation →
          4H reversal pattern → 1H entry zone
Minimum 5/10 confluences required before flagging a setup.
"""

import numpy as np
import pandas as pd

from .smc import _pivot_highs, _pivot_lows, calculate_vwap, get_trendlines


SYMBOL = "GBPJPY"


# ── Step 1 — Monthly: R/S Flip Zone ─────────────────────────────────────────

def check_monthly_rs_flip(df_monthly: pd.DataFrame) -> dict:
    """
    Look for prior resistance that price broke through and is now retesting as support.
    Signal: current price is pulling back into a former resistance level.
    """
    ph = _pivot_highs(df_monthly, left=2, right=2)
    if len(ph) < 2:
        return {"found": False, "reason": "Not enough monthly pivot highs"}

    current_price = df_monthly["close"].iloc[-1]

    # Find a prior resistance that price broke above and is now near
    for pivot in reversed(ph[:-1]):
        level = pivot["price"]
        broke_above = any(
            df_monthly["close"].iloc[i] > level
            for i in range(pivot["index"] + 1, len(df_monthly))
        )
        near_level = abs(current_price - level) / level < 0.02  # within 2%
        price_above = current_price > level * 0.995  # price holding above

        if broke_above and price_above:
            return {
                "found": True,
                "zone_level": round(level, 3),
                "current_price": round(current_price, 3),
                "near_level": near_level,
                "signal": "Price retesting former resistance as support (R/S flip)",
            }

    return {"found": False, "reason": "No R/S flip zone identified on monthly"}


# ── Step 2 — Weekly: Bullish Intent ─────────────────────────────────────────

def check_weekly_bullish_intent(df_weekly: pd.DataFrame, zone_level: float) -> dict:
    """
    Look for 2+ bullish closes with wick rejections at or near the monthly zone.
    Wick rejection = lower wick > body size (buyers defending the level).
    """
    recent = df_weekly.tail(8)
    rejection_count = 0
    bullish_close_count = 0

    for _, row in recent.iterrows():
        near_zone = abs(row["low"] - zone_level) / zone_level < 0.015
        if not near_zone:
            continue

        lower_wick = row["open"] - row["low"] if row["close"] > row["open"] else row["close"] - row["low"]
        body = abs(row["close"] - row["open"])
        has_rejection_wick = lower_wick > body * 0.5

        if has_rejection_wick:
            rejection_count += 1
        if row["close"] > row["open"]:
            bullish_close_count += 1

    score = min(rejection_count + bullish_close_count, 3)
    return {
        "rejection_wicks": rejection_count,
        "bullish_closes": bullish_close_count,
        "score": score,
        "signal": "Buyers defending zone" if score >= 2 else "Weak weekly confirmation",
    }


# ── Step 3 — Daily: Zone Validation ─────────────────────────────────────────

def check_daily_validation(df_daily: pd.DataFrame, zone_level: float) -> dict:
    """
    3+ rejection closes off zone + shrinking candle size (ATR decay) + multi-day consolidation.
    """
    recent = df_daily.tail(20)

    # Rejection closes: candle closed above zone_level after testing below it
    rejection_closes = 0
    for _, row in recent.iterrows():
        if row["low"] < zone_level and row["close"] > zone_level:
            rejection_closes += 1

    # Candle size decay: compare ATR of last 5 vs prior 10 (shrinking = accumulation)
    atr_recent = (recent["high"] - recent["low"]).tail(5).mean()
    atr_prior = (recent["high"] - recent["low"]).head(10).mean()
    size_decreasing = atr_recent < atr_prior * 0.85

    # Consolidation: price range of last 5 days < 30% of prior 10-day range
    range_recent = recent["high"].tail(5).max() - recent["low"].tail(5).min()
    range_prior = recent["high"].head(10).max() - recent["low"].head(10).min()
    consolidating = range_recent < range_prior * 0.35

    score = sum([rejection_closes >= 3, size_decreasing, consolidating])
    return {
        "rejection_closes": rejection_closes,
        "candle_size_decreasing": size_decreasing,
        "consolidating": consolidating,
        "atr_recent": round(atr_recent, 3),
        "atr_prior": round(atr_prior, 3),
        "score": score,
        "signal": "High-probability base" if score >= 2 else "Zone not yet validated on daily",
    }


# ── Step 4 — 4H: Reversal Pattern ───────────────────────────────────────────

def check_4h_reversal_pattern(df_4h: pd.DataFrame, zone_level: float) -> dict:
    """
    Scan for Inverse H&S, Double Bottom, or Morning Star inside the zone.
    Inverse H&S: 3 lows where middle is lowest + neckline formed.
    Double Bottom: 2 lows at similar levels with bounce between.
    """
    pl = _pivot_lows(df_4h, left=3, right=3)
    zone_pivots = [p for p in pl if abs(p["price"] - zone_level) / zone_level < 0.02]

    # Inverse Head & Shoulders
    if len(zone_pivots) >= 3:
        ls, head, rs = zone_pivots[-3], zone_pivots[-2], zone_pivots[-1]
        if (
            head["price"] < ls["price"]
            and head["price"] < rs["price"]
            and rs["price"] > head["price"]
        ):
            neckline = max(
                df_4h["high"].iloc[ls["index"]: head["index"]].max(),
                df_4h["high"].iloc[head["index"]: rs["index"]].max(),
            )
            current_price = df_4h["close"].iloc[-1]
            neckline_broken = current_price > neckline
            return {
                "pattern": "Inverse Head & Shoulders",
                "left_shoulder": round(ls["price"], 3),
                "head": round(head["price"], 3),
                "right_shoulder": round(rs["price"], 3),
                "neckline": round(neckline, 3),
                "neckline_broken": neckline_broken,
                "entry_signal": "Right shoulder low — enter on zone retest" if not neckline_broken else "Neckline broken — wait for retest",
                "found": True,
            }

    # Double Bottom
    if len(zone_pivots) >= 2:
        b1, b2 = zone_pivots[-2], zone_pivots[-1]
        price_similarity = abs(b1["price"] - b2["price"]) / b1["price"] < 0.005
        bounce_between = df_4h["high"].iloc[b1["index"]: b2["index"]].max() > b1["price"] * 1.002

        if price_similarity and bounce_between:
            return {
                "pattern": "Double Bottom",
                "bottom_1": round(b1["price"], 3),
                "bottom_2": round(b2["price"], 3),
                "found": True,
                "entry_signal": "Enter on zone retest or neckline break",
            }

    # Morning Star (last 3 candles: bearish large → small doji → bullish large)
    if len(df_4h) >= 3:
        c1, c2, c3 = df_4h.iloc[-3], df_4h.iloc[-2], df_4h.iloc[-1]
        avg_body = (df_4h["close"] - df_4h["open"]).abs().mean()
        is_morning_star = (
            c1["close"] < c1["open"]
            and abs(c1["close"] - c1["open"]) > avg_body
            and abs(c2["close"] - c2["open"]) < avg_body * 0.4
            and c3["close"] > c3["open"]
            and abs(c3["close"] - c3["open"]) > avg_body
        )
        if is_morning_star:
            return {
                "pattern": "Morning Star",
                "found": True,
                "entry_signal": "Bullish reversal candle forming — enter on confirmation",
            }

    return {"pattern": None, "found": False, "entry_signal": "No 4H reversal pattern detected yet"}


# ── Step 5 — 1H: Entry Zone ──────────────────────────────────────────────────

def get_1h_entry(df_1h: pd.DataFrame, zone_level: float, pattern_info: dict) -> dict:
    """
    Entry = right shoulder low or zone retest on 1H.
    Scale: largest lots at zone bottom, smaller higher up.
    SL = below lowest wick of full consolidation.
    """
    pl = _pivot_lows(df_1h, left=2, right=2)
    zone_pivots = [p for p in pl if abs(p["price"] - zone_level) / zone_level < 0.025]

    current_price = df_1h["close"].iloc[-1]
    zone_low = df_1h["low"].tail(50).min()
    sl = round(zone_low * 0.9995, 3)

    if zone_pivots:
        entry_zone_bottom = round(min(p["price"] for p in zone_pivots[-3:]), 3)
        entry_zone_top = round(max(p["price"] for p in zone_pivots[-3:]), 3)
        risk_pips = round((current_price - sl) * 100, 1)
    else:
        entry_zone_bottom = round(zone_level * 0.998, 3)
        entry_zone_top = round(zone_level * 1.002, 3)
        risk_pips = round((current_price - sl) * 100, 1)

    position_in_zone = "bottom" if current_price < zone_level * 1.005 else "upper" if current_price > zone_level * 1.015 else "mid"
    lot_guidance = {"bottom": "Largest lot size", "mid": "Medium lot size", "upper": "Smallest lots only"}

    # VWAP and trendline from 1H
    vwap = calculate_vwap(df_1h)
    vwap_level = round(vwap.iloc[-1], 3)
    trendlines = get_trendlines(df_1h)

    return {
        "entry_zone": f"{entry_zone_bottom} — {entry_zone_top}",
        "sl": sl,
        "risk_pips": risk_pips,
        "position_in_zone": position_in_zone,
        "lot_guidance": lot_guidance[position_in_zone],
        "current_price": round(current_price, 3),
        "vwap_1h": vwap_level,
        "price_above_vwap": current_price > vwap_level,
        "trendlines": trendlines,
        "rule": "Buy LOW in zone. Never chase price up.",
    }


# ── Full GBPJPY Confluence Check ─────────────────────────────────────────────

def _resample_daily_to(df_daily: pd.DataFrame, freq: str) -> pd.DataFrame:
    """Resample 1D candles to weekly or monthly using pandas period resampling."""
    df = df_daily.copy()
    df = df.set_index("time")
    rule = "W" if freq == "weekly" else "ME"
    resampled = df.resample(rule).agg({
        "open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"
    }).dropna().reset_index()
    resampled.rename(columns={"time": "time"}, inplace=True)
    return resampled


def run_gbpjpy_confluence_check(client) -> dict:
    """
    Full top-down confluence check for GBPJPY.
    Uses 1D data resampled to monthly/weekly (TradeLocker doesn't support 1M/1W).
    Minimum 5/10 score required to flag setup.
    """
    # Fetch only supported resolutions — resample up for monthly/weekly
    df_daily_long = client.get_candles(SYMBOL, "1D", bars=500)
    df_monthly = _resample_daily_to(df_daily_long, "monthly")
    df_weekly = _resample_daily_to(df_daily_long, "weekly")
    df_daily = df_daily_long.tail(60).reset_index(drop=True)
    df_4h = client.get_candles(SYMBOL, "4H", bars=120)
    df_1h = client.get_candles(SYMBOL, "1H", bars=200)

    # Step 1: Monthly R/S flip
    monthly = check_monthly_rs_flip(df_monthly)
    zone_level = monthly.get("zone_level", df_daily["close"].iloc[-1])

    # Step 2: Weekly bullish intent
    weekly = check_weekly_bullish_intent(df_weekly, zone_level)

    # Step 3: Daily validation
    daily = check_daily_validation(df_daily, zone_level)

    # Step 4: 4H reversal pattern
    reversal = check_4h_reversal_pattern(df_4h, zone_level)

    # Step 5: 1H entry
    entry = get_1h_entry(df_1h, zone_level, reversal)

    # Confluence scoring (10 possible points from KJ checklist)
    checklist = {
        "monthly_rs_flip": monthly["found"],
        "weekly_wick_rejections": weekly["rejection_wicks"] >= 2,
        "weekly_bullish_closes": weekly["bullish_closes"] >= 1,
        "daily_rejection_closes": daily["rejection_closes"] >= 3,
        "daily_candle_size_decreasing": daily["candle_size_decreasing"],
        "daily_consolidation": daily["consolidating"],
        "4h_reversal_pattern": reversal["found"],
        "1h_entry_in_zone": entry["position_in_zone"] in ("bottom", "mid"),
        "vwap_aligned": entry["price_above_vwap"],
        "trendline_confluence": bool(entry["trendlines"].get("support_trendline", {}).get("price_near_line")),
    }

    score = sum(checklist.values())
    setup_valid = score >= 5

    return {
        "symbol": SYMBOL,
        "confluence_score": f"{score}/10",
        "setup_valid": setup_valid,
        "checklist": checklist,
        "monthly": monthly,
        "weekly": weekly,
        "daily": daily,
        "4h_reversal": reversal,
        "1h_entry": entry,
        "message": (
            f"SETUP CONFIRMED — {score}/10 confluences. {reversal.get('entry_signal', '')}"
            if setup_valid
            else f"Not ready — only {score}/10 confluences (need 5+). Keep monitoring."
        ),
    }
