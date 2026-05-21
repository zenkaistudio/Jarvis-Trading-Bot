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
    score = result.get("confluence_score", "0/10")
    entry = result.get("1h_entry", {})
    reversal = result.get("4h_reversal", {})
    return (
        f"🎯 *JARVIS — GBPJPY CONFLUENCE ALERT*\n\n"
        f"Score: *{score}*\n"
        f"Pattern: `{reversal.get('pattern', 'N/A')}`\n"
        f"Entry Zone: `{entry.get('entry_zone', 'N/A')}`\n"
        f"Stop Loss: `{entry.get('sl', 'N/A')}`\n"
        f"Position: `{entry.get('position_in_zone', 'N/A')} of zone`\n"
        f"Lot Guidance: `{entry.get('lot_guidance', 'N/A')}`\n\n"
        f"_{reversal.get('entry_signal', '')}_"
    )
