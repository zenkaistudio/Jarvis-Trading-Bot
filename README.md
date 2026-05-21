# Jarvis — TradeLocker Trading Bot

Jarvis is a Python trading assistant that connects to the TradeLocker REST API and puts a Telegram chatbot on top of it. You text Jarvis commands, it scans the market using Smart Money Concepts (SMC) and a GBPJPY-specific top-down framework (KJ strategy), fires price alerts when your levels are hit, and optionally places limit orders automatically.

It also runs as a Claude Code MCP server, so you can interact with it directly inside Claude.

---

## Features

- **Live price and candle data** from TradeLocker (HeroFX or any supported broker)
- **SMC setup scanner** — 4H structure, 1H demand/supply zones, FVG confluence, liquidity sweep detection, VWAP bias, trendline proximity, R:R filter (minimum 3:1)
- **GBPJPY KJ confluence checker** — full top-down 5-step analysis (Monthly → Weekly → Daily → 4H → 1H), 10-point scoring, minimum 5/10 to flag a setup
- **Dynamic price-level watcher** — after any scan, Jarvis tracks your entry zone, SL, TP, and neckline levels in memory and alerts you when price hits them
- **Auto-order execution** — when price reaches the entry zone, Jarvis places a limit order on your account (demo or live) with position sizing calculated from your risk percentage
- **Telegram chatbot interface** — control everything via text commands from your phone or desktop
- **Claude Code MCP server** — all tools available natively inside Claude Code
- **Demo and live account switching** — one command to flip between accounts

---

## Prerequisites

- **Mac or Linux** (Windows is not tested)
- **Python 3.10+**
- **TradeLocker account** with any supported broker (HeroFX, or any broker that provides TradeLocker access)
- **Telegram account** and a bot created via BotFather

---

## Setup

### 1. Clone the repository

```bash
git clone https://github.com/YOUR_USERNAME/tradelocker-bot.git
cd tradelocker-bot
```

### 2. Create a virtual environment

```bash
python3 -m venv venv
source venv/bin/activate
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

Dependencies: `requests`, `pandas`, `numpy`, `mcp`

### 4. Configure TradeLocker credentials

Copy the example config and fill in your broker details:

```bash
cp tradelocker_config.example.json ../tradelocker_config.json
```

Open `../tradelocker_config.json` and fill in:

| Field | What to put |
|---|---|
| `email` | Your TradeLocker login email |
| `password` | Your TradeLocker password |
| `server` | Your broker server name (e.g. `HeroFX-Live`) |
| `account_id` | Your numeric account ID (found in TradeLocker platform) |
| `acc_num` | Your account number (the short number, e.g. `5`) |
| `base_url` | `https://live.tradelocker.com/backend-api` for live, `https://demo.tradelocker.com/backend-api` for demo |

The `watchlist` section maps symbol names to their `tradableInstrumentId` and `routeId`. These values are broker-specific. The defaults in the example file are for HeroFX live accounts. If you use a different broker, you will need to fetch the instrument list from the TradeLocker API to get the correct IDs.

**Note:** `tradelocker_config.json` is kept one directory above the repo root (`../tradelocker_config.json`) so it is never accidentally committed.

### 5. Create a Telegram bot

See [TELEGRAM_SETUP.md](TELEGRAM_SETUP.md) for the full step-by-step guide.

Short version:
1. Open Telegram, message `@BotFather`
2. Send `/newbot` and follow the prompts
3. Copy the bot token
4. Get your chat ID by messaging the bot and calling `https://api.telegram.org/botTOKEN/getUpdates`

### 6. Configure Jarvis

Copy the Jarvis config example:

```bash
cp jarvis_config.example.json jarvis_config.json
```

Open `jarvis_config.json` and fill in:

| Field | Description |
|---|---|
| `telegram_token` | The bot token from BotFather |
| `telegram_chat_id` | Your Telegram user ID (numeric) |
| `monitor_interval_minutes` | How often the full SMC scan runs (default: 15) |
| `watchlist` | Symbols to monitor (GBPJPY, EURUSD, XAUUSD, NAS100, NZDJPY, AUDCAD) |
| `gbpjpy_alert_threshold` | Minimum confluence score to send a GBPJPY alert (default: 5) |
| `auto_trade` | `true` or `false` — enables automatic order placement |
| `trade_mode` | `"demo"` or `"live"` |
| `risk_percent` | Risk per trade as a percentage of account balance (default: 2.0) |
| `demo.base_url` | Demo TradeLocker API URL |
| `demo.server` | Demo broker server name |
| `demo.account_id` | Demo account ID |
| `demo.acc_num` | Demo account number |
| `live.*` | Same fields for your live account |

### 7. Connect to Claude Code (MCP)

This step lets you talk to Jarvis directly inside Claude Code.

Run this command (update the path to match where you cloned the repo):

```bash
claude mcp add tradelocker -- python "/path/to/tradelocker-bot/mcp_server.py"
```

Or add it manually to `~/.claude/settings.json`:

