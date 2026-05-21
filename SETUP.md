# TradeLocker Bot — Setup Guide

## 1. Install dependencies (Mac terminal)

```bash
cd "/Users/zenkai.inc/Desktop/A.I. Assets/tradelocker-bot"
pip install -r requirements.txt
```

## 2. Connect to Claude Code

Add this to your Claude Code MCP settings.
Open terminal and run:

```bash
claude mcp add tradelocker -- python "/Users/zenkai.inc/Desktop/A.I. Assets/tradelocker-bot/mcp_server.py"
```

Or add manually to ~/.claude/settings.json:

```json
{
  "mcpServers": {
    "tradelocker": {
      "command": "python",
      "args": ["/Users/zenkai.inc/Desktop/A.I. Assets/tradelocker-bot/mcp_server.py"]
    }
  }
}
```

## 3. Test it

Restart Claude Code, then ask:
- "What's my account balance?"
- "Scan GBPJPY for an SMC setup"
- "Run the GBPJPY confluence check"
- "Scan the full watchlist"

## Tools available to Claude

| Tool | What it does |
|------|-------------|
| `get_account` | Balance, equity, margin |
| `get_positions` | Open trades |
| `get_price` | Live price for any symbol |
| `get_candles` | OHLCV data (any symbol, any timeframe) |
| `scan_smc` | Full SMC scan: structure → zones → FVG → sweep → VWAP → trendline → R:R |
| `check_gbpjpy` | Full KJ top-down confluence check (10-point score) |
| `scan_watchlist` | Scan all 6 symbols at once |

## File structure

```
tradelocker-bot/
├── mcp_server.py          ← Claude connects here
├── tradelocker_client.py  ← TradeLocker REST API
├── strategies/
│   ├── smc.py             ← SMC: zones, FVG, sweep, VWAP, trendlines
│   └── gbpjpy.py          ← GBPJPY: Monthly → 1H top-down check
├── requirements.txt
└── SETUP.md
```

Config loaded from: ../tradelocker_config.json (already exists)
