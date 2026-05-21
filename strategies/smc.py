"""
SMC Strategy — based on SMC_Strategy_Playbook.md
Logic: 4H structure → 1H zones → FVG → liquidity sweep → VWAP + trendline confluence → R:R ≥ 3:1
"""

import numpy as np
import pandas as pd


# ── Market Structure ──────────────────────────────────────────────────────────

def _pivot_highs(df: pd.DataFrame, left: int = 3, right: int = 3) -> list:
    pivots = []
    for i in range(left, len(df) - right):
        if df["high"].iloc[i] == df["high"].iloc[i - left: i + right + 1].max():
            pivots.append({"index": i, "price": df["high"].iloc[i], "time": str(df["time"].iloc[i])})
    return pivots


def _pivot_lows(df: pd.DataFrame, left: int = 3, right: int = 3) -> list:
    pivots = []
    for i in range(left, len(df) - right):
        if df["low"].iloc[i] == df["low"].iloc[i - left: i + right + 1].min():
            pivots.append({"index": i, "price": df["low"].iloc[i], "time": str(df["time"].iloc[i])})
    return pivots


def get_market_structure(df_4h: pd.DataFrame) -> str:
    """Return 'bullish', 'bearish', or 'ranging' based on 4H HH/HL or LH/LL."""
    ph = _pivot_highs(df_4h)
    pl = _pivot_lows(df_4h)

    if len(ph) < 2 or len(pl) < 2:
        return "ranging"

    hh = ph[-1]["price"] > ph[-2]["price"]
    hl = pl[-1]["price"] > pl[-2]["price"]
    lh = ph[-1]["price"] < ph[-2]["price"]
    ll = pl[-1]["price"] < pl[-2]["price"]

    if hh and hl:
        return "bullish"
    if lh and ll:
        return "bearish"
    return "ranging"


# ── VWAP ──────────────────────────────────────────────────────────────────────

def calculate_vwap(df: pd.DataFrame) -> pd.Series:
    """
    Session VWAP. Resets each calendar day.
    Price above VWAP = bullish bias. Price below = bearish bias.
    """
    df = df.copy()
    df["date"] = df["time"].dt.date
    df["typical"] = (df["high"] + df["low"] + df["close"]) / 3
    df["tp_vol"] = df["typical"] * df["volume"]

    vwap_values = []
    for date, group in df.groupby("date"):
        cum_tp_vol = group["tp_vol"].cumsum()
        cum_vol = group["volume"].cumsum()
        session_vwap = cum_tp_vol / cum_vol.replace(0, np.nan)
        vwap_values.extend(session_vwap.tolist())

    return pd.Series(vwap_values, index=df.index)


def get_vwap_bias(df: pd.DataFrame) -> dict:
    """Returns current price vs VWAP and the resulting bias."""
    vwap = calculate_vwap(df)
    current_price = df["close"].iloc[-1]
    current_vwap = vwap.iloc[-1]
    distance_pips = round((current_price - current_vwap) * 10000, 1)

    bias = "bullish" if current_price > current_vwap else "bearish"
    return {
        "vwap": round(current_vwap, 5),
        "price": round(current_price, 5),
        "bias": bias,
        "distance_pips": distance_pips,
    }


# ── Trend Lines ───────────────────────────────────────────────────────────────

def get_trendlines(df: pd.DataFrame) -> dict:
    """
    Builds two trendlines from recent pivot structure:
    - Support line: connects last 2 pivot lows (ascending = bullish)
    - Resistance line: connects last 2 pivot highs (descending = bearish)
    Returns slope, current projected level, and whether price is near the line.
    """
    ph = _pivot_highs(df, left=4, right=4)
    pl = _pivot_lows(df, left=4, right=4)
    current_idx = len(df) - 1

    result = {}

    if len(pl) >= 2:
        p1, p2 = pl[-2], pl[-1]
        slope = (p2["price"] - p1["price"]) / max(p2["index"] - p1["index"], 1)
        projected = p2["price"] + slope * (current_idx - p2["index"])
        current_price = df["close"].iloc[-1]
        near = abs(current_price - projected) / projected < 0.002  # within 0.2%
        result["support_trendline"] = {
            "slope": round(slope, 6),
            "direction": "ascending" if slope > 0 else "descending",
            "projected_level": round(projected, 5),
            "price_near_line": near,
        }

    if len(ph) >= 2:
        p1, p2 = ph[-2], ph[-1]
        slope = (p2["price"] - p1["price"]) / max(p2["index"] - p1["index"], 1)
        projected = p2["price"] + slope * (current_idx - p2["index"])
        current_price = df["close"].iloc[-1]
        near = abs(current_price - projected) / projected < 0.002
        result["resistance_trendline"] = {
            "slope": round(slope, 6),
            "direction": "ascending" if slope > 0 else "descending",
            "projected_level": round(projected, 5),
            "price_near_line": near,
        }

    return result