```json
{
  "mcpServers": {
    "tradelocker": {
      "command": "python",
      "args": ["/path/to/tradelocker-bot/mcp_server.py"]
    }
  }
}
```

Restart Claude Code. You can now ask things like:
- "What's my account balance?"
- "Scan GBPJPY for an SMC setup"
- "Run the GBPJPY confluence check"
- "Scan the full watchlist"
- "Start monitoring"

### 8. Start Jarvis background monitoring

Once everything is configured, tell Jarvis to start monitoring via Claude Code or Telegram:

- **In Claude Code:** ask "Start Jarvis monitoring"
- **In Telegram:** send `start` to your bot

Jarvis will send you a confirmation in Telegram and begin scanning on the configured interval.

---

## Telegram Command Reference

Send any of these to your Jarvis bot:

| Command | Description |
|---|---|
| `help` | Show the full command list |
| `balance` | Account balance, equity, and status |
| `positions` | All open trades |
| `price GBPJPY` | Live price for any symbol (also: `price gold`, `price nas100`) |
| `scan GBPJPY` | Full SMC scan for a symbol + auto-watch the levels found |
| `scan all` | Scan all watchlist symbols at once |
| `gbpjpy` | Full KJ top-down confluence check for GBPJPY |
| `watching` | Show all price levels Jarvis is actively tracking |
| `auto on` | Enable automatic order placement |
| `auto off` | Disable auto-trading (alerts only) |
| `risk 2` | Set risk per trade to 2% (accepted range: 0.5–5%) |
| `mode demo` | Switch to demo account |
| `mode live` | Switch to live account |
| `start` | Start background monitoring and price alerts |
| `stop` | Stop background monitoring |
| `status` | Show monitoring status, interval, and watched setup count |

**Symbol aliases** — you can use shorthand: `gj` or `guppy` for GBPJPY, `gold` or `xau` for XAUUSD, `nas` or `nasdaq` for NAS100, `eu` or `euro` for EURUSD, `nj` for NZDJPY, `ac` for AUDCAD.

---

## Strategies

### SMC (Smart Money Concepts)

The SMC scanner follows a strict 7-step pipeline. A setup is only flagged when **all steps pass** and R:R is at minimum 3:1.

**Step 1 — 4H Market Structure**
Jarvis reads 120 bars of 4H data and identifies higher highs/higher lows (bullish) or lower highs/lower lows (bearish) from pivot points. Ranging markets are skipped — no trade.

**Step 2 — 1H Demand or Supply Zones**
Zones are built from 250 bars of 1H data. A demand zone is the last bearish candle before an aggressive bullish impulse (unmitigated by future price). A supply zone is the inverse. Up to 3 zones are tracked per direction.

**Step 3 — Fair Value Gap (FVG) inside the zone**
A 3-candle imbalance where the third candle's wick does not overlap the first candle's wick. The FVG must sit inside the demand or supply zone to count as confluence.

**Step 4 — Liquidity Sweep (required entry trigger)**
Jarvis confirms a wick beyond the zone boundary with price closing back inside within 3 candles. Per the playbook: **do not enter before a sweep is confirmed**. This is the single most critical filter.

**Step 5 — VWAP bias alignment**
A session-reset VWAP is calculated on 1H data. Price above VWAP = bullish. Price below = bearish. This must agree with the trade direction (adds confluence, not a hard filter).

**Step 6 — Trendline proximity**
Support and resistance trendlines are projected from the last two pivot lows and highs respectively. If price is within 0.2% of the trendline at entry, trendline confluence is confirmed.

**Step 7 — R:R filter**
Entry is placed at the FVG level. Stop is placed below the sweep wick. Target is the opposing zone or 4x the risk if no zone exists. If R:R is below 3.0, the setup is discarded.

Confluence score is 1–5 based on how many of the above signals aligned.

---

### GBPJPY KJ Strategy (Top-Down)

This is a specific multi-timeframe confluence framework for GBPJPY. Minimum 5/10 confluences required before Jarvis flags a setup.

**Step 1 — Monthly: R/S Flip Zone**
Jarvis resamples 500 bars of daily data to monthly. It looks for a prior resistance level that price has broken above and is now retesting. Score: 1 point if confirmed.

**Step 2 — Weekly: Bullish Intent**
Resampled weekly candles are checked for wick rejections at the monthly zone level (lower wick > 50% of body size) and bullish closes. Score: up to 3 points based on rejection wicks and bullish close count.

**Step 3 — Daily: Zone Validation**
Three conditions scored on 20 bars of daily data:
- 3+ rejection closes (wick below zone, close above)
- Candle size decreasing (recent ATR < 85% of prior ATR — accumulation signal)
- Price consolidating (recent 5-day range < 35% of prior 10-day range)

Score: 1 point each, max 3.

**Step 4 — 4H: Reversal Pattern**
Jarvis scans for one of three patterns inside the monthly zone:
- **Inverse Head & Shoulders** — three pivot lows where the middle is the lowest, with a neckline level
- **Double Bottom** — two lows within 0.5% of each other with a bounce between them
- **Morning Star** — three-candle reversal: large bearish → small doji → large bullish

