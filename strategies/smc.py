"""
SMC Strategy — based on SMC_Strategy_Playbook.md
Logic: 4H structure → 1H zones → FVG → liquidity sweep → VWAP + trendline confluence → R:R ≥ 3:1
"""

import math

import numpy as np
import pandas as pd


# ── Corridor Levels ───────────────────────────────────────────────────────────
# Quarter-interval spacing per instrument. These define the "box" price moves
# between: the floor, ceiling, and midpoint of each institutional corridor.

CORRIDOR_INTERVALS = {
    "GBPJPY": 0.25,    # JPY pairs — 25 pip corridors
    "NZDJPY": 0.25,
    "EURUSD": 0.0025,  # 4-decimal pairs — 25 pip corridors
    "AUDCAD": 0.0025,
    "XAUUSD": 25.0,    # Gold — $25 corridors
    "NAS100": 100.0,   # Nasdaq — 100-point corridors
}


def get_corridor(symbol: str, price: float) -> dict:
    """
    Identify the current quarter-level corridor for any symbol.
    Returns the floor/ceiling of the box price is in, position within it,
    and a zone label: base / lower / mid / upper / top.
    """
    interval = CORRIDOR_INTERVALS.get(symbol.upper(), 0.0025)
    lower = round(math.floor(price / interval) * interval, 5)
    upper = round(lower + interval, 5)
    midpoint = round(lower + interval / 2, 5)
    position_pct = round((price - lower) / interval * 100, 1)

    if position_pct <= 20:
        zone = "base"       # sitting on the floor — strongest support
    elif position_pct <= 40:
        zone = "lower"      # lower third — good entry for longs
    elif position_pct <= 60:
        zone = "mid"        # middle of corridor — less directional edge
    elif position_pct <= 80:
        zone = "upper"      # upper third — approaching ceiling resistance
    else:
        zone = "top"        # near ceiling — overextended / short entry

    return {
        "floor": lower,
        "ceiling": upper,
        "midpoint": midpoint,
        "interval": interval,
        "position_pct": position_pct,
        "zone": zone,
    }


def detect_blown_quarter(symbol: str, df: pd.DataFrame, lookback_bars: int = 30) -> dict:
    """
    Detects if price recently made an aggressive momentum break through a quarter level.
    "Blown quarter" = large-body candle (≥1.5× avg) whose close crossed a quarter level cleanly.
    This flags either bullish continuation (direction=UP) or bearish breakdown (direction=DOWN).
    Returns the most recent blow within lookback_bars.
    """
    interval = CORRIDOR_INTERVALS.get(symbol.upper(), 0.0025)
    threshold = interval * 0.10

    recent = df.tail(lookback_bars).reset_index(drop=True)
    avg_body = (recent["close"] - recent["open"]).abs().mean()
    if avg_body == 0:
        return {"detected": False}

    # All quarter levels touched in this window
    price_min = recent["low"].min()
    price_max = recent["high"].max()
    first_q = round(math.floor(price_min / interval) * interval, 5)
    q_levels = []
    lvl = first_q
    while lvl <= round(price_max + interval, 5):
        q_levels.append(round(lvl, 5))
        lvl = round(lvl + interval, 5)

    blows = []
    for i in range(1, len(recent)):
        candle = recent.iloc[i]
        prev = recent.iloc[i - 1]
        body = abs(candle["close"] - candle["open"])
        if body < avg_body * 1.5:
            continue
        for q in q_levels:
            if prev["close"] < q - threshold and candle["close"] > q + threshold:
                blows.append({"direction": "UP", "level": q, "bar": i,
                               "close": round(candle["close"], 5)})
            elif prev["close"] > q + threshold and candle["close"] < q - threshold:
                blows.append({"direction": "DOWN", "level": q, "bar": i,
                               "close": round(candle["close"], 5)})

    if not blows:
        return {"detected": False}

    latest = blows[-1]
    return {
        "detected": True,
        "direction": latest["direction"],
        "broken_level": latest["level"],
        "close_after": latest["close"],
        "bars_ago": len(recent) - 1 - latest["bar"],
    }