# ── Demand / Supply Zones ─────────────────────────────────────────────────────

def _dedupe_zones(zones: list) -> list:
    """Remove duplicate zones that overlap significantly."""
    unique = []
    for z in zones:
        overlap = any(
            abs(z["top"] - u["top"]) / z["top"] < 0.001 and
            abs(z["bottom"] - u["bottom"]) / z["bottom"] < 0.001
            for u in unique
        )
        if not overlap:
            unique.append(z)
    return unique


def find_demand_zones(df: pd.DataFrame, lookback: int = 150) -> list:
    """
    Demand zone = last bearish candle before an aggressive bullish impulse.
    Mitigated only when price CLOSES below zone bottom (full penetration, not just a wick touch).
    """
    df = df.tail(lookback).reset_index(drop=True)
    avg_body = (df["close"] - df["open"]).abs().mean()
    zones = []

    for i in range(1, len(df) - 5):
        candle = df.iloc[i]
        if candle["close"] - candle["open"] < 2 * avg_body or candle["close"] <= candle["open"]:
            continue

        base_idx = i - 1
        while base_idx >= 0 and df.iloc[base_idx]["close"] > df.iloc[base_idx]["open"]:
            base_idx -= 1
        if base_idx < 0:
            continue

        base = df.iloc[base_idx]
        z_top = round(max(base["open"], base["close"]), 5)
        z_bot = round(min(base["open"], base["close"]), 5)
        if z_top <= z_bot:
            continue

        # Mitigated = price CLOSED below zone bottom (not just a wick through the top)
        future = df.iloc[i + 1:]
        mitigated = any(row["close"] < z_bot for _, row in future.iterrows())
        if not mitigated:
            zones.append({"type": "demand", "top": z_top, "bottom": z_bot, "time": str(base["time"])})

    return _dedupe_zones(zones)[-3:]


def find_supply_zones(df: pd.DataFrame, lookback: int = 150) -> list:
    """
    Supply zone = last bullish candle before an aggressive bearish impulse.
    Mitigated only when price CLOSES above zone top.
    """
    df = df.tail(lookback).reset_index(drop=True)
    avg_body = (df["close"] - df["open"]).abs().mean()
    zones = []

    for i in range(1, len(df) - 5):
        candle = df.iloc[i]
        if candle["open"] - candle["close"] < 2 * avg_body or candle["close"] >= candle["open"]:
            continue

        base_idx = i - 1
        while base_idx >= 0 and df.iloc[base_idx]["close"] < df.iloc[base_idx]["open"]:
            base_idx -= 1
        if base_idx < 0:
            continue

        base = df.iloc[base_idx]
        z_top = round(max(base["open"], base["close"]), 5)
        z_bot = round(min(base["open"], base["close"]), 5)
        if z_top <= z_bot:
            continue

        # Mitigated = price CLOSED above zone top
        future = df.iloc[i + 1:]
        mitigated = any(row["close"] > z_top for _, row in future.iterrows())
        if not mitigated:
            zones.append({"type": "supply", "top": z_top, "bottom": z_bot, "time": str(base["time"])})

    return _dedupe_zones(zones)[-3:]


# ── Fair Value Gap ────────────────────────────────────────────────────────────

def find_fvg(df: pd.DataFrame, lookback: int = 80) -> list:
    """3-candle FVG: c3 wick does not overlap c1 wick (imbalance zone)."""
    recent = df.tail(lookback).reset_index(drop=True)
    fvgs = []

    for i in range(1, len(recent) - 1):
        c1, c2, c3 = recent.iloc[i - 1], recent.iloc[i], recent.iloc[i + 1]

        if c3["low"] > c1["high"] and c2["close"] > c2["open"]:
            fvgs.append({
                "type": "bullish",
                "fvl": round(c1["high"], 5),
                "fvb": round((c1["high"] + c3["low"]) / 2, 5),
                "fvh": round(c3["low"], 5),
                "time": str(c2["time"]),
            })

        elif c3["high"] < c1["low"] and c2["close"] < c2["open"]:
            fvgs.append({
                "type": "bearish",
                "fvh": round(c1["low"], 5),
                "fvb": round((c1["low"] + c3["high"]) / 2, 5),
                "fvl": round(c3["high"], 5),
                "time": str(c2["time"]),
            })

    return fvgs[-5:]


