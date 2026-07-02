import requests


def send_telegram(token: str, chat_id: str, message: str) -> bool:
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": message, "parse_mode": "Markdown"},
            timeout=10,
        )
        return resp.ok
    except Exception as e:
        print(f"[Jarvis] Telegram send failed: {e}")
        return False


def format_smc_alert(symbol: str, structure: str, setup: dict) -> str:
    trade_type = setup.get("trade_type", "TREND")
    direction = setup["direction"]

    if trade_type == "SCALP":
        emoji = "⚡" if direction == "SHORT" else "⚡"
        type_label = "SCALP (counter-trend)"
        risk_note = "_Counter-trend trade — use smaller lot size. Higher risk._"
    else:
        emoji = "🟢" if direction == "LONG" else "🔴"
        type_label = "TREND trade"
        risk_note = "_Liquidity sweep confirmed. Trade with structure._"

    return (
        f"{emoji} *JARVIS — {symbol} {type_label.upper()}*\n\n"
        f"Direction: *{direction}*\n"
        f"Type: `{type_label}`\n"
        f"Entry: `{setup['entry']}`\n"
        f"Stop Loss: `{setup['sl']}`\n"
        f"Take Profit: `{setup['tp']}`\n"
        f"R:R: `{setup['rr']}:1`\n"
        f"Confluence: `{setup.get('confluence_score', 'N/A')}`\n"
        f"Structure: `{structure.upper()}`\n\n"
        f"{risk_note}"
    )


def format_gbpjpy_alert(result: dict) -> str:
    score = result.get("confluence_score", "0/14")
    entry = result.get("1h_entry", {})
    reversal = result.get("4h_reversal", {})
    tp = entry.get("tp", "N/A")
    rr = entry.get("rr", "N/A")

    # Corridor
    corridor = result.get("corridor", {})
    corridor_line = ""
    if corridor:
        c_zone = corridor.get("zone", "?")
        c_pct = corridor.get("position_pct", 0)
        c_floor = corridor.get("floor", "?")
        c_ceil = corridor.get("ceiling", "?")
        c_emoji = {"base": "🟢", "lower": "🟡", "mid": "⚪", "upper": "🟠", "top": "🔴"}.get(c_zone, "⚪")
        corridor_line = f"📦 Corridor: `{c_floor}→{c_ceil}` | {c_emoji} `{c_zone} ({c_pct}%)`"

    # Behavioral quarter read
    qpa = result.get("qpa_profiles", {})
    _rtag = {"STRONG S/R": "🔒", "TRAP ZONE": "🪤", "S/R": "✅",
             "BROKEN": "🔓", "CONTESTED": "⚔️", "UNTESTED": "—"}
    floor_react = qpa.get(corridor.get("floor"), {}).get("reaction", "UNTESTED") if corridor else "UNTESTED"
    ceil_react = qpa.get(corridor.get("ceiling"), {}).get("reaction", "UNTESTED") if corridor else "UNTESTED"
    qpa_line = (f"Floor `{corridor.get('floor','?')}` {_rtag.get(floor_react,'?')} {floor_react} | "
                f"Ceiling `{corridor.get('ceiling','?')}` {_rtag.get(ceil_react,'?')} {ceil_react}")

    # Blown quarter
    blown_q = result.get("blown_quarter", {})
    blown_line = ""
    if blown_q.get("detected"):
        arrow = "⬆️" if blown_q["direction"] == "UP" else "⬇️"
        blown_line = f"\n{arrow} Blown quarter: broke `{blown_q['broken_level']}` {blown_q.get('bars_ago','?')}h ago"

    return (
        f"🎯 *JARVIS — GBPJPY CONFLUENCE ALERT*\n\n"
        f"Score: *{score}*\n"
        f"Pattern: `{reversal.get('pattern', 'N/A')}`\n"
        f"Entry Zone: `{entry.get('entry_zone', 'N/A')}`\n"
        f"Stop Loss: `{entry.get('sl', 'N/A')}`\n"
        f"Take Profit: `{tp}`\n"
        f"R:R: `{rr}:1`\n"
        f"Position: `{entry.get('position_in_zone', 'N/A')} of zone`\n"
        f"Lot Guidance: `{entry.get('lot_guidance', 'N/A')}`\n"
        + (f"{corridor_line}\n" if corridor_line else "")
        + f"{qpa_line}"
        + blown_line
        + f"\n\n_{reversal.get('entry_signal', '')}_"
    )