Score: 1 point if any pattern is found.

**Step 5 — 1H: Entry Zone**
Entry zone is defined by 1H pivot lows inside the monthly zone. Position in zone (bottom/mid/upper) determines lot guidance:
- Bottom of zone: largest position size
- Mid zone: medium size
- Upper zone: smallest lots only

VWAP and trendline confluence from 1H data are added to the scoring.

**Full 10-point checklist:**
1. Monthly R/S flip found
2. Weekly wick rejections (2+)
3. Weekly bullish closes (1+)
4. Daily rejection closes (3+)
5. Daily candle size decreasing
6. Daily consolidation
7. 4H reversal pattern found
8. 1H price inside entry zone
9. VWAP aligned bullish
10. Trendline confluence

---

## Auto-Trading

Auto-trading is **disabled by default**. Enable it explicitly:

- Telegram: `auto on`
- Claude Code: "Enable auto-trading"

**How it works:**
1. Jarvis finds a valid setup via SMC or GBPJPY scan
2. The entry zone, SL, and TP are added to the active watchlist
3. The price-check loop runs every 5 minutes
4. When price enters the zone, Jarvis places a limit order at the entry price with SL and TP set
5. Position size is calculated automatically: `risk_amount / (sl_pips × pip_value_per_lot)`

**Risk management:**
- Default risk is 2% per trade
- Minimum lot size is 0.01, maximum is 10.0
- Accepted risk range: 0.5%–5% (Jarvis will cap values outside this range)
- Change risk: `risk 1.5` (Telegram) or update `risk_percent` in `jarvis_config.json`

**Demo vs live:**
- Default is `demo`
- Switch with `mode live` (Telegram) or update `trade_mode` in `jarvis_config.json`
- Demo and live accounts use separate instrument IDs and route IDs — make sure both sets are filled in correctly in `jarvis_config.json`

**Auto-trade alert flow:**
When a setup triggers, you receive in sequence:
1. Entry zone alert with direction, entry, SL, TP
2. Order confirmation (symbol, lots, risk amount) — or error message if placement fails
3. TP approaching alert when price is within 0.3% of target
4. Neckline break alert (GBPJPY IH&S only)

---

## File Structure

```
tradelocker-bot/
│
├── mcp_server.py              # MCP server — Claude Code connects here
├── monitor.py                 # JarvisMonitor: scan loops, chat handler, Telegram commands
├── tradelocker_client.py      # TradeLocker REST API client (auth, candles, account, positions)
├── notifier.py                # Telegram send functions and alert formatters
├── order_executor.py          # Position sizing and limit order placement
├── watcher.py                 # SetupWatcher: tracks entry zones, fires level alerts
│
├── strategies/
│   ├── __init__.py
│   ├── smc.py                 # SMC scan pipeline (structure, zones, FVG, sweep, VWAP, trendlines, R:R)
│   └── gbpjpy.py              # GBPJPY KJ strategy (Monthly → Weekly → Daily → 4H → 1H)
│
├── requirements.txt           # Python dependencies
├── jarvis_config.json         # Runtime config (DO NOT commit — in .gitignore)
├── jarvis_config.example.json # Safe template for jarvis_config.json
│
├── SETUP.md                   # Quick-start for MCP-only usage
├── TELEGRAM_SETUP.md          # Telegram bot creation guide
└── README.md
```

`tradelocker_config.json` lives **one level up** from this repo (`../tradelocker_config.json`) to prevent accidental commits of broker credentials.

---

## MCP Tools Reference

When connected to Claude Code, the following tools are exposed:

| Tool | Description |
|---|---|
| `get_account` | Balance, equity, margin info |
| `get_positions` | All open positions |
| `get_price(symbol)` | Current price for any watchlist symbol |
| `get_candles(symbol, resolution, bars)` | OHLCV data — resolutions: 1m, 5m, 15m, 30m, 1H, 4H, 1D, 1W, 1M |
| `scan_smc(symbol)` | Full SMC pipeline scan for one symbol |
| `check_gbpjpy()` | Full GBPJPY KJ top-down confluence check |
| `scan_watchlist()` | SMC scan across all 6 watchlist symbols |
| `start_monitoring(interval_minutes)` | Start background scan and alert loop |
| `stop_monitoring()` | Stop background monitoring |
| `monitoring_status()` | Running state, interval, Telegram config, watched setup count |
| `get_watched_setups()` | All price levels currently being tracked |

---

## Contributing

1. Fork the repository
2. Create a feature branch: `git checkout -b feature/my-feature`
3. Commit your changes
4. Open a pull request against `main`

Issues and PRs are welcome. If you add a new strategy, follow the pattern in `strategies/smc.py` — a pure function `run_X_scan(symbol, client)` returning a standardised dict.

---

## Disclaimer

This software is for educational and research purposes only. It is not financial advice. Trading carries significant risk of financial loss. The strategies implemented here are not guaranteed to be profitable. Always test thoroughly on a demo account before using live funds. Use at your own risk.

The author is not responsible for any trading losses incurred through use of this software.