def analyze_quarter_pa(symbol: str, df: pd.DataFrame, current_price: float, lookback: int = 300) -> dict:
    """
    Scans recent candles and classifies what price ACTUALLY DID at each quarter level.
    For every level touched: counts rejections, clean breaks, and false breaks (traps).
    Returns a profile dict keyed by quarter level price.
    """
    interval = CORRIDOR_INTERVALS.get(symbol.upper(), 0.0025)
    touch_threshold = interval * 0.12   # within 12% of the interval = "at the level"
    confirm_bars = 5                     # bars after touch to confirm outcome

    recent = df.tail(lookback).reset_index(drop=True)
    price_min = recent["low"].min()
    price_max = recent["high"].max()

    # All quarter levels in the data's price range
    first_q = round(math.floor(price_min / interval) * interval, 5)
    q_levels = []
    lvl = first_q
    while lvl <= round(price_max + interval, 5):
        q_levels.append(round(lvl, 5))
        lvl = round(lvl + interval, 5)

    profiles = {}

    for q in q_levels:
        events = []
        last_touch_bar = -10

        for i in range(2, len(recent) - confirm_bars):
            c = recent.iloc[i]
            at_level = abs(c["high"] - q) <= touch_threshold or abs(c["low"] - q) <= touch_threshold
            if not at_level or i - last_touch_bar < 5:
                continue
            last_touch_bar = i

            prev_close = recent.iloc[i - 1]["close"]
            from_above = prev_close > q
            future_closes = recent.iloc[i + 1: i + 1 + confirm_bars]["close"].tolist()

            if from_above:
                # Testing level as support from above
                broke = any(c < q - touch_threshold for c in future_closes)
                if broke:
                    broke_idx = next(j for j, c in enumerate(future_closes) if c < q - touch_threshold)
                    recovered = any(c > q + touch_threshold for c in future_closes[broke_idx + 1:])
                    outcome = "FALSE_BREAK_DOWN" if recovered else "BREAK_DOWN"
                else:
                    outcome = "REJECT_UP"
            else:
                # Testing level as resistance from below
                broke = any(c > q + touch_threshold for c in future_closes)
                if broke:
                    broke_idx = next(j for j, c in enumerate(future_closes) if c > q + touch_threshold)
                    recovered = any(c < q - touch_threshold for c in future_closes[broke_idx + 1:])
                    outcome = "FALSE_BREAK_UP" if recovered else "BREAK_UP"
                else:
                    outcome = "REJECT_DOWN"

            events.append({"bar": i, "from_above": from_above, "outcome": outcome})

        if not events:
            continue

        rejects = sum(1 for e in events if e["outcome"].startswith("REJECT"))
        breaks = sum(1 for e in events if e["outcome"] in ("BREAK_UP", "BREAK_DOWN"))
        false_breaks = sum(1 for e in events if e["outcome"].startswith("FALSE"))
        total = len(events)

        if rejects >= 2 and breaks == 0:
            reaction = "STRONG S/R"
        elif false_breaks >= 1 and breaks == 0:
            reaction = "TRAP ZONE"
        elif rejects > breaks:
            reaction = "S/R"
        elif breaks > rejects:
            reaction = "BROKEN"
        else:
            reaction = "CONTESTED"

        profiles[q] = {
            "level": q,
            "touches": total,
            "rejects": rejects,
            "breaks": breaks,
            "false_breaks": false_breaks,
            "reject_rate": round(rejects / total * 100) if total else 0,
            "reaction": reaction,
        }

    return profiles