# ── Liquidity Sweep ───────────────────────────────────────────────────────────

def check_liquidity_sweep(df: pd.DataFrame, zone: dict) -> dict:
    """
    Confirmed sweep = wick beyond zone boundary + close back inside within 3 candles.
    Per playbook: DO NOT enter before sweep — it's the single most critical trigger.
    """
    recent = df.tail(40).reset_index(drop=True)

    for i in range(len(recent) - 3):
        candle = recent.iloc[i]

        if zone["type"] == "demand" and candle["low"] < zone["bottom"]:
            for j in range(1, 4):
                if i + j < len(recent) and recent.iloc[i + j]["close"] > zone["bottom"]:
                    return {"occurred": True, "sweep_low": round(candle["low"], 5), "time": str(candle["time"])}

        elif zone["type"] == "supply" and candle["high"] > zone["top"]:
            for j in range(1, 4):
                if i + j < len(recent) and recent.iloc[i + j]["close"] < zone["top"]:
                    return {"occurred": True, "sweep_high": round(candle["high"], 5), "time": str(candle["time"])}

    return {"occurred": False}


# ── R:R Calculator ────────────────────────────────────────────────────────────

def calculate_rr(entry: float, sl: float, tp: float) -> float:
    risk = abs(entry - sl)
    reward = abs(tp - entry)
    return round(reward / risk, 2) if risk > 0 else 0.0


# ── Full SMC Scan ─────────────────────────────────────────────────────────────

def _valid_fvgs_for_direction(fvgs: list, direction: str, current_price: float) -> list:
    """
    Filter FVGs to only those that make sense as entries given current price.
    LONG entry (bullish FVG): FVG must be BELOW current price (waiting for retracement down).
    SHORT entry (bearish FVG): FVG must be ABOVE current price (waiting for retracement up).
    """
    if direction == "LONG":
        return [f for f in fvgs if f["type"] == "bullish" and f["fvb"] < current_price]
    else:
        return [f for f in fvgs if f["type"] == "bearish" and f["fvb"] > current_price]


def _valid_tp(opposing: list, direction: str, current_price: float, entry: float, sl: float) -> float:
    """
    Pick the nearest opposing zone that is beyond current price (not already passed).
    Falls back to 4R target if no valid zone found.
    """
    if direction == "LONG":
        # TP must be above current price for a long
        valid = [z for z in opposing if z["bottom"] > current_price]
        return valid[0]["bottom"] if valid else round(entry + abs(entry - sl) * 4, 5)
    else:
        # TP must be below current price for a short
        valid = [z for z in opposing if z["top"] < current_price]
        return valid[-1]["top"] if valid else round(entry - abs(sl - entry) * 4, 5)


