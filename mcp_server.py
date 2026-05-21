"""
TradeLocker MCP Server
Exposes TradeLocker market data + SMC/GBPJPY strategy analysis as Claude tools.
Run: python mcp_server.py
Claude Code config: { "command": "python", "args": ["/path/to/mcp_server.py"] }
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from mcp.server.fastmcp import FastMCP

from tradelocker_client import TradeLockerClient
from strategies.smc import run_smc_scan
from strategies.gbpjpy import run_gbpjpy_confluence_check
from monitor import JarvisMonitor

mcp = FastMCP("Jarvis")
client = TradeLockerClient()
jarvis = JarvisMonitor(client)


# ── Account Tools ─────────────────────────────────────────────────────────────

@mcp.tool()
def get_account() -> str:
    """Get current TradeLocker account balance, equity, and margin info."""
    try:
        data = client.get_account_info()
        return json.dumps(data, indent=2)
    except Exception as e:
        return f"Error fetching account: {e}"


@mcp.tool()
def get_positions() -> str:
    """Get all currently open positions on the TradeLocker account."""
    try:
        positions = client.get_positions()
        if not positions:
            return "No open positions."
        return json.dumps(positions, indent=2)
    except Exception as e:
        return f"Error fetching positions: {e}"


# ── Market Data Tools ─────────────────────────────────────────────────────────

@mcp.tool()
def get_price(symbol: str) -> str:
    """
    Get current price for a symbol.
    Valid symbols: GBPJPY, EURUSD, XAUUSD, NAS100, NZDJPY, AUDCAD
    """
    try:
        tick = client.get_tick(symbol.upper())
        return json.dumps(tick, indent=2)
    except Exception as e:
        return f"Error fetching price for {symbol}: {e}"


@mcp.tool()
def get_candles(symbol: str, resolution: str = "1H", bars: int = 20) -> str:
    """
    Get OHLCV candle data for a symbol.
    symbol: GBPJPY, EURUSD, XAUUSD, NAS100, NZDJPY, AUDCAD
    resolution: 1m, 5m, 15m, 30m, 1H, 4H, 1D, 1W, 1M
    bars: number of candles to return (max 200)
    """
    try:
        df = client.get_candles(symbol.upper(), resolution, min(bars, 200))
        records = df.tail(bars).to_dict(orient="records")
        for r in records:
            r["time"] = str(r["time"])
        return json.dumps(records, indent=2)
    except Exception as e:
        return f"Error fetching candles for {symbol} {resolution}: {e}"


# ── Strategy Tools ────────────────────────────────────────────────────────────

@mcp.tool()
def scan_smc(symbol: str) -> str:
    """
    Run a full SMC setup scan on a symbol.
    Checks: 4H market structure → 1H demand/supply zones → FVG confluence →
            liquidity sweep → VWAP bias → trendline → R:R ≥ 3:1
    symbol: GBPJPY, EURUSD, XAUUSD, NAS100, NZDJPY, AUDCAD
    """
    try:
        result = run_smc_scan(symbol.upper(), client)
        return json.dumps(result, indent=2, default=str)
    except Exception as e:
        return f"Error running SMC scan for {symbol}: {e}"


@mcp.tool()
def check_gbpjpy() -> str:
    """
    Run full GBPJPY top-down confluence check.
    Analyzes Monthly → Weekly → Daily → 4H → 1H using the KJ strategy framework.
    Returns confluence score out of 10, full checklist, and entry guidance.
    Minimum 5/10 required for a valid setup.
    """
    try:
        result = run_gbpjpy_confluence_check(client)
        return json.dumps(result, indent=2, default=str)
    except Exception as e:
        return f"Error running GBPJPY confluence check: {e}"


@mcp.tool()
def scan_watchlist() -> str:
    """
    Scan all 6 watchlist symbols (GBPJPY, EURUSD, XAUUSD, NAS100, NZDJPY, AUDCAD)
    for active SMC setups. Returns a summary of any valid setups found.
    """
    watchlist = ["GBPJPY", "EURUSD", "XAUUSD", "NAS100", "NZDJPY", "AUDCAD"]
    results = {}

    for symbol in watchlist:
        try:
            scan = run_smc_scan(symbol, client)
            results[symbol] = {
                "structure": scan.get("structure"),
                "setups_found": scan.get("setup_count", 0),
                "message": scan.get("message"),
            }
            if scan.get("valid_setups"):
                top = scan["valid_setups"][0]
                results[symbol]["best_setup"] = {
                    "direction": top["direction"],
                    "entry": top["entry"],
                    "sl": top["sl"],
                    "tp": top["tp"],
                    "rr": top["rr"],
                    "confluence": top.get("confluence_score"),
                }
        except Exception as e:
            results[symbol] = {"error": str(e)}

    summary = [s for s in watchlist if results.get(s, {}).get("setups_found", 0) > 0]
    output = {
        "symbols_with_setups": summary,
        "total_setups": len(summary),
        "details": results,
    }
    return json.dumps(output, indent=2, default=str)


# ── Monitoring / Alert Tools ──────────────────────────────────────────────────

@mcp.tool()
def start_monitoring(interval_minutes: int = 15) -> str:
    """
    Start Jarvis background monitoring. Scans all watchlist symbols on a timer
    and sends Telegram alerts when valid SMC or GBPJPY setups appear.
    interval_minutes: how often to scan (default 15, minimum 5)
    Requires jarvis_config.json to have telegram_token and telegram_chat_id set.
    """
    interval = max(interval_minutes, 5)
    return jarvis.start(interval)


@mcp.tool()
def stop_monitoring() -> str:
    """Stop Jarvis background monitoring and clear alert history."""
    return jarvis.stop()


@mcp.tool()
def monitoring_status() -> str:
    """Check if Jarvis monitoring is running and whether Telegram is configured."""
    return json.dumps(jarvis.status(), indent=2)


@mcp.tool()
def get_watched_setups() -> str:
    """Show all price levels Jarvis is currently watching for alerts."""
    return jarvis.watcher.watchlist_summary()


if __name__ == "__main__":
    mcp.run()