def diagnose_quarter_path(symbol: str, current_price: float, profiles: dict) -> dict:
    """
    Uses quarter level profiles to produce a plain-English read of likely price path.
    Diagnoses floor/ceiling behavior and forecasts the next probable move.
    """
    interval = CORRIDOR_INTERVALS.get(symbol.upper(), 0.0025)
    corridor = get_corridor(symbol, current_price)
    floor_lvl = corridor["floor"]
    ceil_lvl = corridor["ceiling"]
    pos_zone = corridor["zone"]
    pos_pct = corridor["position_pct"]

    floor_p = profiles.get(floor_lvl, {})
    ceil_p = profiles.get(ceil_lvl, {})
    floor_react = floor_p.get("reaction", "UNTESTED")
    ceil_react = ceil_p.get("reaction", "UNTESTED")

    def _level_line(p: dict, lvl: float, role: str) -> str:
        if not p:
            return f"{role} `{lvl}` — untested (no prior data)"
        t, r, b, fb = p["touches"], p["rejects"], p["breaks"], p["false_breaks"]
        tag = {"STRONG S/R": "🔒 HOLDING", "TRAP ZONE": "🪤 TRAPPY",
               "S/R": "✅ S/R", "BROKEN": "🔓 BROKEN", "CONTESTED": "⚔️ CONTESTED"}.get(p["reaction"], "?")
        parts = [f"{role} `{lvl}` — {t} touch(es) | rej {r} · brk {b}"]
        if fb:
            parts.append(f"· false break {fb}")
        parts.append(f"→ {tag}")
        return "  ".join(parts)

    diagnosis = []

    # Position read
    if pos_zone in ("base", "lower"):
        diagnosis.append(f"Price is sitting at the floor `{floor_lvl}` ({pos_pct}% into corridor)")
    elif pos_zone == "mid":
        diagnosis.append(f"Price is at the midpoint `{corridor['midpoint']}` — corridor is undecided")
    else:
        diagnosis.append(f"Price is pressing the ceiling `{ceil_lvl}` ({100 - pos_pct:.0f}% headroom left)")

    # Floor read
    diagnosis.append(_level_line(floor_p, floor_lvl, "Floor"))

    # Ceiling read
    diagnosis.append(_level_line(ceil_p, ceil_lvl, "Ceiling"))

    # Trap warning
    if floor_react == "TRAP ZONE" and pos_zone in ("base", "lower"):
        diagnosis.append(f"⚠️ Floor has trapped sellers before — expect false break below `{floor_lvl}` then snap back up")
    if ceil_react == "TRAP ZONE" and pos_zone in ("upper", "top"):
        diagnosis.append(f"⚠️ Ceiling has trapped buyers before — expect false break above `{ceil_lvl}` then reversal")

    # Forecast
    forecast = []
    next_up = round(ceil_lvl + interval, 5)
    next_down = round(floor_lvl - interval, 5)

    if pos_zone in ("base", "lower"):
        if floor_react in ("STRONG S/R", "S/R", "TRAP ZONE"):
            forecast.append(f"▸ Floor holds → run to ceiling `{ceil_lvl}`")
            if ceil_react == "BROKEN":
                forecast.append(f"▸ Ceiling broken before → continuation to `{next_up}` likely")
            elif ceil_react in ("STRONG S/R", "S/R"):
                forecast.append(f"▸ Ceiling is firm → expect rejection back to `{floor_lvl}`")
            else:
                forecast.append(f"▸ Ceiling untested → first-touch reaction is the tell")
        if floor_react == "BROKEN":
            forecast.append(f"▸ Floor previously broken — thin support. Break below → `{next_down}`")
        forecast.append(f"▸ Floor fails → next support `{next_down}`")

    elif pos_zone in ("upper", "top"):
        if ceil_react in ("STRONG S/R", "S/R", "TRAP ZONE"):
            forecast.append(f"▸ Ceiling rejects → back to floor `{floor_lvl}`")
            if floor_react == "BROKEN":
                forecast.append(f"▸ Floor weak → breakdown below `{floor_lvl}` into `{next_down}`")
        if ceil_react == "BROKEN":
            forecast.append(f"▸ Ceiling broken before → bulls pressing for `{next_up}`")
        forecast.append(f"▸ Ceiling breaks → next target `{next_up}`")

    else:  # mid
        if floor_react in ("STRONG S/R",) and ceil_react in ("STRONG S/R",):
            forecast.append(f"▸ Compressed between strong levels — range `{floor_lvl}↔{ceil_lvl}` until one breaks")
        elif floor_react == "BROKEN":
            forecast.append(f"▸ Floor weak — bias short toward `{next_down}`")
        elif ceil_react == "BROKEN":
            forecast.append(f"▸ Ceiling broken — bias long toward `{next_up}`")
        else:
            forecast.append(f"▸ Mid-corridor, mixed levels — wait for price to reach floor or ceiling before acting")

    return {
        "corridor": corridor,
        "floor": floor_lvl,
        "ceiling": ceil_lvl,
        "floor_profile": floor_p,
        "ceiling_profile": ceil_p,
        "diagnosis": diagnosis,
        "forecast": forecast,
        "all_profiles": profiles,
    }


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


def _build_setups(df_entry, fvgs, vwap_data, trendlines, direction, zones, opposing, trade_type="TREND", current_price=None):
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

        sweep = check_liquidity_sweep(df_entry, zone)
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