def _build_setups(df_1h, fvgs, vwap_data, trendlines, direction, zones, opposing, trade_type="TREND", current_price=None):
    """
    Shared setup builder for both trend and scalp directions.
    trade_type: 'TREND' (with structure) or 'SCALP' (counter-trend, stricter R:R)
    Scalp trades use R:R ≥ 2:1 but flag as higher risk.
    """
    min_rr = 3.0 if trade_type == "TREND" else 2.0
    vwap_aligned = (
        (direction == "LONG" and vwap_data["bias"] == "bullish") or
        (direction == "SHORT" and vwap_data["bias"] == "bearish")
    )

    # Only use FVGs that are on the correct side of current price
    valid_fvgs = _valid_fvgs_for_direction(fvgs, direction, current_price) if current_price else fvgs
    setups = []

    for zone in zones:
        # Zone must also be on the correct side of current price
        if current_price:
            if direction == "LONG" and zone["top"] >= current_price:
                continue  # demand zone above current price — price already past it
            if direction == "SHORT" and zone["bottom"] <= current_price:
                continue  # supply zone below current price — price already past it

        sweep = check_liquidity_sweep(df_1h, zone)
        if not sweep["occurred"]:
            continue

        fvg_in_zone = next(
            (f for f in valid_fvgs if zone["bottom"] <= f["fvb"] <= zone["top"]),
            None,
        )
        if not fvg_in_zone:
            continue

        if direction == "LONG":
            entry = fvg_in_zone["fvl"]
            sl = sweep.get("sweep_low", round(zone["bottom"] * 0.9997, 5))
        else:
            entry = fvg_in_zone["fvh"]
            sl = sweep.get("sweep_high", round(zone["top"] * 1.0003, 5))

        # TP must be beyond current price, not already passed
        tp = _valid_tp(opposing, direction, current_price or entry, entry, sl) if current_price else (
            opposing[-1]["bottom"] if opposing and direction == "LONG" else
            opposing[-1]["top"] if opposing else
            round(entry + abs(entry - sl) * 4, 5)
        )

        # Final sanity check: entry must be below price for LONG, above for SHORT
        if current_price:
            if direction == "LONG" and entry >= current_price:
                continue
            if direction == "SHORT" and entry <= current_price:
                continue

        rr = calculate_rr(entry, sl, tp)
        if rr < min_rr:
            continue

        tl_key = "support_trendline" if direction == "LONG" else "resistance_trendline"
        tl_confluence = trendlines.get(tl_key, {}).get("price_near_line", False)

        confluence_score = sum([
            True,
            bool(fvg_in_zone),
            sweep["occurred"],
            vwap_aligned,
            tl_confluence,
        ])

        setups.append({
            "direction": direction,
            "trade_type": trade_type,
            "entry": round(entry, 5),
            "sl": round(sl, 5),
            "tp": round(tp, 5),
            "rr": rr,
            "confluence_score": f"{confluence_score}/5",
            "vwap_aligned": vwap_aligned,
            "trendline_confluence": tl_confluence,
            "zone": zone,
            "fvg": fvg_in_zone,
            "sweep": sweep,
        })

    return setups


def run_smc_scan(symbol: str, client) -> dict:
    """
    Full SMC scan — detects both TREND and SCALP setups:
    - TREND: trades with 4H structure (LONG in bullish, SHORT in bearish), R:R ≥ 3:1
    - SCALP: counter-trend from opposing zones (short from supply in bullish market,
             long from demand in bearish market), R:R ≥ 2:1, flagged as higher risk
    """
    df_4h = client.get_candles(symbol, "4H", bars=120)
    structure = get_market_structure(df_4h)

    df_1h = client.get_candles(symbol, "1H", bars=250)
    fvgs = find_fvg(df_1h)
    vwap_data = get_vwap_bias(df_1h)
    trendlines = get_trendlines(df_1h)

    demand_zones = find_demand_zones(df_1h)
    supply_zones = find_supply_zones(df_1h)
    current_price = df_1h["close"].iloc[-1]

    valid_setups = []

    if structure == "ranging":
        return {
            "symbol": symbol,
            "structure": "ranging",
            "vwap": vwap_data,
            "trendlines": trendlines,
            "valid_setups": [],
            "message": "No trade — market structure is ranging.",
        }

    if structure == "bullish":
        valid_setups += _build_setups(df_1h, fvgs, vwap_data, trendlines,
                                       "LONG", demand_zones, supply_zones, "TREND", current_price)
        valid_setups += _build_setups(df_1h, fvgs, vwap_data, trendlines,
                                       "SHORT", supply_zones, demand_zones, "SCALP", current_price)

    elif structure == "bearish":
        valid_setups += _build_setups(df_1h, fvgs, vwap_data, trendlines,
                                       "SHORT", supply_zones, demand_zones, "TREND", current_price)
        valid_setups += _build_setups(df_1h, fvgs, vwap_data, trendlines,
                                       "LONG", demand_zones, supply_zones, "SCALP", current_price)

    trend_count = sum(1 for s in valid_setups if s["trade_type"] == "TREND")
    scalp_count = sum(1 for s in valid_setups if s["trade_type"] == "SCALP")

    parts = []
    if trend_count:
        parts.append(f"{trend_count} TREND setup(s)")
    if scalp_count:
        parts.append(f"{scalp_count} SCALP opportunity(s)")
    message = ", ".join(parts) + " found." if parts else "No valid setup — checklist incomplete."

    trend_bias = "bullish" if structure == "bullish" else "bearish"

    return {
        "symbol": symbol,
        "structure": structure,
        "direction_bias": trend_bias,
        "vwap": vwap_data,
        "trendlines": trendlines,
        "valid_setups": valid_setups,
        "setup_count": len(valid_setups),
        "message": message,
    }
