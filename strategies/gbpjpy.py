"""
GBPJPY Confluence Strategy — based on KJ_GBPJPY_Strategy.md
Top-down: Monthly R/S flip → Weekly bullish intent → Daily validation →
          4H reversal pattern → 1H entry zone
Minimum 5/10 confluences required before flagging a setup.
"""

import numpy as np
import pandas as pd

from .smc import (_pivot_highs, _pivot_lows, calculate_vwap, get_trendlines,
                   find_supply_zones, find_demand_zones, get_corridor,
                   analyze_quarter_pa, detect_blown_quarter, find_fvg)


SYMBOL = "GBPJPY"


# ── Step 1 — Monthly: R/S Flip Zone ─────────────────────────────────────────

def check_monthly_rs_flip(df_monthly: pd.DataFrame, direction: str = "LONG") -> dict:
    """
    LONG: prior resistance broke above, now retesting as support.
    SHORT: prior support broke below, now retesting as resistance.
    """
    current_price = df_monthly["close"].iloc[-1]

    if direction == "LONG":
        ph = _pivot_highs(df_monthly, left=2, right=2)
        if len(ph) < 2:
            return {"found": False, "reason": "Not enough monthly pivot highs"}
        for pivot in reversed(ph[:-1]):
            level = pivot["price"]
            broke_above = any(df_monthly["close"].iloc[i] > level
                              for i in range(pivot["index"] + 1, len(df_monthly)))
            if broke_above and current_price > level * 0.995:
                return {"found": True, "zone_level": round(level, 3),
                        "current_price": round(current_price, 3),
                        "near_level": abs(current_price - level) / level < 0.02,
                        "signal": "Price retesting former resistance as support (R/S flip)"}
        return {"found": False, "reason": "No R/S flip zone on monthly (LONG)"}
    else:
        pl = _pivot_lows(df_monthly, left=2, right=2)
        if len(pl) < 2:
            return {"found": False, "reason": "Not enough monthly pivot lows"}
        for pivot in reversed(pl[:-1]):
            level = pivot["price"]
            broke_below = any(df_monthly["close"].iloc[i] < level
                              for i in range(pivot["index"] + 1, len(df_monthly)))
            if broke_below and current_price < level * 1.005:
                return {"found": True, "zone_level": round(level, 3),
                        "current_price": round(current_price, 3),
                        "near_level": abs(current_price - level) / level < 0.02,
                        "signal": "Price retesting former support as resistance (S/R flip SHORT)"}
        return {"found": False, "reason": "No S/R flip zone on monthly (SHORT)"}


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
    TP = nearest unmitigated supply zone above current price, fallback 3:1 R:R.
    """
    pl = _pivot_lows(df_1h, left=2, right=2)
    zone_pivots = [p for p in pl if abs(p["price"] - zone_level) / zone_level < 0.025]

    current_price = df_1h["close"].iloc[-1]
    zone_low = df_1h["low"].tail(50).min()
    sl = round(zone_low * 0.9995, 3)

    if zone_pivots:
        entry_zone_bottom = round(min(p["price"] for p in zone_pivots[-3:]), 3)
        entry_zone_top = round(max(p["price"] for p in zone_pivots[-3:]), 3)
    else:
        entry_zone_bottom = round(zone_level * 0.998, 3)
        entry_zone_top = round(zone_level * 1.002, 3)

    risk_pips = round((current_price - sl) * 100, 1)
    risk = entry_zone_bottom - sl

    # TP: nearest supply zone above current price, else 3:1 R:R fallback
    supply_zones = find_supply_zones(df_1h)
    valid_supplies = [z for z in supply_zones if z["bottom"] > current_price]
    if valid_supplies:
        tp = round(valid_supplies[0]["bottom"], 3)
    else:
        tp = round(entry_zone_bottom + abs(risk) * 3, 3)

    position_in_zone = "bottom" if current_price < zone_level * 1.005 else "upper" if current_price > zone_level * 1.015 else "mid"
    lot_guidance = {
        "bottom": "Best entry — full 2% risk allocation",
        "mid": "Mid-zone — use half your intended size",
        "upper": "Top of zone — small entry only, wait for better price",
    }

    rr = round(abs(tp - entry_zone_bottom) / abs(risk), 2) if risk != 0 else 0.0

    # VWAP and trendline from 1H
    vwap = calculate_vwap(df_1h)
    vwap_level = round(vwap.iloc[-1], 3)
    trendlines = get_trendlines(df_1h)

    return {
        "entry_zone": f"{entry_zone_bottom} — {entry_zone_top}",
        "sl": sl,
        "tp": tp,
        "rr": rr,
        "risk_pips": risk_pips,
        "position_in_zone": position_in_zone,
        "lot_guidance": lot_guidance[position_in_zone],
        "current_price": round(current_price, 3),
        "vwap_1h": vwap_level,
        "price_above_vwap": current_price > vwap_level,
        "trendlines": trendlines,
        "rule": "Buy LOW in zone. Never chase price up.",
    }


# ── Candle Reaction — Final Tell ─────────────────────────────────────────────

def check_candle_reaction(df_1h: pd.DataFrame, df_4h: pd.DataFrame,
                          zone_level: float, direction: str) -> dict:
    """
    Final confirmation layer — how are candles actually behaving at the zone?
    Checks wick rejections, order block presence, body strength, TF alignment,
    and engulfing patterns. direction: 'LONG' or 'SHORT'.
    """
    # ── Wick rejection at zone (last 5 1H candles) ──
    wick_rejections = 0
    for _, c in df_1h.tail(5).iterrows():
        pin = c["low"] if direction == "LONG" else c["high"]
        if abs(pin - zone_level) / zone_level > 0.005:
            continue
        body = abs(c["close"] - c["open"])
        if direction == "LONG":
            wick = min(c["open"], c["close"]) - c["low"]
            if wick > body and c["close"] > c["open"]:
                wick_rejections += 1
        else:
            wick = c["high"] - max(c["open"], c["close"])
            if wick > body and c["close"] < c["open"]:
                wick_rejections += 1

    # ── Order block — last opposite-direction 4H candle before current move ──
    ob_found = False
    ob_zone = None
    ob_level = None
    for i in range(len(df_4h) - 1, max(len(df_4h) - 20, -1), -1):
        row = df_4h.iloc[i]
        is_opposite = (row["close"] < row["open"]) if direction == "LONG" else (row["close"] > row["open"])
        if is_opposite:
            ob_found = True
            ob_level = round(row["high"] if direction == "LONG" else row["low"], 3)
            ob_zone = f"{round(row['low'], 3)} — {round(row['high'], 3)}"
            break

    # ── Current 1H candle quality ──
    curr = df_1h.iloc[-1]
    candle_range = curr["high"] - curr["low"]
    body_size = abs(curr["close"] - curr["open"])
    body_ratio = body_size / candle_range if candle_range > 0 else 0
    body_strong = body_ratio > 0.5

    if candle_range > 0:
        if direction == "LONG":
            close_pos = (curr["close"] - curr["low"]) / candle_range
        else:
            close_pos = (curr["high"] - curr["close"]) / candle_range
    else:
        close_pos = 0
    closing_right = close_pos > 0.6

    # ── 4H + 1H alignment ──
    curr_4h = df_4h.iloc[-1]
    if direction == "LONG":
        tf_aligned = curr_4h["close"] > curr_4h["open"] and curr["close"] > curr["open"]
    else:
        tf_aligned = curr_4h["close"] < curr_4h["open"] and curr["close"] < curr["open"]

    # ── Engulfing (1H) ──
    engulfing = False
    if len(df_1h) >= 2:
        prev = df_1h.iloc[-2]
        if direction == "LONG":
            engulfing = (curr["close"] > curr["open"] and prev["close"] < prev["open"]
                         and curr["close"] > prev["open"] and curr["open"] < prev["close"])
        else:
            engulfing = (curr["close"] < curr["open"] and prev["close"] > prev["open"]
                         and curr["close"] < prev["open"] and curr["open"] > prev["close"])

    passed = sum([wick_rejections >= 1, ob_found, body_strong, closing_right, tf_aligned, engulfing])

    return {
        "wick_rejections": wick_rejections,
        "order_block_found": ob_found,
        "order_block_zone": ob_zone,
        "order_block_level": ob_level,
        "body_ratio": round(body_ratio, 2),
        "body_strong": body_strong,
        "closing_right": closing_right,
        "tf_aligned_4h_1h": tf_aligned,
        "engulfing": engulfing,
        "checks_passed": passed,
        "confirmed": passed >= 3,
        "signal": (
            f"PA confirming — {passed}/6 candle checks ✓"
            if passed >= 3
            else f"PA inconclusive — only {passed}/6 candle checks"
        ),
    }


# ── Quarter Level Theory (GBPJPY-specific) ───────────────────────────────────

def _nearest_quarter_level(price: float) -> float:
    """Return the nearest .00/.25/.50/.75 quarter level to the given price."""
    big = int(price)
    candidates = [big + q for q in (0.0, 0.25, 0.50, 0.75, 1.00)]
    return min(candidates, key=lambda q: abs(price - q))


def check_quarter_level_confluence(zone_level: float, tp_level: float) -> dict:
    """
    GBPJPY quarter theory: institutional orders cluster at .00/.25/.50/.75 levels.
    Zone check: key level within 10 pips (0.10) of a quarter level.
    TP check: take profit within 15 pips (0.15) of the next quarter level up.
    """
    zone_q = _nearest_quarter_level(zone_level)
    zone_dist = abs(zone_level - zone_q)
    zone_at_quarter = zone_dist <= 0.10

    tp_q = _nearest_quarter_level(tp_level)
    tp_dist = abs(tp_level - tp_q)
    tp_at_quarter = tp_dist <= 0.15

    return {
        "zone_at_quarter": zone_at_quarter,
        "nearest_zone_quarter": round(zone_q, 2),
        "zone_distance_pips": round(zone_dist * 100, 1),
        "tp_at_quarter": tp_at_quarter,
        "nearest_tp_quarter": round(tp_q, 2),
        "tp_distance_pips": round(tp_dist * 100, 1),
    }


# ── FVG Proximity Check ───────────────────────────────────────────────────────

def _fvg_near_zone(fvgs: list, zone_level: float, direction: str,
                   interval: float = 0.25) -> dict:
    """Most recent FVG whose midpoint is within one quarter-interval of zone_level."""
    for fvg in reversed(fvgs):
        if direction == "LONG" and fvg["type"] == "bullish":
            if abs(fvg["fvb"] - zone_level) <= interval:
                return {"found": True, "type": "bullish",
                        "zone": f"{fvg['fvl']} — {fvg['fvh']}",
                        "midpoint": fvg["fvb"], "time": fvg["time"]}
        elif direction == "SHORT" and fvg["type"] == "bearish":
            if abs(fvg["fvb"] - zone_level) <= interval:
                return {"found": True, "type": "bearish",
                        "zone": f"{fvg['fvl']} — {fvg['fvh']}",
                        "midpoint": fvg["fvb"], "time": fvg["time"]}
    return {"found": False}


# ── 15m CHoCH / BOS ───────────────────────────────────────────────────────────

def detect_choch_bos(df_15m: pd.DataFrame, direction: str) -> dict:
    """
    CHoCH (Change of Character): first break of opposing swing structure on 15m — signals shift.
    BOS (Break of Structure): each new swing confirms continuation.
    SHORT: looks for a close below a prior swing low.
    LONG: looks for a close above a prior swing high.
    """
    if df_15m is None or len(df_15m) < 10:
        return {"confirmed": False, "type": None, "signal": "Insufficient 15m data"}

    current_price = df_15m["close"].iloc[-1]

    if direction == "SHORT":
        pl = _pivot_lows(df_15m, left=2, right=2)
        if len(pl) < 2:
            return {"confirmed": False, "type": None, "signal": "No 15m swing lows for CHoCH"}
        prev_low = pl[-2]["price"]
        last_low = pl[-1]["price"]
        bos = last_low < prev_low
        choch = current_price < prev_low and not bos
        confirmed = bos or choch
        structure_type = "CHoCH" if choch else ("BOS" if bos else None)
        broken_level = round(prev_low, 3)
    else:
        ph = _pivot_highs(df_15m, left=2, right=2)
        if len(ph) < 2:
            return {"confirmed": False, "type": None, "signal": "No 15m swing highs for CHoCH"}
        prev_high = ph[-2]["price"]
        last_high = ph[-1]["price"]
        bos = last_high > prev_high
        choch = current_price > prev_high and not bos
        confirmed = bos or choch
        structure_type = "CHoCH" if choch else ("BOS" if bos else None)
        broken_level = round(prev_high, 3)

    return {
        "confirmed": confirmed,
        "type": structure_type,
        "broken_level": broken_level,
        "current_price": round(current_price, 3),
        "signal": (f"15m {structure_type} @ {broken_level} ✓"
                   if confirmed else f"No 15m structure break ({direction})"),
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


def run_gbpjpy_confluence_check(client, direction: str = "LONG") -> dict:
    """
    Full top-down confluence check for GBPJPY.
    direction: 'LONG' or 'SHORT' — all checks flip accordingly.

    14-item tiered checklist:
      CRITICAL (5): monthly flip, 4H reversal, blown quarter, candle reaction, order block
      HIGH (4):     quarter level strength, weekly wicks, FVG near zone, 15m CHoCH/BOS
      MEDIUM (5):   VWAP, trendline, daily rejection closes, daily compression, corridor position
    Threshold: 8/14
    """
    df_daily_long = client.get_candles(SYMBOL, "1D", bars=500)
    df_monthly = _resample_daily_to(df_daily_long, "monthly")
    df_weekly = _resample_daily_to(df_daily_long, "weekly")
    df_daily = df_daily_long.tail(60).reset_index(drop=True)
    df_4h = client.get_candles(SYMBOL, "4H", bars=120)
    df_1h = client.get_candles(SYMBOL, "1H", bars=200)
    try:
        df_15m = client.get_candles(SYMBOL, "15m", bars=100)
    except Exception:
        df_15m = None

    monthly = check_monthly_rs_flip(df_monthly, direction)
    zone_level = monthly.get("zone_level", df_daily["close"].iloc[-1])

    weekly = _check_weekly_intent(df_weekly, zone_level, direction)
    daily = check_daily_validation(df_daily, zone_level)

    if direction == "LONG":
        reversal = check_4h_reversal_pattern(df_4h, zone_level)
        entry = get_1h_entry(df_1h, zone_level, reversal)
        entry_vwap_ok = entry["price_above_vwap"]
        entry_tl_ok = bool(entry["trendlines"].get("support_trendline", {}).get("price_near_line"))
    else:
        reversal = _check_4h_reversal_generic(df_4h, zone_level, direction)
        entry = _get_1h_entry_generic(df_1h, zone_level, reversal, direction)
        entry_vwap_ok = entry["vwap_aligned"]
        entry_tl_ok = entry["trendline_near"]

    quarter = check_quarter_level_confluence(zone_level, entry["tp"])
    current_price = df_1h["close"].iloc[-1]
    corridor = get_corridor(SYMBOL, current_price)

    qpa_profiles = analyze_quarter_pa(SYMBOL, df_1h, current_price)
    floor_profile = qpa_profiles.get(corridor["floor"], {})
    ceil_profile = qpa_profiles.get(corridor["ceiling"], {})
    floor_reaction = floor_profile.get("reaction", "UNTESTED")
    ceil_reaction = ceil_profile.get("reaction", "UNTESTED")

    if direction == "LONG":
        quarter_level_strong = floor_reaction in ("STRONG S/R", "S/R", "TRAP ZONE")
        quarter_path_clear = ceil_reaction not in ("STRONG S/R", "S/R")
        corridor_position_ok = corridor["zone"] in ("base", "lower")
        blown_directional = blown_q_dir = "UP"
    else:
        quarter_level_strong = ceil_reaction in ("STRONG S/R", "S/R", "TRAP ZONE")
        quarter_path_clear = floor_reaction not in ("STRONG S/R", "S/R")
        corridor_position_ok = corridor["zone"] in ("upper", "top")
        blown_directional = blown_q_dir = "DOWN"

    blown_q = detect_blown_quarter(SYMBOL, df_1h)
    blown_momentum = blown_q.get("detected", False) and blown_q.get("direction") == blown_q_dir

    candle_pa = check_candle_reaction(df_1h, df_4h, zone_level, direction)

    fvgs_1h = find_fvg(df_1h)
    fvg_check = _fvg_near_zone(fvgs_1h, zone_level, direction)

    choch = detect_choch_bos(df_15m, direction)

    daily_compression = daily["candle_size_decreasing"] or daily["consolidating"]

    checklist = {
        # ── CRITICAL ──────────────────────────────────────────────────────────
        "monthly_rs_flip":         monthly["found"],
        "4h_reversal_pattern":     reversal["found"],
        "blown_quarter_momentum":  blown_momentum,
        "candle_reaction_at_zone": candle_pa["wick_rejections"] >= 1 or candle_pa["engulfing"],
        "order_block_confirmed":   candle_pa["order_block_found"] and candle_pa["tf_aligned_4h_1h"],
        # ── HIGH ──────────────────────────────────────────────────────────────
        "quarter_level_strong":    quarter_level_strong,
        "weekly_wick_rejections":  weekly["rejection_wicks"] >= 2,
        "fvg_near_zone":           fvg_check["found"],
        "choch_bos_15m":           choch["confirmed"],
        # ── MEDIUM ────────────────────────────────────────────────────────────
        "vwap_aligned":            entry_vwap_ok,
        "trendline_confluence":    entry_tl_ok,
        "daily_rejection_closes":  daily["rejection_closes"] >= 3,
        "daily_compression":       daily_compression,
        "corridor_position":       corridor_position_ok,
    }

    score = sum(checklist.values())
    setup_valid = score >= 8

    critical_score = sum(checklist[k] for k in
        ["monthly_rs_flip", "4h_reversal_pattern", "blown_quarter_momentum",
         "candle_reaction_at_zone", "order_block_confirmed"])
    high_score = sum(checklist[k] for k in
        ["quarter_level_strong", "weekly_wick_rejections", "fvg_near_zone", "choch_bos_15m"])

    return {
        "symbol": SYMBOL,
        "direction": direction,
        "confluence_score": f"{score}/14",
        "critical_score": f"{critical_score}/5",
        "high_score": f"{high_score}/4",
        "setup_valid": setup_valid,
        "checklist": checklist,
        "checklist_tiers": {
            "CRITICAL": ["monthly_rs_flip", "4h_reversal_pattern", "blown_quarter_momentum",
                         "candle_reaction_at_zone", "order_block_confirmed"],
            "HIGH":     ["quarter_level_strong", "weekly_wick_rejections", "fvg_near_zone", "choch_bos_15m"],
            "MEDIUM":   ["vwap_aligned", "trendline_confluence", "daily_rejection_closes",
                         "daily_compression", "corridor_position"],
        },
        "monthly": monthly,
        "weekly": weekly,
        "daily": daily,
        "4h_reversal": reversal,
        "1h_entry": entry,
        "quarter": quarter,
        "corridor": corridor,
        "qpa_profiles": qpa_profiles,
        "blown_quarter": blown_q,
        "candle_reaction": candle_pa,
        "fvg": fvg_check,
        "choch_bos": choch,
        "message": (
            f"SETUP CONFIRMED ({direction}) — {score}/14 | Critical {critical_score}/5 | "
            f"FVG: {'✓' if fvg_check['found'] else '✗'} | CHoCH: {'✓' if choch['confirmed'] else '✗'} | "
            f"{reversal.get('entry_signal', '')}"
            if setup_valid
            else f"Not ready ({direction}) — {score}/14 (need 8+). Critical: {critical_score}/5. "
                 f"Missing: {', '.join(k for k, v in checklist.items() if not v)}"
        ),
    }


# ══════════════════════════════════════════════════════════════════════════════
# Universal KJ Confluence — works on ANY symbol, both LONG and SHORT
# ══════════════════════════════════════════════════════════════════════════════

def _find_key_level(df_daily_full: "pd.DataFrame", direction: str) -> dict:
    """Find the most significant demand (LONG) or supply (SHORT) zone on daily TF."""
    current_price = df_daily_full["close"].iloc[-1]
    if direction == "LONG":
        zones = find_demand_zones(df_daily_full, lookback=500)
        below = [z for z in zones if z["top"] < current_price]
        if below:
            best = min(below, key=lambda z: current_price - z["top"])
            mid = round((best["top"] + best["bottom"]) / 2, 5)
            return {"found": True, "zone_level": mid, "zone_top": best["top"],
                    "zone_bottom": best["bottom"], "current_price": round(current_price, 5),
                    "signal": f"Daily demand: {best['bottom']}–{best['top']}"}
    else:
        zones = find_supply_zones(df_daily_full, lookback=500)
        above = [z for z in zones if z["bottom"] > current_price]
        if above:
            best = min(above, key=lambda z: z["bottom"] - current_price)
            mid = round((best["top"] + best["bottom"]) / 2, 5)
            return {"found": True, "zone_level": mid, "zone_top": best["top"],
                    "zone_bottom": best["bottom"], "current_price": round(current_price, 5),
                    "signal": f"Daily supply: {best['bottom']}–{best['top']}"}
    return {"found": False, "zone_level": current_price, "reason": f"No {direction} zone on daily"}


def _check_weekly_intent(df_weekly: "pd.DataFrame", zone_level: float, direction: str) -> dict:
    recent = df_weekly.tail(8)
    rejection_count = 0
    directional_closes = 0
    for _, row in recent.iterrows():
        ref = row["low"] if direction == "LONG" else row["high"]
        if abs(ref - zone_level) / zone_level > 0.02:
            continue
        body = abs(row["close"] - row["open"])
        if direction == "LONG":
            wick = (row["open"] if row["close"] > row["open"] else row["close"]) - row["low"]
            if wick > body * 0.5:
                rejection_count += 1
            if row["close"] > row["open"]:
                directional_closes += 1
        else:
            wick = row["high"] - (row["open"] if row["close"] < row["open"] else row["close"])
            if wick > body * 0.5:
                rejection_count += 1
            if row["close"] < row["open"]:
                directional_closes += 1
    score = min(rejection_count + directional_closes, 3)
    return {
        "rejection_wicks": rejection_count,
        "directional_closes": directional_closes,
        "score": score,
        "signal": f"{'Buyers' if direction == 'LONG' else 'Sellers'} defending zone" if score >= 2 else "Weak weekly confirmation",
    }


def _check_daily_validation_generic(df_daily: "pd.DataFrame", zone_level: float, direction: str) -> dict:
    recent = df_daily.tail(20)
    rejection_closes = 0
    for _, row in recent.iterrows():
        if direction == "LONG" and row["low"] < zone_level and row["close"] > zone_level:
            rejection_closes += 1
        elif direction == "SHORT" and row["high"] > zone_level and row["close"] < zone_level:
            rejection_closes += 1
    atr_recent = (recent["high"] - recent["low"]).tail(5).mean()
    atr_prior = (recent["high"] - recent["low"]).head(10).mean()
    size_decreasing = atr_recent < atr_prior * 0.85
    range_recent = recent["high"].tail(5).max() - recent["low"].tail(5).min()
    range_prior = recent["high"].head(10).max() - recent["low"].head(10).min()
    consolidating = range_recent < range_prior * 0.35
    score = sum([rejection_closes >= 3, size_decreasing, consolidating])
    return {
        "rejection_closes": rejection_closes,
        "candle_size_decreasing": size_decreasing,
        "consolidating": consolidating,
        "score": score,
        "signal": "High-probability base" if score >= 2 else "Zone not yet validated",
    }


def _check_4h_reversal_generic(df_4h: "pd.DataFrame", zone_level: float, direction: str) -> dict:
    if direction == "LONG":
        zone_pivots = [p for p in _pivot_lows(df_4h, left=3, right=3)
                       if abs(p["price"] - zone_level) / zone_level < 0.025]
        if len(zone_pivots) >= 3:
            ls, head, rs = zone_pivots[-3], zone_pivots[-2], zone_pivots[-1]
            if head["price"] < ls["price"] and head["price"] < rs["price"]:
                neckline = max(
                    df_4h["high"].iloc[ls["index"]: head["index"]].max(),
                    df_4h["high"].iloc[head["index"]: rs["index"]].max(),
                )
                return {"pattern": "Inverse Head & Shoulders", "neckline": round(neckline, 5),
                        "neckline_broken": df_4h["close"].iloc[-1] > neckline,
                        "entry_signal": "Right shoulder low — enter on zone retest", "found": True}
        if len(zone_pivots) >= 2:
            b1, b2 = zone_pivots[-2], zone_pivots[-1]
            if (abs(b1["price"] - b2["price"]) / b1["price"] < 0.005
                    and df_4h["high"].iloc[b1["index"]: b2["index"]].max() > b1["price"] * 1.002):
                return {"pattern": "Double Bottom", "found": True, "entry_signal": "Enter on retest", "neckline": None}
        if len(df_4h) >= 3:
            c1, c2, c3 = df_4h.iloc[-3], df_4h.iloc[-2], df_4h.iloc[-1]
            avg_body = (df_4h["close"] - df_4h["open"]).abs().mean()
            if (c1["close"] < c1["open"] and abs(c1["close"] - c1["open"]) > avg_body
                    and abs(c2["close"] - c2["open"]) < avg_body * 0.4
                    and c3["close"] > c3["open"] and abs(c3["close"] - c3["open"]) > avg_body):
                return {"pattern": "Morning Star", "found": True, "entry_signal": "Bullish reversal forming", "neckline": None}
    else:
        zone_pivots = [p for p in _pivot_highs(df_4h, left=3, right=3)
                       if abs(p["price"] - zone_level) / zone_level < 0.025]
        if len(zone_pivots) >= 3:
            ls, head, rs = zone_pivots[-3], zone_pivots[-2], zone_pivots[-1]
            if head["price"] > ls["price"] and head["price"] > rs["price"]:
                neckline = min(
                    df_4h["low"].iloc[ls["index"]: head["index"]].min(),
                    df_4h["low"].iloc[head["index"]: rs["index"]].min(),
                )
                return {"pattern": "Head & Shoulders", "neckline": round(neckline, 5),
                        "neckline_broken": df_4h["close"].iloc[-1] < neckline,
                        "entry_signal": "Right shoulder high — enter on zone retest", "found": True}
        if len(zone_pivots) >= 2:
            t1, t2 = zone_pivots[-2], zone_pivots[-1]
            if (abs(t1["price"] - t2["price"]) / t1["price"] < 0.005
                    and df_4h["low"].iloc[t1["index"]: t2["index"]].min() < t1["price"] * 0.998):
                return {"pattern": "Double Top", "found": True, "entry_signal": "Enter on zone retest", "neckline": None}
        if len(df_4h) >= 3:
            c1, c2, c3 = df_4h.iloc[-3], df_4h.iloc[-2], df_4h.iloc[-1]
            avg_body = (df_4h["close"] - df_4h["open"]).abs().mean()
            if (c1["close"] > c1["open"] and abs(c1["close"] - c1["open"]) > avg_body
                    and abs(c2["close"] - c2["open"]) < avg_body * 0.4
                    and c3["close"] < c3["open"] and abs(c3["close"] - c3["open"]) > avg_body):
                return {"pattern": "Evening Star", "found": True, "entry_signal": "Bearish reversal forming", "neckline": None}
    return {"pattern": None, "found": False, "entry_signal": "No 4H reversal pattern detected", "neckline": None}


def _get_1h_entry_generic(df_1h: "pd.DataFrame", zone_level: float, reversal: dict, direction: str) -> dict:
    current_price = df_1h["close"].iloc[-1]
    vwap = calculate_vwap(df_1h)
    vwap_level = round(vwap.iloc[-1], 5)
    trendlines = get_trendlines(df_1h)

    if direction == "LONG":
        zone_pivots = [p for p in _pivot_lows(df_1h, left=2, right=2)
                       if abs(p["price"] - zone_level) / zone_level < 0.025]
        sl = round(df_1h["low"].tail(50).min() * 0.9995, 5)
        if zone_pivots:
            entry_bottom = round(min(p["price"] for p in zone_pivots[-3:]), 5)
            entry_top = round(max(p["price"] for p in zone_pivots[-3:]), 5)
        else:
            entry_bottom = round(zone_level * 0.998, 5)
            entry_top = round(zone_level * 1.002, 5)
        risk = entry_bottom - sl
        valid_tp = [z for z in find_supply_zones(df_1h) if z["bottom"] > current_price]
        tp = round(valid_tp[0]["bottom"], 5) if valid_tp else round(entry_bottom + abs(risk) * 3, 5)
        rr = round(abs(tp - entry_bottom) / abs(risk), 2) if risk != 0 else 0.0
        pos = "bottom" if current_price < zone_level * 1.005 else "upper" if current_price > zone_level * 1.015 else "mid"
        vwap_aligned = current_price > vwap_level
        tl_key = "support_trendline"
    else:
        zone_pivots = [p for p in _pivot_highs(df_1h, left=2, right=2)
                       if abs(p["price"] - zone_level) / zone_level < 0.025]
        sl = round(df_1h["high"].tail(50).max() * 1.0005, 5)
        if zone_pivots:
            entry_top = round(max(p["price"] for p in zone_pivots[-3:]), 5)
            entry_bottom = round(min(p["price"] for p in zone_pivots[-3:]), 5)
        else:
            entry_top = round(zone_level * 1.002, 5)
            entry_bottom = round(zone_level * 0.998, 5)
        entry_bottom, entry_top = min(entry_bottom, entry_top), max(entry_bottom, entry_top)
        risk = sl - entry_top
        valid_tp = [z for z in find_demand_zones(df_1h) if z["top"] < current_price]
        tp = round(valid_tp[-1]["top"], 5) if valid_tp else round(entry_top - abs(risk) * 3, 5)
        rr = round(abs(tp - entry_top) / abs(risk), 2) if risk != 0 else 0.0
        pos = "top" if current_price > zone_level * 0.995 else "lower" if current_price < zone_level * 0.985 else "mid"
        vwap_aligned = current_price < vwap_level
        tl_key = "resistance_trendline"

    return {
        "entry_zone": f"{entry_bottom} — {entry_top}",
        "sl": sl, "tp": tp, "rr": rr,
        "position_in_zone": pos,
        "current_price": round(current_price, 5),
        "vwap_1h": vwap_level,
        "price_above_vwap": current_price > vwap_level,
        "vwap_aligned": vwap_aligned,
        "trendlines": trendlines,
        "trendline_near": bool(trendlines.get(tl_key, {}).get("price_near_line")),
    }


def run_kj_confluence_check(symbol: str, client) -> dict:
    """
    Universal top-down KJ confluence check — any symbol, LONG or SHORT.
    Key level = nearest unmitigated daily demand (LONG) or supply (SHORT) zone.
    Returns the higher-scoring direction with 10-point checklist.
    """
    df_daily_full = client.get_candles(symbol, "1D", bars=500)
    df_monthly = _resample_daily_to(df_daily_full, "monthly")
    df_weekly = _resample_daily_to(df_daily_full, "weekly")
    df_daily = df_daily_full.tail(60).reset_index(drop=True)
    df_4h = client.get_candles(symbol, "4H", bars=120)
    df_1h = client.get_candles(symbol, "1H", bars=200)
    try:
        df_15m = client.get_candles(symbol, "15m", bars=100)
    except Exception:
        df_15m = None

    best_result = None
    best_score = -1

    for direction in ("LONG", "SHORT"):
        key = _find_key_level(df_daily_full, direction)
        zone_level = key["zone_level"]

        weekly = _check_weekly_intent(df_weekly, zone_level, direction)
        daily = _check_daily_validation_generic(df_daily, zone_level, direction)
        reversal = _check_4h_reversal_generic(df_4h, zone_level, direction)
        entry = _get_1h_entry_generic(df_1h, zone_level, reversal, direction)
        candle_pa = check_candle_reaction(df_1h, df_4h, zone_level, direction)

        fvgs_1h = find_fvg(df_1h)
        fvg_check = _fvg_near_zone(fvgs_1h, zone_level, direction)
        choch = detect_choch_bos(df_15m, direction)
        daily_compression = daily["candle_size_decreasing"] or daily["consolidating"]

        checklist = {
            # ── CRITICAL ──────────────────────────────────────────────────────
            "key_level_identified":    key["found"],
            "4h_reversal_pattern":     reversal["found"],
            "candle_reaction_at_zone": candle_pa["wick_rejections"] >= 1 or candle_pa["engulfing"],
            "order_block_confirmed":   candle_pa["order_block_found"] and candle_pa["tf_aligned_4h_1h"],
            # ── HIGH ──────────────────────────────────────────────────────────
            "weekly_wick_rejections":  weekly["rejection_wicks"] >= 2,
            "fvg_near_zone":           fvg_check["found"],
            "choch_bos_15m":           choch["confirmed"],
            # ── MEDIUM ────────────────────────────────────────────────────────
            "weekly_directional_closes": weekly["directional_closes"] >= 1,
            "daily_rejection_closes":  daily["rejection_closes"] >= 3,
            "daily_compression":       daily_compression,
            "vwap_aligned":            entry["vwap_aligned"],
            "trendline_confluence":    entry["trendline_near"],
            "1h_entry_in_zone":        entry["position_in_zone"] in ("bottom", "mid", "top"),
            "pa_signal_strength":      candle_pa["checks_passed"] >= 3,
        }
        score = sum(checklist.values())

        if score > best_score:
            best_score = score
            best_result = {
                "symbol": symbol,
                "direction": direction,
                "confluence_score": f"{score}/14",
                "raw_score": score,
                "setup_valid": score >= 7,
                "checklist": checklist,
                "key_level": key,
                "weekly": weekly,
                "daily": daily,
                "4h_reversal": reversal,
                "1h_entry": entry,
                "candle_reaction": candle_pa,
                "fvg": fvg_check,
                "choch_bos": choch,
                "message": (
                    f"SETUP CONFIRMED ({direction}) — {score}/14. "
                    f"FVG: {'✓' if fvg_check['found'] else '✗'} | "
                    f"CHoCH: {'✓' if choch['confirmed'] else '✗'} | "
                    f"{reversal.get('entry_signal', '')}"
                    if score >= 7
                    else f"Not ready ({direction}) — {score}/14 (need 7+). "
                         f"Missing: {', '.join(k for k, v in checklist.items() if not v)}"
                ),
            }

    return best_result or {
        "symbol": symbol, "direction": "LONG",
        "confluence_score": "0/14", "raw_score": 0, "setup_valid": False,
        "message": "Could not identify key levels.",
    }