def run_range_scan(symbol: str, client, entry_tf: str = "15m") -> dict:
    """
    Range scan: when price is bound between support and resistance, trade the extremes.
    LONG from demand zones in the lower 40% of the range.
    SHORT from supply zones in the upper 40% of the range.
    R:R ≥ 2:1. No structural confirmation required.
    entry_tf: timeframe for sweep detection (5m, 15m, 30m, 1H).
    """
    df_4h = client.get_candles(symbol, "4H", bars=120)
    structure = get_market_structure(df_4h)

    df_1h = client.get_candles(symbol, "1H", bars=250)
    vwap_data = get_vwap_bias(df_1h)
    demand_zones = find_demand_zones(df_1h)
    supply_zones = find_supply_zones(df_1h)
    current_price = df_1h["close"].iloc[-1]

    _tf_bars = {"5m": 300, "15m": 200, "30m": 150, "1H": 200}
    df_entry = client.get_candles(symbol, entry_tf, bars=_tf_bars.get(entry_tf, 400))

    recent = df_1h.tail(100)
    range_high = round(recent["high"].max(), 5)
    range_low = round(recent["low"].min(), 5)
    range_size = range_high - range_low

    setups = []

    for zone in demand_zones:
        if zone["top"] > range_low + range_size * 0.4:
            continue
        if current_price <= zone["top"]:
            continue
        entry = zone["top"]
        sl = round(zone["bottom"] * 0.9997, 5)
        valid_supplies = [z for z in supply_zones if z["bottom"] > entry]
        tp = valid_supplies[0]["bottom"] if valid_supplies else round(range_high * 0.998, 5)
        rr = calculate_rr(entry, sl, tp)
        if rr < 2.0:
            continue
        sweep = check_liquidity_sweep(df_entry, zone)
        setups.append({
            "direction": "LONG",
            "trade_type": "RANGE",
            "entry": round(entry, 5),
            "sl": round(sl, 5),
            "tp": round(tp, 5),
            "rr": rr,
            "sweep_confirmed": sweep["occurred"],
            "zone": zone,
        })

    for zone in supply_zones:
        if zone["bottom"] < range_high - range_size * 0.4:
            continue
        if current_price >= zone["bottom"]:
            continue
        entry = zone["bottom"]
        sl = round(zone["top"] * 1.0003, 5)
        valid_demands = [z for z in demand_zones if z["top"] < entry]
        tp = valid_demands[-1]["top"] if valid_demands else round(range_low * 1.002, 5)
        rr = calculate_rr(entry, sl, tp)
        if rr < 2.0:
            continue
        sweep = check_liquidity_sweep(df_entry, zone)
        setups.append({
            "direction": "SHORT",
            "trade_type": "RANGE",
            "entry": round(entry, 5),
            "sl": round(sl, 5),
            "tp": round(tp, 5),
            "rr": rr,
            "sweep_confirmed": sweep["occurred"],
            "zone": zone,
        })

    msg = f"{len(setups)} range setup(s) found." if setups else "No valid range setups — zones too close or R:R insufficient."
    return {
        "symbol": symbol,
        "structure": structure,
        "entry_tf": entry_tf,
        "range_high": range_high,
        "range_low": range_low,
        "vwap": vwap_data,
        "valid_setups": setups,
        "setup_count": len(setups),
        "message": msg,
    }


def run_smc_scan(symbol: str, client, entry_tf: str = "15m") -> dict:
    """
    Full SMC scan — detects both TREND and SCALP setups:
    - TREND: trades with 4H structure (LONG in bullish, SHORT in bearish), R:R ≥ 3:1
    - SCALP: counter-trend from opposing zones, R:R ≥ 2:1, flagged as higher risk
    entry_tf: timeframe for FVG + liquidity sweep detection (5m, 15m, 30m, 1H)
    Zones and structure are always identified on 1H/4H for stability.
    """
    df_4h = client.get_candles(symbol, "4H", bars=120)
    structure = get_market_structure(df_4h)

    df_1h = client.get_candles(symbol, "1H", bars=250)
    vwap_data = get_vwap_bias(df_1h)
    trendlines = get_trendlines(df_1h)
    demand_zones = find_demand_zones(df_1h)
    supply_zones = find_supply_zones(df_1h)
    current_price = df_1h["close"].iloc[-1]

    # Entry TF: FVG and sweep on the user's preferred precision timeframe
    _tf_bars = {"5m": 300, "15m": 200, "30m": 150, "1H": 200}
    df_entry = client.get_candles(symbol, entry_tf, bars=_tf_bars.get(entry_tf, 400))
    fvgs = find_fvg(df_entry)

    valid_setups = []

    corridor = get_corridor(symbol, current_price)

    if structure == "ranging":
        return {
            "symbol": symbol,
            "structure": "ranging",
            "entry_tf": entry_tf,
            "vwap": vwap_data,
            "trendlines": trendlines,
            "demand_zones": demand_zones,
            "supply_zones": supply_zones,
            "current_price": current_price,
            "corridor": corridor,
            "valid_setups": [],
            "message": "No trade — market structure is ranging. Try `range` command.",
        }

    if structure == "bullish":
        valid_setups += _build_setups(df_entry, fvgs, vwap_data, trendlines,
                                       "LONG", demand_zones, supply_zones, "TREND", current_price)
        valid_setups += _build_setups(df_entry, fvgs, vwap_data, trendlines,
                                       "SHORT", supply_zones, demand_zones, "SCALP", current_price)

    elif structure == "bearish":
        valid_setups += _build_setups(df_entry, fvgs, vwap_data, trendlines,
                                       "SHORT", supply_zones, demand_zones, "TREND", current_price)
        valid_setups += _build_setups(df_entry, fvgs, vwap_data, trendlines,
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
        "entry_tf": entry_tf,
        "direction_bias": trend_bias,
        "vwap": vwap_data,
        "trendlines": trendlines,
        "demand_zones": demand_zones,
        "supply_zones": supply_zones,
        "current_price": current_price,
        "corridor": corridor,
        "valid_setups": valid_setups,
        "setup_count": len(valid_setups),
        "message": message,
    }
